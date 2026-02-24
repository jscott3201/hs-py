"""WebSocket compression throughput benchmark.

Starts a standalone WebSocket server and runs clients in three modes:
  1. JSON envelopes (baseline)
  2. Binary frames (no compression)
  3. Binary frames + zlib compression

Measures messages/sec, latency percentiles, and total bytes transferred.
Designed to run inside Docker for process isolation.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time

# Ensure the src package is importable when running outside of an installed env
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hs_py.encoding.json import decode_grid, encode_grid
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref
from hs_py.ops import HaystackOps
from hs_py.storage.memory import InMemoryAdapter
from hs_py.ws_client import WebSocketClient
from hs_py.ws_codec import COMP_LZMA, COMP_ZLIB
from hs_py.ws_server import WebSocketServer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "9876"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "10"))
DURATION_S = int(os.environ.get("DURATION_S", "15"))
WARMUP_S = int(os.environ.get("WARMUP_S", "3"))
AUTH_TOKEN = "bench-token-42"

# Number of entity rows to seed into the server (controls response payload size)
ENTITY_COUNT = int(os.environ.get("ENTITY_COUNT", "5000"))


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------


def _build_entities(n: int) -> list[dict]:
    """Build *n* site+point entities for the in-memory adapter."""
    entities: list[dict] = []
    for i in range(n):
        entities.append(
            {
                "id": Ref(f"p:{i}"),
                "dis": f"Point {i}",
                "point": MARKER,
                "sensor": MARKER,
                "kind": "Number",
                "unit": "°F",
                "curVal": Number(72.0 + (i % 30), "°F"),
                "siteRef": Ref("s:site-1"),
            }
        )
    return entities


# ---------------------------------------------------------------------------
# Benchmark worker
# ---------------------------------------------------------------------------


async def _worker(
    url: str,
    mode: str,
    results: list[tuple[str, float, int, bool]],
    stop: asyncio.Event,
    *,
    binary: bool = False,
    binary_compression: int | None = None,
    chunked: bool = False,
) -> None:
    """Run ops in a loop until *stop* is set.

    Each result tuple: (op_name, latency_s, response_bytes, ok).
    """
    try:
        async with WebSocketClient(
            url,
            auth_token=AUTH_TOKEN,
            binary=binary,
            binary_compression=binary_compression,
            chunked=chunked,
            pythonic=False,
            timeout=10.0,
            heartbeat=0,
        ) as client:
            while not stop.is_set():
                for op_name, coro_fn in [
                    ("about", lambda: client.about(raw=True)),
                    ("read", lambda: client.read("point and sensor", raw=True)),
                ]:
                    if stop.is_set():
                        break
                    t0 = time.monotonic()
                    try:
                        grid = await coro_fn()
                        elapsed = time.monotonic() - t0
                        # Estimate response size from grid
                        resp_bytes = len(encode_grid(grid)) if isinstance(grid, Grid) else 0
                        results.append((op_name, elapsed, resp_bytes, True))
                    except Exception as exc:
                        elapsed = time.monotonic() - t0
                        results.append((op_name, elapsed, 0, False))
                        if not hasattr(_worker, "_err_logged"):
                            print(f"  [{mode}] {op_name} error: {exc!r}")
                            _worker._err_logged = True  # type: ignore[attr-defined]
                        if "closed" in str(exc).lower():
                            return
    except Exception as exc:
        print(f"  [{mode}] Worker connect error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Benchmark runner for a single mode
# ---------------------------------------------------------------------------


async def _run_mode(
    url: str,
    mode: str,
    *,
    binary: bool = False,
    binary_compression: int | None = None,
    chunked: bool = False,
) -> dict:
    """Run a benchmark for a single mode and return stats."""
    print(f"\n{'='*60}")
    print(f"  Mode: {mode}")
    print(f"  Concurrency: {CONCURRENCY}, Duration: {DURATION_S}s, Warmup: {WARMUP_S}s")
    print(f"{'='*60}")

    # Warmup
    print(f"  Warming up ({WARMUP_S}s)...")
    warmup: list[tuple[str, float, int, bool]] = []
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(
            _worker(
                url,
                mode,
                warmup,
                stop,
                binary=binary,
                binary_compression=binary_compression,
                chunked=chunked,
            )
        )
        for _ in range(CONCURRENCY)
    ]
    await asyncio.sleep(WARMUP_S)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    print(f"  Warmup: {len(warmup)} messages")

    # Benchmark
    print(f"  Benchmarking ({DURATION_S}s)...")
    results: list[tuple[str, float, int, bool]] = []
    stop = asyncio.Event()
    t_start = time.monotonic()
    tasks = [
        asyncio.create_task(
            _worker(
                url,
                mode,
                results,
                stop,
                binary=binary,
                binary_compression=binary_compression,
                chunked=chunked,
            )
        )
        for _ in range(CONCURRENCY)
    ]
    await asyncio.sleep(DURATION_S)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    wall_time = time.monotonic() - t_start

    return _compute_stats(mode, results, wall_time)


def _compute_stats(
    mode: str, results: list[tuple[str, float, int, bool]], wall_time: float
) -> dict:
    total = len(results)
    errors = sum(1 for *_, ok in results if not ok)
    latencies = [t for _, t, _, ok in results if ok]
    resp_bytes = [b for _, _, b, ok in results if ok]

    if not latencies:
        return {
            "mode": mode,
            "total_messages": total,
            "errors": errors,
            "messages_per_sec": 0,
            "wall_time_s": round(wall_time, 2),
            "concurrency": CONCURRENCY,
            "total_response_MB": 0,
            "throughput_MB_s": 0,
            "latency_ms": {},
            "operations": {},
        }

    latencies.sort()
    total_bytes = sum(resp_bytes)
    stats: dict = {
        "mode": mode,
        "total_messages": total,
        "errors": errors,
        "wall_time_s": round(wall_time, 2),
        "messages_per_sec": round(total / wall_time, 1),
        "concurrency": CONCURRENCY,
        "total_response_MB": round(total_bytes / (1024 * 1024), 2),
        "throughput_MB_s": round(total_bytes / (1024 * 1024) / wall_time, 2),
        "latency_ms": {
            "min": round(latencies[0] * 1000, 2),
            "p50": round(_percentile(latencies, 50) * 1000, 2),
            "p95": round(_percentile(latencies, 95) * 1000, 2),
            "p99": round(_percentile(latencies, 99) * 1000, 2),
            "max": round(latencies[-1] * 1000, 2),
            "mean": round(statistics.mean(latencies) * 1000, 2),
        },
    }

    # Per-op breakdown
    by_op: dict[str, list[float]] = {}
    for op, t, _, ok in results:
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _main() -> None:
    # 1. Build entities and start server
    print(f"Building {ENTITY_COUNT} entities...")
    entities = _build_entities(ENTITY_COUNT)

    adapter = InMemoryAdapter()
    await adapter.start()
    adapter.load_entities(entities)

    ops = HaystackOps(storage=adapter)
    url = f"ws://{HOST}:{PORT}"
    print(f"Server will listen at {url} with {ENTITY_COUNT} entities")

    all_results: list[dict] = []

    modes: list[tuple[str, dict]] = [
        ("json", {"binary": False}),
        ("binary", {"binary": True}),
        ("binary+chunked", {"binary": True, "chunked": True}),
        ("binary+zlib", {"binary": True, "binary_compression": COMP_ZLIB}),
        ("binary+zlib+chunked", {"binary": True, "binary_compression": COMP_ZLIB, "chunked": True}),
        ("binary+lzma", {"binary": True, "binary_compression": COMP_LZMA}),
    ]

    for mode_name, client_opts in modes:
        is_binary = client_opts.get("binary", False)
        server_comp = client_opts.get("binary_compression")
        server_chunked = client_opts.get("chunked", False)
        server = WebSocketServer(
            ops,
            auth_token=AUTH_TOKEN,
            host=HOST,
            port=PORT,
            heartbeat=0,
            binary=is_binary,
            binary_compression=server_comp,
            chunked=server_chunked,
        )
        await server.start()
        await asyncio.sleep(0.2)

        try:
            r = await _run_mode(url, mode_name, **client_opts)
            all_results.append(r)
            _print_summary(r)
        finally:
            await server.stop()
            await asyncio.sleep(0.2)

    # Print comparison table
    _print_comparison(all_results)

    # Write results
    out = os.environ.get("RESULTS_FILE", "/results/ws_compression.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults written to {out}")


def _print_summary(r: dict) -> None:
    print(f"\n  Results: {r['total_messages']} msgs, "
          f"{r['messages_per_sec']} msg/s, "
          f"{r.get('throughput_MB_s', 0)} MB/s, "
          f"{r['errors']} errors")
    lat = r.get("latency_ms", {})
    print(f"  Latency: p50={lat.get('p50', '?')}ms  "
          f"p95={lat.get('p95', '?')}ms  "
          f"p99={lat.get('p99', '?')}ms")


def _print_comparison(results: list[dict]) -> None:
    print(f"\n{'='*80}")
    print("COMPARISON TABLE")
    print(f"{'='*80}")
    print(f"{'Mode':<25} {'msg/s':>8} {'p50ms':>8} {'p95ms':>8} {'p99ms':>8} {'MB/s':>8} {'errs':>6}")
    print("-" * 80)
    for r in results:
        lat = r.get("latency_ms", {})
        print(
            f"{r['mode']:<25} "
            f"{r.get('messages_per_sec', 0):>8} "
            f"{lat.get('p50', '?'):>8} "
            f"{lat.get('p95', '?'):>8} "
            f"{lat.get('p99', '?'):>8} "
            f"{r.get('throughput_MB_s', 0):>8} "
            f"{r.get('errors', 0):>6}"
        )
    print("=" * 80)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
