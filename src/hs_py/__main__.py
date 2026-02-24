"""Run the hs-py server: ``python -m hs_py``.

Loads entity data from ``_data/`` JSON files into Redis and starts an
HTTP server. Requires a running Redis 8 instance (default:
``localhost:6379``).

Environment variables:

- ``HAYSTACK_USER`` — Username for SCRAM-SHA-256 authentication (required).
- ``HAYSTACK_PASS`` — Password for SCRAM-SHA-256 authentication (required).
- ``HOST`` — Bind address (default: ``0.0.0.0``).
- ``PORT`` — Bind port (default: ``8080``).
- ``REDIS_URL`` — Redis connection URL (default: ``redis://localhost:6379``).
- ``REDIS_TLS_CERT`` — Path to client certificate PEM file.
- ``REDIS_TLS_KEY`` — Path to client private key PEM file.
- ``REDIS_TLS_CA`` — Path to CA certificate PEM file.
- ``DATA_DIR`` — Path to entity data directory (default: ``_data/`` relative
  to the project root).

When TLS environment variables are set, the Redis connection uses TLS 1.3
with mutual authentication via the same CA infrastructure as the HTTP and
WebSocket transports.

SCRAM authentication is required by default.  Set ``HAYSTACK_USER`` and
``HAYSTACK_PASS`` to configure credentials.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_log = logging.getLogger(__name__)

_DATA_DIR = Path(
    os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent.parent / "_data")
)


def main() -> None:
    """Load data into Redis and start the server."""
    import uvicorn

    from hs_py.auth_types import SimpleAuthenticator
    from hs_py.encoding.json import decode_grid
    from hs_py.fastapi_server import create_fastapi_app
    from hs_py.redis_ops import RedisOps, create_redis_client

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))

    # Auth credentials (required)
    hs_user = os.environ.get("HAYSTACK_USER")
    hs_pass = os.environ.get("HAYSTACK_PASS")

    if not hs_user or not hs_pass:
        _log.error(
            "HAYSTACK_USER and HAYSTACK_PASS must be set. "
            "Server requires SCRAM-SHA-256 authentication."
        )
        sys.exit(1)

    authenticator = SimpleAuthenticator({hs_user: hs_pass})

    tls_cert = os.environ.get("REDIS_TLS_CERT")
    tls_key = os.environ.get("REDIS_TLS_KEY")
    tls_ca = os.environ.get("REDIS_TLS_CA")

    tls = None
    if tls_cert and tls_key and tls_ca:
        from hs_py.tls import TLSConfig

        tls = TLSConfig(
            certificate_path=tls_cert,
            private_key_path=tls_key,
            ca_certificates_path=tls_ca,
        )

    redis = create_redis_client(redis_url, tls=tls)
    ops = RedisOps(redis)

    app = create_fastapi_app(ops=ops, authenticator=authenticator)

    # Wrap the existing lifespan (or create one) to handle adapter lifecycle
    # and data loading before the FastAPI server begins serving requests.
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app_: object) -> AsyncGenerator[None]:
        await ops.start()

        # Load entity data from _data/ JSON files
        if _DATA_DIR.is_dir():
            for json_file in sorted(_DATA_DIR.rglob("*.json")):
                raw = json_file.read_bytes()
                grid = decode_grid(raw)
                entities = [dict(row) for row in grid]
                await ops.load_entities(entities)

        async with original_lifespan(app_):
            yield

        await ops.stop()

    app.router.lifespan_context = lifespan

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
