"""In-memory Haystack server with FastAPI.

Demonstrates setting up a Haystack server with the in-memory storage
adapter, SCRAM-SHA-256 authentication, and custom ops implementation.

Usage::

    uv run python examples/server_memory.py
    # Then connect with: uv run python examples/client_basics.py
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import uvicorn

from hs_py import MARKER, GridBuilder, Number, Ref
from hs_py.auth_types import SimpleAuthenticator
from hs_py.fastapi_server import create_fastapi_app
from hs_py.ops import HaystackOps
from hs_py.storage.memory import InMemoryAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

def build_seed_data() -> list[dict]:
    """Create a small building model for demonstration."""
    return [
        {
            "id": Ref("site-1", "Main Office"),
            "dis": "Main Office",
            "site": MARKER,
            "area": Number(50000, "ft²"),
            "geoAddr": "123 Main St, Anytown USA",
        },
        {
            "id": Ref("ahu-1", "AHU-1"),
            "dis": "AHU-1",
            "equip": MARKER,
            "ahu": MARKER,
            "siteRef": Ref("site-1"),
        },
        {
            "id": Ref("znt-1", "Zone Temp 1"),
            "dis": "Zone Temp 1",
            "point": MARKER,
            "sensor": MARKER,
            "temp": MARKER,
            "zone": MARKER,
            "air": MARKER,
            "kind": "Number",
            "unit": "°F",
            "siteRef": Ref("site-1"),
            "equipRef": Ref("ahu-1"),
            "curVal": Number(72.4, "°F"),
        },
        {
            "id": Ref("dat-1", "Discharge Air Temp"),
            "dis": "Discharge Air Temp",
            "point": MARKER,
            "sensor": MARKER,
            "temp": MARKER,
            "discharge": MARKER,
            "air": MARKER,
            "kind": "Number",
            "unit": "°F",
            "siteRef": Ref("site-1"),
            "equipRef": Ref("ahu-1"),
            "curVal": Number(55.1, "°F"),
        },
    ]


# ---------------------------------------------------------------------------
# Server ops
# ---------------------------------------------------------------------------

class DemoOps(HaystackOps):
    """Ops backed by in-memory storage with seed data."""

    def __init__(self, storage: InMemoryAdapter) -> None:
        self._storage = storage

    async def about(self):
        return GridBuilder().add_col("serverName").add_col("productName").add_row(
            {"serverName": "haystack-py Demo Server", "productName": "haystack-py"}
        ).to_grid()

    async def read(self, filt, limit=None):
        return await self._storage.read(filt, limit)

    async def nav(self, nav_id=None):
        return await self._storage.nav(nav_id)

    async def read_by_ids(self, ids):
        return await self._storage.read_by_ids(ids)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Seed the storage on startup."""
    storage = app.state.storage
    for rec in build_seed_data():
        storage._recs[rec["id"].val] = rec
    print(f"Loaded {len(storage._recs)} seed records")
    yield


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    storage = InMemoryAdapter()
    ops = DemoOps(storage)
    auth = SimpleAuthenticator({"admin": "admin123"})

    app = create_fastapi_app(
        ops=ops,
        authenticator=auth,
        lifespan=lifespan,
    )
    app.state.storage = storage
    return app


if __name__ == "__main__":
    app = create_app()
    print("Starting haystack-py demo server on http://localhost:8080")
    print("Credentials: admin / admin123")
    uvicorn.run(app, host="0.0.0.0", port=8080)
