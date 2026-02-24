Security
========

Authentication, TLS, and the FastAPI server framework.

Authentication
--------------

SCRAM-SHA-256 and PLAINTEXT client authentication handshake for the Haystack
HTTP protocol.

.. automodule:: hs_py.auth
   :members:

Auth Types
----------

Authentication protocol interfaces and credential backends.

.. automodule:: hs_py.auth_types
   :members:

TLS
---

TLS configuration helpers for client and server SSL contexts, including
mutual TLS (mTLS) and test certificate generation.

.. automodule:: hs_py.tls
   :members:

FastAPI Server
--------------

FastAPI-based Haystack HTTP server with SCRAM authentication middleware,
content negotiation, and WebSocket endpoint.

.. automodule:: hs_py.fastapi_server
   :members:
