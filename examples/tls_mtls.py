"""TLS mutual authentication — secure client and server.

Demonstrates generating test certificates, configuring TLS on the
server, and connecting with mutual TLS (mTLS) from the client.

Usage::

    uv run python examples/tls_mtls.py
"""

from __future__ import annotations

import asyncio
import ssl
import tempfile
from pathlib import Path

import uvicorn

from hs_py import Client, GridBuilder
from hs_py.ops import HaystackOps
from hs_py.fastapi_server import create_fastapi_app
from hs_py.tls import (
    TLSConfig,
    build_client_ssl_context,
    build_server_ssl_context,
    generate_test_certificates,
)


class MinimalOps(HaystackOps):
    """Minimal ops for the TLS demo."""

    async def about(self):
        return GridBuilder().add_col("serverName").add_row(
            {"serverName": "haystack-py TLS Demo"}
        ).to_grid()


async def main() -> None:
    # Generate test certificates (CA → server cert + client cert)
    with tempfile.TemporaryDirectory() as tmp:
        certs = generate_test_certificates(Path(tmp))
        print("Generated test certificates:")
        print(f"  CA:     {certs.ca_cert}")
        print(f"  Server: {certs.server_cert}")
        print(f"  Client: {certs.client_cert}")

        # Build SSL contexts
        server_ssl = build_server_ssl_context(
            TLSConfig(
                cert_file=certs.server_cert,
                key_file=certs.server_key,
                ca_file=certs.ca_cert,
                verify_client=True,
            )
        )

        client_ssl = build_client_ssl_context(
            TLSConfig(
                cert_file=certs.client_cert,
                key_file=certs.client_key,
                ca_file=certs.ca_cert,
            )
        )

        # Start server with TLS
        app = create_fastapi_app(ops=MinimalOps())
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, ssl=server_ssl, log_level="warning"
        )
        server = uvicorn.Server(config)

        # Run server in background
        task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.5)  # Let it start

        # Find the actual port
        port = server.servers[0].sockets[0].getsockname()[1]
        url = f"https://127.0.0.1:{port}/api"
        print(f"\nServer listening on {url}")

        # Connect with mTLS client
        async with Client(url, ssl_context=client_ssl) as client:
            about = await client.about()
            print(f"Connected! Server says: {about[0]['serverName']}")

        print("\nmTLS handshake successful ✓")

        # Cleanup
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main())
