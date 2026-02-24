"""Benchmark server entrypoint.

Starts a Haystack HTTP+WS server with a configurable storage backend
(inmemory, redis, timescale) and loads data from all supported formats
(JSON, Trio, Zinc) to benchmark decoding + ingest.

Environment variables:

- ``BACKEND`` — Storage backend: ``inmemory``, ``redis``, ``timescale``
  (default: ``inmemory``).
- ``REDIS_URL`` — Redis connection URL (default: ``redis://redis:6379``).
- ``TIMESCALE_DSN`` — TimescaleDB DSN (default:
  ``postgresql://postgres:test@timescaledb:5432/haystack``).
- ``DATA_DIR`` — Path to entity data directory (default: ``/app/data``).
- ``HOST`` / ``PORT`` — Bind address (default: ``0.0.0.0:8080``).
- ``HAYSTACK_USER`` / ``HAYSTACK_PASS`` — SCRAM credentials.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("bench_server")

BACKEND = os.environ.get("BACKEND", "inmemory")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
HS_USER = os.environ.get("HAYSTACK_USER", "admin")
HS_PASS = os.environ.get("HAYSTACK_PASS", "secret")


def _load_and_time_formats(data_dir: Path) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Load entities from JSON and time decoding of all formats.

    Only JSON entities are returned for ingest (Trio/Zinc contain the same
    data). All three decoders are exercised and timed for the benchmark report.
    """
    from hs_py.encoding.json import decode_grid as json_decode
    from hs_py.encoding.trio import parse_trio
    from hs_py.encoding.zinc import decode_grid as zinc_decode

    all_entities: list[dict[str, Any]] = []
    timings: dict[str, float] = {}

    for json_file in sorted(data_dir.rglob("*.json")):
        t0 = time.monotonic()
        grid = json_decode(json_file.read_bytes())
        elapsed = time.monotonic() - t0
        rows = [dict(row) for row in grid]
        all_entities.extend(rows)
        timings[json_file.name] = elapsed
        _log.info("JSON  %s: %d entities in %.3fs", json_file.name, len(rows), elapsed)

    for trio_file in sorted(data_dir.rglob("*.trio")):
        t0 = time.monotonic()
        try:
            records = parse_trio(trio_file.read_text())
            elapsed = time.monotonic() - t0
            timings[trio_file.name] = elapsed
            _log.info("Trio  %s: %d records in %.3fs", trio_file.name, len(records), elapsed)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            timings[trio_file.name] = elapsed
            _log.warning("Trio  %s: decode error in %.3fs: %s", trio_file.name, elapsed, exc)

    for zinc_file in sorted(data_dir.rglob("*.zinc")):
        t0 = time.monotonic()
        try:
            grid = zinc_decode(zinc_file.read_text())
            elapsed = time.monotonic() - t0
            timings[zinc_file.name] = elapsed
            _log.info("Zinc  %s: %d rows in %.3fs", zinc_file.name, len(grid), elapsed)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            timings[zinc_file.name] = elapsed
            _log.warning("Zinc  %s: decode error in %.3fs: %s", zinc_file.name, elapsed, exc)

    _log.info(
        "Decode timings: %s",
        ", ".join(f"{k}={v:.3f}s" for k, v in sorted(timings.items())),
    )
    return all_entities, timings


async def _create_adapter(
    entities: list[dict[str, Any]],
) -> Any:
    """Create and populate a storage adapter for the configured backend."""
    if BACKEND == "inmemory":
        from hs_py.storage.memory import InMemoryAdapter

        adapter = InMemoryAdapter()
        await adapter.start()
        adapter.load_entities(entities)
        _log.info("InMemory: %d entities loaded", len(entities))
        return adapter

    if BACKEND == "redis":
        from hs_py.storage.redis import RedisAdapter, create_redis_client

        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379")
        redis = create_redis_client(redis_url)
        adapter = RedisAdapter(redis)
        await adapter.start()
        t0 = time.monotonic()
        count = await adapter.load_entities(entities)
        _log.info("Redis: %d entities loaded in %.3fs", count, time.monotonic() - t0)
        return adapter

    if BACKEND == "timescale":
        from hs_py.storage.timescale import TimescaleAdapter, create_timescale_pool

        dsn = os.environ.get(
            "TIMESCALE_DSN", "postgresql://postgres:test@timescaledb:5432/haystack"
        )
        pool = await create_timescale_pool(dsn)
        adapter = TimescaleAdapter(pool)
        await adapter.start()
        t0 = time.monotonic()
        count = await adapter.load_entities(entities)
        _log.info("TimescaleDB: %d entities loaded in %.3fs", count, time.monotonic() - t0)
        return adapter

    _log.error("Unknown backend: %s", BACKEND)
    sys.exit(1)


def main() -> None:
    """Start the benchmark server."""
    import uvicorn

    from hs_py.auth_types import SimpleAuthenticator
    from hs_py.fastapi_server import create_fastapi_app
    from hs_py.ops import HaystackOps
    from hs_py.storage.memory import InMemoryAdapter

    if not HS_USER or not HS_PASS:
        _log.error("HAYSTACK_USER and HAYSTACK_PASS must be set.")
        sys.exit(1)

    authenticator = SimpleAuthenticator({HS_USER: HS_PASS})
    entities, decode_timings = _load_and_time_formats(DATA_DIR)
    _log.info("Total entities for ingest: %d", len(entities))

    # Create app with a temporary adapter — real one is swapped in at startup
    app = create_fastapi_app(
        ops=HaystackOps(storage=InMemoryAdapter()),
        authenticator=authenticator,
    )
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app_: object) -> AsyncGenerator[None]:
        adapter = await _create_adapter(entities)
        ops = HaystackOps(storage=adapter)
        app.state.ops = ops
        _log.info("Backend ready: %s", BACKEND)

        async with original_lifespan(app_):
            yield

        if hasattr(adapter, "close"):
            await adapter.close()
        elif hasattr(adapter, "stop"):
            await adapter.stop()

    app.router.lifespan_context = lifespan

    _log.info("Starting server on %s:%d (backend=%s)", HOST, PORT, BACKEND)
    uvicorn.run(app, host=HOST, port=PORT, ws_max_size=2**24)


if __name__ == "__main__":
    main()
