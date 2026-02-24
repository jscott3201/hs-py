Storage Backends
================

hs-py ships with three storage backends that implement the
:class:`~hs_py.storage.protocol.StorageAdapter` protocol. All backends
support the same set of Haystack operations — entity CRUD, filter reads,
navigation, history, point writes, and watch subscriptions.

Choose the backend that matches your deployment needs:

.. list-table::
   :header-rows: 1
   :widths: 20 30 50

   * - Backend
     - Best For
     - Dependencies
   * - **Memory**
     - Testing, prototyping
     - None (built-in)
   * - **Redis**
     - Production, horizontal scaling
     - ``redis``, ``redisvl`` (``pip install hs-py[server]``)
   * - **TimescaleDB**
     - SQL analytics, time-series queries
     - ``asyncpg`` (``pip install hs-py[timescale]``)


Memory Backend
--------------

:class:`~hs_py.storage.memory.MemoryAdapter` stores entities and history
in-memory. Useful for unit tests and rapid prototyping. All data is lost when
the process exits.

.. code-block:: python

   from hs_py.storage.memory import MemoryAdapter

   storage = MemoryAdapter()

   # Pre-load entities
   storage.entities["site-1"] = {"id": Ref("site-1"), "dis": "HQ", "site": MARKER}

No configuration is required. Pass the adapter to
:func:`~hs_py.fastapi_server.create_app`:

.. code-block:: python

   from hs_py.fastapi_server import create_app
   from hs_py.storage.memory import MemoryAdapter

   app = create_app(storage=MemoryAdapter())


Redis Backend
-------------

The Redis backend uses `RediSearch <https://redis.io/docs/stack/search/>`_
for full-text indexed entity queries and `RedisTimeSeries
<https://redis.io/docs/stack/timeseries/>`_ for time-series history storage.

The implementation is split across two modules:

- :mod:`hs_py.redis_ops` — Low-level Redis operations (entity hash maps,
  RediSearch indexing, TimeSeries commands)
- :mod:`hs_py.storage.redis` — :class:`~hs_py.storage.redis.RedisAdapter`
  wrapping ``redis_ops`` behind the ``StorageAdapter`` protocol

Configuration
^^^^^^^^^^^^^

Set the ``REDIS_URL`` environment variable or pass it to the adapter:

.. code-block:: python

   from hs_py.storage.redis import RedisAdapter

   adapter = RedisAdapter(redis_url="redis://localhost:6379")

Docker Compose
^^^^^^^^^^^^^^

.. code-block:: yaml

   services:
     redis:
       image: redis/redis-stack-server:latest
       ports:
         - "6379:6379"
       healthcheck:
         test: ["CMD", "redis-cli", "ping"]
         interval: 3s

Redis Stack includes RediSearch and RedisTimeSeries modules automatically.

Seeding Data
^^^^^^^^^^^^

Use the ``/load`` endpoint or call the adapter directly:

.. code-block:: python

   import json

   with open("_data/Alpha/alpha.json") as f:
       entities = json.load(f)

   for entity in entities:
       await adapter.create(entity)


TimescaleDB Backend
-------------------

:class:`~hs_py.storage.timescale.TimescaleAdapter` stores entities as
PostgreSQL JSONB rows and time-series data in TimescaleDB hypertables. Filter
expressions are translated to SQL ``WHERE`` clauses for server-side pushdown.

Schema
^^^^^^

The adapter auto-creates two tables on ``connect()``:

.. code-block:: sql

   CREATE TABLE IF NOT EXISTS entities (
       id   TEXT PRIMARY KEY,
       tags JSONB NOT NULL
   );

   CREATE TABLE IF NOT EXISTS history (
       point_id  TEXT        NOT NULL,
       ts        TIMESTAMPTZ NOT NULL,
       val       DOUBLE PRECISION
   );

   -- TimescaleDB hypertable for history
   SELECT create_hypertable('history', 'ts', if_not_exists => TRUE);

Configuration
^^^^^^^^^^^^^

Pass a PostgreSQL DSN to the adapter:

.. code-block:: python

   from hs_py.storage.timescale import TimescaleAdapter

   adapter = TimescaleAdapter(dsn="postgresql://user:pass@localhost:5432/haystack")

   await adapter.connect()
   # ... use adapter ...
   await adapter.close()

Or use environment variables:

.. code-block:: bash

   export TIMESCALE_DSN="postgresql://user:pass@localhost:5432/haystack"

Docker Compose
^^^^^^^^^^^^^^

.. code-block:: yaml

   services:
     timescaledb:
       image: timescale/timescaledb:latest-pg16
       ports:
         - "5432:5432"
       environment:
         POSTGRES_USER: haystack
         POSTGRES_PASSWORD: haystack
         POSTGRES_DB: haystack
       healthcheck:
         test: ["CMD-SHELL", "pg_isready -U haystack"]
         interval: 3s

Filter Pushdown
^^^^^^^^^^^^^^^

Haystack filter expressions are compiled to SQL ``WHERE`` clauses. The
``_ast_to_sql`` method translates filter AST nodes to parameterised
PostgreSQL queries using JSONB operators:

- ``has`` → ``tags ? 'tagName'``
- ``missing`` → ``NOT (tags ? 'tagName')``
- ``==`` → ``tags->>'tagName' = $1``
- ``!=`` → ``tags->>'tagName' != $1``
- ``> / >= / < / <=`` → ``(tags->>'tagName')::float > $1::float``

Ref-valued tag comparisons use the nested JSONB path
``tags->'tagName'->>'val'`` to extract the reference id string.

History Queries
^^^^^^^^^^^^^^^

Time-series data is stored in a TimescaleDB hypertable for efficient
range queries. Use standard Haystack date range strings:

.. code-block:: python

   # Single day
   his = await adapter.his_read("point-1", "2024-06-15")

   # Date range
   his = await adapter.his_read("point-1", "2024-06-01,2024-06-30")


StorageAdapter Protocol
-----------------------

All backends implement the :class:`~hs_py.storage.protocol.StorageAdapter`
protocol. To create a custom backend, implement these methods:

.. code-block:: python

   from hs_py.storage.protocol import StorageAdapter

   class MyAdapter(StorageAdapter):
       async def about(self) -> dict: ...
       async def read(self, filter_str: str, limit: int) -> list[dict]: ...
       async def read_by_ids(self, ids: list[str]) -> list[dict]: ...
       async def nav(self, nav_id: str | None) -> list[dict]: ...
       async def his_read(self, id: str, range_str: str) -> list[dict]: ...
       async def his_write(self, id: str, items: list[dict]) -> None: ...
       async def point_write(self, id: str, level: int, val, who: str) -> list[dict]: ...
       async def watch_sub(self, watch_id: str, ids: list[str]) -> list[dict]: ...
       async def watch_unsub(self, watch_id: str, ids: list[str]) -> None: ...
       async def watch_poll(self, watch_id: str, refresh: bool) -> list[dict]: ...

See :mod:`hs_py.storage.protocol` for the full method signatures and
type annotations.
