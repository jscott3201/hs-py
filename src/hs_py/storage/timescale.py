"""TimescaleDB StorageAdapter for Haystack server backends.

Provides ``TimescaleAdapter`` implementing the ``StorageAdapter`` Protocol
using asyncpg for async PostgreSQL/TimescaleDB access.

Schema is created automatically on ``start()``. Entities are stored as JSONB
in ``hs_entities``. Time-series history uses ``hs_history`` (optionally a
TimescaleDB hypertable). Priority arrays are stored in ``hs_priority``.
Watches are tracked in ``hs_watches`` and ``hs_watch_entities``.

Usage::

    pool = await create_timescale_pool("postgresql://localhost/haystack")
    adapter = TimescaleAdapter(pool)
    await adapter.start()
    await adapter.load_entities([{"id": Ref("site1"), "site": MARKER, "dis": "My Site"}])
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

    from hs_py.filter.ast import Node
    from hs_py.kinds import Ref
    from hs_py.tls import TLSConfig

__all__ = [
    "TimescaleAdapter",
    "create_timescale_pool",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema SQL
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hs_entities (
    id TEXT PRIMARY KEY,
    tags JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_entities_tags ON hs_entities USING GIN (tags);

CREATE TABLE IF NOT EXISTS hs_history (
    entity_id TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    val DOUBLE PRECISION NOT NULL,
    unit TEXT,
    PRIMARY KEY (entity_id, ts)
);

CREATE TABLE IF NOT EXISTS hs_priority (
    entity_id TEXT NOT NULL,
    level SMALLINT NOT NULL,
    val JSONB,
    who TEXT DEFAULT '',
    PRIMARY KEY (entity_id, level)
);

CREATE TABLE IF NOT EXISTS hs_watches (
    watch_id TEXT PRIMARY KEY,
    dis TEXT NOT NULL DEFAULT '',
    lease_secs INTEGER DEFAULT 300,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hs_watch_entities (
    watch_id TEXT REFERENCES hs_watches(watch_id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
    dirty BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (watch_id, entity_id)
);
"""

_HYPERTABLE_SQL = """
SELECT create_hypertable('hs_history', 'ts', if_not_exists => true);
"""


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def _encode_tags(entity: dict[str, Any]) -> dict[str, Any]:
    """Encode entity tags to a JSONB-compatible dict using Haystack JSON v4."""
    from hs_py.encoding.json import encode_val

    return {k: encode_val(v) for k, v in entity.items()}


def _decode_tags(tags: dict[str, Any]) -> dict[str, Any]:
    """Decode JSONB tags dict back to Haystack kinds using JSON v4."""
    from hs_py.encoding.json import decode_val

    return {k: decode_val(v) for k, v in tags.items()}


# ---------------------------------------------------------------------------
# Filter AST → PostgreSQL JSONB translation
# ---------------------------------------------------------------------------


def _is_simple_path(path: Any) -> bool:
    """Return True if the path has exactly one segment (simple tag name)."""
    return len(path.names) == 1


