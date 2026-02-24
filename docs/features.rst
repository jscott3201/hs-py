Features
========

haystack-py provides a complete implementation of the Project Haystack protocol for
both client and server use cases. This page summarises the library's
capabilities by category.

Transports
----------

Two transport layers are supported, both fully asynchronous:

- **HTTP** — ``aiohttp`` based with persistent connections, connection pooling,
  timeout configuration, and content-negotiated responses.
- **WebSocket** *(experimental)* — ``websockets`` sans-I/O protocol with
  per-message deflate compression, batch request pipelining, and watch push
  delivery.  The WebSocket API is subject to breaking changes in future releases.

Wire Formats
------------

All four Haystack wire formats are implemented:

- **JSON** — v3 and v4 encoding via ``orjson``, with optional *pythonic* decode
  mode that converts values to native Python types. :class:`~hs_py.encoding.json.JsonVersion`
  enum selects the format.
- **Zinc** — The standard Haystack text grid format.
  :func:`~hs_py.encoding.zinc.encode_grid` / :func:`~hs_py.encoding.zinc.decode_grid`
  for full grids, plus scalar helpers.
- **Trio** — Line-oriented tagged record format.
  :func:`~hs_py.encoding.trio.parse_trio` / :func:`~hs_py.encoding.trio.encode_trio`.
- **CSV** — Lossy comma-separated export (encode-only, per spec).

See :doc:`guide/encoding-guide` for usage examples and format details.

Client
------

:class:`~hs_py.client.Client` provides an async HTTP client with all 13
standard Haystack operations:

- **about** — Server information
- **ops** — Supported operations listing
- **formats** — Content type negotiation
- **read** — Filter-based and ID-based entity reads
- **nav** — Site/equip/point tree navigation
- **hisRead / hisWrite** — Time-series history access
- **pointWrite** — Priority array writes for writable points
- **watchSub / watchUnsub / watchPoll** — Real-time change subscriptions
- **invokeAction** — Server-defined actions

:class:`~hs_py.ws_client.WebSocketClient` provides the same operations over
a persistent WebSocket connection, plus:

- **Batch requests** — Multiple ops in a single round-trip
- **Push delivery** — Server-initiated watch updates
- **Auto-reconnection** — :class:`~hs_py.ws_client.ReconnectingWebSocketClient`
  with exponential backoff
- **Connection pooling** — :class:`~hs_py.ws_client.WebSocketPool` multiplexed
  channels over a single WebSocket

See :doc:`guide/client-guide` and :doc:`guide/websocket-guide`.

Server
------

A FastAPI-based server framework with content-negotiated routes, SCRAM
middleware, and a WebSocket endpoint:

- :func:`~hs_py.fastapi_server.create_app` factory returns a ready-to-serve
  FastAPI application
- :class:`~hs_py.ops.HaystackOps` dispatches all 13 ops to a pluggable
  :class:`~hs_py.storage.protocol.StorageAdapter`
- :class:`~hs_py.ws_server.WebSocketServer` standalone WebSocket server with
  SCRAM handshake and batch dispatch

See :doc:`guide/server-guide`.

Storage Backends
----------------

A :class:`~hs_py.storage.protocol.StorageAdapter` protocol decouples ops from
data storage.  A companion :class:`~hs_py.storage.protocol.UserStore` protocol
handles user persistence.  Three implementations ship with the library, each
implementing both protocols:

- **Memory** — :class:`~hs_py.storage.memory.MemoryAdapter` for testing and
  prototyping
- **Redis** — RediSearch full-text indexes + RedisTimeSeries for history
- **TimescaleDB** — PostgreSQL JSONB entities, hypertable time-series, filter
  AST → SQL pushdown via ``asyncpg``

See :doc:`guide/storage-guide`.

Authentication
--------------

- **SCRAM-SHA-256** — Full client and server implementation over both HTTP and
  WebSocket. Constant-time key derivation, RFC 5802 compliant.
- **PLAINTEXT** — Fallback mode for development/testing.
- **Token-based** — Bearer token authentication for WebSocket connections.
- **mTLS** — :class:`~hs_py.auth_types.CertAuthenticator` validates client
  certificates for mutual TLS authentication.
- **Storage-backed** — :class:`~hs_py.auth_types.StorageAuthenticator` reads
  SCRAM credentials from any :class:`~hs_py.storage.protocol.UserStore`
  backend.  Disabled users are automatically denied.
