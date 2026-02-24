"""Client exercise script for the hs-py Haystack server.

Connects to a running server and exercises all standard ops across 8 phases,
printing timing and throughput benchmarks for each.

Usage::

    uv run python scripts/exercise.py [--url http://localhost:8080/api]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import math
import random
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from hs_py.client import Client  # noqa: E402
from hs_py.grid import Grid  # noqa: E402
from hs_py.kinds import Number, Ref  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RESULTS: list[tuple[str, float, str]] = []


def _banner(phase: int, title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Phase {phase}: {title}")
    print(f"{'=' * 60}")


def _record(label: str, elapsed: float, detail: str = "") -> None:
    _RESULTS.append((label, elapsed, detail))
    msg = f"  {label}: {elapsed:.3f}s"
    if detail:
        msg += f" ({detail})"
    print(msg)


# ---------------------------------------------------------------------------
# Phase 1: About / Ops / Formats
# ---------------------------------------------------------------------------


async def phase_about(client: Client) -> None:
    _banner(1, "About / Ops / Formats")

    t0 = time.perf_counter()
    about = await client.about()
    _record("about", time.perf_counter() - t0, f"{len(about)} rows")

    t0 = time.perf_counter()
    ops = await client.ops()
    _record("ops", time.perf_counter() - t0, f"{len(ops)} ops")

    t0 = time.perf_counter()
    fmt = await client.formats()
    _record("formats", time.perf_counter() - t0, f"{len(fmt)} formats")


# ---------------------------------------------------------------------------
# Phase 2: Read by filter
# ---------------------------------------------------------------------------


async def phase_read_filter(client: Client) -> dict[str, Grid]:
    _banner(2, "Read by Filter")

    filters = {
        "site": "site",
        "ahu": "ahu",
        "vav": "vav",
        "floor": "floor",
        "point": "point",
        "zone-air-temp": "point and zone and air and temp and sensor",
        "his-points": "point and his",
    }
    results: dict[str, Grid] = {}
    for label, filt in filters.items():
        t0 = time.perf_counter()
        grid = await client.read(filt)
        elapsed = time.perf_counter() - t0
        results[label] = grid
        _record(f"read({label})", elapsed, f"{len(grid)} entities")

    return results


# ---------------------------------------------------------------------------
# Phase 3: Read by ID
# ---------------------------------------------------------------------------


async def phase_read_ids(client: Client) -> None:
    _banner(3, "Read by ID")

    # Read a batch of known refs (site + first 10 floors + some AHUs)
    ids = [Ref(f"d-{i:04x}") for i in range(20)]

    t0 = time.perf_counter()
    grid = await client.read_by_ids(ids)
    _record("read_by_ids(20)", time.perf_counter() - t0, f"{len(grid)} entities")


# ---------------------------------------------------------------------------
# Phase 4: Nav
# ---------------------------------------------------------------------------


async def phase_nav(client: Client) -> None:
    _banner(4, "Nav")

    # Root navigation
    t0 = time.perf_counter()
    root = await client.nav()
    _record("nav(root)", time.perf_counter() - t0, f"{len(root)} children")

    # Drill into site
    if len(root) > 0:
        site_id = root[0].get("id")
        if isinstance(site_id, Ref):
            t0 = time.perf_counter()
            site_children = await client.nav(site_id.val)
            _record("nav(site)", time.perf_counter() - t0, f"{len(site_children)} children")

            # Drill one level deeper
            if len(site_children) > 0:
                child_id = site_children[0].get("id")
                if isinstance(child_id, Ref):
                    t0 = time.perf_counter()
                    grandchildren = await client.nav(child_id.val)
                    _record(
                        "nav(floor)",
                        time.perf_counter() - t0,
                        f"{len(grandchildren)} children",
                    )


# ---------------------------------------------------------------------------
# Phase 5: His Write
# ---------------------------------------------------------------------------


def _generate_samples(
    point_idx: int,
    count: int = 96,
) -> list[dict]:
    """Generate *count* 15-min samples as a sine wave 68-76 degF."""
    base = datetime.datetime(2025, 6, 1, tzinfo=datetime.UTC)
    samples: list[dict] = []
    random.seed(point_idx)
    for i in range(count):
        ts = base + datetime.timedelta(minutes=15 * i)
        # Sinusoidal temperature 68-76 degF
        val = 72.0 + 4.0 * math.sin(2 * math.pi * i / 96) + random.uniform(-0.5, 0.5)
        samples.append({"ts": ts, "val": Number(round(val, 1), "\u00b0F")})
    return samples


async def phase_his_write(client: Client, his_points: Grid) -> list[Ref]:
    _banner(5, "His Write")

    # Write to up to 50 his points for manageable timing
    limit = min(len(his_points), 50)
    point_refs: list[Ref] = []

    t0 = time.perf_counter()
    for i in range(limit):
        row = his_points[i]
        ref = row["id"]
        if not isinstance(ref, Ref):
            continue
        point_refs.append(ref)
        samples = _generate_samples(i)
        await client.his_write(ref, samples)

    elapsed = time.perf_counter() - t0
    total_samples = limit * 96
    rate = total_samples / elapsed if elapsed > 0 else 0
    _record(
        f"his_write({limit} points x 96 samples)",
        elapsed,
        f"{total_samples} samples, {rate:.0f} samples/s",
    )
    return point_refs


# ---------------------------------------------------------------------------
# Phase 6: His Read
# ---------------------------------------------------------------------------


async def phase_his_read(client: Client, point_refs: list[Ref]) -> None:
    _banner(6, "His Read")

    total_rows = 0
    t0 = time.perf_counter()
    for ref in point_refs[:10]:  # Read back first 10
        grid = await client.his_read(ref, "2025-06-01")
        total_rows += len(grid)

    elapsed = time.perf_counter() - t0
    _record(
        "his_read(10 points)",
        elapsed,
        f"{total_rows} total rows",
    )


# ---------------------------------------------------------------------------
# Phase 7: Point Write
# ---------------------------------------------------------------------------


async def phase_point_write(client: Client, his_points: Grid) -> None:
    _banner(7, "Point Write")

    if len(his_points) == 0:
        print("  No points available for point write")
        return

    ref = his_points[0]["id"]
    if not isinstance(ref, Ref):
        print("  First his point has no valid id")
        return

    # Write at priority level 8 (manual override)
    t0 = time.perf_counter()
    await client.point_write(ref, level=8, val=Number(72.0, "\u00b0F"), who="exercise")
    _record("point_write(level=8)", time.perf_counter() - t0)

    # Write at priority level 10 (scheduled)
    t0 = time.perf_counter()
    await client.point_write(ref, level=10, val=Number(74.0, "\u00b0F"), who="exercise")
    _record("point_write(level=10)", time.perf_counter() - t0)

    # Read back priority array
    t0 = time.perf_counter()
    arr = await client.point_write_array(ref)
    _record("point_write_array", time.perf_counter() - t0, f"{len(arr)} levels")

    # Release level 8
    t0 = time.perf_counter()
    await client.point_write(ref, level=8, val=None, who="exercise")
    _record("point_write(release level=8)", time.perf_counter() - t0)


# ---------------------------------------------------------------------------
# Phase 8: Watch Lifecycle
# ---------------------------------------------------------------------------


async def phase_watch(client: Client) -> None:
    _banner(8, "Watch Lifecycle")

    watch_ids = [Ref(f"d-{i:04x}") for i in range(5)]

    # Subscribe
    t0 = time.perf_counter()
    sub = await client.watch_sub(watch_ids, watch_dis="exercise-watch")
    watch_id = sub.meta.get("watchId", "")
    _record("watch_sub(5 entities)", time.perf_counter() - t0, f"watchId={watch_id}")

    if not watch_id:
        print("  No watchId returned, skipping remaining watch ops")
        return

    # Poll (should return empty — no changes)
    t0 = time.perf_counter()
    poll1 = await client.watch_poll(str(watch_id))
    _record("watch_poll(no changes)", time.perf_counter() - t0, f"{len(poll1)} changes")

    # Poll with refresh
    t0 = time.perf_counter()
    poll2 = await client.watch_poll(str(watch_id), refresh=True)
    _record("watch_poll(refresh)", time.perf_counter() - t0, f"{len(poll2)} entities")

    # Close watch
    t0 = time.perf_counter()
    await client.watch_close(str(watch_id))
    _record("watch_close", time.perf_counter() - t0)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _print_summary() -> None:
    print(f"\n{'=' * 60}")
    print("  Summary")
    print(f"{'=' * 60}")
    print(f"  {'Operation':<40} {'Time':>8}  Detail")
    print(f"  {'-' * 40} {'-' * 8}  {'-' * 30}")
    total = 0.0
    for label, elapsed, detail in _RESULTS:
        print(f"  {label:<40} {elapsed:>7.3f}s  {detail}")
        total += elapsed
    print(f"  {'-' * 40} {'-' * 8}")
    print(f"  {'TOTAL':<40} {total:>7.3f}s")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(url: str) -> None:
    """Run all exercise phases against the server."""
    print(f"Connecting to {url}")

    async with Client(url) as client:
        # Phase 1
        await phase_about(client)

        # Phase 2
        read_results = await phase_read_filter(client)

        # Phase 3
        await phase_read_ids(client)

        # Phase 4
        await phase_nav(client)

        # Phase 5
        his_points = read_results.get("his-points", Grid.make_empty())
        point_refs = await phase_his_write(client, his_points)

        # Phase 6
        await phase_his_read(client, point_refs)

        # Phase 7
        await phase_point_write(client, his_points)

        # Phase 8
        await phase_watch(client)

    _print_summary()


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise the hs-py Haystack server")
    parser.add_argument(
        "--url",
        default="http://localhost:8080/api",
        help="Server base URL (default: http://localhost:8080/api)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.url))


if __name__ == "__main__":
    main()
