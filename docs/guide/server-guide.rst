HTTP Server
===========

The server framework provides a FastAPI-based implementation of the Haystack
HTTP API. Plug in a :class:`~hs_py.storage.protocol.StorageAdapter` and an
optional :class:`~hs_py.auth_types.Authenticator` and the framework handles
routing, content negotiation, authentication, and error wrapping.

.. seealso::

   :doc:`../api/security` for the server and auth API reference,
   :doc:`storage-guide` for storage backend configuration.

.. _guide-server-quickstart:

Quick Start
-----------

.. code-block:: python

   import uvicorn
   from hs_py.fastapi_server import create_fastapi_app
   from hs_py.auth_types import SimpleAuthenticator
   from hs_py.storage.memory import MemoryAdapter

   storage = MemoryAdapter()
   auth = SimpleAuthenticator({"admin": "secret"})
   app = create_fastapi_app(storage=storage, authenticator=auth)

   uvicorn.run(app, host="0.0.0.0", port=8080)

This starts a FastAPI server on port 8080 with SCRAM-SHA-256 authentication
and routes for all standard Haystack ops.

.. _guide-server-ops:

Implementing Operations
-----------------------

:class:`~hs_py.ops.HaystackOps` wraps a
:class:`~hs_py.storage.protocol.StorageAdapter` to implement every standard
Haystack operation. When you pass a ``storage`` argument to
:func:`~hs_py.fastapi_server.create_fastapi_app`, a ``HaystackOps`` instance
is created automatically.

.. list-table:: Haystack Operations
   :header-rows: 1
   :widths: 25 30 45

   * - Method
     - HTTP Route
     - Description
   * - ``about()``
     - ``POST /about``
     - Server information
   * - ``ops()``
     - ``POST /ops``
     - Supported operations (auto-discovered)
   * - ``formats()``
     - ``POST /formats``
     - Supported MIME types
   * - ``read(grid)``
     - ``POST /read``
     - Read records by filter or id
   * - ``nav(grid)``
     - ``POST /nav``
     - Navigate the entity tree
   * - ``his_read(grid)``
     - ``POST /hisRead``
     - Read time-series data
   * - ``his_write(grid)``
     - ``POST /hisWrite``
     - Write time-series data
   * - ``point_write(grid)``
     - ``POST /pointWrite``
     - Write to priority array
   * - ``watch_sub(grid)``
     - ``POST /watchSub``
     - Subscribe to changes
   * - ``watch_unsub(grid)``
     - ``POST /watchUnsub``
     - Unsubscribe points
   * - ``watch_poll(grid)``
     - ``POST /watchPoll``
     - Poll for changes
   * - ``invoke_action(grid)``
     - ``POST /invokeAction``
     - Trigger a named action
   * - ``defs(grid)``
     - ``POST /defs``
     - Query ontology definitions
   * - ``libs(grid)``
     - ``POST /libs``
     - Query ontology libraries

Custom Operations
^^^^^^^^^^^^^^^^^

To customise operation behaviour, subclass
:class:`~hs_py.ops.HaystackOps` and pass the instance directly:

.. code-block:: python

   from hs_py.ops import HaystackOps
   from hs_py import Grid

   class MyOps(HaystackOps):
       async def about(self) -> Grid:
           grid = await super().about()
           # Add custom metadata
           return grid

   ops = MyOps(storage=storage)
   app = create_fastapi_app(ops=ops, authenticator=auth)

.. _guide-server-auth:

Authentication
--------------

The framework provides two authentication strategies out of the box, and
supports custom backends via the :class:`~hs_py.auth_types.Authenticator`
protocol.

SCRAM-SHA-256 (Password)
^^^^^^^^^^^^^^^^^^^^^^^^^

:class:`~hs_py.auth_types.SimpleAuthenticator` accepts a ``dict[str, str]``
mapping usernames to passwords. SCRAM keys are pre-derived from
:class:`~hs_py.auth_types.ScramCredentials` on construction.

.. code-block:: python

   from hs_py.auth_types import SimpleAuthenticator

   auth = SimpleAuthenticator({"admin": "s3cret", "viewer": "readonly"})

The SCRAM handshake flow is:

1. Client sends ``HELLO`` header with username.
2. Server responds with handshake token, hash algorithm, and salt.
3. Client computes SCRAM proof, server verifies.
4. Server issues a bearer token for subsequent requests.

Security limits:

- In-progress handshakes capped at **1,000** to prevent memory exhaustion.
- Bearer tokens expire after **3,600 seconds**.
- Total token count capped at **10,000**.

mTLS Client Certificates
^^^^^^^^^^^^^^^^^^^^^^^^^