def _ast_to_sql(
    node: Node,
    params: list[Any],
) -> str | None:
    """Translate a filter AST node to a PostgreSQL JSONB expression.

    Returns a SQL fragment string, or ``None`` if the node cannot be
    translated (falls back to Python evaluation).

    :param node: Filter AST node to translate.
    :param params: Mutable list to append parameter values to.
    :returns: SQL fragment or ``None``.
    """
    from hs_py.filter.ast import And, Cmp, CmpOp, Has, Missing, Or

    if isinstance(node, Has):
        if not _is_simple_path(node.path):
            return None
        name = node.path.names[0]
        return f"tags ? {_pg_literal(name)}"

    if isinstance(node, Missing):
        if not _is_simple_path(node.path):
            return None
        name = node.path.names[0]
        return f"NOT (tags ? {_pg_literal(name)})"

    if isinstance(node, Cmp):
        if not _is_simple_path(node.path):
            return None
        name = node.path.names[0]
        sql_val = _encode_cmp_val(node.val)
        if sql_val is None:
            return None

        idx = len(params) + 1

        if node.op == CmpOp.EQ:
            params.append(sql_val)
            return f"tags->>{_pg_literal(name)} = ${idx}"
        if node.op == CmpOp.NE:
            params.append(sql_val)
            return f"tags->>{_pg_literal(name)} != ${idx}"

        # Numeric comparisons — pass float so asyncpg binds correctly
        try:
            float_val = float(sql_val)
        except (ValueError, TypeError):
            return None
        params.append(float_val)

        if node.op == CmpOp.GT:
            return f"(tags->>{_pg_literal(name)})::float > ${idx}::float"
        if node.op == CmpOp.GE:
            return f"(tags->>{_pg_literal(name)})::float >= ${idx}::float"
        if node.op == CmpOp.LT:
            return f"(tags->>{_pg_literal(name)})::float < ${idx}::float"
        if node.op == CmpOp.LE:
            return f"(tags->>{_pg_literal(name)})::float <= ${idx}::float"
        return None

    if isinstance(node, And):
        left = _ast_to_sql(node.left, params)
        right = _ast_to_sql(node.right, params)
        if left is None or right is None:
            return None
        return f"({left}) AND ({right})"

    if isinstance(node, Or):
        left = _ast_to_sql(node.left, params)
        right = _ast_to_sql(node.right, params)
        if left is None or right is None:
            return None
        return f"({left}) OR ({right})"

    return None


def _encode_cmp_val(val: Any) -> str | None:
    """Convert a Haystack comparison value to a string for SQL parameter binding.

    Returns ``None`` for unsupported types.
    """
    from hs_py.kinds import Marker, Number, Ref

    if isinstance(val, str):
        return val
    if isinstance(val, bool):
        return str(val).lower()
    if isinstance(val, int | float):
        return str(val)
    if isinstance(val, Number):
        return str(val.val)
    if isinstance(val, Ref):
        return val.val
    if isinstance(val, Marker):
        return None  # Markers can't be meaningfully compared as strings
    return None


def _pg_literal(name: str) -> str:
    """Return a PostgreSQL string literal for a column/key name.

    Uses single-quoting with embedded single quotes doubled.
    Validates that the name matches Haystack tag name rules.

    :raises ValueError: If *name* contains disallowed characters.
    """
    if not _TAG_NAME_RE.match(name):
        msg = f"Invalid tag name for SQL: {name!r}"
        raise ValueError(msg)
    escaped = name.replace("'", "''")
    return f"'{escaped}'"


# Strict Haystack tag name pattern: starts with lowercase letter,
# then alphanumeric or underscore.
_TAG_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


# ---------------------------------------------------------------------------
# History range parsing
# ---------------------------------------------------------------------------


