"""WebSocket client — persistent connection with multiplexed ops.

Demonstrates using the WebSocket client for low-latency bidirectional
communication with a Haystack server.

Usage::

    uv run python examples/websocket_client.py --url ws://localhost:8080/api/ws
"""

from __future__ import annotations

import argparse
import asyncio

from hs_py import Grid, Ref, WebSocketClient


async def main(url: str) -> None:
    async with WebSocketClient(url) as ws:
        # Server info
        about = await ws.about()
        print("Connected to:", about[0]["serverName"])

        # List supported ops
        ops = await ws.ops()
        print(f"\nSupported ops ({len(ops)}):")
        for row in ops:
            print(f"  {row['name']}")

        # Read all sites
        sites = await ws.read("site")
        print(f"\nSites ({len(sites)}):")
        for site in sites:
            print(f"  {site.get('dis', '?')}")

        # Concurrent reads via gather — all multiplexed on one connection
        if sites:
            site_ref = sites[0]["id"]
            results = await asyncio.gather(
                ws.read(f"equip and siteRef=={site_ref}"),
                ws.read(f"point and siteRef=={site_ref}"),
                ws.nav(),
            )
            equips, points, nav = results
            print(f"\nConcurrent results for {sites[0].get('dis', '?')}:")
            print(f"  Equips: {len(equips)}")
            print(f"  Points: {len(points)}")
            print(f"  Nav items: {len(nav)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebSocket Haystack client example")
    parser.add_argument("--url", default="ws://localhost:8080/api/ws")
    args = parser.parse_args()
    asyncio.run(main(args.url))
