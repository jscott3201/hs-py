# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.10] - 2026-02-24

### Added

- **WebSocket binary compression**: Codec-level zlib and LZMA compression for binary frames via `FLAG_COMPRESSED` (v2 frame format). Configurable per-connection through capabilities negotiation.
- **WebSocket chunked transfer**: Large binary payloads automatically split into 256 KB chunks via `FLAG_CHUNKED`, with `ChunkAssembler` for ordered reassembly. Reduces peak memory and enables streaming.
- **WebSocket capabilities negotiation**: Client and server exchange supported features (compression algorithms, chunking) on connect, agreeing on the intersection.
- **Watch push chunking**: `push_watch()` now chunks large watch payloads through `encode_chunked_frames` when chunking is enabled, consistent with the response path.
- **`ReconnectingWebSocketClient` completeness**: Added 7 missing delegation methods: `batch`, `his_read_batch`, `his_write_batch`, `point_write`, `point_write_array`, `watch_close`, `close`.
- **Batch handler metrics**: WS batch dispatch now fires `on_error` and `on_ws_message_sent` metrics callbacks.

### Fixed

- **WebSocket TEXT vs BINARY frame routing**: `HaystackWebSocket.recv()` now returns `str` for TEXT opcodes and `bytes` for BINARY, fixing silent capabilities negotiation failures where text-frame JSON was misrouted to the binary decoder.
- **Server double-encoding on chunked responses**: Response path now encodes the grid payload once and checks raw size against `CHUNK_THRESHOLD`, instead of encoding twice or checking post-compression size.
- **ChunkAssembler index validation**: Reassembly now validates that all expected chunk indices (0..N-1) are present, raising `ValueError` on gaps or duplicates instead of `KeyError`.
- **Chunked frame guard**: Both server and client now log a warning and skip chunked frames when chunking is not enabled, preventing silent data corruption from chunk headers leaking into payload bytes.
- **ChunkAssembler memory cleanup**: Periodic cleanup of orphaned chunk buffers (30s interval) wired into both server `_message_loop` and client `_recv_loop`, preventing unbounded memory growth from incomplete sequences.

### Changed

- **`decode_chunk_header` → `_decode_chunk_header`**: Internal function made private; not part of public API.
- **`CHUNK_SIZE` and `CHUNK_THRESHOLD` exported**: Added to `ws_codec.__all__` for external use.
- **`time` import moved to module level** in `ws_codec.py` (was lazy-imported inside `_ChunkBuffer.__init__`).
- **Redundant `None` check removed** in `encode_chunked_frames` — `threshold=0` guarantees compression, replaced with assert.

## [0.1.9] - 2026-02-24

### Security

