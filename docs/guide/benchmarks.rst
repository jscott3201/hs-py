Benchmarks
==========

haystack-py includes a Docker-based benchmark suite that measures HTTP and
WebSocket throughput against all three storage backends (InMemory, Redis,
TimescaleDB).  The suite also times the decoding of entity data in each
supported wire format (JSON, Trio, Zinc).

.. contents:: On this page
   :local:
   :depth: 2


Test Setup
----------

All benchmarks were run on Docker Desktop with a single client container
driving a single server container sequentially (HTTP first, then WebSocket)
for each backend.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Parameter
     - Value
   * - **Server container**
     - 3 GB RAM limit
   * - **Client container**
     - 1.5 GB RAM limit
   * - **HTTP concurrency**
     - 50 connections
   * - **WebSocket concurrency**
     - 20 connections
   * - **Duration**
     - 15 s per test (+ 3 s warmup)
   * - **Entity dataset**
     - 3,109 entities (Alpha 2,032 + Bravo 1,077)
   * - **Entity mix**
     - 2,764 points, 333 equips, 2 sites, 8 VAVs, AHUs, meters
   * - **Server**
     - Uvicorn (single worker), FastAPI, SCRAM-SHA-256 auth
   * - **Wire formats decoded**
     - JSON (orjson), Trio, Zinc

The server loads entity data from JSON files (Trio and Zinc files are decoded
for timing but not double-ingested).  Each client authenticates via
SCRAM-SHA-256 then sends a mix of Haystack operations (``about``, ``ops``,
``read`` with filters, ``nav``) in a tight loop for the benchmark duration.


HTTP API Results
----------------

.. list-table::
   :header-rows: 1
   :widths: 20 15 15 15 15

   * - Backend
     - RPS
     - p50
     - p95
     - p99
   * - **InMemory**
     - 3,380
     - 15.3 ms
     - 20.8 ms
     - 37.9 ms
   * - **Redis**
     - 3,578
     - 13.8 ms
     - 21.7 ms
     - 40.2 ms
   * - **TimescaleDB**
     - 3,713
     - 12.7 ms
     - 23.3 ms
     - 34.3 ms

All three backends achieve comparable throughput (3,300–3,700 rps) thanks to
response caching and decode-path optimizations that eliminate storage I/O as a
bottleneck after warmup.  The server is bottlenecked on HTTP/ASGI overhead
rather than backend speed.

Per-endpoint breakdown (InMemory, 50 connections):

.. list-table::
   :header-rows: 1
   :widths: 25 15 15 15

   * - Endpoint
     - RPS
     - p50
     - p99
   * - ``/about``
     - 677
     - 9.9 ms
     - 29.1 ms
   * - ``/ops``
     - 677
     - 9.9 ms
     - 28.9 ms
   * - ``/read`` (filter)
     - 1,352
     - 16.8 ms
     - 40.0 ms
   * - ``/nav``
     - 675
     - 16.3 ms
     - 39.2 ms


WebSocket API Results
---------------------

.. list-table::
   :header-rows: 1
   :widths: 20 18 15 15 15

   * - Backend
     - msg/s
     - p50
     - p95
     - p99
   * - **InMemory**
     - 1,552
     - 12.7 ms
     - 23.8 ms
     - 29.4 ms
   * - **Redis**
     - 1,616
     - 10.2 ms
     - 30.9 ms
     - 40.7 ms
   * - **TimescaleDB**
     - 1,797
     - 4.0 ms
     - 38.4 ms
     - 46.4 ms

WebSocket throughput ranges from 1,550–1,800 msg/s across backends with 20
concurrent connections.  The persistent connection eliminates per-request
auth/HTTP overhead.  Response payloads are larger than HTTP (full entity grids
per message), which makes the transport I/O the primary bottleneck.  All three
backends perform similarly because read caches serve repeated queries from
memory.

Per-operation breakdown (TimescaleDB, 20 connections):

.. list-table::
   :header-rows: 1
   :widths: 20 15 15 15

   * - Operation
     - msg/s
     - p50
     - p99
   * - ``about``
     - 360
     - 0.8 ms
     - 12.1 ms
   * - ``ops``
     - 359
     - 0.6 ms
     - 17.4 ms
   * - ``read``
     - 718
     - 11.5 ms
     - 40.7 ms
   * - ``nav``
     - 359
     - 25.2 ms
     - 49.3 ms


Wire Format Decode Performance
------------------------------

Decoding 3,109 entities (Alpha + Bravo datasets):

.. list-table::
   :header-rows: 1
   :widths: 15 20 20

   * - Format
     - Alpha (2,032)
     - Bravo (1,077)
   * - **JSON** (orjson)
     - 34 ms
     - 56 ms
   * - **Trio**
     - 67 ms
     - 93 ms
   * - **Zinc**
     - 75 ms
     - N/A\ :sup:`1`

.. note::

   :sup:`1` The Bravo Zinc file contains a scientific notation edge case
   (``10.6E``) that the current Zinc scanner does not handle.  This is a
   known limitation tracked for a future release.

JSON (via orjson) is the fastest decoder — roughly 2× faster than Trio and
Zinc for the same dataset.  All formats decode the full 2,032-entity Alpha
dataset in under 100 ms.


Running the Benchmarks
----------------------

The benchmark suite lives in ``benchmarks/`` and can be run with a single
script:

.. code-block:: bash

   cd benchmarks
   ./run_benchmarks.sh

This will:

1. Build the server and client Docker images
2. Start each backend (InMemory → Redis → TimescaleDB)
3. Run HTTP then WebSocket client sequentially per backend
4. Collect results to ``benchmarks/results/``
5. Print an aggregated summary

To run a single backend manually:

.. code-block:: bash

   # Start server with InMemory backend
   docker compose -f benchmarks/docker-compose.yml up -d --wait server-inmemory

   # Run one HTTP client
   docker compose -f benchmarks/docker-compose.yml run --rm http-inmemory

   # Run one WebSocket client
   docker compose -f benchmarks/docker-compose.yml run --rm ws-inmemory

   # Cleanup
   docker compose -f benchmarks/docker-compose.yml down -v --remove-orphans

Configuration is via environment variables:

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Variable
     - Default
     - Description
   * - ``HS_PY_CONCURRENCY``
     - 50 / 20
     - Concurrent connections (HTTP / WebSocket)
   * - ``HS_PY_DURATION``
     - 15
     - Benchmark duration in seconds
   * - ``HS_PY_WARMUP``
     - 3
     - Warmup duration in seconds
   * - ``BACKEND``
     - inmemory
     - Server storage backend (``inmemory``, ``redis``, ``timescale``)
