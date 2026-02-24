# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
