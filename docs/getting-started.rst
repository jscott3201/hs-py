Getting Started
===============

Installation
------------

Install with pip:

.. code-block:: bash

   pip install hs-py

Or with `uv <https://docs.astral.sh/uv/>`_:

.. code-block:: bash

   uv add hs-py

Optional extras for server and storage backends:

.. code-block:: bash

   pip install hs-py[server]       # FastAPI + Redis backend
   pip install hs-py[timescale]    # TimescaleDB/PostgreSQL backend
   pip install hs-py[rdf]          # RDF ontology import (rdflib)
   pip install hs-py[all]          # All optional dependencies

Requirements: Python 3.13+. Core dependencies (aiohttp, orjson, cryptography,
websockets) are installed automatically.

Development Setup
-----------------

.. code-block:: bash

   git clone https://github.com/jscott3201/hs-py.git
   cd hs-py
   uv sync --group dev

Run the test suite:

.. code-block:: bash

   make check      # lint + typecheck + test
   make coverage   # tests with coverage report

Your First Read
---------------

Connect to a Haystack server, authenticate with SCRAM-SHA-256, and read data:

.. code-block:: python

   import asyncio
   from hs_py import Client, Ref

   async def main():
       async with Client("http://host/api", "admin", "secret") as c:
           # Server info
           about = await c.about()

           # Read all sites
           sites = await c.read("site")
           for row in sites:
               print(row.get("dis"), row.get("id"))

           # Read specific entities by id
           recs = await c.read_by_ids([Ref("a-0000"), Ref("b-0000")])

           # History read
           his = await c.his_read(Ref("a-0001"), "yesterday")
           for row in his:
               print(row["ts"], row["val"])

   asyncio.run(main())

See :doc:`guide/client-guide` for the full client API, including watches,
history writes, point writes, and batch operations.

Your First Write
----------------

Write a value to a writable point's priority array:

.. code-block:: python

   async with Client("http://host/api", "admin", "secret") as c:
       await c.point_write(Ref("point-1"), level=8, val=72.5)

       # Relinquish a level (write None)
       await c.point_write(Ref("point-1"), level=8, val=None)

Filter Expressions
------------------

Parse and evaluate Haystack filter strings locally:

.. code-block:: python

   from hs_py import MARKER, parse, evaluate

   f = parse("point and sensor and curVal > 72")

   rec = {"point": MARKER, "sensor": MARKER, "curVal": 75.0}
   assert evaluate(f, rec)

See :doc:`guide/filter-guide` for filter syntax, path traversal, and grid
filtering.

Grid Builder
------------

Build grids programmatically with :class:`~hs_py.grid.GridBuilder`:

.. code-block:: python

   from hs_py import GridBuilder, Number, Ref, MARKER

   b = GridBuilder()
   b.add_col("id")
   b.add_col("dis")
   b.add_col("point")
   b.add_col("curVal")
   b.add_row({
       "id": Ref("p1"),
       "dis": "Sensor 1",
       "point": MARKER,
       "curVal": Number(72.5, "°F"),
   })
   grid = b.to_grid()

See :doc:`guide/data-types` for the complete type system and grid model.

Configuration
-------------

The :class:`~hs_py.client.Client` constructor accepts configuration options:

.. code-block:: python

   from hs_py import Client

   async with Client(
       "http://server/api",
       username="admin",
       password="secret",
       timeout=30.0,        # per-request timeout in seconds
   ) as c:
       ...

For WebSocket clients:

.. code-block:: python

   from hs_py import WebSocketClient

   async with WebSocketClient(
       "ws://server/api/ws",
       auth_token="bearer-token",
   ) as ws:
       ...

Error Handling
--------------

All client methods raise from a common exception hierarchy:

.. code-block:: python

   from hs_py import Client, AuthError, CallError, NetworkError

   async with Client("http://server/api", "admin", "secret") as c:
       try:
           grid = await c.read("site")
       except AuthError:
           print("Authentication failed")
       except CallError as e:
           print(f"Server error: {e}")
       except NetworkError:
           print("Connection lost")

See :doc:`guide/error-handling` for the full exception hierarchy and recovery
patterns.

Debugging and Logging
---------------------

Enable debug logging to see HTTP requests and SCRAM handshakes:

.. code-block:: python

   import logging
   logging.basicConfig(level=logging.DEBUG)
   logging.getLogger("hs_py").setLevel(logging.DEBUG)

Next Steps
----------

- :doc:`features` — Full feature list with links to guides and API docs
- :doc:`guide/data-types` — Haystack value types and the Grid data model
- :doc:`guide/client-guide` — HTTP client operations and authentication
- :doc:`guide/server-guide` — Building a Haystack server with FastAPI
- :doc:`guide/storage-guide` — Storage backends (Redis, TimescaleDB, Memory)
- :doc:`guide/websocket-guide` — WebSocket transport and connection pooling
- :doc:`guide/encoding-guide` — JSON, Zinc, Trio, and CSV wire formats
- :doc:`guide/tls-guide` — TLS and mutual TLS configuration
