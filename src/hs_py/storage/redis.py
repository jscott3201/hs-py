"""Redis storage adapter for Haystack servers.

Implements :class:`~hs_py.storage.protocol.StorageAdapter` using Redis 8
with RedisJSON, RedisTimeSeries, and RediSearch.

Requires Redis 8+ (ships with JSON, TimeSeries, and Search modules) and the
``redis[hiredis]`` Python package (installed via ``pip install hs-py[server]``).

Key schema::

    hs:e:{ref_val}          RedisJSON document (entity + _tags index field)
    hs:ids                  Set of all entity ref vals
    hs:tag:{tagname}        Set of ref vals that have this tag
    hs:ts:{ref_val}         TimeSeries key for history data
    hs:pri:{ref_val}        Hash mapping level -> JSON-encoded value
    hs:w:{watch_id}         Hash with watch metadata (dis, lease)
    hs:w:{watch_id}:ids     Set of watched ref vals
    hs:w:{watch_id}:dirty   Set of dirty ref vals

RediSearch index ``hs_idx`` is created on ``hs:e:*`` JSON documents with:

- ``_tags`` TAG field (comma-separated tag names) for Has/Missing queries
- ``siteRef`` TAG field (``$.siteRef.val``) for site navigation and filters
- ``equipRef`` TAG field (``$.equipRef.val``) for equip navigation and filters
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import re
import secrets
from typing import TYPE_CHECKING, Any

import orjson

from hs_py.encoding.json import _decode_val_v4, encode_val
from hs_py.filter import evaluate
from hs_py.filter.ast import And, Cmp, CmpOp, Has, Missing, Node, Or
from hs_py.kinds import Number, Ref
from hs_py.user import User, derive_scram_credentials, user_from_dict, user_to_dict

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from hs_py.tls import TLSConfig

__all__ = ["RedisAdapter", "create_redis_client"]

_log = logging.getLogger(__name__)

# Default connection settings (production best practices).
_SOCKET_TIMEOUT = 10.0
_SOCKET_CONNECT_TIMEOUT = 5.0
_HEALTH_CHECK_INTERVAL = 30
_RETRY_ATTEMPTS = 3
_MAX_CONNECTIONS = 50
_PIPELINE_BATCH_SIZE = 500

# Key prefixes
_E = "hs:e:"
_IDS = "hs:ids"
_TAG = "hs:tag:"
_TS = "hs:ts:"
_PRI = "hs:pri:"
_W = "hs:w:"
_USER = "hs:user:"

# RediSearch index name and schema
_FT_INDEX = "hs_idx"

# Maximum results from a single RediSearch query.
_MAX_FT_RESULTS = 10_000

# Maximum entities to scan in Python fallback when no tag index narrows candidates.
_MAX_FALLBACK_SCAN = 50_000

# Allowed pattern for Redis key components (Haystack identifiers).
_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9_:\-.~]+$")

# Ref-valued fields indexed as TAG in RediSearch for efficient querying.
_FT_REF_FIELDS = frozenset({"siteRef", "equipRef"})

# Expected field names in the RediSearch index.
_FT_EXPECTED_FIELDS = frozenset({"_tags", "siteRef", "equipRef"})


def _validate_key_part(value: str, label: str = "key") -> None:
    """Validate that a value is safe for use in a Redis key."""
    if not _SAFE_KEY_RE.match(value):
        msg = f"Invalid characters in {label}: {value!r}"
        raise ValueError(msg)


def _entity_key(ref_val: str) -> str:
    _validate_key_part(ref_val, "ref_val")
    return f"{_E}{ref_val}"


def _tag_key(tag: str) -> str:
    return f"{_TAG}{tag}"


def _ts_key(ref_val: str) -> str:
    return f"{_TS}{ref_val}"


def _pri_key(ref_val: str) -> str:
    return f"{_PRI}{ref_val}"


def _watch_key(watch_id: str) -> str:
    return f"{_W}{watch_id}"


def _watch_ids_key(watch_id: str) -> str:
    return f"{_W}{watch_id}:ids"


def _watch_dirty_key(watch_id: str) -> str:
    return f"{_W}{watch_id}:dirty"


def _encode_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """Encode an entity dict to JSON-serializable form (v4 format).

    Adds a ``_tags`` field with comma-separated tag names for RediSearch
    TAG indexing.  The dict is suitable for passing directly to
    ``r.json().set()``.
    """
    encoded = {k: encode_val(v) for k, v in entity.items()}
    encoded["_tags"] = ",".join(entity.keys())
    return encoded


def _decode_entity(raw: dict[str, Any]) -> dict[str, Any]:
    """Decode a JSON-serialized entity dict back to Haystack kinds.

    Strips the internal ``_tags`` index field.
    """
    raw.pop("_tags", None)
    return {k: _decode_val_v4(v) for k, v in raw.items()}


def _extract_has_tags(node: Node) -> set[str]:
    """Extract simple tag names from Has nodes in a filter AST.

    Used for candidate narrowing via tag index Sets before full evaluation.
    Only extracts tags from Has nodes connected by And -- Or and Cmp nodes
    are not useful for narrowing since they don't guarantee tag presence.
    """
    if isinstance(node, Has) and len(node.path.names) == 1:
        return {node.path.names[0]}
    if isinstance(node, And):
        return _extract_has_tags(node.left) | _extract_has_tags(node.right)
    return set()


def _build_ft_query(node: Node) -> str | None:
    """Try to build a RediSearch query from a filter AST.

    Supports:

    - Has/Missing on single-segment tag paths
    - Cmp ``==`` on Ref-valued indexed fields (siteRef, equipRef)
    - And/Or combinations of the above

    Returns ``None`` if the filter contains unsupported nodes (multi-segment
    paths, non-EQ comparisons, unindexed fields).
    """
    if isinstance(node, Has) and len(node.path.names) == 1:
        tag = node.path.names[0]
        return f"@_tags:{{{_ft_escape(tag)}}}"
    if isinstance(node, Missing) and len(node.path.names) == 1:
        tag = node.path.names[0]
        return f"-@_tags:{{{_ft_escape(tag)}}}"
    if isinstance(node, Cmp) and node.op == CmpOp.EQ and len(node.path.names) == 1:
        field = node.path.names[0]
        if field in _FT_REF_FIELDS:
            if isinstance(node.val, Ref):
                return f"@{field}:{{{_ft_escape(node.val.val)}}}"
            if isinstance(node.val, str):
                return f"@{field}:{{{_ft_escape(node.val)}}}"
    if isinstance(node, And):
        left = _build_ft_query(node.left)
        right = _build_ft_query(node.right)
        if left is not None and right is not None:
            return f"({left} {right})"
        return None
    if isinstance(node, Or):
        left = _build_ft_query(node.left)
        right = _build_ft_query(node.right)
        if left is not None and right is not None:
            return f"({left})|({right})"
        return None
    return None


# Characters that need escaping in RediSearch TAG values
_FT_SPECIAL = frozenset(r",.<>{}[]\"':;!@#$%^&*()-+=~ ")


def _ft_escape(tag: str) -> str:
    """Escape special characters in a RediSearch TAG value."""
    return "".join(f"\\{c}" if c in _FT_SPECIAL else c for c in tag)


def _parse_ft_fields(info: dict[str, Any]) -> set[str]:
    """Extract indexed field attribute names from an ``ft().info()`` response.

    With RESP3, ``attributes`` is a list of dicts each containing an
    ``'attribute'`` key.
    """
    fields: set[str] = set()
    for attr in info.get("attributes", []):
        if isinstance(attr, dict):
            name = attr.get("attribute")
            if isinstance(name, str):
                fields.add(name)
    return fields


def create_redis_client(
    url: str = "redis://localhost:6379",
    *,
    tls: TLSConfig | None = None,
    max_connections: int = _MAX_CONNECTIONS,
) -> Redis[str]:
    """Create an async Redis client with optional TLS 1.3.

    Uses RESP3 protocol, automatic string decoding, connection health checks,
    and jittered exponential-backoff retries.  When *tls* is provided, the
    connection enforces TLS 1.3 minimum, loads the configured client
    certificate for mutual authentication, and verifies the server
    certificate against the configured CA.

    :param url: Redis connection URL (``redis://`` or ``rediss://``).
    :param tls: Optional TLS configuration for encrypted connections.
    :param max_connections: Maximum connections in the pool (default 50).
    :returns: An async ``redis.asyncio.Redis`` client.

    Example::

        from hs_py.tls import TLSConfig
        from hs_py.storage.redis import RedisAdapter, create_redis_client

        tls = TLSConfig(
            certificate_path="client.pem",
            private_key_path="client.key",
            ca_certificates_path="ca.pem",
        )
        redis = create_redis_client("rediss://redis:6379", tls=tls)
        adapter = RedisAdapter(redis)
    """
    import ssl as _ssl

    from redis.asyncio import Redis
    from redis.backoff import EqualJitterBackoff
    from redis.exceptions import BusyLoadingError
    from redis.retry import Retry

    retry = Retry(EqualJitterBackoff(), _RETRY_ATTEMPTS)

    common: dict[str, Any] = {
        "protocol": 3,
        "decode_responses": True,
        "max_connections": max_connections,
        "socket_timeout": _SOCKET_TIMEOUT,
        "socket_connect_timeout": _SOCKET_CONNECT_TIMEOUT,
        "socket_keepalive": True,
        "health_check_interval": _HEALTH_CHECK_INTERVAL,
        "retry": retry,
        "retry_on_timeout": True,
        "retry_on_error": [BusyLoadingError],
    }

    if tls is None:
        return Redis.from_url(url, **common)

    # Ensure rediss:// scheme for TLS connections
    tls_url = url.replace("redis://", "rediss://") if url.startswith("redis://") else url

    return Redis.from_url(  # type: ignore[call-overload,no-any-return]
        tls_url,
        **common,
        ssl_certfile=tls.certificate_path,
        ssl_keyfile=tls.private_key_path,
        ssl_ca_certs=tls.ca_certificates_path,
        ssl_password=tls.key_password,
        ssl_min_version=_ssl.TLSVersion.TLSv1_3,
        ssl_cert_reqs="required",
        ssl_check_hostname=True,
    )


class RedisAdapter:
    """StorageAdapter backed by Redis 8 (JSON + TimeSeries + Search).

    Implements :class:`~hs_py.storage.protocol.StorageAdapter` using
    RedisJSON for entity storage, RedisTimeSeries for history data, and
    RediSearch for efficient filter queries.

    :param redis: A ``redis.asyncio.Redis`` client instance created with
        ``protocol=3`` and ``decode_responses=True``.
    """

    def __init__(self, redis: Redis[str]) -> None:
        self._r = redis
        self._read_cache: dict[tuple[str, int | None], list[dict[str, Any]]] = {}
        self._read_cache_max = 64
        self._all_col_names: tuple[str, ...] | None = None

    # ---- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Verify Redis connection and create RediSearch index."""
        await self._r.ping()
        _log.info("RedisAdapter connected to Redis")
        await self._ensure_search_index()

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._r.aclose()  # type: ignore[attr-defined]
        _log.info("RedisAdapter disconnected from Redis")

    # ---- Search index --------------------------------------------------------

    async def _ensure_search_index(self) -> None:
        """Create or rebuild the RediSearch index with the expected schema.

        If the index exists but is missing expected fields (e.g. after a
        schema upgrade), it is dropped and recreated so RediSearch re-indexes
        all existing JSON documents.
        """
        from redis.commands.search.field import TagField
        from redis.commands.search.index_definition import IndexDefinition, IndexType
        from redis.exceptions import ResponseError

        ft = self._r.ft(_FT_INDEX)

        try:
            info = await ft.info()  # type: ignore[no-untyped-call]
            existing = _parse_ft_fields(info)
            if existing >= _FT_EXPECTED_FIELDS:
                _log.info("RediSearch index '%s' schema is current", _FT_INDEX)
                return
            _log.info("RediSearch index '%s' schema outdated, rebuilding", _FT_INDEX)
            await ft.dropindex()
        except ResponseError:
            pass  # Index does not exist yet

        schema = (
            TagField("$._tags", as_name="_tags", separator=","),
            TagField("$.siteRef.val", as_name="siteRef"),
            TagField("$.equipRef.val", as_name="equipRef"),
        )
        definition = IndexDefinition(  # type: ignore[no-untyped-call]
            prefix=[_E], index_type=IndexType.JSON
        )
        await ft.create_index(schema, definition=definition)
        _log.info("Created RediSearch index '%s'", _FT_INDEX)

    # ---- Internal helpers ----------------------------------------------------

    async def _store_entity(self, ref_val: str, entity: dict[str, Any]) -> None:
        """Store a single entity with tag indexes.

        When updating an existing entity, stale tag index entries are removed.
        """
        old_entity = await self._load_entity(ref_val)
        old_tags = set(old_entity) if old_entity else set()
        new_tags = set(entity)
        removed_tags = old_tags - new_tags

        encoded = _encode_entity(entity)
        pipe = self._r.pipeline()
        pipe.json().set(_entity_key(ref_val), "$", encoded)
        pipe.sadd(_IDS, ref_val)
        for tag in new_tags:
            pipe.sadd(_tag_key(tag), ref_val)
        for tag in removed_tags:
            pipe.srem(_tag_key(tag), ref_val)
        await pipe.execute()

    async def _load_entity(self, ref_val: str) -> dict[str, Any] | None:
        """Load a single entity by ref val."""
        raw: dict[str, Any] | None = await self._r.json().get(_entity_key(ref_val))
        if raw is None:
            return None
        return _decode_entity(raw)

    async def _load_entities(self, ref_vals: list[str]) -> list[dict[str, Any] | None]:
        """Load multiple entities by ref val via ``json().mget()``."""
        if not ref_vals:
            return []
        keys = [_entity_key(rv) for rv in ref_vals]
        # json().mget with "$" returns [[doc], [doc], None, ...] per key
        results: list[Any] = await self._r.json().mget(keys, "$")  # type: ignore[no-untyped-call]
        out: list[dict[str, Any] | None] = []
        for r in results:
            if r is None:
                out.append(None)
            elif isinstance(r, list) and r:
                out.append(_decode_entity(r[0]))
            else:
                out.append(None)
        return out

    async def _ft_search(self, query_str: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Execute a RediSearch query and return decoded entities.

        Returns document content inline from FT.SEARCH (no separate load step).
        """
        from redis.commands.search.query import Query

        max_results = min(limit, _MAX_FT_RESULTS) if limit is not None else _MAX_FT_RESULTS
        q = Query(query_str).paging(0, max_results)  # type: ignore[no-untyped-call]
        result: Any = await self._r.ft(_FT_INDEX).search(q)  # type: ignore[misc]

        # RESP3 returns a dict with inline document content
        total: int = result.get("total_results", 0)
        if not total:
            return []

        rows: list[dict[str, Any]] = []
        for doc in result["results"]:
            attrs = doc.get("extra_attributes", {})
            raw = attrs.get("$", None)
            if raw is not None:
                if isinstance(raw, str):
                    raw = orjson.loads(raw)
                rows.append(_decode_entity(raw))
            else:
                # Fallback: load by ID if content not inline
                ref_val = doc["id"][len(_E) :]
                entity = await self._load_entity(ref_val)
                if entity is not None:
                    rows.append(entity)
        return rows

    # ---- StorageAdapter methods ----------------------------------------------

    @property
    def all_col_names(self) -> tuple[str, ...] | None:
        """Cached column names across all entities, or ``None`` if unknown."""
        return self._all_col_names

    async def read_by_filter(
        self,
        ast: Node,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return entities matching a filter AST.

        Attempts to delegate fully to RediSearch; falls back to Python
        evaluation with tag-index candidate narrowing for unsupported filter
        constructs.

        :param ast: Compiled filter AST from :func:`~hs_py.filter.parse`.
        :param limit: Maximum number of results to return.  ``None`` means
            no limit.
        :returns: List of matching entity dicts.
        """
        # Try to fully delegate to RediSearch
        ft_query = _build_ft_query(ast)
        if ft_query is not None:
            cache_key = (ft_query, limit)
            cached = self._read_cache.get(cache_key)
            if cached is not None:
                return cached
            results = await self._ft_search(ft_query, limit)
            if len(self._read_cache) < self._read_cache_max:
                self._read_cache[cache_key] = results
            return results

        # Fallback: use tag index Sets for candidate narrowing, then Python eval
        has_tags = _extract_has_tags(ast)

        if has_tags:
            tag_keys = [_tag_key(t) for t in has_tags]
            if len(tag_keys) == 1:
                candidate_ids: list[str] = [str(v) for v in await self._r.smembers(tag_keys[0])]
            else:
                candidate_ids = [str(v) for v in await self._r.sinter(*tag_keys)]
        else:
            # No tag narrowing — cap full scan to prevent loading entire dataset
            all_ids = await self._r.srandmember(_IDS, _MAX_FALLBACK_SCAN)
            candidate_ids = [str(v) for v in (all_ids or [])]

        if not candidate_ids:
            return []

        entities = await self._load_entities(candidate_ids)

        # Build a resolver from loaded entities for multi-segment paths
        entity_map: dict[str, dict[str, Any]] = {}
        for ref_val, entity in zip(candidate_ids, entities, strict=True):
            if entity is not None:
                entity_map[ref_val] = entity

        def resolver(ref: Ref) -> dict[str, Any] | None:
            return entity_map.get(ref.val)

        rows: list[dict[str, Any]] = []
        for entity in entity_map.values():
            if evaluate(ast, entity, resolver):
                rows.append(entity)
                if limit is not None and len(rows) >= limit:
                    break

        return rows

    async def read_by_ids(self, ids: list[Ref]) -> list[dict[str, Any] | None]:
        """Return entities for a list of Refs, preserving order.

        :param ids: Ordered list of entity Refs to fetch.
        :returns: List the same length as *ids*.  Each entry is the entity
            dict if found, or ``None`` if the Ref does not exist.
        """
        ref_vals = [ref.val for ref in ids]
        return await self._load_entities(ref_vals)

    async def nav(self, nav_id: str | None = None) -> list[dict[str, Any]]:
        """Navigate the site/equip/point hierarchy.

        Uses RediSearch indexed ``siteRef`` and ``equipRef`` fields for
        efficient lookups instead of loading all entities into memory.

        :param nav_id: The ``Ref.val`` of the entity whose children should be
            returned.  Pass ``None`` to get root-level sites.
        :returns: List of child entity dicts.
        """
        if nav_id is None:
            # Root: return sites via RediSearch
            return await self._ft_search("@_tags:{site}")

        # Load the target entity to determine its type
        target = await self._load_entity(nav_id)
        if target is None:
            return []

        escaped_id = _ft_escape(nav_id)
        if "site" in target:
            # Site -> equips with matching siteRef via RediSearch
            return await self._ft_search(f"@_tags:{{equip}} @siteRef:{{{escaped_id}}}")
        if "equip" in target:
            # Equip -> points with matching equipRef via RediSearch
            return await self._ft_search(f"@_tags:{{point}} @equipRef:{{{escaped_id}}}")
        return []

    async def his_read(
        self,
        ref: Ref,
        range_str: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return time-series history for a point.

        :param ref: Entity Ref of the point.
        :param range_str: Optional range string (currently ignored; all data
            is returned).
        :returns: List of dicts with ``"ts"`` (datetime) and ``"val"`` keys.
        """
        from redis.exceptions import ResponseError

        ts_key = _ts_key(ref.val)

        try:
            samples: list[tuple[int, float]] = await self._r.ts().range(ts_key, "-", "+")
        except ResponseError:
            return []

        # Look up the entity to get the unit
        entity = await self._load_entity(ref.val)
        unit = entity.get("unit") if entity is not None else None

        rows: list[dict[str, Any]] = []
        for ts_ms, val_float in samples:
            dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.UTC)
            val = Number(val_float, unit) if unit else Number(val_float)
            rows.append({"ts": dt, "val": val})
        return rows

    async def his_write(self, ref: Ref, items: list[dict[str, Any]]) -> None:
        """Append time-series data for a point.

        :param ref: Entity Ref of the point.
        :param items: List of dicts with ``"ts"`` and ``"val"`` keys.
        """
        from redis.exceptions import ResponseError

        ts_key = _ts_key(ref.val)

        # Ensure the TS key exists with labels and duplicate policy.
        # Use try/except to avoid TOCTOU race with concurrent writers.
        entity = await self._load_entity(ref.val)
        ts_labels: dict[str, str] = {"entity": ref.val}
        if entity is not None:
            unit = entity.get("unit")
            if isinstance(unit, str):
                ts_labels["unit"] = unit
        with contextlib.suppress(ResponseError):
            await self._r.ts().create(ts_key, duplicate_policy="last", labels=ts_labels)

        pipe = self._r.pipeline()
        for row in items:
            val = row.get("val")
            ts = row.get("ts")

            # Extract numeric value
            if isinstance(val, Number):
                float_val = val.val
            elif isinstance(val, (int, float)):
                float_val = float(val)
            else:
                continue

            # Convert timestamp: datetime → ms epoch, int/str passthrough
            ts_arg: int | str
            if isinstance(ts, datetime.datetime):
                ts_arg = int(ts.timestamp() * 1000)
            elif isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
                ts_arg = int(ts)
            else:
                ts_arg = "*"

            pipe.ts().add(ts_key, ts_arg, float_val)

        await pipe.execute()

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
        :param who: Optional identifier of who is writing (ignored).
        :param duration: Optional duration override (ignored).
        """
        pri_key = _pri_key(ref.val)
        if val is None:
            await self._r.hdel(pri_key, str(level))
        else:
            encoded = orjson.dumps(encode_val(val))
            await self._r.hset(pri_key, str(level), encoded)

    async def point_read_array(self, ref: Ref) -> list[dict[str, Any]]:
        """Return the 17-level priority array for a writable point.

        :param ref: Entity Ref of the writable point.
        :returns: List of 17 dicts, each with a ``"level"`` key and an
            optional ``"val"`` key (absent when the level is unset).
        """
        pri_key = _pri_key(ref.val)
        raw = await self._r.hgetall(pri_key)
        rows: list[dict[str, Any]] = []
        for level in range(1, 18):
            row: dict[str, Any] = {"level": Number(float(level)), "val": None}
            level_str = str(level)
            if level_str in raw:
                val_json = orjson.loads(raw[level_str])
                row["val"] = _decode_val_v4(val_json)
            rows.append(row)
        return rows

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
        if not watch_id or not await self._r.exists(_watch_key(watch_id)):
            watch_id = f"w-{secrets.token_hex(4)}"
            await self._r.hset(
                _watch_key(watch_id),
                mapping={"dis": str(dis), "lease": "300"},
            )

        # Collect entity state for subscribed IDs
        ids_key = _watch_ids_key(watch_id)
        ref_vals: list[str] = [ref.val for ref in ids]

        if ref_vals:
            await self._r.sadd(ids_key, *ref_vals)

        # Load current state of watched entities
        entities = await self._load_entities(ref_vals)
        return watch_id, [e for e in entities if e is not None]

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
        :raises ValueError: If *watch_id* is not found.
        """
        watch_key = _watch_key(watch_id)
        if not await self._r.exists(watch_key):
            msg = f"Unknown watch: {watch_id}"
            raise ValueError(msg)

        if close:
            pipe = self._r.pipeline()
            pipe.delete(watch_key)
            pipe.delete(_watch_ids_key(watch_id))
            pipe.delete(_watch_dirty_key(watch_id))
            await pipe.execute()
            return

        # Remove specific IDs
        ids_key = _watch_ids_key(watch_id)
        dirty_key = _watch_dirty_key(watch_id)
        ref_vals = [ref.val for ref in ids]

        if ref_vals:
            pipe = self._r.pipeline()
            pipe.srem(ids_key, *ref_vals)
            pipe.srem(dirty_key, *ref_vals)
            await pipe.execute()

    async def watch_poll(
        self,
        watch_id: str,
        *,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Poll for changed entities.

        :param watch_id: Watch to poll.
        :param refresh: If ``True``, return all watched entities (full
            refresh) regardless of dirty state.
        :returns: List of entity dicts that have changed since the last poll
            (or all entities if *refresh* is ``True``).
        :raises ValueError: If *watch_id* is not found.
        """
        watch_key = _watch_key(watch_id)
        if not await self._r.exists(watch_key):
            msg = f"Unknown watch: {watch_id}"
            raise ValueError(msg)

        ids_key = _watch_ids_key(watch_id)
        dirty_key = _watch_dirty_key(watch_id)

        if refresh:
            # Atomically get watched IDs and clear dirty set
            pipe = self._r.pipeline()
            pipe.smembers(ids_key)
            pipe.delete(dirty_key)
            results = await pipe.execute()
            ref_vals: list[str] = [str(v) for v in results[0]]
        else:
            # Atomically read dirty + watched sets and clear dirty
            pipe = self._r.pipeline()
            pipe.smembers(dirty_key)
            pipe.smembers(ids_key)
            pipe.delete(dirty_key)
            results = await pipe.execute()
            dirty_members = results[0]
            watched_members = results[1]
            watched_set = {str(v) for v in watched_members}
            ref_vals = [str(v) for v in dirty_members if str(v) in watched_set]

        if not ref_vals:
            return []

        entities = await self._load_entities(ref_vals)
        return [e for e in entities if e is not None]

    # ---- Non-protocol helpers ------------------------------------------------

    async def load_entities(self, entities: list[dict[str, Any]]) -> int:
        """Bulk-load a list of entity dicts into Redis.

        Each entity must have an ``id`` :class:`~hs_py.kinds.Ref`.  Entities
        without an ``id`` are silently skipped.  Large batches are chunked
        to avoid unbounded pipeline memory usage.

        :param entities: List of entity dicts to load.
        :returns: Number of entities actually stored.
        """
        count = 0
        skipped = 0
        cmds_in_pipe = 0
        pipe = self._r.pipeline()
        for entity in entities:
            ref = entity.get("id")
            if not isinstance(ref, Ref):
                skipped += 1
                continue
            encoded = _encode_entity(entity)

            pipe.json().set(_entity_key(ref.val), "$", encoded)
            pipe.sadd(_IDS, ref.val)
            for tag in entity:
                pipe.sadd(_tag_key(tag), ref.val)
            count += 1
            cmds_in_pipe += 2 + len(entity)

            if cmds_in_pipe >= _PIPELINE_BATCH_SIZE:
                await pipe.execute()
                pipe = self._r.pipeline()
                cmds_in_pipe = 0

        if cmds_in_pipe:
            await pipe.execute()
        if skipped:
            _log.warning("Skipped %d rows without 'id' Ref during load", skipped)
        _log.info("Loaded %d entities into Redis", count)
        # Compute column names for Grid construction fast path.
        seen: dict[str, None] = {}
        for entity in entities:
            for key in entity:
                if key not in seen:
                    seen[key] = None
        self._all_col_names = tuple(seen)
        return count

    # ---- UserStore implementation --------------------------------------------

    def _user_key(self, username: str) -> str:
        """Return the Redis key for a user."""
        if not _SAFE_KEY_RE.match(username):
            msg = f"Invalid username for Redis key: {username!r}"
            raise ValueError(msg)
        return f"{_USER}{username}"

    async def get_user(self, username: str) -> User | None:
        """Return a user by username, or ``None`` if not found."""
        data = await self._r.json().get(self._user_key(username))
        if data is None:
            return None
        return user_from_dict(data)

    async def list_users(self) -> list[User]:
        """Return all users."""
        keys: list[str] = []
        async for key in self._r.scan_iter(match=f"{_USER}*", count=1000):
            keys.append(str(key))
        if not keys:
            return []
        users: list[User] = []
        for key in keys:
            data = await self._r.json().get(key)
            if data is not None:
                users.append(user_from_dict(data))
        return users

    async def create_user(self, user: User) -> None:
        """Persist a new user.

        :raises ValueError: If a user with the same username already exists.
        """
        key = self._user_key(user.username)
        existing = await self._r.json().get(key)
        if existing is not None:
            msg = f"User already exists: {user.username!r}"
            raise ValueError(msg)
        await self._r.json().set(key, "$", user_to_dict(user))

    async def update_user(self, username: str, **fields: Any) -> User:
        """Update fields on an existing user.

        :raises KeyError: If the user does not exist.
        """
        import time

        key = self._user_key(username)
        data = await self._r.json().get(key)
        if data is None:
            msg = f"User not found: {username!r}"
            raise KeyError(msg)

        existing = user_from_dict(data)
        updates: dict[str, Any] = {"updated_at": time.time()}
        if "password" in fields:
            updates["credentials"] = derive_scram_credentials(fields.pop("password"))

        allowed = {"first_name", "last_name", "email", "role", "enabled", "credentials"}
        for k, v in fields.items():
            if k in allowed:
                updates[k] = v

        from dataclasses import asdict

        merged = {**asdict(existing), **updates}
        merged["credentials"] = updates.get("credentials", existing.credentials)
        # asdict() converts Role enum to its value — restore the enum instance
        if isinstance(merged.get("role"), str):
            from hs_py.user import Role

            merged["role"] = Role(merged["role"])
        new_user = User(**merged)
        await self._r.json().set(key, "$", user_to_dict(new_user))
        return new_user

    async def delete_user(self, username: str) -> bool:
        """Delete a user by username."""
        key = self._user_key(username)
        return bool(await self._r.delete(key))
