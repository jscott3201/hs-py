"""Basic HTTP client — connect, browse, and read data.

Demonstrates connecting to a Haystack server, browsing the site
hierarchy, reading tagged entities, and fetching historical data.

Usage::

    uv run python examples/client_basics.py --url http://localhost:8080/api
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from hs_py import Client, Ref


async def main(url: str) -> None:
    async with Client(url) as client:
        # Server info
        about = await client.about()
        print("Server:", about[0]["serverName"])

        # Browse top-level navigation
        nav = await client.nav()
        print(f"\nTop-level nav ({len(nav)} items):")
        for row in nav:
            print(f"  {row.get('dis', row.get('navId', '?'))}")

        # Read all sites
        sites = await client.read('site')
        print(f"\nSites ({len(sites)}):")
        for site in sites:
            print(f"  {site['dis']} — {site['id']}")

        if not sites:
            return

        # Read points under the first site
        site_ref = sites[0]["id"]
        points = await client.read(f'point and siteRef=={site_ref}')
        print(f"\nPoints under {sites[0]['dis']}: {len(points)}")
        for pt in points[:5]:
            print(f"  {pt.get('dis', '?')} [{pt.get('kind', '?')}]")
        if len(points) > 5:
            print(f"  ... and {len(points) - 5} more")

        # Read history for the first his-point
        his_points = [p for p in points if "his" in p]
        if his_points:
            ref = his_points[0]["id"]
            end = date.today()
            start = end - timedelta(days=1)
            his = await client.his_read(ref, start, end)
            print(f"\nHistory for {his_points[0].get('dis', ref)} ({len(his)} samples)")
            for row in his[:3]:
                print(f"  {row['ts']}  →  {row['val']}")

        # Read by explicit IDs
        if len(sites) >= 2:
            ids = [s["id"] for s in sites[:2]]
            batch = await client.read_by_ids(ids)
            print(f"\nBatch read {len(batch)} records by ID")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Basic Haystack client example")
    parser.add_argument("--url", default="http://localhost:8080/api")
    args = parser.parse_args()
    asyncio.run(main(args.url))