def _parse_his_range(
    range_str: str,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Parse a Haystack history range string to a (start, end) UTC pair.

    Supported formats:
    - ``"today"`` — today 00:00 to tomorrow 00:00 UTC
    - ``"yesterday"`` — yesterday 00:00 to today 00:00 UTC
    - ``"YYYY-MM-DD"`` — single date (00:00 to 24:00)
    - ``"YYYY-MM-DD,YYYY-MM-DD"`` — date range (inclusive)
    - ``"YYYY-MM-DDTHH:MM:SS,YYYY-MM-DDTHH:MM:SS"`` — datetime range
    """
    utc = datetime.UTC
    today = datetime.datetime.now(utc).date()

    range_str = range_str.strip()

    if range_str == "today":
        start = datetime.datetime(today.year, today.month, today.day, tzinfo=utc)
        end = start + datetime.timedelta(days=1)
        return start, end

    if range_str == "yesterday":
        yesterday = today - datetime.timedelta(days=1)
        start = datetime.datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=utc)
        end = start + datetime.timedelta(days=1)
        return start, end

    if "," in range_str:
        parts = [p.strip() for p in range_str.split(",", 1)]
        start = _parse_datetime_str(parts[0], utc)
        end_raw = _parse_datetime_str(parts[1], utc)
        # If end is a date (no time component), extend to end of day
        if "T" not in parts[1] and len(parts[1]) == 10:
            end_raw = end_raw + datetime.timedelta(days=1)
        return start, end_raw

    # Single date
    start = _parse_datetime_str(range_str, utc)
    end = start + datetime.timedelta(days=1)
    return start, end


def _parse_datetime_str(s: str, utc: datetime.timezone) -> datetime.datetime:
    """Parse a date or datetime string to a UTC datetime."""
    if "T" in s or " " in s:
        dt = datetime.datetime.fromisoformat(s.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=utc)
        return dt.astimezone(utc)
    # date-only
    d = datetime.date.fromisoformat(s)
    return datetime.datetime(d.year, d.month, d.day, tzinfo=utc)


# ---------------------------------------------------------------------------
# TimescaleAdapter
# ---------------------------------------------------------------------------


class TimescaleAdapter:
    """StorageAdapter backed by PostgreSQL/TimescaleDB via asyncpg.

    :param pool: asyncpg connection pool to use.
    """

    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        """Initialise the adapter with an existing asyncpg pool.

        :param pool: Open asyncpg connection pool.
        """
        self._pool = pool

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Create database schema (idempotent).

        Runs DDL to create tables and indexes if they do not already exist.
        Attempts to create a TimescaleDB hypertable for ``hs_history``; the
        attempt is silently skipped if TimescaleDB is not available.
        """
        import asyncpg as _asyncpg

        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
            try:
                await conn.execute(_HYPERTABLE_SQL)
            except _asyncpg.UndefinedFunctionError:
                _log.debug("TimescaleDB hypertable creation skipped (not available)")

    async def close(self) -> None:
        """Close the underlying connection pool."""
        await self._pool.close()

    # -----------------------------------------------------------------------
    # Entity read operations
    # -----------------------------------------------------------------------

    async def read_by_filter(
        self,
        ast: Node,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return entities matching the filter AST.

        Translates simple (single-segment) filter nodes to JSONB SQL. Falls
        back to Python-side evaluation via :func:`hs_py.filter.evaluate` for
        multi-segment paths or unsupported node types.

        :param ast: Parsed filter AST.
        :param limit: Maximum number of entities to return.  ``None`` means
            no limit.
        :returns: List of entity tag dicts.
        """
        from hs_py.filter.eval import evaluate

        params: list[Any] = []
        sql_clause = _ast_to_sql(ast, params)

        if sql_clause is not None:
            base = "SELECT tags FROM hs_entities WHERE " + sql_clause
            if limit is not None:
                base += f" LIMIT {limit}"
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(base, *params)
            results = [_decode_tags(dict(row["tags"])) for row in rows]
        else:
            base = "SELECT tags FROM hs_entities"
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(base)
            results = []
            for row in rows:
                entity = _decode_tags(dict(row["tags"]))
                if evaluate(ast, entity):
                    results.append(entity)
                    if limit is not None and len(results) >= limit:
                        break

        return results

    async def read_by_ids(self, ids: list[Ref]) -> list[dict[str, Any] | None]:
        """Return entities for a list of Refs, preserving input order.

        :param ids: Ordered list of entity Refs to fetch.
        :returns: List the same length as *ids*.  Each entry is the entity
            dict if found, or ``None`` if the Ref does not exist.
        """
        if not ids:
            return []

        ref_vals = [ref.val for ref in ids]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, tags FROM hs_entities WHERE id = ANY($1::text[])",
                ref_vals,
            )

        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            by_id[row["id"]] = _decode_tags(dict(row["tags"]))

        return [by_id.get(rv) for rv in ref_vals]

    async def nav(self, nav_id: str | None = None) -> list[dict[str, Any]]:
        """Navigate the entity tree.

        - ``nav_id=None`` returns all site entities.
        - ``nav_id`` set to a site id returns equip entities for that site.
        - ``nav_id`` set to an equip id returns point entities for that equip.

        :param nav_id: ``None`` for root, or an entity id to navigate into.
        :returns: List of entity tag dicts.
        """
        async with self._pool.acquire() as conn:
            if nav_id is None:
                # Root: return sites
                rows = await conn.fetch("SELECT tags FROM hs_entities WHERE tags ? 'site'")
            else:
                # Determine whether nav_id is a site or equip
                parent = await conn.fetchrow(
                    "SELECT tags FROM hs_entities WHERE id = $1",
                    nav_id,
                )
                if parent is None:
                    return []

                parent_tags = _decode_tags(dict(parent["tags"]))

                if "site" in parent_tags:
                    # Site → return equips referencing this site
                    rows = await conn.fetch(
                        """
                        SELECT tags FROM hs_entities
                        WHERE tags ? 'equip'
                        AND tags->'siteRef'->>'val' = $1
                        """,
                        nav_id,
                    )
                else:
                    # Equip (or anything else) → return points referencing this equip
                    rows = await conn.fetch(
                        """
                        SELECT tags FROM hs_entities
                        WHERE tags ? 'point'
                        AND tags->'equipRef'->>'val' = $1
                        """,
                        nav_id,
                    )

        return [_decode_tags(dict(row["tags"])) for row in rows]

    # -----------------------------------------------------------------------
    # History operations
    # -----------------------------------------------------------------------

    async def his_read(
        self,
        ref: Ref,
        range_str: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return time-series history for a point.

        :param ref: Entity Ref of the point.
        :param range_str: Optional Haystack range string (e.g. ``"today"``,
            ``"2024-01-01,2024-01-31"``).  If ``None``, all data is returned.
        :returns: List of dicts with ``"ts"`` (datetime) and ``"val"`` keys.
        """
        ref_val = ref.val
        if range_str is not None:
            start, end = _parse_his_range(range_str)
        else:
            start = datetime.datetime.min.replace(tzinfo=datetime.UTC)
            end = datetime.datetime.max.replace(tzinfo=datetime.UTC)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT ts, val, unit FROM hs_history
                WHERE entity_id = $1 AND ts >= $2 AND ts < $3
                ORDER BY ts
                """,
                ref_val,
                start,
                end,
            )

        result = []
        for row in rows:
            item: dict[str, Any] = {"ts": row["ts"], "val": row["val"]}
            if row["unit"] is not None:
                item["unit"] = row["unit"]
            result.append(item)
        return result

    async def his_write(self, ref: Ref, items: list[dict[str, Any]]) -> None:
        """Append time-series data for a point.

        :param ref: Entity Ref of the point.
        :param items: List of dicts with ``"ts"`` and ``"val"`` keys.
        """
        from hs_py.kinds import Number

        if not items:
            return

        ref_val = ref.val
        records = []
        for item in items:
            ts = item["ts"]
            raw_val = item.get("val", 0)
            unit: str | None = None
            if isinstance(raw_val, Number):
                unit = raw_val.unit
                val = raw_val.val
            else:
                val = float(raw_val)

            if isinstance(ts, datetime.datetime) and ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.UTC)

            records.append((ref_val, ts, val, unit))

        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO hs_history (entity_id, ts, val, unit)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (entity_id, ts) DO UPDATE
                    SET val = EXCLUDED.val, unit = EXCLUDED.unit
                """,
                records,
            )

    # -----------------------------------------------------------------------
    # Priority array operations
    # -----------------------------------------------------------------------

    async def point_write(
        self,
        ref: Ref,
        level: int,
        val: Any,
        who: str = "",
        duration: Any = None,
    ) -> None:
        """Write a value to a writable point's priority array.

        :param ref: Entity Ref of the writable point.
        :param level: Priority level (1-17).
        :param val: Value to write.  Pass ``None`` to clear the level.
        :param who: Optional identifier of who is writing.
        :param duration: Ignored by this backend.
        """
        import orjson

        from hs_py.encoding.json import encode_val

        ref_val = ref.val
        if val is None:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM hs_priority WHERE entity_id = $1 AND level = $2",
                    ref_val,
                    level,
                )
        else:
            encoded = encode_val(val)
            val_json = orjson.dumps(encoded).decode()
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO hs_priority (entity_id, level, val, who)
                    VALUES ($1, $2, $3::jsonb, $4)
                    ON CONFLICT (entity_id, level) DO UPDATE
                        SET val = EXCLUDED.val, who = EXCLUDED.who
                    """,
                    ref_val,
                    level,
                    val_json,
                    who or "",
                )

    async def point_read_array(self, ref: Ref) -> list[dict[str, Any]]:
        """Return the 17-level priority array for a writable point.

        :param ref: Entity Ref of the writable point.
        :returns: List of 17 dicts, each with a ``"level"`` key and an
            optional ``"val"`` key (absent when the level is unset).
        """
        from hs_py.encoding.json import decode_val
        from hs_py.kinds import Number

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT level, val, who FROM hs_priority WHERE entity_id = $1",
                ref.val,
            )

        by_level: dict[int, dict[str, Any]] = {}
        for row in rows:
            raw_val = row["val"]
            decoded_val: Any = None
            if raw_val is not None:
                import orjson

                obj = orjson.loads(raw_val) if isinstance(raw_val, str) else raw_val
                decoded_val = decode_val(obj)
            by_level[row["level"]] = {"level": Number(float(row["level"])), "val": decoded_val}

        rows_out: list[dict[str, Any]] = []
        for lvl in range(1, 18):
            if lvl in by_level:
                rows_out.append(by_level[lvl])
            else:
                row_d: dict[str, Any] = {"level": Number(float(lvl)), "val": None}
                rows_out.append(row_d)
        return rows_out

    # -----------------------------------------------------------------------
    # Watch operations
    # -----------------------------------------------------------------------

    async def watch_sub(
        self,
        watch_id: str | None,
        ids: list[Ref],
        dis: str = "watch",
    ) -> tuple[str, list[dict[str, Any]]]:
        """Create or extend a watch subscription.

        :param watch_id: Existing watch ID to extend, or ``None`` to create a
            new watch.
        :param ids: Entity Refs to add to the watch.
        :param dis: Human-readable display name for a new watch.
        :returns: ``(watch_id, entities)`` where *entities* is the current
            state of all newly subscribed entities.
        """
        import secrets

        if not watch_id:
            watch_id = f"w-{secrets.token_hex(4)}"

        ref_vals = [ref.val for ref in ids]

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO hs_watches (watch_id, dis)
                VALUES ($1, $2)
                ON CONFLICT (watch_id) DO NOTHING
                """,
                watch_id,
                dis,
            )

            if ref_vals:
                records = [(watch_id, rv) for rv in ref_vals]
                await conn.executemany(
                    """
                    INSERT INTO hs_watch_entities (watch_id, entity_id, dirty)
                    VALUES ($1, $2, true)
                    ON CONFLICT (watch_id, entity_id) DO NOTHING
                    """,
                    records,
                )

            entities = await self._fetch_watch_entities_with_conn(conn, watch_id)
        return watch_id, entities

    async def watch_unsub(
        self,
        watch_id: str,
        ids: list[Ref],
        *,
        close: bool = False,
    ) -> None:
        """Remove entities from a watch, or close the watch entirely.

        :param watch_id: Watch to modify.
        :param ids: Entity Refs to remove.  Ignored when *close* is ``True``.
        :param close: If ``True``, the entire watch is torn down.
        """
        async with self._pool.acquire() as conn:
            if close:
                await conn.execute(
                    "DELETE FROM hs_watches WHERE watch_id = $1",
                    watch_id,
                )
            else:
                ref_vals = [ref.val for ref in ids]
                if ref_vals:
                    await conn.execute(
                        """
                        DELETE FROM hs_watch_entities
                        WHERE watch_id = $1 AND entity_id = ANY($2::text[])
                        """,
                        watch_id,
                        ref_vals,
                    )

    async def watch_poll(
        self,
        watch_id: str,
        *,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Poll a watch for changed entities.

        Uses a transaction to atomically fetch and clear dirty flags,
        preventing lost notifications from concurrent writers.

        :param watch_id: Watch to poll.
        :param refresh: If ``True``, return all watched entities regardless of
            dirty state.
        :returns: List of entity dicts that have changed since the last poll.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            if refresh:
                entities = await self._fetch_watch_entities_with_conn(conn, watch_id)
            else:
                entities = await self._fetch_dirty_watch_entities_with_conn(conn, watch_id)

            await conn.execute(
                "UPDATE hs_watch_entities SET dirty = false WHERE watch_id = $1",
                watch_id,
            )

        return entities

    # -----------------------------------------------------------------------
    # Helper: load_entities
    # -----------------------------------------------------------------------

    async def load_entities(self, entities: list[dict[str, Any]]) -> int:
        """Bulk-upsert a list of entity dicts into the store.

        Uses a staging table with COPY for fast bulk loading, then upserts
        into the main table.  The ``id`` tag (a :class:`~hs_py.kinds.Ref`) is
        extracted and used as the primary key. Entities without an ``id`` are
        skipped.

        :param entities: List of entity tag dicts.
        :returns: Number of entities written.
        """
        import orjson

        from hs_py.kinds import Ref

        records = []
        for entity in entities:
            id_tag = entity.get("id")
            if not isinstance(id_tag, Ref):
                continue
            tags_json = orjson.dumps(_encode_tags(entity)).decode()
            records.append((id_tag.val, tags_json))

        if not records:
            return 0

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("CREATE TEMP TABLE _staging (id TEXT, tags TEXT) ON COMMIT DROP")
            await conn.copy_records_to_table("_staging", records=records, columns=["id", "tags"])
            await conn.execute(
                """
                INSERT INTO hs_entities (id, tags, updated_at)
                SELECT id, tags::jsonb, now() FROM _staging
                ON CONFLICT (id) DO UPDATE
                    SET tags = EXCLUDED.tags, updated_at = now()
                """
            )

        return len(records)

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _fetch_watch_entities_with_conn(
        self, conn: asyncpg.Connection[Any], watch_id: str
    ) -> list[dict[str, Any]]:
        """Fetch all entities subscribed to a watch using an existing connection."""
        rows = await conn.fetch(
            """
            SELECT e.tags
            FROM hs_watch_entities we
            JOIN hs_entities e ON e.id = we.entity_id
            WHERE we.watch_id = $1
            """,
            watch_id,
        )
        return [_decode_tags(dict(row["tags"])) for row in rows]

    async def _fetch_dirty_watch_entities_with_conn(
        self, conn: asyncpg.Connection[Any], watch_id: str
    ) -> list[dict[str, Any]]:
        """Fetch only dirty (changed) entities subscribed to a watch."""
        rows = await conn.fetch(
            """
            SELECT e.tags
            FROM hs_watch_entities we
            JOIN hs_entities e ON e.id = we.entity_id
            WHERE we.watch_id = $1 AND we.dirty = true
            """,
            watch_id,
        )
        return [_decode_tags(dict(row["tags"])) for row in rows]


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


async def _init_connection(conn: asyncpg.Connection[Any]) -> None:
    """Register type codecs on each new physical connection.

    Uses orjson for JSONB encoding/decoding for consistency with the rest
    of the codebase and better performance.
    """
    import orjson

    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: orjson.dumps(v).decode(),
        decoder=orjson.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=lambda v: orjson.dumps(v).decode(),
        decoder=orjson.loads,
        schema="pg_catalog",
    )


async def create_timescale_pool(
    dsn: str = "postgresql://localhost:5432/haystack",
    *,
    min_size: int = 2,
    max_size: int = 10,
    command_timeout: float = 60.0,
    tls: TLSConfig | None = None,
) -> asyncpg.Pool[Any]:
    """Create an asyncpg connection pool for TimescaleDB.

    Configures connection recycling, idle connection cleanup, and
    registers orjson-based JSONB codecs on each new connection.

    :param dsn: PostgreSQL DSN string.
    :param min_size: Minimum number of pooled connections.
    :param max_size: Maximum number of pooled connections.
    :param command_timeout: Per-query timeout in seconds (default 60).
    :param tls: Optional TLS configuration for SSL connections.
    :returns: Open :class:`asyncpg.Pool` ready for use.
    """
    import asyncpg as _asyncpg

    ssl_ctx: Any = None
    if tls is not None:
        from hs_py.tls import build_client_ssl_context

        ssl_ctx = build_client_ssl_context(tls)

    pool: asyncpg.Pool[Any] = await _asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        max_queries=50_000,
        max_inactive_connection_lifetime=300.0,
        command_timeout=command_timeout,
        init=_init_connection,
        ssl=ssl_ctx,
    )
    return pool
