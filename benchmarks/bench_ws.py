"""WebSocket API throughput benchmark.

Authenticates via SCRAM-SHA-256 over HTTP, then opens WebSocket connections
and sends Haystack ops measuring messages/sec and latency percentiles.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time

import aiohttp

from bench_auth import scram_auth

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("HS_PY_SERVER_URL", "http://server:8080/api")
WS_URL = BASE_URL.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
USERNAME = os.environ.get("HS_PY_USER", "admin")
PASSWORD = os.environ.get("HS_PY_PASS", "secret")
CONCURRENCY = int(os.environ.get("HS_PY_CONCURRENCY", "50"))
DURATION_S = int(os.environ.get("HS_PY_DURATION", "30"))
WARMUP_S = int(os.environ.get("HS_PY_WARMUP", "3"))

# Ops to cycle through — read/nav require a proper grid payload
OPS = [
    {"op": "about"},
    {"op": "ops"},
    {"op": "read", "grid": {
        "_kind": "grid", "meta": {"ver": "3.0"},
        "cols": [{"name": "filter"}], "rows": [{"filter": "site"}],
    }},
    {"op": "read", "grid": {
        "_kind": "grid", "meta": {"ver": "3.0"},
        "cols": [{"name": "filter"}], "rows": [{"filter": "point"}],
    }},
    {"op": "nav", "grid": {
        "_kind": "grid", "meta": {"ver": "3.0"},
        "cols": [{"name": "navId"}], "rows": [{}],
    }},
]


# ---------------------------------------------------------------------------
# WebSocket benchmark worker
# ---------------------------------------------------------------------------


async def ws_worker(
    session: aiohttp.ClientSession,
    token: str,
    results: list[tuple[str, float, bool]],
    stop: asyncio.Event,
) -> None:
    """Open a WebSocket and send ops until stopped."""
    try:
        async with session.ws_connect(
            WS_URL, protocols=["haystack"], max_msg_size=2**24,
        ) as ws:
            # First message must authenticate
            await ws.send_json({"authToken": token})
            idx = 0
            msg_id = 0
            while not stop.is_set():
                op = OPS[idx % len(OPS)]
                msg_id += 1
                payload = {**op, "id": msg_id}
                t0 = time.monotonic()
                try:
                    await ws.send_json(payload)
                    resp = await asyncio.wait_for(ws.receive(), timeout=5.0)
                    elapsed = time.monotonic() - t0
                    ok = resp.type == aiohttp.WSMsgType.TEXT
                    results.append((op["op"], elapsed, ok))
                except Exception:
                    elapsed = time.monotonic() - t0
                    results.append((op["op"], elapsed, False))
                idx += 1
    except Exception as exc:
        print(f"Worker connection error: {exc}")


async def run_benchmark() -> dict:
    """Run the WebSocket benchmark and return results dict."""
    conn = aiohttp.TCPConnector(limit=CONCURRENCY + 10, force_close=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        print(f"Authenticating to {BASE_URL} as {USERNAME}...")
        token = await scram_auth(session, BASE_URL, USERNAME, PASSWORD)
        print(f"Authenticated. Opening {CONCURRENCY} WebSocket connections...")

        # Warmup
        print(f"Warming up for {WARMUP_S}s...")
        warmup_results: list[tuple[str, float, bool]] = []
        stop = asyncio.Event()
        tasks = [
            asyncio.create_task(ws_worker(session, token, warmup_results, stop))
            for _ in range(CONCURRENCY)
        ]
        await asyncio.sleep(WARMUP_S)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        print(f"Warmup done: {len(warmup_results)} messages")

        # Benchmark
        print(f"Benchmarking for {DURATION_S}s with {CONCURRENCY} connections...")
        results: list[tuple[str, float, bool]] = []
        stop = asyncio.Event()
        t_start = time.monotonic()
        tasks = [
            asyncio.create_task(ws_worker(session, token, results, stop))
            for _ in range(CONCURRENCY)
        ]
        await asyncio.sleep(DURATION_S)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        wall_time = time.monotonic() - t_start

    return _compute_stats(results, wall_time)


def _compute_stats(
    results: list[tuple[str, float, bool]], wall_time: float
) -> dict:
    total = len(results)
    errors = sum(1 for _, _, ok in results if not ok)
    latencies = [t for _, t, ok in results if ok]

    if not latencies:
        return {"total": total, "errors": errors, "mps": 0}

    latencies.sort()
    stats = {
        "total_messages": total,
        "errors": errors,
        "wall_time_s": round(wall_time, 2),
        "messages_per_sec": round(total / wall_time, 1),
        "concurrency": CONCURRENCY,
        "duration_s": DURATION_S,
        "latency_ms": {
            "min": round(latencies[0] * 1000, 2),
            "p50": round(_percentile(latencies, 50) * 1000, 2),
            "p95": round(_percentile(latencies, 95) * 1000, 2),
            "p99": round(_percentile(latencies, 99) * 1000, 2),
            "max": round(latencies[-1] * 1000, 2),
            "mean": round(statistics.mean(latencies) * 1000, 2),
            "stdev": round(statistics.stdev(latencies) * 1000, 2) if len(latencies) > 1 else 0,
        },
    }

    # Per-op breakdown
    by_op: dict[str, list[float]] = {}
    for op, t, ok in results:
        if ok:
            by_op.setdefault(op, []).append(t)

    stats["operations"] = {}
    for op, lats in sorted(by_op.items()):
        lats.sort()
        stats["operations"][op] = {
            "count": len(lats),
            "mps": round(len(lats) / wall_time, 1),
            "p50_ms": round(_percentile(lats, 50) * 1000, 2),
            "p99_ms": round(_percentile(lats, 99) * 1000, 2),
        }

    return stats


def _percentile(data: list[float], pct: int) -> float:
    idx = int(len(data) * pct / 100)
    return data[min(idx, len(data) - 1)]


def main() -> None:
    results = asyncio.run(run_benchmark())
    print("\n" + "=" * 60)
    print("WEBSOCKET BENCHMARK RESULTS")
    print("=" * 60)
    print(json.dumps(results, indent=2))

    out = os.environ.get("HS_PY_RESULTS_FILE", "/results/ws_results.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults written to {out}")


if __name__ == "__main__":
    main()
