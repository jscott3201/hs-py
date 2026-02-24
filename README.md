# hs-py

[![PyPI](https://img.shields.io/pypi/v/hs-py)](https://pypi.org/project/hs-py/)
[![Python](https://img.shields.io/pypi/pyversions/hs-py)](https://pypi.org/project/hs-py/)
[![License](https://img.shields.io/github/license/jscott3201/hs-py)](LICENSE)
[![CI](https://github.com/jscott3201/hs-py/actions/workflows/ci.yml/badge.svg)](https://github.com/jscott3201/hs-py/actions/workflows/ci.yml)

Asynchronous [Project Haystack](https://project-haystack.org/) client and server library for Python 3.13+. HTTP and WebSocket transports, four wire formats, SCRAM-SHA-256 and mTLS authentication, pluggable storage backends (Redis, TimescaleDB), and full ontology support. Built on native `asyncio`.

[Documentation](https://jscott3201.github.io/hs-py/) | [Getting Started](https://jscott3201.github.io/hs-py/getting-started.html) | [API Reference](https://jscott3201.github.io/hs-py/api/index.html) | [Changelog](CHANGELOG.md)

```python
from hs_py import Client

async with Client("http://server/api", "admin", "secret") as c:
    about = await c.about()
    points = await c.read("point and temp and sensor")
```

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Storage Backends](#storage-backends)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Testing](#testing)
- [Requirements](#requirements)
- [License](#license)

## Features

| Category | Highlights |
|----------|-----------|
| **Transports** | HTTP with aiohttp, WebSocket with `websockets` sans-I/O, persistent connections, per-message deflate compression |
| **Client** | `Client` (HTTP) and `WebSocketClient` with all 13 standard ops, batch requests, watch subscriptions, auto-auth |
| **Server** | FastAPI application factory, SCRAM-SHA-256 middleware, content negotiation (JSON/Zinc/Trio/CSV), standalone `WebSocketServer` |
| **Wire Formats** | JSON v3/v4 (`orjson`), Zinc (text), Trio (tagged records), CSV (export-only) |
| **Authentication** | SCRAM-SHA-256 over HTTP and WebSocket, PLAINTEXT fallback, token-based WebSocket auth, mTLS with `CertAuthenticator` |
| **TLS** | TLS 1.3 enforced, mutual authentication, test certificate generation (EC P-256), `TLSConfig` dataclass |
| **Storage** | Pluggable `StorageAdapter` protocol with Redis (RediSearch + RedisTimeSeries), TimescaleDB (asyncpg + JSONB), and in-memory backends |
| **Data Model** | All Haystack value types as frozen dataclasses, `Grid` / `GridBuilder` as universal message format |
| **Filters** | Recursive descent parser, AST representation, evaluation against dicts and grids, SQL pushdown for JSONB/RediSearch |
| **Ontology** | Def/Lib/Namespace model, taxonomy queries, tag normalization, dict-to-def reflection |
| **WebSocket Extras** | `ReconnectingWebSocketClient` with backoff, `WebSocketPool` / `ChannelClient` multiplexing, binary frame codec |
| **Observability** | `MetricsHooks` for connection, message, request, and error callbacks |
| **Watch** | Server-side `WatchState` delta encoding, client-side `WatchAccumulator` delta merging |
| **Quality** | 1,200+ tests, 69 end-to-end integration tests, 122 TimescaleDB tests, mypy strict, ruff linting, frozen dataclasses throughout |

## Installation

```bash
pip install hs-py
```

Optional extras:

```bash
pip install hs-py[server]       # FastAPI + Redis backend (server-side)
pip install hs-py[timescale]    # TimescaleDB/PostgreSQL backend
pip install hs-py[rdf]          # RDF ontology import (rdflib)
pip install hs-py[all]          # All optional dependencies
```

### Development

```bash
git clone https://github.com/jscott3201/hs-py.git
cd hs-py
uv sync --group dev
```

## Quick Start

### HTTP Client

```python
import asyncio
from hs_py import Client, Ref


async def main():
    async with Client("http://server/api", "admin", "secret") as c:
        # Server info
        about = await c.about()
        print(about[0]["serverName"])

        # Filter-based read
        sites = await c.read("site")
        for row in sites:
            print(row.get("dis"), row.get("id"))

        # ID-based read
        entities = await c.read_by_ids([Ref("site-1"), Ref("equip-2")])

        # Navigation
        nav = await c.nav()  # root sites
        children = await c.nav(Ref("site-1"))  # site's equips

        # History
        history = await c.his_read(Ref("point-1"), "yesterday")
        for row in history:
            print(row["ts"], row["val"])

        # Write a point value
        await c.point_write(Ref("point-1"), level=8, val=72.5)


asyncio.run(main())
```

### WebSocket Client

```python
import asyncio
from hs_py import WebSocketClient, Grid, Ref


async def main():
    async with WebSocketClient("ws://server/api/ws", auth_token="token") as ws:
        about = await ws.about()

        # Batch: multiple ops in one round-trip
        results = await ws.batch(
            ("about", Grid.make_empty()),
            ("read", Grid.make_rows([{"filter": "site"}])),
            ("read", Grid.make_rows([{"filter": "point and temp"}])),
        )

        # Watch: subscribe to entity changes
        watch = await ws.watch_sub([Ref("p-1"), Ref("p-2")], "my-watch")
        watch_id = watch.meta["watchId"]
        poll = await ws.watch_poll(watch_id)
        await ws.watch_close(watch_id)


asyncio.run(main())
```

### Server

```python
import asyncio
from hs_py.ops import HaystackOps
from hs_py.storage.memory import MemoryAdapter
from hs_py.auth_types import SimpleAuthenticator
from hs_py.fastapi_server import create_app
import uvicorn


async def main():
    storage = MemoryAdapter()
    await storage.start()
    await storage.load_entities([
        {"id": Ref("s1"), "site": MARKER, "dis": "My Building"},
    ])

    ops = HaystackOps(storage=storage)
    auth = SimpleAuthenticator({"admin": "secret"})
    app = create_app(ops, authenticator=auth)
    config = uvicorn.Config(app, host="0.0.0.0", port=8080)
    server = uvicorn.Server(config)
    await server.serve()


asyncio.run(main())
```

### Wire Formats

```python
from hs_py.encoding import json, zinc, trio, csv
from hs_py.encoding.json import JsonVersion

# JSON v4
grid = json.decode_grid(data, version=JsonVersion.V4)
json_bytes = json.encode_grid(grid, version=JsonVersion.V4)

# Zinc
zinc_text = zinc.encode_grid(grid)
grid = zinc.decode_grid(zinc_text)

# Trio records
records = trio.parse_trio(trio_text)
trio_text = trio.encode_trio(records)

# CSV (encode-only)
csv_text = csv.encode_grid(grid)
```

### Filters

```python
from hs_py import MARKER, parse, evaluate, evaluate_grid

# Parse filter to AST
ast = parse("point and temp and sensor")

# Evaluate against a dict
entity = {"point": MARKER, "temp": MARKER, "sensor": MARKER}
assert evaluate(ast, entity) is True

# Filter a grid
matching = evaluate_grid(ast, grid)
```

### Ontology

```python
from hs_py.ontology.namespace import Namespace, load_lib_from_trio
from hs_py.ontology.reflect import reflect

lib = load_lib_from_trio(trio_text)
ns = Namespace([lib])

# Taxonomy queries
assert ns.is_subtype("ahu", "equip")
subtypes = ns.subtypes("equip")  # [ahu, vav, ...]

# Reflect entities against definitions
defs = reflect(ns, entity_dict)
```

## Storage Backends

hs-py defines a `StorageAdapter` protocol that decouples server operations from data storage. Three implementations are provided:

| Backend | Module | Best For |
|---------|--------|----------|
| **Memory** | `storage.memory` | Testing, prototyping, small datasets |
| **Redis** | `storage.redis` | Production with RediSearch full-text and RedisTimeSeries |
| **TimescaleDB** | `storage.timescale` | Production with PostgreSQL JSONB and time-series hypertables |

### Redis

```python
from hs_py.storage.redis import RedisAdapter, create_redis_client

r = await create_redis_client("redis://localhost:6379")
adapter = RedisAdapter(r)
await adapter.start()
```

Features: RediSearch indexes for filter queries, RedisTimeSeries for history, pub/sub for watch notifications.

### TimescaleDB

```python
from hs_py.storage.timescale import TimescaleAdapter, create_timescale_pool

pool = await create_timescale_pool("postgresql://localhost/haystack")
adapter = TimescaleAdapter(pool)
await adapter.start()  # Creates schema + hypertable
```

Features: JSONB entity storage with GIN indexes, filter AST → SQL pushdown, hypertable time-series, COPY-based bulk loading.

## Architecture

```
src/hs_py/
  kinds.py            Haystack value types (Marker, Number, Ref, Coord, etc.)
  grid.py             Grid, Col, GridBuilder -- universal message format
  errors.py           Exception hierarchy (HaystackError, CallError, AuthError)
  auth.py             SCRAM-SHA-256 / PLAINTEXT client auth handshake
  auth_types.py       Authenticator protocol, SimpleAuthenticator, CertAuthenticator
  client.py           Async HTTP client with all standard ops
  ops.py              HaystackOps base class with storage-backed op dispatch
  fastapi_server.py   FastAPI application factory, SCRAM middleware, WebSocket endpoint
  metrics.py          MetricsHooks for transport-level observability
  tls.py              TLSConfig, SSL context builders, certificate generation
  security.py         Security hardening utilities
  watch.py            WatchState (server delta), WatchAccumulator (client merge)
  ws.py               Sans-I/O WebSocket wrapper (websockets library)
  ws_client.py        WebSocketClient, ReconnectingWebSocketClient, WebSocketPool
  ws_server.py        Standalone WebSocket server with SCRAM auth and batch dispatch
  ws_codec.py         Binary frame codec (4-byte header + JSON payload)
  encoding/
    json.py           JSON v3/v4 encode/decode via orjson
    zinc.py           Zinc text format encode/decode
    trio.py           Trio tagged record format
    csv.py            CSV export (encode-only, lossy)
    scanner.py        Shared Zinc value scanning
  filter/
    ast.py            Filter AST nodes (Has, Missing, Cmp, And, Or, Path)
    lexer.py          Filter expression tokenizer
    parser.py         Recursive descent parser
    eval.py           Filter evaluation against dicts/grids
  storage/
    protocol.py       StorageAdapter protocol (11 async methods)
    memory.py         In-memory adapter for testing
    redis.py          Redis + RediSearch + RedisTimeSeries adapter
    timescale.py      PostgreSQL/TimescaleDB adapter via asyncpg
  ontology/
    defs.py           Def and Lib frozen dataclasses
    namespace.py      Namespace container, symbol resolution
    taxonomy.py       Subtype tree, tag inheritance
    normalize.py      Normalization pipeline
    reflect.py        Dict-to-def reflection engine
```

### Key Classes

| Class | Module | Purpose |
|-------|--------|---------|
| `Client` | `client` | Async HTTP client with SCRAM auth and all Haystack ops |
| `WebSocketClient` | `ws_client` | Persistent WebSocket client with batch and watch support |
| `ReconnectingWebSocketClient` | `ws_client` | Auto-reconnecting WebSocket client with exponential backoff |
| `WebSocketPool` | `ws_client` | Multiplexed channels over a single WebSocket connection |
| `HaystackOps` | `ops` | Storage-backed server operation handler for all 13 ops |
| `WebSocketServer` | `ws_server` | Standalone WebSocket server with SCRAM auth and push |
| `Grid` | `grid` | Universal Haystack message format (immutable) |
| `GridBuilder` | `grid` | Fluent builder for constructing grids |
| `StorageAdapter` | `storage.protocol` | Protocol for pluggable storage backends |
| `TLSConfig` | `tls` | TLS certificate configuration |
| `MetricsHooks` | `metrics` | Optional observability callbacks |
| `WatchState` | `watch` | Server-side watch delta computation |
| `WatchAccumulator` | `watch` | Client-side watch delta merging |
| `Namespace` | `ontology.namespace` | Resolved ontology with taxonomy queries |

### Error Handling

All client methods raise from a common exception hierarchy:

```python
from hs_py import HaystackError, CallError, AuthError, NetworkError

# HaystackError         Base for all hs-py errors
#   CallError            Server returned an error grid
#   AuthError            Authentication failure
#   NetworkError         Transport-level failure
```

## Configuration

### Docker Compose

The included `docker/docker-compose.yml` provides a complete development stack:

```bash
docker compose -f docker/docker-compose.yml up -d
```

Services:

| Service | Port | Description |
|---------|------|-------------|
| `server` | 8080 | FastAPI Haystack server with SCRAM auth |
| `redis` | 6379 | Redis with RediSearch and RedisTimeSeries |
| `timescaledb` | 5432 | TimescaleDB (PostgreSQL 16) |
| `redis-tls` | 6380 | Redis with mTLS for TLS integration tests |

Environment variables for the server:

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://redis:6379` | Redis connection URL |
| `HAYSTACK_USER` | `admin` | SCRAM username |
| `HAYSTACK_PASS` | `secret` | SCRAM password |

### Seed Data

The `_data/` directory contains Project Haystack example building datasets in JSON v4 format:

- **Alpha** — 2,032 entities (1 site, 184 equips, 1,846 points)
- **Bravo** — 1,077 entities (1 site, 149 equips, 918 points)

These are loaded automatically by the Docker server on startup.

## Testing

```bash
make test          # 1,200+ unit tests
make lint          # ruff check + format verification
make typecheck     # mypy strict
make check         # lint + typecheck + test (all of the above)
make coverage      # tests with coverage report
make fix           # auto-fix lint/format issues
make docs          # sphinx-build
```

### Docker Integration Tests

End-to-end testing against real services with full SCRAM authentication:

```bash
make docker-server         # Start Redis + FastAPI server stack
make docker-test-e2e       # 69 end-to-end tests (HTTP, WebSocket, auth, history, watch)
make docker-server-clean   # Tear down server stack
```

### Storage Backend Tests

```bash
make docker-test              # Redis adapter integration tests
make docker-test-tls          # Redis mTLS integration tests
make docker-test-timescale    # 122 TimescaleDB integration tests
```

### Cleanup

```bash
make docker-clean             # Remove Redis containers
make docker-clean-timescale   # Remove TimescaleDB containers
```

## Requirements

- Python >= 3.13
- [aiohttp](https://docs.aiohttp.org/) >= 3.10
- [orjson](https://github.com/ijl/orjson) >= 3.10
- [cryptography](https://cryptography.io/) >= 42.0
- [websockets](https://websockets.readthedocs.io/) >= 14.0
- Optional: [FastAPI](https://fastapi.tiangolo.com/) + [Redis](https://redis.io/) for server mode
- Optional: [asyncpg](https://magicstack.github.io/asyncpg/) for TimescaleDB backend
- Docker and Docker Compose for integration tests

## License

MIT
