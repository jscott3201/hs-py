"""Two-minute WebSocket stress test for the hs-py Haystack server.

Opens multiple persistent WebSocket connections and fires concurrent requests
across all op types. Tests both JSON and binary frame modes.

Usage::

    uv run python scripts/ws_stress.py [--url ws://localhost:8080/api/ws] [--duration 120]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import math
import random
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from hs_py.kinds import Number, Ref  # noqa: E402
from hs_py.ws_client import WebSocketClient  # noqa: E402

DURATION = 120
PIPELINE_DEPTH = 10


# ---------------------------------------------------------------------------
# Latency collector
# ---------------------------------------------------------------------------


class Stats:
    def __init__(self, name: str) -> None:
        self.name = name
        self.latencies: list[float] = []
        self.errors: int = 0

    def record(self, elapsed: float) -> None:
        self.latencies.append(elapsed)

    def record_error(self) -> None:
        self.errors += 1

    def report(self) -> str:
        n = len(self.latencies)
        if n == 0:
            return f"  {self.name:<32} no successful requests  err={self.errors}"
        lat = sorted(self.latencies)
        p50 = lat[int(n * 0.50)]
        p95 = lat[int(n * 0.95)]
        p99 = lat[min(int(n * 0.99), n - 1)]
        return (
            f"  {self.name:<32} {n:>7} ops  "
            f"p50={p50 * 1000:>6.1f}ms  "
            f"p95={p95 * 1000:>6.1f}ms  "
            f"p99={p99 * 1000:>6.1f}ms  "
            f"err={self.errors:>4}"
        )


# ---------------------------------------------------------------------------
# Workers — each gets its own WebSocket connection
# ---------------------------------------------------------------------------


async def worker_about(url: str, stats: Stats, deadline: float, binary: bool) -> None:
    try:
        async with WebSocketClient(url, binary=binary, heartbeat=0) as ws:
            while time.monotonic() < deadline:
                tasks = [ws.about() for _ in range(PIPELINE_DEPTH)]
                t0 = time.perf_counter()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                elapsed = (time.perf_counter() - t0) / len(results)
                for r in results:
                    if isinstance(r, Exception):
                        stats.record_error()
                    else:
                        stats.record(elapsed)
    except Exception:
        stats.record_error()


async def worker_read_filter(url: str, stats: Stats, deadline: float, binary: bool) -> None:
    filters = [
        "site",
        "ahu",
        "vav",
        "floor",
        "equip",
        "point and zone and air and temp",
        "point and his",
        "point and sensor",
    ]
    try:
        async with WebSocketClient(url, binary=binary, heartbeat=0) as ws:
            i = 0
            while time.monotonic() < deadline:
                tasks = []
                for _ in range(PIPELINE_DEPTH):
                    filt = filters[i % len(filters)]
                    i += 1
                    tasks.append(ws.read(filt))
                t0 = time.perf_counter()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                elapsed = (time.perf_counter() - t0) / len(results)
                for r in results:
                    if isinstance(r, Exception):
                        stats.record_error()
                    else:
                        stats.record(elapsed)
    except Exception:
        stats.record_error()


async def worker_read_ids(url: str, stats: Stats, deadline: float, binary: bool) -> None:
    try:
        async with WebSocketClient(url, binary=binary, heartbeat=0) as ws:
            while time.monotonic() < deadline:
                tasks = []
                for _ in range(PIPELINE_DEPTH):
                    start = random.randint(0, 200)
                    ids = [Ref(f"d-{start + j:04x}") for j in range(50)]
                    tasks.append(ws.read_by_ids(ids))
                t0 = time.perf_counter()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                elapsed = (time.perf_counter() - t0) / len(results)
                for r in results:
                    if isinstance(r, Exception):
                        stats.record_error()
                    else:
                        stats.record(elapsed)
    except Exception:
        stats.record_error()


async def worker_nav(url: str, stats: Stats, deadline: float, binary: bool) -> None:
    nav_ids = [None, "d-0000", "d-0001", "d-0002", "d-0005"]
    try:
        async with WebSocketClient(url, binary=binary, heartbeat=0) as ws:
            i = 0
            while time.monotonic() < deadline:
                tasks = []
                for _ in range(PIPELINE_DEPTH):
                    nav_id = nav_ids[i % len(nav_ids)]
                    i += 1
                    tasks.append(ws.nav(nav_id))
                t0 = time.perf_counter()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                elapsed = (time.perf_counter() - t0) / len(results)
                for r in results:
                    if isinstance(r, Exception):
                        stats.record_error()
                    else:
                        stats.record(elapsed)
    except Exception:
        stats.record_error()


async def worker_his_write(url: str, stats: Stats, deadline: float, binary: bool) -> None:
    base_ts = datetime.datetime(2025, 8, 1, tzinfo=datetime.UTC)
    batch = 0
    try:
        async with WebSocketClient(url, binary=binary, heartbeat=0) as ws:
            while time.monotonic() < deadline:
                write_tasks = []
                for _ in range(PIPELINE_DEPTH):
                    ref_idx = random.randint(0x00D3, 0x0456)
                    point_idx = 0x0D0F + (ref_idx - 0x00D3) * 5
                    ref = Ref(f"d-{point_idx:04x}")
                    samples = []
                    for j in range(24):
                        ts = base_ts + datetime.timedelta(hours=batch * 24, minutes=j * 15)
                        val = 72.0 + 4.0 * math.sin(2 * math.pi * j / 96) + random.uniform(-1, 1)
                        samples.append({"ts": ts, "val": Number(round(val, 1), "\u00b0F")})
                    batch += 1
                    write_tasks.append(ws.his_write(ref, samples))
                t0 = time.perf_counter()
                results = await asyncio.gather(*write_tasks, return_exceptions=True)
                elapsed = (time.perf_counter() - t0) / len(results)
                for r in results:
                    if isinstance(r, Exception):
                        stats.record_error()
                    else:
                        stats.record(elapsed)
    except Exception:
        stats.record_error()


async def worker_his_read(url: str, stats: Stats, deadline: float, binary: bool) -> None:
    try:
        async with WebSocketClient(url, binary=binary, heartbeat=0) as ws:
            while time.monotonic() < deadline:
                tasks = []
                for _ in range(PIPELINE_DEPTH):
                    ref_idx = random.randint(0x00D3, 0x0456)
                    point_idx = 0x0D0F + (ref_idx - 0x00D3) * 5
                    ref = Ref(f"d-{point_idx:04x}")
                    tasks.append(ws.his_read(ref, "2025-06-01"))
                t0 = time.perf_counter()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                elapsed = (time.perf_counter() - t0) / len(results)
                for r in results:
                    if isinstance(r, Exception):
                        stats.record_error()
                    else:
                        stats.record(elapsed)
    except Exception:
        stats.record_error()


async def worker_watch(url: str, stats: Stats, deadline: float, binary: bool) -> None:
    try:
        async with WebSocketClient(url, binary=binary, heartbeat=0) as ws:
            while time.monotonic() < deadline:
                ids = [Ref(f"d-{random.randint(0, 100):04x}") for _ in range(5)]
                t0 = time.perf_counter()
                try:
                    sub = await ws.watch_sub(ids, watch_dis="ws-stress")
                    watch_id = str(sub.meta.get("watchId", ""))
                    if watch_id:
                        await ws.watch_poll(watch_id)
                        await ws.watch_poll(watch_id, refresh=True)
                        await ws.watch_close(watch_id)
                    stats.record(time.perf_counter() - t0)
                except Exception:
                    stats.record_error()
    except Exception:
        stats.record_error()


async def worker_batch(url: str, stats: Stats, deadline: float, binary: bool) -> None:
    """Fire batch requests (JSON mode only, batch is JSON-envelope feature)."""
    if binary:
        return
    from hs_py.grid import Grid, GridBuilder

    try:
        async with WebSocketClient(url, binary=False, heartbeat=0) as ws:
            while time.monotonic() < deadline:
                calls = [
                    ("about", Grid.make_empty()),
                    (
                        "read",
                        GridBuilder()
                        .add_col("filter")
                        .add_col("limit")
                        .add_row({"filter": "site"})
                        .to_grid(),
                    ),
                    (
                        "read",
                        GridBuilder()
                        .add_col("filter")
                        .add_col("limit")
                        .add_row({"filter": "ahu"})
                        .to_grid(),
                    ),
                ]
                t0 = time.perf_counter()
                try:
                    await ws.batch(*calls)
                    stats.record(time.perf_counter() - t0)
                except Exception:
                    stats.record_error()
    except Exception:
        stats.record_error()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

WORKER_DEFS = [
    ("about", worker_about, 3),
    ("read_filter", worker_read_filter, 4),
    ("read_ids", worker_read_ids, 3),
    ("nav", worker_nav, 2),
    ("his_write", worker_his_write, 2),
    ("his_read", worker_his_read, 2),
    ("watch", worker_watch, 2),
    ("batch", worker_batch, 2),
]


async def run_mode(url: str, duration: int, binary: bool) -> list[Stats]:
    mode = "binary" if binary else "JSON"
    total_workers = sum(c for _, _, c in WORKER_DEFS)
    # batch workers are no-ops in binary mode
    if binary:
        total_workers -= next(c for n, _, c in WORKER_DEFS if n == "batch")
    print(f"\n{'=' * 90}")
    print(f"  WebSocket Stress Test — {mode} mode")
    print(f"  Duration: {duration}s | Workers: {total_workers} connections")
    print(f"{'=' * 90}")

    deadline = time.monotonic() + duration
    all_stats: list[Stats] = []
    tasks: list[asyncio.Task] = []

    for name, worker_fn, count in WORKER_DEFS:
        s = Stats(f"{mode}:{name}")
        all_stats.append(s)
        for _ in range(count):
            tasks.append(asyncio.create_task(worker_fn(url, s, deadline, binary)))

    start = time.monotonic()
    while time.monotonic() < deadline:
        await asyncio.sleep(10)
        elapsed = time.monotonic() - start
        total_ops = sum(len(s.latencies) for s in all_stats)
        total_err = sum(s.errors for s in all_stats)
        remaining = max(0, duration - elapsed)
        print(
            f"  [{elapsed:5.0f}s] {total_ops:>7} ops  "
            f"({total_ops / elapsed:.0f} ops/s)  "
            f"errors={total_err}  "
            f"remaining={remaining:.0f}s"
        )

    await asyncio.gather(*tasks)
    return all_stats


def print_report(label: str, all_stats: list[Stats], elapsed: float) -> None:
    total_ops = sum(len(s.latencies) for s in all_stats)
    total_err = sum(s.errors for s in all_stats)

    print(f"\n  --- {label} Results ({elapsed:.1f}s, {total_ops} ops, {total_err} errors) ---")
    print(f"  {'Op':<32} {'Count':>7}      {'p50':>9}  {'p95':>9}  {'p99':>9}  {'Errors':>6}")
    print(f"  {'-' * 32} {'-' * 7}      {'-' * 9}  {'-' * 9}  {'-' * 9}  {'-' * 6}")

    for s in all_stats:
        print(s.report())

    print(f"  {'-' * 32} {'-' * 7}")
    rate = total_ops / elapsed if elapsed > 0 else 0
    print(f"  {'TOTAL':<32} {total_ops:>7} ops   {rate:.0f} ops/s")

    all_lat = []
    for s in all_stats:
        all_lat.extend(s.latencies)
    if all_lat:
        all_lat.sort()
        n = len(all_lat)
        print(
            f"\n  Latency: "
            f"min={all_lat[0] * 1000:.1f}ms  "
            f"mean={statistics.mean(all_lat) * 1000:.1f}ms  "
            f"p50={all_lat[int(n * 0.50)] * 1000:.1f}ms  "
            f"p95={all_lat[int(n * 0.95)] * 1000:.1f}ms  "
            f"p99={all_lat[min(int(n * 0.99), n - 1)] * 1000:.1f}ms  "
            f"max={all_lat[-1] * 1000:.1f}ms"
        )


async def run(url: str, duration: int) -> None:
    print(f"WebSocket stress test: {url}")
    print(f"Duration: {duration}s")

    t0 = time.monotonic()
    all_stats = await run_mode(url, duration, binary=False)
    elapsed = time.monotonic() - t0
    print_report("WebSocket", all_stats, elapsed)


def main() -> None:
    parser = argparse.ArgumentParser(description="WebSocket stress test for hs-py")
    parser.add_argument("--url", default="ws://localhost:8080/api/ws")
    parser.add_argument("--duration", type=int, default=DURATION, help="Duration per mode (s)")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.duration))


if __name__ == "__main__":
    main()
