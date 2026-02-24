#!/usr/bin/env python3
"""Generate TLS certificates for Docker Redis using the haystack-py CA infrastructure.

Writes certificates to ``docker/tls/``:

- ``ca.pem`` — CA certificate (shared trust root)
- ``server.pem`` / ``server.key`` — Redis server certificate
- ``client.pem`` / ``client.key`` — Client certificate for redis-py

Uses the same EC P-256 / SHA-256 / TLS 1.3 infrastructure as the rest of
the stack (``hs_py.tls.generate_test_certificates``).

Usage::

    python docker/gen_tls_certs.py
    # or
    make docker-tls-certs
"""

from __future__ import annotations

from pathlib import Path

from hs_py.tls import generate_test_certificates

_TLS_DIR = Path(__file__).resolve().parent / "tls"


def main() -> None:
    """Generate TLS certs for Docker Redis."""
    _TLS_DIR.mkdir(parents=True, exist_ok=True)
    config = generate_test_certificates(str(_TLS_DIR))
    print(f"Generated TLS certificates in {_TLS_DIR}")
    print(f"  CA:          {_TLS_DIR / 'ca.pem'}")
    print(f"  Server cert: {config.certificate_path}")
    print(f"  Server key:  {config.private_key_path}")
    print(f"  Client cert: {_TLS_DIR / 'client.pem'}")
    print(f"  Client key:  {_TLS_DIR / 'client.key'}")


if __name__ == "__main__":
    main()
