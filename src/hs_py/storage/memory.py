"""In-memory Haystack storage adapter.

Provides :class:`InMemoryAdapter`, a pure-Python implementation of
:class:`~hs_py.storage.protocol.StorageAdapter` backed by plain dicts.
Suitable for testing, demos, and small deployments that do not require
persistence.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hs_py.filter import evaluate
from hs_py.filter.ast import Has
from hs_py.kinds import Number, Ref
from hs_py.user import User, derive_scram_credentials

if TYPE_CHECKING:
    from hs_py.filter.ast import Node

__all__ = ["InMemoryAdapter"]


# ---------------------------------------------------------------------------
# Internal watch state
# ---------------------------------------------------------------------------


@dataclass
class _WatchState:
    """Mutable state for a single active watch subscription."""

    dis: str
    ids: set[str] = field(default_factory=set)
    dirty: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# InMemoryAdapter
# ---------------------------------------------------------------------------


class InMemoryAdapter:
    """In-memory implementation of :class:`~hs_py.storage.protocol.StorageAdapter`.

    All state is held in plain Python dicts; nothing is persisted to disk.
    Thread-safety is not guaranteed — use within a single asyncio event loop.

    :param entities: Optional initial list of entity dicts (each must have an
        ``id`` :class:`~hs_py.kinds.Ref`).
    """

    def __init__(self, entities: list[dict[str, Any]] | None = None) -> None:
        # ref_val -> entity dict
        self._entities: dict[str, dict[str, Any]] = {}
        # tag_name -> set of ref_vals (tag presence index for fast Has queries)
        self._tag_index: dict[str, set[str]] = {}
        # ref_val -> list of {ts, val} dicts
        self._timeseries: dict[str, list[dict[str, Any]]] = {}
        # ref_val -> {level (1-17) -> val}
        self._priority: dict[str, dict[int, Any]] = {}
        # watch_id -> _WatchState
        self._watches: dict[str, _WatchState] = {}
        # username -> User
        self._users: dict[str, User] = {}
        # Cached column names across all entities (invalidated on load)
        self._all_col_names: tuple[str, ...] | None = None

        if entities:
            self.load_entities(entities)

    # ---- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """No-op initializer (in-memory adapter needs no setup)."""

    async def close(self) -> None:
        """No-op teardown (no resources to release)."""

    # ---- Bulk load -----------------------------------------------------------

    def load_entities(self, entities: list[dict[str, Any]]) -> int:
        """Bulk-load a list of entity dicts.

        Each entity must have an ``id`` :class:`~hs_py.kinds.Ref`.  Entities
        without an ``id`` are silently skipped.

        :param entities: List of entity dicts to load.
        :returns: Number of entities actually stored.
        """
        count = 0
        tag_index = self._tag_index
        for entity in entities:
            ref = entity.get("id")
            if isinstance(ref, Ref):
                ent = dict(entity)
                ref_val = ref.val
                self._entities[ref_val] = ent
                # Update tag presence index
                for tag_name in ent:
                    idx = tag_index.get(tag_name)
                    if idx is None:
                        idx = set()
                        tag_index[tag_name] = idx
                    idx.add(ref_val)
                count += 1
        # Invalidate column cache
        self._all_col_names = None
        return count

    # ---- Internal helpers ----------------------------------------------------

    def _resolver(self, ref: Ref) -> dict[str, Any] | None:
        """Resolve a Ref to an entity dict for multi-segment filter paths."""
        return self._entities.get(ref.val)

    def _ref_val(self, ref_or_str: Any) -> str | None:
        """Extract a ref string value from a Ref or str tag value."""
        if isinstance(ref_or_str, Ref):
            return ref_or_str.val
        if isinstance(ref_or_str, str):
            return ref_or_str
        return None

    @property
    def all_col_names(self) -> tuple[str, ...]:
        """Return all unique tag names across all entities (cached)."""
        if self._all_col_names is None:
            seen: dict[str, None] = {}
            for entity in self._entities.values():
                for key in entity:
                    if key not in seen:
                        seen[key] = None
            self._all_col_names = tuple(seen)
        return self._all_col_names

    # ---- Read ops ------------------------------------------------------------

    async def read_by_filter(
        self,
        ast: Node,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return entities matching a filter AST.

        :param ast: Compiled filter AST from :func:`~hs_py.filter.parse`.
        :param limit: Maximum number of results to return.  ``None`` means no
            limit.
        :returns: List of matching entity dicts.
        """
        # Fast path: simple Has("tagName") uses the tag index directly.
        if type(ast) is Has and len(ast.path.names) == 1:
            tag = ast.path.names[0]
            ref_vals = self._tag_index.get(tag)
            if ref_vals is None:
                return []
            entities = self._entities
            if limit is not None:
                rows: list[dict[str, Any]] = []
                for rv in ref_vals:
                    ent = entities.get(rv)
                    if ent is not None:
                        rows.append(ent)
                        if len(rows) >= limit:
                            break
                return rows
            return [entities[rv] for rv in ref_vals if rv in entities]

        # General path: evaluate filter against every entity.
        rows = []
        for entity in self._entities.values():
            if evaluate(ast, entity, self._resolver):
                rows.append(entity)
                if limit is not None and len(rows) >= limit:
                    break
        return rows

    async def read_by_ids(self, ids: list[Ref]) -> list[dict[str, Any] | None]:
        """Return entities for a list of Refs, preserving input order.

        :param ids: Ordered list of entity Refs to fetch.
        :returns: List the same length as *ids*.  Each entry is the entity
            dict if found, or ``None`` if the Ref does not exist.
        """
        return [self._entities.get(ref.val) for ref in ids]

    # ---- Navigation ----------------------------------------------------------

    async def nav(self, nav_id: str | None = None) -> list[dict[str, Any]]:
        """Navigate the site/equip/point hierarchy.

        - ``nav_id=None`` — return all entities with the ``site`` tag.
        - ``nav_id`` of a site — return equips whose ``siteRef`` matches.
        - ``nav_id`` of an equip — return points whose ``equipRef`` matches.

        :param nav_id: Ref val of the parent entity, or ``None`` for roots.
        :returns: List of child entity dicts.
        """
        if nav_id is None:
            return [e for e in self._entities.values() if "site" in e]

        target = self._entities.get(nav_id)
        if target is None:
            return []

        if "site" in target:
            # Return equips with a matching siteRef
            result: list[dict[str, Any]] = []
            for entity in self._entities.values():
                if "equip" not in entity:
                    continue
                site_ref_val = self._ref_val(entity.get("siteRef"))
                if site_ref_val == nav_id:
                    result.append(entity)
            return result

        if "equip" in target:
            # Return points with a matching equipRef
            result = []
            for entity in self._entities.values():
                if "point" not in entity:
                    continue
                equip_ref_val = self._ref_val(entity.get("equipRef"))
                if equip_ref_val == nav_id:
                    result.append(entity)
            return result

        return []

    # ---- History ops ---------------------------------------------------------

    async def his_read(
        self,
        ref: Ref,
        range_str: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return time-series history for a point.

        :param ref: Entity Ref of the point.
        :param range_str: Optional range string (currently ignored; all data
            is returned).
        :returns: List of dicts with ``"ts"`` and ``"val"`` keys.
        """
        return list(self._timeseries.get(ref.val, []))

    async def his_write(self, ref: Ref, items: list[dict[str, Any]]) -> None:
        """Append time-series data for a point.

        :param ref: Entity Ref of the point.
        :param items: List of dicts with ``"ts"`` and ``"val"`` keys to
            append.
        """
        bucket = self._timeseries.setdefault(ref.val, [])
        bucket.extend(items)

    # ---- Priority array ops --------------------------------------------------

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
        :param who: Optional identifier of who is writing (stored for
            reference but not currently used).
        :param duration: Ignored by this backend.
        """
        pri = self._priority.setdefault(ref.val, {})
        if val is None:
            pri.pop(level, None)
        else:
            pri[level] = val

    async def point_read_array(self, ref: Ref) -> list[dict[str, Any]]:
        """Return the 17-level priority array for a writable point.

        :param ref: Entity Ref of the writable point.
        :returns: List of 17 dicts, each containing ``"level"`` (:class:`Number`)
            and optionally ``"val"`` (absent when the level is unset).
        """
        pri = self._priority.get(ref.val, {})
        rows: list[dict[str, Any]] = []
        for level in range(1, 18):
            row: dict[str, Any] = {"level": Number(float(level))}
            if level in pri:
                row["val"] = pri[level]
            rows.append(row)
        return rows

    # ---- Watch ops -----------------------------------------------------------

    async def watch_sub(
        self,
        watch_id: str | None,
        ids: list[Ref],
        dis: str = "watch",
    ) -> tuple[str, list[dict[str, Any]]]:
        """Create or extend a watch subscription.

        :param watch_id: Existing watch ID to extend, or ``None`` to create a
            new watch.  If the provided ID does not exist it is treated as
            ``None`` and a new watch is created.
        :param ids: Entity Refs to subscribe to.
        :param dis: Human-readable name for a newly created watch.
        :returns: ``(watch_id, entities)`` — the (possibly new) watch ID and
            the current state of all subscribed entities.
        """
        # Resolve or create the watch
        if watch_id is None or watch_id not in self._watches:
            watch_id = f"w-{secrets.token_hex(4)}"
            self._watches[watch_id] = _WatchState(dis=dis)

        state = self._watches[watch_id]
        for ref in ids:
            state.ids.add(ref.val)

        # Return current state of subscribed entities
        entities = [self._entities[rv] for rv in state.ids if rv in self._entities]
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
        :param close: If ``True``, tear down the entire watch.
        """
        if watch_id not in self._watches:
            return

        if close:
            del self._watches[watch_id]
            return

        state = self._watches[watch_id]
        for ref in ids:
            state.ids.discard(ref.val)
            state.dirty.discard(ref.val)

    async def watch_poll(
        self,
        watch_id: str,
        *,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Poll a watch for changed entities.

        :param watch_id: Watch to poll.
        :param refresh: If ``True``, return all watched entities regardless of
            dirty state.
        :returns: List of entity dicts that have changed since the last poll,
            or all watched entities when *refresh* is ``True``.  The dirty set
            is cleared after polling.
        """
        state = self._watches.get(watch_id)
        if state is None:
            return []

        ref_vals = set(state.ids) if refresh else state.dirty & state.ids

        state.dirty.clear()

        return [self._entities[rv] for rv in ref_vals if rv in self._entities]

    # ---- Mutation helpers (for testing / server push) -----------------------

    def mark_dirty(self, ref_val: str) -> None:
        """Mark an entity as changed in all watches that subscribe to it.

        :param ref_val: The ``Ref.val`` of the entity that changed.
        """
        for state in self._watches.values():
            if ref_val in state.ids:
                state.dirty.add(ref_val)

    # ---- UserStore implementation --------------------------------------------

    async def get_user(self, username: str) -> User | None:
        """Return a user by username, or ``None`` if not found."""
        return self._users.get(username)

    async def list_users(self) -> list[User]:
        """Return all users."""
        return list(self._users.values())

    async def create_user(self, user: User) -> None:
        """Persist a new user.

        :raises ValueError: If a user with the same username already exists.
        """
        if user.username in self._users:
            msg = f"User already exists: {user.username!r}"
            raise ValueError(msg)
        self._users[user.username] = user

    async def update_user(self, username: str, **fields: Any) -> User:
        """Update fields on an existing user.

        :raises KeyError: If the user does not exist.
        """
        existing = self._users.get(username)
        if existing is None:
            msg = f"User not found: {username!r}"
            raise KeyError(msg)

        import time

        updates: dict[str, Any] = {"updated_at": time.time()}
        if "password" in fields:
            updates["credentials"] = derive_scram_credentials(fields.pop("password"))

        allowed = {"first_name", "last_name", "email", "role", "enabled", "credentials"}
        for key, val in fields.items():
            if key in allowed:
                updates[key] = val

        from dataclasses import asdict

        merged = {**asdict(existing), **updates}
        merged["credentials"] = updates.get("credentials", existing.credentials)
        # asdict() converts Role enum to its value — restore the enum instance
        if isinstance(merged.get("role"), str):
            from hs_py.user import Role

            merged["role"] = Role(merged["role"])
        new_user = User(**merged)
        self._users[username] = new_user
        return new_user

    async def delete_user(self, username: str) -> bool:
        """Delete a user by username."""
        return self._users.pop(username, None) is not None