:class:`~hs_py.auth_types.CertAuthenticator` authenticates clients by their TLS
client certificate Common Name (CN).

.. code-block:: python

   from hs_py.auth_types import CertAuthenticator

   # Only accept clients with these CNs
   auth = CertAuthenticator(allowed_cns={"device-01", "device-02"})

See :doc:`tls-guide` for setting up mTLS with :class:`~hs_py.tls.TLSConfig`.

Custom Authenticator
^^^^^^^^^^^^^^^^^^^^

Implement the :class:`~hs_py.auth_types.Authenticator` protocol for custom
backends (LDAP, database, OAuth):

.. code-block:: python

   from hs_py.auth_types import Authenticator, ScramCredentials

   class MyAuthenticator:
       async def scram_credentials(self, username: str) -> ScramCredentials | None:
           # Look up credentials from your backend
           ...

.. _guide-server-app:

Application Setup
-----------------

:func:`~hs_py.fastapi_server.create_fastapi_app` is the main factory function.
It accepts an ``ops`` instance (or ``storage`` to auto-create one), an
optional ``authenticator``, and a URL ``prefix``:

.. code-block:: python

   from hs_py.fastapi_server import create_fastapi_app

   # Option 1: Pass storage (auto-creates HaystackOps)
   app = create_fastapi_app(storage=storage, authenticator=auth)

   # Option 2: Pass custom ops instance
   app = create_fastapi_app(ops=my_ops, authenticator=auth, prefix="/haystack")

The returned ``FastAPI`` application can be extended with additional routes,
middleware, and startup hooks:

.. code-block:: python

   @app.get("/health")
   async def health():
       return {"status": "ok"}

Production Deployment
^^^^^^^^^^^^^^^^^^^^^

Use ``uvicorn`` for production:

.. code-block:: bash

   uvicorn myapp:app --host 0.0.0.0 --port 8080 --workers 4

Or from Docker:

.. code-block:: yaml

   services:
     server:
       build: .
       ports:
         - "8080:8080"
       environment:
         REDIS_URL: redis://redis:6379
         HAYSTACK_USER: admin
         HAYSTACK_PASS: secret
       depends_on:
         redis:
           condition: service_healthy

.. _guide-server-errors:

Error Handling
--------------

The :class:`~hs_py.fastapi_server.ScramAuthMiddleware` wraps authentication
errors. Operation errors are returned as Haystack error grids automatically.

To return a deliberate error response, raise :class:`~hs_py.errors.CallError`:

.. code-block:: python

   from hs_py.errors import CallError
   from hs_py import Grid

   class MyOps(HaystackOps):
       async def read(self, grid: Grid) -> Grid:
           filter_str = grid.meta.get("filter")
           if not filter_str:
               raise CallError(
                   "missing filter parameter",
                   Grid.make_error("missing filter parameter"),
               )
           ...

See :doc:`error-handling` for error handling patterns.

.. _guide-server-push:

Watch Push Delivery
-------------------

:class:`~hs_py.ops.HaystackOps` supports pushing watch updates to subscribed
clients via :meth:`~hs_py.ops.HaystackOps.push_watch`. The framework calls
:meth:`~hs_py.ops.HaystackOps.set_push_handler` during setup to wire the
delivery mechanism (HTTP or WebSocket).

.. code-block:: python

   from hs_py.ops import HaystackOps
   from hs_py import Grid

   class MyOps(HaystackOps):
       async def notify_change(self, watch_id: str, update: Grid) -> None:
           # Push the delta to all subscribed clients
           await self.push_watch(watch_id, update)

.. _guide-server-websocket:

WebSocket Endpoint
------------------

The FastAPI app includes a WebSocket endpoint at ``{prefix}/ws`` that supports
token-based authentication. Clients authenticate via HTTP SCRAM first, then
pass the bearer token as the first WebSocket message:

.. code-block:: python

   from hs_py import WebSocketClient

   async with WebSocketClient("ws://host/api/ws", auth_token=token) as ws:
       about = await ws.about()

For standalone WebSocket servers with SCRAM handshake support, see
:class:`~hs_py.ws_server.WebSocketServer` and :doc:`websocket-guide`.

.. _guide-server-tls:

TLS Configuration
-----------------

For production deployments with TLS:

.. code-block:: python

   from hs_py import TLSConfig

   tls = TLSConfig(
       certificate_path="server.crt",
       private_key_path="server.key",
       ca_certificates_path="ca.crt",
   )

   # Use with uvicorn
   uvicorn.run(
       app,
       host="0.0.0.0",
       port=8443,
       ssl_certfile="server.crt",
       ssl_keyfile="server.key",
   )

See :doc:`tls-guide` for generating test certificates and configuring mTLS.