- **SCRAM nonce verification**: Server-side `scram_step2` now verifies the client nonce prefix and channel-binding data, preventing replay and MitM attacks.
- **SCRAM empty nonce rejection**: `scram_step1` rejects empty client nonces.
- **Mandatory server signature**: Client SCRAM auth raises `AuthError` if the server omits the signature proof (`v=`), preventing authentication bypass.
- **Minimum SCRAM iterations**: Client enforces a floor of 4,096 PBKDF2 iterations to resist brute-force attacks.
- **SCRAM iterations error handling**: Non-integer iteration count in server response now raises `AuthError` instead of crashing.
- **WebSocket role enforcement**: Standalone WS server now tracks authenticated username and checks role permissions before dispatching ops; write ops require Operator or Admin role.
- **WebSocket max message size**: Standalone WS server enforces a 10 MB message size limit on `accept()`.
- **WebSocket batch size limit**: Both WS and FastAPI servers cap batch requests to 1,000 items.
- **Filter parser recursion limit**: Recursive descent parser enforces a maximum nesting depth of 50 to prevent stack overflow.
- **Ref decoder validation**: `_decode_ref_v4` fast path now validates the ref value against the Haystack identifier regex before bypassing `__post_init__`.
- **Nested grid depth threading**: Zinc `decode_grid` → `_parse_row_line` → `scan_val` now threads `_depth` to enforce the existing `MAX_SCAN_DEPTH` limit on nested grids.
- **Bounded response caches**: HTTP and WS response caches capped at 2,048 entries to prevent unbounded memory growth.
- **Cache invalidation on writes**: Mutation ops (`hisWrite`, `pointWrite`, `invokeAction`) now clear both HTTP and WS response caches.
- **History write limits**: `InMemoryAdapter.his_write` caps per-point history at 100,000 items, trimming oldest entries on overflow.
- **`pointWrite` level validation**: Rejects levels outside 1–17 with an error grid.
- **`close` op permission**: HTTP `close` endpoint now requires Operator or Admin role.
- **Bounded list/dict scanning**: Zinc scanner limits list and dict elements to 100,000 to prevent memory exhaustion.
- **Zinc grid decode limits**: `decode_grid` enforces max 200,000 rows and 10,000 columns.
- **RediSearch escape hardening**: Added `|`, `/`, `?`, and `` ` `` to the escaped character set in `_ft_escape`.
- **Unicode escape validation**: Zinc scanner and filter lexer now reject incomplete `\u` sequences and surrogate codepoints (U+D800–U+DFFF).
- **Private key file permissions**: TLS key files are written with `0o600` mode.
- **WebSocket finally-block safety**: `remote` variable initialized before the try block to prevent `NameError` on early connection failure.

### Fixed

- **Documentation**: Corrected `MemoryAdapter` → `InMemoryAdapter`, `create_app` → `create_fastapi_app`, fixed return type annotations in client guide, added `raw=True` to watch/WebSocket examples, corrected TLS 1.2 → 1.3, fixed Docker env var names, removed stale `[rdf]` extra reference, updated test count.

## [0.1.8] - 2026-02-24

### Optimized

- **JSON encoder**: Type dispatch table (`_V4_TYPE_ENCODERS`) for O(1) encode lookup; singleton identity checks for Marker/NA/REMOVE; inlined Ref encoding in orjson hook; eliminated unnecessary `dict(row)` copies in grid encoding.
- **JSON decoder**: Inlined `_decode_kind_v4` into `_decode_val_v4`; type identity checks (`type(obj) is str`) instead of isinstance chains; fast Ref decode via `__new__` + `object.__setattr__` bypassing frozen dataclass `__post_init__`.
- **DateTime caching**: Encode cache (`_DT_CACHE`) and decode cache (`_DECODE_DT_CACHE`) for datetime values — 31× hit ratio on typical entity datasets.
- **ZoneInfo caching**: `_tz_cache` in scanner eliminates filesystem I/O on every `ZoneInfo()` call (was 48% of server CPU).
- **Filter evaluation**: Type dispatch table (`_EVAL_DISPATCH`); single-segment fast paths for Has/Cmp nodes; tag presence index in InMemoryAdapter.
- **Grid construction**: `_COL_CACHE` for Col objects with no metadata; `Grid._fast_init()` classmethod bypassing dataclass `__init__`/`__post_init__`; `make_rows_with_col_names` skips column inference scan.
- **Adapter read caches**: Both RedisAdapter and TimescaleAdapter cache decoded read results by `(query, limit)` key (max 64 entries).
- **`all_col_names` property**: Added to InMemory, Redis, and TimescaleDB adapters — populated at entity load time, enables `Grid.make_rows_with_col_names` fast path.
- **WebSocket standalone server**: Byte concatenation replaces `encode_grid_dict` → `orjson.dumps(wrapper)` double-serialization; `send_text_preencoded()` skips `str→encode→bytes` roundtrip; response cache for read ops; concurrent batch dispatch via `asyncio.gather`.
- **WebSocket sans-I/O layer**: `deque` for pending frames (O(1) popleft); `send_text_preencoded(bytes)` method for pre-encoded UTF-8 payloads.
- **HTTP response caching**: Static responses (`/about`, `/ops`, `/formats`) and read responses cached by `(filter, limit, format)` key.

### Added

- Pure ASGI SCRAM-SHA-256 auth middleware for FastAPI server.
- Pydantic request/response models for auth endpoints.
- CORS and HSTS security headers on FastAPI server.
- Error message sanitization — internal details no longer leaked to clients.
- Docker benchmark suite with single-client sequential HTTP/WS tests per backend.
- PyInstrument profiling infrastructure (`bench_profile_server.py`, `bench_profile_client.py`).
- Binary WebSocket frame codec (`ws_codec.py`) with 4-byte header format.

### Changed

- Benchmark Docker Compose simplified to single client per transport/backend (was 3 clients).
- Benchmark duration reduced to 15s + 3s warmup (was 30s + 5s).
- Client container memory limit increased to 1.5 GB (was 768 MB).
- Updated benchmark documentation with current results: HTTP 3,300–3,700 rps, WS 1,550–1,800 msg/s.

## [0.1.7] - 2026-02-24

### Added

- `Role` enum (`ADMIN`, `OPERATOR`, `VIEWER`) for role-based access control.
- Permission helpers: `can_admin()`, `can_write()`, `can_read()` and op classification sets `WRITE_OPS`/`READ_OPS`.
- Role enforcement on Haystack POST ops — write ops (hisWrite, pointWrite, invokeAction, watches) require Operator or Admin role.
- Role enforcement on WebSocket ops — same permission model as HTTP.
- User management endpoints now validate `role` field (string) on create/update.

### Changed

- **Breaking:** Replaced `is_superuser: bool` on `User` with `role: Role` enum field.
- `create_user()` accepts `role=Role.VIEWER` (default) instead of `is_superuser=False`.
- User API responses return `"role": "admin"|"operator"|"viewer"` instead of `"is_superuser": bool`.
- `ensure_superuser()` bootstrap now checks for `role == Role.ADMIN` instead of `is_superuser`.
- TimescaleDB `hs_users` table schema: `is_superuser BOOLEAN` column replaced with `role TEXT`.
- All three storage backends (InMemory, Redis, TimescaleDB) updated for `role` field.

## [0.1.6] - 2026-02-24

### Optimized

- Zinc `encode_grid()` uses `join()` instead of `+=` string concatenation for metadata tags.
- `Grid.col_names` cached at construction time instead of rebuilding tuple on every access.
- `escape_str()` fast path returns input unchanged when no escaping is needed.
- `evaluate_grid()` constructs Grid directly, reusing immutable cols tuple (no GridBuilder copy).
- Removed unnecessary `sorted()` in timezone city map construction.
- `_resolve_path()` uses single `dict.get()` with sentinel instead of two lookups.
- Ontology conjunct matching uses pre-built frozenset index for O(1) lookup (was O(N) scan).
- Redis filter fallback capped at 50,000 entities (was unbounded full-table scan).
- `watch_poll()` batches dirty + watched ID lookups into a single Redis pipeline.
- TimescaleDB filter fallback capped at 50,000 rows (was unbounded).
- Zinc list encoding uses list comprehension inside `join()` to avoid generator overhead.
- JSON pythonic transform fast path skips rebuild for common non-transformable value types.
- CSV cell escape uses `frozenset.intersection()` instead of 4 separate `in` scans.
- WebSocket request ID space expanded from 16-bit to 32-bit to prevent theoretical collision.

## [0.1.5] - 2026-02-24

### Security

- Capped PBKDF2 iteration count at 100,000 to prevent CPU exhaustion DoS.
- SCRAM username now escaped per RFC 5802 (`=` → `=3D`, `,` → `=2C`).
- Missing server signature in SCRAM now raises `AuthError` (prevents MitM).
- Enforced minimum 16-byte salt length in SCRAM per NIST SP 800-132.
- Validated tag names against strict regex before SQL interpolation in TimescaleDB adapter.
- Validated Redis key components against Haystack identifier character set.
- HTTP client disables redirect following to prevent SSRF.
- Client password cleared from memory after successful authentication.
- Client `__repr__` no longer exposes password.

### Added

- Recursion depth limits (64) on Zinc scanner (`scan_val`, `scan_list`, `scan_dict`, nested grids).
- Recursion depth limit (32) on Trio parser for nested records.
- Grid decode limits: max 100,000 rows, 10,000 columns in JSON decoder.
- String and URI length cap (1 MB) in Zinc scanner.
- Filter parser rejects expressions exceeding 10 KB before LRU caching.
- `Ref.val` format validation against Haystack identifier characters.
- Atomic pipeline for Redis watch dirty-set read-and-clear (fixes TOCTOU race).
- RediSearch query result cap at 10,000.
- Full project metadata in `pyproject.toml` (authors, keywords, classifiers, URLs).

## [0.1.4] - 2026-02-24

### Added

- Formal `examples/` directory with 8 runnable example scripts covering client,
  server, WebSocket, encoding formats, filters, grid building, ontology, and TLS.

### Removed

- Removed ad-hoc benchmark scripts from `scripts/` (replaced by formal examples).

## [0.1.3] - 2026-02-24

### Changed

- Renamed PyPI package from ``hs-py`` to ``haystack-py``.

## [0.1.2] - 2026-02-24

### Fixed

- Fixed SCRAM middleware tests using monotonic-relative timestamps for reliable CI.

## [0.1.1] - 2026-02-24

### Changed

- Made `rdflib` a core dependency (no longer optional).
- Local `make` targets now run with `--all-extras` to match CI.

### Fixed

- Fixed SCRAM auth middleware tests (stale handshake purge and token expiry).
- Fixed RDF turtle export test assertion for prefixed namespace output.

### Added

- MIT license file.
- License metadata in `pyproject.toml`.

## [0.1.0] - 2026-02-24

Initial release.
