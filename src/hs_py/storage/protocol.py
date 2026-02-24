"""StorageAdapter Protocol for Haystack server backends.

Defines the async interface that all storage backends must implement.
Concrete implementations include :class:`~hs_py.storage.memory.InMemoryAdapter`
and :class:`~hs_py.redis_ops.RedisOps`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from hs_py.filter.ast import Node
    from hs_py.kinds import Ref

__all__ = ["StorageAdapter"]


@runtime_checkable
class StorageAdapter(Protocol):
    """Protocol for Haystack server storage backends.

    All methods are async. Concrete backends must implement every method.
    The ``start()`` / ``close()`` methods bracket the lifetime of the adapter
    and are called by the server on startup and shutdown respectively.

    Entity dicts use native Haystack kinds (``Ref``, ``Marker``, ``Number``,
    etc.) — not wire-encoded JSON dicts.  Callers are responsible for encoding
    before sending to clients.
    """

    async def read_by_filter(
        self,
        ast: Node,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return entities matching a filter AST.

        :param ast: Compiled filter AST from :func:`~hs_py.filter.parse`.
        :param limit: Maximum number of results to return.  ``None`` means
            no limit.
        :returns: List of entity dicts (order unspecified).
        """
        ...

    async def read_by_ids(self, ids: list[Ref]) -> list[dict[str, Any] | None]:
        """Return entities for a list of Refs, preserving order.

        :param ids: Ordered list of entity Refs to fetch.
        :returns: List the same length as *ids*.  Each entry is the entity
            dict if found, or ``None`` if the Ref does not exist.
        """
        ...

    async def nav(self, nav_id: str | None = None) -> list[dict[str, Any]]:
        """Navigate the site/equip/point hierarchy.

        :param nav_id: The ``Ref.val`` of the entity whose children should be
            returned.  Pass ``None`` to get root-level sites.
        :returns: List of child entity dicts.
        """
        ...

    async def his_read(
        self,
        ref: Ref,
        range_str: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return time-series history for a point.

        :param ref: Entity Ref of the point.
        :param range_str: Optional range string (e.g. ``"today"``,
            ``"2024-01-01,2024-01-31"``).  If ``None``, all data is returned.
        :returns: List of dicts, each with ``"ts"`` (datetime) and ``"val"``
            keys.
        """
        ...

    async def his_write(self, ref: Ref, items: list[dict[str, Any]]) -> None:
        """Append time-series data for a point.

        :param ref: Entity Ref of the point.
        :param items: List of dicts with ``"ts"`` (datetime) and ``"val"``
            keys.
        """
        ...

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
        :param level: Priority level (1-17).  Level 17 is the default.
        :param val: Value to write.  Pass ``None`` to clear the level.
        :param who: Optional identifier of who is writing.
        :param duration: Optional duration override (ignored by most backends).
        """
        ...

    async def point_read_array(self, ref: Ref) -> list[dict[str, Any]]:
        """Return the 17-level priority array for a writable point.

        :param ref: Entity Ref of the writable point.
        :returns: List of 17 dicts, each with a ``"level"`` key and an
            optional ``"val"`` key (absent when the level is unset).
        """
        ...

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
        ...

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
        ...

    async def watch_poll(
        self,
        watch_id: str,
        *,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Poll a watch for changed entities.

        :param watch_id: Watch to poll.
        :param refresh: If ``True``, return all watched entities (full
            refresh) regardless of dirty state.
        :returns: List of entity dicts that have changed since the last poll
            (or all entities if *refresh* is ``True``).  The dirty set is
            cleared after each poll.
        """
        ...

    async def start(self) -> None:
        """Initialize the backend.

        Called once before the server begins serving requests.  May open
        connections, create indexes, warm caches, etc.
        """
        ...

    async def close(self) -> None:
        """Tear down the backend.

        Called when the server shuts down.  Must release all resources
        (connections, file handles, etc.).
        """
        ...
