"""Watch state tracking with delta encoding and server-side filtering.

Provides :class:`WatchState` for server-side delta computation and
:class:`WatchAccumulator` for client-side delta merging.  Both classes
track entity state per watch subscription to minimise push payload size.

Server-side filtering evaluates a Haystack filter expression against
entities before pushing, so clients only receive matching changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hs_py.grid import Grid
from hs_py.kinds import MARKER, REMOVE, Ref

if TYPE_CHECKING:
    from hs_py.filter.ast import Node

__all__ = [
    "WatchAccumulator",
    "WatchState",
]


class WatchState:
    """Server-side entity state tracker for a single watch.

    Maintains a cache of the last-sent entity state so that
    :meth:`compute_delta` can emit only changed, new, or removed
    tags on each push cycle.

    :param watch_id: The watch identifier.
    :param filter_ast: Optional parsed filter AST for server-side filtering.
    """

    def __init__(self, watch_id: str, *, filter_ast: Node | None = None) -> None:
        """Initialise watch state.

        :param watch_id: The watch identifier.
        :param filter_ast: Optional parsed filter AST for server-side filtering.
        """
        self.watch_id = watch_id
        self.filter_ast = filter_ast
        self._cache: dict[str, dict[str, Any]] = {}

    def compute_delta(self, current: Grid) -> Grid:
        """Compute a delta grid from the current full state.

        - New entities (not in cache) are included in full.
        - Changed tags are included with their new values.
        - Removed tags are represented as :data:`~hs_py.kinds.REMOVE`.
        - Entities in the cache but absent from *current* get an
          ``_removed`` marker row.

        :param current: Full current state of watched entities.
        :returns: Grid with only the changes since last push.
        """
        current_ids: set[str] = set()
        delta_rows: list[dict[str, Any]] = []

        for row in current:
            ref = row.get("id")
            if not isinstance(ref, Ref):
                continue
            entity_id = ref.val
            current_ids.add(entity_id)
            cached = self._cache.get(entity_id)

            if cached is None:
                # New entity — send in full
                delta_rows.append(dict(row))
            else:
                # Compute tag diff
                diff: dict[str, Any] = {"id": ref}
                for key, val in row.items():
                    if key == "id":
                        continue
                    if key not in cached or cached[key] != val:
                        diff[key] = val
                for key in cached:
                    if key != "id" and key not in row:
                        diff[key] = REMOVE
                if len(diff) > 1:  # More than just "id"
                    delta_rows.append(diff)

        # Entities removed from the watch
        for entity_id in list(self._cache):
            if entity_id not in current_ids:
                delta_rows.append({"id": Ref(entity_id), "_removed": MARKER})

        if not delta_rows:
            return Grid.make_empty()
        return Grid.make_rows(delta_rows)

    def update(self, current: Grid) -> None:
        """Update the cache with the current full entity state.

        Call this after :meth:`compute_delta` to keep the cache in sync.

        :param current: Full current state of watched entities.
        """
        current_ids: set[str] = set()
        for row in current:
            ref = row.get("id")
            if isinstance(ref, Ref):
                current_ids.add(ref.val)
                self._cache[ref.val] = dict(row)
        # Remove entities no longer in the current set
        for entity_id in list(self._cache):
            if entity_id not in current_ids:
                del self._cache[entity_id]

    def apply_filter(self, grid: Grid) -> Grid:
        """Filter a grid using the watch's filter expression.

        If no filter is configured, returns the grid unchanged.

        :param grid: Grid of entities to filter.
        :returns: Filtered grid with only matching rows.
        """
        if self.filter_ast is None:
            return grid
        from hs_py.filter.eval import evaluate

        matching = [row for row in grid if evaluate(self.filter_ast, row)]
        if not matching:
            return Grid.make_empty()
        return Grid.make_rows(matching)


class WatchAccumulator:
    """Client-side state accumulator for delta watch pushes.

    Merges incoming delta grids into a full entity state cache.
    """

    def __init__(self) -> None:
        """Initialise an empty accumulator."""
        self._entities: dict[str, dict[str, Any]] = {}

    def apply_delta(self, delta: Grid) -> None:
        """Merge a delta grid into the accumulated state.

        - New entities are added.
        - Changed tags are updated.
        - Tags with :data:`~hs_py.kinds.REMOVE` value are deleted.
        - Rows with ``_removed`` marker are removed entirely.

        :param delta: Delta grid from the server.
        """
        for row in delta:
            ref = row.get("id")
            if not isinstance(ref, Ref):
                continue
            entity_id = ref.val

            if row.get("_removed") is MARKER:
                self._entities.pop(entity_id, None)
                continue

            entity = self._entities.get(entity_id)
            if entity is None:
                entity = {}
                self._entities[entity_id] = entity

            for key, val in row.items():
                if val is REMOVE:
                    entity.pop(key, None)
                else:
                    entity[key] = val

    @property
    def entities(self) -> dict[str, dict[str, Any]]:
        """Return the current accumulated entity state."""
        return self._entities

    def to_grid(self) -> Grid:
        """Return the accumulated state as a :class:`~hs_py.grid.Grid`."""
        if not self._entities:
            return Grid.make_empty()
        return Grid.make_rows(list(self._entities.values()))

    def get(self, entity_id: str) -> dict[str, Any] | None:
        """Look up a single entity by ID.

        :param entity_id: Entity identifier string.
        :returns: Tag dict, or ``None`` if not present.
        """
        return self._entities.get(entity_id)
