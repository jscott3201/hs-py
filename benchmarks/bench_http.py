"""HTTP API throughput benchmark.

Authenticates via SCRAM-SHA-256, then sends concurrent requests to the
Haystack HTTP API measuring requests/sec and latency percentiles.
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
# Configuration from environment
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("HS_PY_SERVER_URL", "http://server:8080/api")
USERNAME = os.environ.get("HS_PY_USER", "admin")
PASSWORD = os.environ.get("HS_PY_PASS", "secret")
CONCURRENCY = int(os.environ.get("HS_PY_CONCURRENCY", "50"))
DURATION_S = int(os.environ.get("HS_PY_DURATION", "30"))
WARMUP_S = int(os.environ.get("HS_PY_WARMUP", "3"))

# Endpoints to benchmark — mix of GET and POST ops
GET_ENDPOINTS = ["/about", "/ops"]

# POST ops need a grid body
POST_ENDPOINTS = [
    ("/read", {"meta": {"ver": "3.0"}, "cols": [{"name": "filter"}], "rows": [{"filter": "site"}]}),
    ("/read", {"meta": {"ver": "3.0"}, "cols": [{"name": "filter"}], "rows": [{"filter": "point"}]}),
    ("/nav", {"meta": {"ver": "3.0"}, "cols": [{"name": "navId"}], "rows": [{}]}),
]


# ---------------------------------------------------------------------------
# Benchmark driver
# ---------------------------------------------------------------------------


import orjson

# Pre-encode POST bodies for minimal overhead in the hot loop
_POST_BODIES = [(ep, orjson.dumps(body)) for ep, body in POST_ENDPOINTS]
_ALL_OPS = [(ep, None) for ep in GET_ENDPOINTS] + [(ep, body) for ep, body in _POST_BODIES]
_JSON_CT = {"Content-Type": "application/json"}


async def worker(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    results: list[tuple[str, float, int]],
    stop: asyncio.Event,
) -> None:
    """Send requests in a loop until stop is set."""
    post_headers = {**headers, **_JSON_CT}
    idx = 0
    while not stop.is_set():
        endpoint, body = _ALL_OPS[idx % len(_ALL_OPS)]
        url = f"{BASE_URL}{endpoint}"
        t0 = time.monotonic()
        try:
            if body is None:
                async with session.get(url, headers=headers) as resp:
                    await resp.read()
                    elapsed = time.monotonic() - t0
                    results.append((endpoint, elapsed, resp.status))
            else:
                async with session.post(url, headers=post_headers, data=body) as resp:
                    await resp.read()
                    elapsed = time.monotonic() - t0
                    results.append((endpoint, elapsed, resp.status))
        except Exception:
            elapsed = time.monotonic() - t0
            results.append((endpoint, elapsed, 0))
        idx += 1


async def run_benchmark() -> dict:
    """Run the HTTP benchmark and return results dict."""
    conn = aiohttp.TCPConnector(limit=CONCURRENCY + 10, force_close=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        print(f"Authenticating to {BASE_URL} as {USERNAME}...")
        token = await scram_auth(session, BASE_URL, USERNAME, PASSWORD)
        headers = {"Authorization": f"BEARER authToken={token}"}
        print(f"Authenticated. Token: {token[:20]}...")

        # Warmup
        print(f"Warming up for {WARMUP_S}s with {CONCURRENCY} workers...")
        warmup_results: list[tuple[str, float, int]] = []
        stop = asyncio.Event()
        tasks = [
            asyncio.create_task(worker(session, headers, warmup_results, stop))
            for _ in range(CONCURRENCY)
        ]
        await asyncio.sleep(WARMUP_S)
        stop.set()
        await asyncio.gather(*tasks)
        print(f"Warmup done: {len(warmup_results)} requests")

        # Actual benchmark
        print(f"Benchmarking for {DURATION_S}s with {CONCURRENCY} workers...")
        results: list[tuple[str, float, int]] = []
        stop = asyncio.Event()
        t_start = time.monotonic()
        tasks = [
            asyncio.create_task(worker(session, headers, results, stop))
            for _ in range(CONCURRENCY)
        ]
        await asyncio.sleep(DURATION_S)
        stop.set()
        await asyncio.gather(*tasks)
        wall_time = time.monotonic() - t_start

    return _compute_stats(results, wall_time)


def _compute_stats(
    results: list[tuple[str, float, int]], wall_time: float
) -> dict:
    """Compute aggregate and per-endpoint statistics."""
    total = len(results)
    errors = sum(1 for _, _, s in results if s != 200)
    latencies = [t for _, t, s in results if s == 200]

    if not latencies:
        return {"total": total, "errors": errors, "rps": 0}

    latencies.sort()
    stats = {
        "total_requests": total,
        "errors": errors,
        "wall_time_s": round(wall_time, 2),
        "rps": round(total / wall_time, 1),
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

    # Per-endpoint breakdown
    by_endpoint: dict[str, list[float]] = {}
    for ep, t, s in results:
        if s == 200:
            by_endpoint.setdefault(ep, []).append(t)

    stats["endpoints"] = {}
    for ep, lats in sorted(by_endpoint.items()):
        lats.sort()
        stats["endpoints"][ep] = {
            "count": len(lats),
            "rps": round(len(lats) / wall_time, 1),
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
    print("HTTP BENCHMARK RESULTS")
    print("=" * 60)
    print(json.dumps(results, indent=2))

    # Write to file for collection
    out = os.environ.get("HS_PY_RESULTS_FILE", "/results/http_results.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults written to {out}")


if __name__ == "__main__":
    main()
