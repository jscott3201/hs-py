.. _guide-tls:

TLS and mTLS
============

haystack-py provides TLS configuration helpers for securing both client and server
connections.  This guide covers certificate setup, mutual TLS (mTLS), and
generating test certificates for development.

.. seealso::

   :doc:`../api/security` for the full TLS API reference.

.. _guide-tls-config:

TLS Configuration
-----------------

:class:`~hs_py.tls.TLSConfig` is a frozen dataclass holding certificate paths:

.. code-block:: python

   from hs_py import TLSConfig

   tls = TLSConfig(
       certificate_path="path/to/cert.pem",       # Certificate chain
       private_key_path="path/to/key.pem",         # Private key
       ca_certificates_path="path/to/ca.pem",      # CA certificate for verification
   )

All path fields are optional — provide what your deployment requires.
For password-protected private keys, pass ``key_password``:

.. code-block:: python

   tls = TLSConfig(
       certificate_path="cert.pem",
       private_key_path="encrypted.key",
       ca_certificates_path="ca.pem",
       key_password=b"my-key-password",
   )

.. _guide-tls-client:

Client TLS
----------

For connecting to a TLS-enabled Haystack server:

.. code-block:: python

   from hs_py import Client, TLSConfig

   # Server uses TLS, client verifies with CA cert
   tls = TLSConfig(ca_certificates_path="ca.crt")

   async with Client("https://host/api", "user", "pass", tls=tls) as c:
       about = await c.about()

For mTLS (client presents its own certificate):

.. code-block:: python

   tls = TLSConfig(
       certificate_path="client.crt",
       private_key_path="client.key",
       ca_certificates_path="ca.crt",
   )

   async with Client("https://host/api", "user", "pass", tls=tls) as c:
       about = await c.about()

The ``build_client_ssl_context()`` function creates the ``ssl.SSLContext``
from a ``TLSConfig``:

.. code-block:: python

   from hs_py import TLSConfig, build_client_ssl_context

   tls = TLSConfig(
       certificate_path="client.crt",
       private_key_path="client.key",
       ca_certificates_path="ca.crt",
   )
   ctx = build_client_ssl_context(tls)
   # ctx is an ssl.SSLContext configured for TLS 1.3 with the given certs

.. _guide-tls-server:

Server TLS
----------

For serving over TLS:

.. code-block:: python

   import uvicorn
   from hs_py import TLSConfig
   from hs_py.fastapi_server import create_fastapi_app
   from hs_py.auth_types import SimpleAuthenticator

   tls = TLSConfig(
       certificate_path="server.crt",
       private_key_path="server.key",
       ca_certificates_path="ca.crt",  # Enables client certificate verification (mTLS)
   )

   auth = SimpleAuthenticator({"admin": "secret"})
   app = create_fastapi_app(storage=storage, authenticator=auth)
   uvicorn.run(app, host="0.0.0.0", port=8443, ssl_certfile="server.crt", ssl_keyfile="server.key")

When ``ca_certificates_path`` is provided in the server context, client
certificates are required and verified against the CA — this is mutual TLS.

.. _guide-tls-mtls:

Mutual TLS Authentication
--------------------------

mTLS authenticates clients by their TLS certificate Common Name (CN).
Combine ``TLSConfig`` with :class:`~hs_py.auth_types.CertAuthenticator`:

.. code-block:: python

   from hs_py import TLSConfig, build_server_ssl_context
   from hs_py.auth_types import CertAuthenticator
   from hs_py.fastapi_server import create_fastapi_app

   # Only allow these client CNs
   auth = CertAuthenticator(allowed_cns={"device-01", "device-02", "gateway"})

   tls = TLSConfig(
       certificate_path="server.crt",
       private_key_path="server.key",
       ca_certificates_path="ca.crt",
   )
   ctx = build_server_ssl_context(tls)

   app = create_fastapi_app(ops=MyOps(), authenticator=auth)
   # Run with uvicorn: uvicorn myapp:app --ssl-certfile=server.crt --ssl-keyfile=server.key --port 8443

Peer Certificate Inspection
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Extract information from peer certificates at runtime:

.. code-block:: python

   from hs_py.tls import extract_peer_cn, extract_peer_sans

   # In an aiohttp handler or middleware:
   peercert = request.transport.get_extra_info("peercert")
   cn = extract_peer_cn(peercert)       # "device-01"
   sans = extract_peer_sans(peercert)   # ["device-01.example.com", "10.0.1.5"]

.. _guide-tls-websocket:

WebSocket TLS
-------------

WebSocket connections use the same ``TLSConfig``:

.. code-block:: python

   from hs_py.ws_client import WebSocketClient
   from hs_py import TLSConfig

   tls = TLSConfig(
       certificate_path="client.crt",
       private_key_path="client.key",
       ca_certificates_path="ca.crt",
   )

   async with WebSocketClient("wss://host/api/ws", tls=tls, auth_token="token") as ws:
       about = await ws.about()

.. _guide-tls-testcerts:

Test Certificates
-----------------

For development and testing, generate a complete CA + server + client
certificate chain:

.. code-block:: python

   import tempfile
   from hs_py import generate_test_certificates

   with tempfile.TemporaryDirectory() as tmp:
       tls = generate_test_certificates(tmp)
       # tls is a TLSConfig pointing to the server cert and CA
       # Files written to tmp:
       #   ca.pem         — CA certificate
       #   server.pem     — Server certificate
       #   server.key     — Server private key
       #   client.pem     — Client certificate
       #   client.key     — Client private key

The generated certificates use:

- **Algorithm**: EC P-256 (ECDSA with SHA-256)
- **Validity**: 365 days
- **CA CN**: ``Haystack Test CA``
- **Server CN**: ``Haystack Test Server``
- **Client CN**: ``Haystack Test Client``

Using Test Certs in Tests
^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   import tempfile
   from pathlib import Path
   from hs_py import generate_test_certificates, TLSConfig

   with tempfile.TemporaryDirectory() as tmp:
       server_tls = generate_test_certificates(tmp)
       # server_tls has certificate_path, private_key_path, ca_certificates_path

       # Build a client TLS config from the same CA + client cert
       p = Path(tmp)
       client_tls = TLSConfig(
           certificate_path=str(p / "client.pem"),
           private_key_path=str(p / "client.key"),
           ca_certificates_path=str(p / "ca.pem"),
       )
