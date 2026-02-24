#!/usr/bin/env python3
"""Profile the Haystack server locally with PyInstrument.

Starts a Haystack HTTP+WS server on a local port with pyinstrument
profiling the asyncio event loop.  Any client (Docker containers,
``bench_http.py``, curl, etc.) can hit the exposed port.

The server runs until interrupted (Ctrl-C / SIGTERM), then writes
HTML flame graphs and text summaries to ``benchmarks/results/profiles/``.

Usage::

    # InMemory backend, default port 8080
    uv run python benchmarks/bench_profile_server.py

    # Redis backend on custom port
    uv run python benchmarks/bench_profile_server.py --backend redis --port 9090

    # Then point Docker clients at host.docker.internal:<port>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pyinstrument import Profiler

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("profile_server")

DATA_DIR = Path(__file__).resolve().parent / "data"
RESULTS_DIR = Path(__file__).resolve().parent / "results" / "profiles"


# ---------------------------------------------------------------------------
# Data loading & adapter creation
# ---------------------------------------------------------------------------

def _load_entities() -> list[dict[str, Any]]:
    """Load entities from JSON files in the benchmark data directory."""
    from hs_py.encoding.json import decode_grid

    entities: list[dict[str, Any]] = []
    for json_file in sorted(DATA_DIR.rglob("*.json")):
        grid = decode_grid(json_file.read_bytes())
        rows = [dict(row) for row in grid]
        entities.extend(rows)
        _log.info("Loaded %s: %d entities", json_file.name, len(rows))
    return entities


async def _create_adapter(backend: str, entities: list[dict[str, Any]]) -> Any:
    """Create and populate a storage adapter for *backend*."""
    if backend == "inmemory":
        from hs_py.storage.memory import InMemoryAdapter

        adapter = InMemoryAdapter()
        await adapter.start()
        adapter.load_entities(entities)
        return adapter

    if backend == "redis":
        from hs_py.storage.redis import RedisAdapter, create_redis_client

        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        redis = create_redis_client(url)
        adapter = RedisAdapter(redis)
        await adapter.start()
        await adapter.load_entities(entities)
        return adapter

    if backend == "timescale":
        from hs_py.storage.timescale import TimescaleAdapter, create_timescale_pool

        dsn = os.environ.get(
            "TIMESCALE_DSN", "postgresql://postgres:test@localhost:5432/haystack"
        )
        pool = await create_timescale_pool(dsn)
        adapter = TimescaleAdapter(pool)
        await adapter.start()
        await adapter.load_entities(entities)
        return adapter

    _log.error("Unknown backend: %s", backend)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _run(backend: str, host: str, port: int) -> None:
    import uvicorn

    from hs_py.auth_types import SimpleAuthenticator
    from hs_py.fastapi_server import create_fastapi_app
    from hs_py.ops import HaystackOps

    username, password = "admin", "secret"
    authenticator = SimpleAuthenticator({username: password})

    entities = _load_entities()
    _log.info("Total entities: %d", len(entities))

    adapter = await _create_adapter(backend, entities)
    ops = HaystackOps(storage=adapter)
    app = create_fastapi_app(ops=ops, authenticator=authenticator)

    profiler = Profiler(async_mode="enabled")

    config = uvicorn.Config(
        app, host=host, port=port, log_level="info",
        ws_max_size=2**24,  # 16 MiB — large read grids exceed 1 MiB default
    )
    server = uvicorn.Server(config)

    # Trap shutdown signals to save the profile before exiting
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    _log.info("Starting profiled server on %s:%d (backend=%s)", host, port, backend)
    _log.info("Send load from Docker or locally, then Ctrl-C to stop and save profile.")

    profiler.start()
    serve_task = asyncio.create_task(server.serve())

    # Wait for shutdown signal
    await shutdown_event.wait()
    _log.info("Shutdown signal received, stopping…")

    server.should_exit = True
    await serve_task
    profiler.stop()

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    html_path = RESULTS_DIR / f"server_{backend}.html"
    text_path = RESULTS_DIR / f"server_{backend}.txt"
    html_path.write_text(profiler.output_html())
    text_path.write_text(profiler.output_text(unicode=True, color=False))

    _log.info("Profile HTML → %s", html_path)
    _log.info("Profile text → %s", text_path)

    # Print summary to terminal
    print(profiler.output_text(unicode=True, color=True))

    # Cleanup adapter
    if hasattr(adapter, "close"):
        await adapter.close()
    elif hasattr(adapter, "stop"):
        await adapter.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Haystack server locally with PyInstrument profiling."
    )
    parser.add_argument(
        "--backend", choices=["inmemory", "redis", "timescale"],
        default="inmemory", help="Storage backend (default: inmemory)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    args = parser.parse_args()

    print(f"PyInstrument Server Profiler")
    print(f"  Backend:  {args.backend}")
    print(f"  Listen:   {args.host}:{args.port}")
    print(f"  Profiles: {RESULTS_DIR}/")
    print(f"  Credentials: admin / secret")
    print()

    asyncio.run(_run(args.backend, args.host, args.port))


if __name__ == "__main__":
    main()
