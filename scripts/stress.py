"""Two-minute stress test for the hs-py Haystack server.

Fires concurrent requests across multiple op types and reports throughput
and latency percentiles.

Usage::

    uv run python scripts/stress.py [--url http://localhost:8080/api] [--duration 120]
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

from hs_py.client import Client  # noqa: E402
from hs_py.kinds import Number, Ref  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONCURRENCY = 20  # parallel workers per op type
DURATION = 120  # seconds


# ---------------------------------------------------------------------------
# Latency collector
# ---------------------------------------------------------------------------


class Stats:
    """Thread-safe-ish latency collector (single event loop)."""

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
            return f"  {self.name:<30} {'no successful requests':>50}"
        lat = sorted(self.latencies)
        p50 = lat[int(n * 0.50)]
        p95 = lat[int(n * 0.95)]
        p99 = lat[min(int(n * 0.99), n - 1)]
        total_time = sum(lat)
        ops_sec = n / total_time if total_time > 0 else 0
        return (
            f"  {self.name:<24} "
            f"  {n:>6} ops  "
            f"  p50={p50 * 1000:>6.1f}ms  "
            f"  p95={p95 * 1000:>6.1f}ms  "
            f"  p99={p99 * 1000:>6.1f}ms  "
            f"  err={self.errors:>4}"
        )


# ---------------------------------------------------------------------------
# Worker coroutines
# ---------------------------------------------------------------------------


async def worker_read_filter(client: Client, stats: Stats, deadline: float) -> None:
    filters = [
        "site",
        "ahu",
        "vav",
        "point and zone and air and temp",
        "floor",
        "equip",
        "point and his",
        "point and sensor",
    ]
    i = 0
    while time.monotonic() < deadline:
        filt = filters[i % len(filters)]
        i += 1
        t0 = time.perf_counter()
        try:
            await client.read(filt)
            stats.record(time.perf_counter() - t0)
        except Exception:
            stats.record_error()


async def worker_read_ids(client: Client, stats: Stats, deadline: float) -> None:
    batch_size = 50
    while time.monotonic() < deadline:
        start = random.randint(0, 200)
        ids = [Ref(f"d-{start + j:04x}") for j in range(batch_size)]
        t0 = time.perf_counter()
        try:
            await client.read_by_ids(ids)
            stats.record(time.perf_counter() - t0)
        except Exception:
            stats.record_error()


async def worker_nav(client: Client, stats: Stats, deadline: float) -> None:
    nav_ids = [None, "d-0000", "d-0001", "d-0002", "d-0005", "d-000a"]
    i = 0
    while time.monotonic() < deadline:
        nav_id = nav_ids[i % len(nav_ids)]
        i += 1
        t0 = time.perf_counter()
        try:
            await client.nav(nav_id)
            stats.record(time.perf_counter() - t0)
        except Exception:
            stats.record_error()


async def worker_his_write(client: Client, stats: Stats, deadline: float) -> None:
    # Write to random his points with small batches
    base_ts = datetime.datetime(2025, 7, 1, tzinfo=datetime.UTC)
    batch_counter = 0
    while time.monotonic() < deadline:
        ref_idx = random.randint(0x00D3, 0x0456)  # VAV range
        # ZoneAirTemp is first point after each VAV (5 points per VAV)
        point_idx = 0x0D0F + (ref_idx - 0x00D3) * 5
        ref = Ref(f"d-{point_idx:04x}")
        samples = []
        for j in range(24):
            ts = base_ts + datetime.timedelta(hours=batch_counter * 24, minutes=j * 15)
            val = 72.0 + 4.0 * math.sin(2 * math.pi * j / 96) + random.uniform(-1, 1)
            samples.append({"ts": ts, "val": Number(round(val, 1), "\u00b0F")})
        batch_counter += 1
        t0 = time.perf_counter()
        try:
            await client.his_write(ref, samples)
            stats.record(time.perf_counter() - t0)
        except Exception:
            stats.record_error()


async def worker_his_read(client: Client, stats: Stats, deadline: float) -> None:
    while time.monotonic() < deadline:
        ref_idx = random.randint(0x00D3, 0x0456)
        point_idx = 0x0D0F + (ref_idx - 0x00D3) * 5
        ref = Ref(f"d-{point_idx:04x}")
        t0 = time.perf_counter()
        try:
            await client.his_read(ref, "2025-06-01")
            stats.record(time.perf_counter() - t0)
        except Exception:
            stats.record_error()


async def worker_watch(client: Client, stats: Stats, deadline: float) -> None:
    while time.monotonic() < deadline:
        ids = [Ref(f"d-{random.randint(0, 100):04x}") for _ in range(5)]
        t0 = time.perf_counter()
        try:
            sub = await client.watch_sub(ids, watch_dis="stress")
            watch_id = str(sub.meta.get("watchId", ""))
            if watch_id:
                await client.watch_poll(watch_id)
                await client.watch_poll(watch_id, refresh=True)
                await client.watch_close(watch_id)
            stats.record(time.perf_counter() - t0)
        except Exception:
            stats.record_error()


async def worker_about(client: Client, stats: Stats, deadline: float) -> None:
    while time.monotonic() < deadline:
        t0 = time.perf_counter()
        try:
            await client.about()
            stats.record(time.perf_counter() - t0)
        except Exception:
            stats.record_error()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

WORKER_DEFS: list[tuple[str, type, int]] = [
    ("read_filter", worker_read_filter, 6),
    ("read_ids", worker_read_ids, 4),
    ("nav", worker_nav, 3),
    ("his_write", worker_his_write, 3),
    ("his_read", worker_his_read, 3),
    ("watch", worker_watch, 2),
    ("about", worker_about, 2),
]


async def run(url: str, duration: int) -> None:
    print(f"Stress test: {url}")
    print(f"Duration: {duration}s")
    total_workers = sum(count for _, _, count in WORKER_DEFS)
    print(f"Workers: {total_workers} ({', '.join(f'{n}={c}' for n, _, c in WORKER_DEFS)})")
    print()

    deadline = time.monotonic() + duration

    # Create a shared connector with a large pool
    import aiohttp

    connector = aiohttp.TCPConnector(limit=100, limit_per_host=100)

    async with Client(url, connector=connector) as client:
        # Warm up
        await client.about()

        all_stats: list[Stats] = []
        tasks: list[asyncio.Task] = []

        for name, worker_fn, count in WORKER_DEFS:
            s = Stats(name)
            all_stats.append(s)
            for _ in range(count):
                tasks.append(asyncio.create_task(worker_fn(client, s, deadline)))

        # Progress reporting
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

        # Wait for workers to finish
        await asyncio.gather(*tasks)

    # Report
    elapsed = time.monotonic() - start
    total_ops = sum(len(s.latencies) for s in all_stats)
    total_err = sum(s.errors for s in all_stats)

    print(f"\n{'=' * 90}")
    print(f"  Stress Test Results  ({elapsed:.1f}s actual, {total_ops} total ops, {total_err} errors)")
    print(f"{'=' * 90}")
    print(
        f"  {'Op':<24}   {'Count':>6}       "
        f"{'p50':>9}    {'p95':>9}    {'p99':>9}    {'Errors':>6}"
    )
    print(f"  {'-' * 24}   {'-' * 6}       {'-' * 9}    {'-' * 9}    {'-' * 9}    {'-' * 6}")

    for s in all_stats:
        print(s.report())

    print(f"  {'-' * 24}   {'-' * 6}")
    print(f"  {'TOTAL':<24}   {total_ops:>6} ops    {total_ops / elapsed:.0f} ops/s aggregate")

    # Overall latency
    all_lat = []
    for s in all_stats:
        all_lat.extend(s.latencies)
    if all_lat:
        all_lat.sort()
        n = len(all_lat)
        print(
            f"\n  Overall latency: "
            f"min={all_lat[0] * 1000:.1f}ms  "
            f"mean={statistics.mean(all_lat) * 1000:.1f}ms  "
            f"p50={all_lat[int(n * 0.50)] * 1000:.1f}ms  "
            f"p99={all_lat[min(int(n * 0.99), n - 1)] * 1000:.1f}ms  "
            f"max={all_lat[-1] * 1000:.1f}ms"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Stress test the hs-py server")
    parser.add_argument("--url", default="http://localhost:8080/api")
    parser.add_argument("--duration", type=int, default=DURATION)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.duration))


if __name__ == "__main__":
    main()
