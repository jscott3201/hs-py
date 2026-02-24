"""Backward-compatible Redis ops wrapper.

Provides :class:`RedisOps`, a thin :class:`~hs_py.ops.HaystackOps` subclass
that wraps :class:`~hs_py.storage.redis.RedisAdapter`.

For new code, prefer using :class:`~hs_py.storage.redis.RedisAdapter` directly
with :class:`~hs_py.ops.HaystackOps`::

    from hs_py.ops import HaystackOps
    from hs_py.storage.redis import RedisAdapter, create_redis_client

    redis = create_redis_client()
    adapter = RedisAdapter(redis)
    ops = HaystackOps(storage=adapter)
    await adapter.start()
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hs_py.grid import Grid
from hs_py.kinds import Symbol
from hs_py.ops import HaystackOps
from hs_py.storage.redis import RedisAdapter, create_redis_client

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from hs_py.ontology.namespace import Namespace

__all__ = ["RedisOps", "create_redis_client"]


class RedisOps(HaystackOps):
    """Haystack ops backed by Redis 8, wrapping :class:`~hs_py.storage.redis.RedisAdapter`.

    This is a convenience wrapper for backward compatibility.  For new code,
    use ``RedisAdapter`` directly with ``HaystackOps(storage=adapter)``.

    :param redis: A ``redis.asyncio.Redis`` client instance.
    :param namespace: Optional ontology namespace for defs/libs ops.
    """

    def __init__(
        self,
        redis: Redis[str],
        *,
        namespace: Namespace | None = None,
    ) -> None:
        adapter = RedisAdapter(redis)
        super().__init__(storage=adapter, namespace=namespace)
        self._adapter = adapter

    # -- Convenience property proxies ------------------------------------------

    @property
    def _r(self) -> Redis[str]:
        """Direct Redis client access (for testing and internal helpers)."""
        return self._adapter._r

    # -- Lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        """Verify Redis connection and create RediSearch index."""
        await self._adapter.start()

    async def stop(self) -> None:
        """Close the Redis connection."""
        await self._adapter.close()

    # -- Internal helpers (delegated to adapter) ------------------------------

    async def _store_entity(self, ref_val: str, entity: dict[str, Any]) -> None:
        """Store a single entity with tag indexes (delegates to adapter)."""
        await self._adapter._store_entity(ref_val, entity)

    # -- Bulk load -------------------------------------------------------------

    async def load_entities(self, entities: list[dict[str, Any]]) -> int:
        """Bulk-load a list of entity dicts into Redis.

        :param entities: List of entity dicts (each must have an ``id`` Ref).
        :returns: Number of entities actually stored.
        """
        return await self._adapter.load_entities(entities)

    async def load_grid(self, grid: Grid) -> int:
        """Bulk-load entities from a Grid into Redis.

        :param grid: Grid of entities (each row must have an ``id`` Ref).
        :returns: Number of entities loaded.
        """
        entities = [dict(row) for row in grid]
        return await self._adapter.load_entities(entities)

    # -- Standard ops overrides ------------------------------------------------

    async def about(self) -> Grid:
        """Return server information."""
        return Grid.make_rows(
            [
                {
                    "haystackVersion": "4.0",
                    "tz": "New_York",
                    "serverName": "hs-py Redis Server",
                    "productName": "hs-py",
                    "productVersion": "0.3.0",
                }
            ]
        )

    async def filetypes(self, grid: Grid) -> Grid:
        """Return supported file types."""
        return Grid.make_rows(
            [
                {"def": Symbol("filetype:json"), "dis": "JSON", "mime": "application/json"},
                {"def": Symbol("filetype:zinc"), "dis": "Zinc", "mime": "text/zinc"},
                {"def": Symbol("filetype:trio"), "dis": "Trio", "mime": "text/trio"},
                {"def": Symbol("filetype:csv"), "dis": "CSV", "mime": "text/csv"},
            ]
        )

    async def invoke_action(self, grid: Grid) -> Grid:
        """Invoke an action on an entity."""
        action = grid.meta.get("action", "unknown")
        return Grid.make_rows([{"action": str(action), "result": "ok"}])