- **Certificate generation** — :func:`~hs_py.tls.generate_test_certs` creates
  a CA, server, and client certificate chain for development.

See :doc:`guide/auth-users-permissions` and :doc:`guide/tls-guide`.

User Management & Permissions
-----------------------------

- :class:`~hs_py.user.User` — Frozen user model with SCRAM credentials
  (passwords never stored in plaintext).
- :class:`~hs_py.user.Role` — ``ADMIN``, ``OPERATOR``, ``VIEWER`` enum with
  strict ordering for permission checks.
- :class:`~hs_py.storage.protocol.UserStore` — Protocol for user CRUD
  (get, list, create, update, delete) implemented by all three storage backends.
- REST API — Admin-only CRUD endpoints at ``/api/users/`` for creating,
  listing, updating, and deleting users.
- Role enforcement on all Haystack ops: write ops require Operator+,
  user management requires Admin.  Enforced on both HTTP and WebSocket.
- Admin bootstrap: seeds an admin user from environment variables
  (``HS_SUPERUSER_USERNAME`` / ``HS_SUPERUSER_PASSWORD``) on first startup.

See :doc:`guide/auth-users-permissions`.

TLS
---

- TLS 1.3 enforced on all secure connections
- :class:`~hs_py.tls.TLSConfig` frozen dataclass for certificate paths
- :func:`~hs_py.tls.build_client_ssl_context` /
  :func:`~hs_py.tls.build_server_ssl_context` for SSL context construction
- Peer certificate inspection: :func:`~hs_py.tls.extract_peer_cn`,
  :func:`~hs_py.tls.extract_peer_sans`

Data Model
----------

All Haystack value types are implemented as frozen dataclasses:

- :class:`~hs_py.kinds.Marker`, :class:`~hs_py.kinds.Na`,
  :class:`~hs_py.kinds.Remove` — Singleton types
- :class:`~hs_py.kinds.Number` — Numeric value with optional unit
- :class:`~hs_py.kinds.Ref` — Entity reference with optional display name
- :class:`~hs_py.kinds.Symbol` — Def symbol
- :class:`~hs_py.kinds.Uri` — URI value
- :class:`~hs_py.kinds.Coord` — Geographic coordinate (lat/lng)
- :class:`~hs_py.kinds.XStr` — Typed string value

:class:`~hs_py.grid.Grid` is the universal immutable message format.
:class:`~hs_py.grid.GridBuilder` provides fluent construction.

See :doc:`guide/data-types`.

Filters
-------

- Recursive descent parser with LRU-cached AST generation
- Full filter syntax: ``has``, ``missing``, comparison operators, ``and``/``or``,
  path traversal
- Evaluation against dicts and grids
- SQL pushdown for RediSearch and PostgreSQL JSONB

See :doc:`guide/filter-guide`.

Ontology
--------

Full Project Haystack def model:

- :class:`~hs_py.ontology.defs.Def` / :class:`~hs_py.ontology.defs.Lib`
  frozen dataclasses
- :class:`~hs_py.ontology.namespace.Namespace` with symbol resolution
- Taxonomy queries: supertypes, subtypes, conjuncts
- Normalization pipeline: tag inheritance computation
- Reflection: infer entity types from tag dictionaries

See :doc:`guide/ontology-guide`.

Observability
-------------

:class:`~hs_py.metrics.MetricsHooks` provides optional callbacks for:

- Connection events (open, close, error)
- Message events (sent, received)
- Request events (start, complete, error)
- Custom metrics integration (Prometheus, StatsD, etc.)

Standard Python ``logging`` with a structured logger hierarchy.

See :doc:`guide/observability`.

Watch Subscriptions
-------------------

- :class:`~hs_py.watch.WatchState` — Server-side delta computation with dirty
  flag tracking
- :class:`~hs_py.watch.WatchAccumulator` — Client-side delta merging for
  incremental state updates

See :doc:`guide/watch-guide`.

Type Safety
-----------

- ``mypy --strict`` clean
- All protocol types use ``@dataclass(frozen=True, slots=True)``
- ``TYPE_CHECKING`` guarded imports for runtime-free type annotations
- Runtime-checkable ``Protocol`` classes for extensibility

Quality
-------

- **1,600+ unit tests** with ``pytest``
- **69 end-to-end integration tests** against Docker (Redis + FastAPI server)
- **122 TimescaleDB integration tests** against Docker
- **100% coverage** on security and authentication modules
- ``ruff`` linting and formatting
- ``mypy`` strict mode
- Frozen dataclasses throughout
