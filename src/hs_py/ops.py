"""Haystack server operations base class and op registry.

Provides :class:`HaystackOps`, the abstract base for all server-side
operation dispatchers, and the op-name-to-method-name mappings used
by HTTP and WebSocket handlers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hs_py.filter import evaluate, parse
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hs_py.ontology.namespace import Namespace
    from hs_py.storage.protocol import StorageAdapter

__all__ = [
    "HaystackOps",
]


class HaystackOps:
    """Base class for Haystack server operations.

    Subclass and override the operations you support.  When a
    *storage* adapter is provided the base-class implementations
    automatically parse the request :class:`~hs_py.grid.Grid`,
    delegate to the adapter, and wrap the result back into a Grid.
    Without an adapter, unimplemented POST ops return an error grid.
    The ``ops()`` and ``formats()`` methods have useful defaults.
    """

    def __init__(
        self,
        storage: StorageAdapter | None = None,
        namespace: Namespace | None = None,
    ) -> None:
        self._storage = storage
        self._namespace = namespace

    async def about(self) -> Grid:
        """Return server information.

        The default implementation returns minimal metadata.  Subclasses
        may override this to include richer details such as ``serverName``
        or ``vendorUri``.
        """
        return Grid.make_rows(
            [
                {
                    "haystackVersion": "4.0",
                    "productName": "hs-py",
                    "productVersion": "0.3.0",
                }
            ]
        )

    async def ops(self) -> Grid:
        """Return the list of supported operations.

        Auto-discovers which methods the subclass has overridden, plus
        storage-backed ops (when a storage adapter is present) and
        namespace-backed ops (when a namespace is present).
        """
        # Ops backed by a storage adapter
        storage_ops = frozenset(
            (
                "read",
                "nav",
                "his_read",
                "his_write",
                "point_write",
                "watch_sub",
                "watch_unsub",
                "watch_poll",
            )
        )
        # Ops backed by a namespace
        namespace_ops = frozenset(("defs", "libs"))

        has_storage = self._storage is not None
        has_namespace = self._namespace is not None

        rows: list[dict[str, Any]] = []
        for op_name, method_name in _OP_DEFS:
            supported = (
                method_name in ("about", "ops", "formats")
                or method_name in type(self).__dict__
                or (has_storage and method_name in storage_ops)
                or (has_namespace and method_name in namespace_ops)
            )
            if supported:
                rows.append({"name": op_name, "summary": f"Haystack {op_name} operation"})
        return Grid.make_rows(rows)

    async def formats(self) -> Grid:
        """Return supported data formats. Default: JSON only."""
        return Grid.make_rows([{"mime": "application/json", "receive": MARKER, "send": MARKER}])

    async def on_close(self) -> None:
        """Handle server close request. Default: no-op."""

    async def read(self, grid: Grid) -> Grid:
        """Read entities by id or filter."""
        storage = getattr(self, "_storage", None)
        if storage is None:
            return _not_supported("read")
        if not grid.rows:
            return Grid.make_empty()
        first = grid[0]
        if "id" in first:
            ids = [row["id"] for row in grid if isinstance(row.get("id"), Ref)]
            results = await storage.read_by_ids(ids)
            rows = [r for r in results if r is not None]
            return Grid.make_rows(rows) if rows else Grid.make_empty()
        filter_str = first.get("filter", "")
        limit_val = first.get("limit")
        limit: int | None = None
        if isinstance(limit_val, Number):
            limit = int(limit_val.val)
        elif isinstance(limit_val, (int, float)):
            limit = int(limit_val)
        ast = parse(filter_str)
        results_list = await storage.read_by_filter(ast, limit)
        return Grid.make_rows(results_list) if results_list else Grid.make_empty()

    async def nav(self, grid: Grid) -> Grid:
        """Navigate the entity tree."""
        storage = getattr(self, "_storage", None)
        if storage is None:
            return _not_supported("nav")
        nav_id: str | None = None
        if grid.rows:
            val = grid[0].get("navId")
            if val is not None and val != "":
                nav_id = str(val) if not isinstance(val, Ref) else val.val
        rows = await storage.nav(nav_id)
        return Grid.make_rows(rows) if rows else Grid.make_empty()

    async def his_read(self, grid: Grid) -> Grid:
        """Read time-series data."""
        storage = getattr(self, "_storage", None)
        if storage is None:
            return _not_supported("hisRead")
        if not grid.rows:
            return Grid.make_empty()
        ref = grid[0].get("id")
        if not isinstance(ref, Ref):
            return Grid.make_empty()
        range_val = grid[0].get("range")
        range_str: str | None = range_val if isinstance(range_val, str) else None
        items = await storage.his_read(ref, range_str)
        meta: dict[str, Any] = {"id": ref, "hisStart": "start", "hisEnd": "end"}
        if not items:
            builder = GridBuilder().set_meta(meta).add_col("ts").add_col("val")
            return builder.to_grid()
        builder = GridBuilder().set_meta(meta)
        col_names: dict[str, None] = {}
        for item in items:
            for k in item:
                col_names[k] = None
        for name in col_names:
            builder.add_col(name)
        for item in items:
            builder.add_row(item)
        return builder.to_grid()

    async def his_write(self, grid: Grid) -> Grid:
        """Write time-series data."""
        storage = getattr(self, "_storage", None)
        if storage is None:
            return _not_supported("hisWrite")
        ref = grid.meta.get("id")
        if not isinstance(ref, Ref):
            return Grid.make_empty()
        items = [dict(row) for row in grid]
        await storage.his_write(ref, items)
        return Grid.make_empty()

    async def point_write(self, grid: Grid) -> Grid:
        """Write to a point's priority array."""
        storage = getattr(self, "_storage", None)
        if storage is None:
            return _not_supported("pointWrite")
        if not grid.rows:
            return Grid.make_empty()
        first = grid[0]
        ref = first.get("id")
        if not isinstance(ref, Ref):
            return Grid.make_empty()
        if "level" in first:
            level_val = first["level"]
            level = int(level_val.val) if isinstance(level_val, Number) else int(level_val)
            val = first.get("val")
            who = str(first.get("who", ""))
            duration = first.get("duration")
            await storage.point_write(ref, level, val, who, duration)
            return Grid.make_empty()
        rows = await storage.point_read_array(ref)
        return Grid.make_rows(rows) if rows else Grid.make_empty()

    async def watch_sub(self, grid: Grid) -> Grid:
        """Subscribe to a watch."""
        storage = getattr(self, "_storage", None)
        if storage is None:
            return _not_supported("watchSub")
        watch_dis = grid.meta.get("watchDis", "watch")
        watch_id = grid.meta.get("watchId")
        if not isinstance(watch_id, str) or not watch_id:
            watch_id = None
        ids = [row["id"] for row in grid if isinstance(row.get("id"), Ref)]
        wid, entities = await storage.watch_sub(watch_id, ids, str(watch_dis))
        meta: dict[str, Any] = {"watchId": wid, "lease": Number(300.0, "s")}
        if not entities:
            return GridBuilder().set_meta(meta).to_grid()
        builder = GridBuilder().set_meta(meta)
        col_names: dict[str, None] = {}
        for e in entities:
            for k in e:
                col_names[k] = None
        for name in col_names:
            builder.add_col(name)
        for e in entities:
            builder.add_row(e)
        return builder.to_grid()

    async def watch_unsub(self, grid: Grid) -> Grid:
        """Unsubscribe from a watch."""
        storage = getattr(self, "_storage", None)
        if storage is None:
            return _not_supported("watchUnsub")
        watch_id = grid.meta.get("watchId")
        if not isinstance(watch_id, str):
            return Grid.make_error("Unknown watch")
        close = "close" in grid.meta
        ids = [row["id"] for row in grid if isinstance(row.get("id"), Ref)]
        try:
            await storage.watch_unsub(watch_id, ids, close=close)
        except ValueError:
            return Grid.make_error("Unknown watch")
        return Grid.make_empty()

    async def watch_poll(self, grid: Grid) -> Grid:
        """Poll a watch for changes."""
        storage = getattr(self, "_storage", None)
        if storage is None:
            return _not_supported("watchPoll")
        watch_id = grid.meta.get("watchId")
        if not isinstance(watch_id, str):
            return Grid.make_error("Unknown watch")
        refresh = "refresh" in grid.meta
        try:
            rows = await storage.watch_poll(watch_id, refresh=refresh)
        except ValueError:
            return Grid.make_error("Unknown watch")
        return Grid.make_rows(rows) if rows else Grid.make_empty()

    async def invoke_action(self, grid: Grid) -> Grid:
        """Invoke an action on an entity."""
        return _not_supported("invokeAction")

    async def defs(self, grid: Grid) -> Grid:
        """Query ontology definitions."""
        namespace = getattr(self, "_namespace", None)
        if namespace is None:
            return _not_supported("defs")

        filter_str: str | None = None
        limit: int | None = None
        if grid.rows:
            filter_str = grid[0].get("filter")
            limit_val = grid[0].get("limit")
            if isinstance(limit_val, Number):
                limit = int(limit_val.val)

        ast = parse(filter_str) if filter_str else None
        rows: list[dict[str, Any]] = []
        for d in namespace.all_defs():
            row: dict[str, Any] = {"def": d.symbol, "doc": d.doc}
            row.update(d.tags)
            if ast is not None and not evaluate(ast, row):
                continue
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break

        return Grid.make_rows(rows) if rows else Grid.make_empty()

    async def libs(self, grid: Grid) -> Grid:
        """Query ontology libraries."""
        namespace = getattr(self, "_namespace", None)
        if namespace is None:
            return _not_supported("libs")

        filter_str: str | None = None
        limit: int | None = None
        if grid.rows:
            filter_str = grid[0].get("filter")
            limit_val = grid[0].get("limit")
            if isinstance(limit_val, Number):
                limit = int(limit_val.val)

        ast = parse(filter_str) if filter_str else None
        rows: list[dict[str, Any]] = []
        for lib in namespace.all_libs():
            row: dict[str, Any] = {"def": lib.symbol, "version": lib.version}
            if ast is not None and not evaluate(ast, row):
                continue
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break

        return Grid.make_rows(rows) if rows else Grid.make_empty()

    async def filetypes(self, grid: Grid) -> Grid:
        """Query supported file types."""
        return _not_supported("filetypes")

    # -- Watch push support --

    def set_push_handler(self, handler: Callable[[str, Grid], Awaitable[None]]) -> None:
        """Set the push handler for watch notifications.

        Called by the server framework to wire up push delivery.
        Subclasses should not override this method.

        :param handler: Async callable invoked with ``(watch_id, grid)``.
        """
        self._push_handler = handler

    async def push_watch(self, watch_id: str, grid: Grid) -> None:
        """Push changed entities to subscribed watch clients.

        :param watch_id: The watch identifier.
        :param grid: Grid of changed entities.
        """
        handler: Callable[[str, Grid], Awaitable[None]] | None = getattr(
            self, "_push_handler", None
        )
        if handler is not None:
            await handler(watch_id, grid)


def _not_supported(op: str) -> Grid:
    """Return an error grid for an unsupported operation."""
    return Grid.make_error(f"Operation '{op}' is not supported by this server")


async def dispatch_op(ops: HaystackOps, op: str, msg: dict[str, Any]) -> Grid:
    """Dispatch an operation to the appropriate HaystackOps method.

    Shared by all server frameworks (FastAPI, WebSocket server).

    :param ops: :class:`HaystackOps` implementation to dispatch to.
    :param op: Operation name (e.g. ``"about"``, ``"read"``).
    :param msg: JSON envelope dict with optional ``"grid"`` key.
    :returns: Result :class:`~hs_py.grid.Grid`.
    """
    from hs_py.encoding.json import decode_grid_dict

    # GET-style ops (no request grid needed)
    if op == "about":
        return await ops.about()
    if op == "ops":
        return await ops.ops()
    if op == "formats":
        return await ops.formats()
    if op == "close":
        await ops.on_close()
        return Grid.make_empty()

    # POST-style ops (decode request grid)
    method_name = _POST_OP_METHODS.get(op)
    if method_name is None:
        return Grid.make_error(f"Unknown operation: {op}")

    grid_data = msg.get("grid")
    req_grid = decode_grid_dict(grid_data) if grid_data is not None else Grid.make_empty()

    method = getattr(ops, method_name)
    result: Grid = await method(req_grid)
    return result


# Op name → method name mapping for auto-discovery
_OP_DEFS: tuple[tuple[str, str], ...] = (
    ("about", "about"),
    ("ops", "ops"),
    ("formats", "formats"),
    ("read", "read"),
    ("nav", "nav"),
    ("hisRead", "his_read"),
    ("hisWrite", "his_write"),
    ("pointWrite", "point_write"),
    ("watchSub", "watch_sub"),
    ("watchUnsub", "watch_unsub"),
    ("watchPoll", "watch_poll"),
    ("invokeAction", "invoke_action"),
    ("defs", "defs"),
    ("libs", "libs"),
    ("filetypes", "filetypes"),
)

# Map URL op names to HaystackOps method names (POST ops only)
_POST_OP_METHODS: dict[str, str] = {
    "read": "read",
    "nav": "nav",
    "hisRead": "his_read",
    "hisWrite": "his_write",
    "pointWrite": "point_write",
    "watchSub": "watch_sub",
    "watchUnsub": "watch_unsub",
    "watchPoll": "watch_poll",
    "invokeAction": "invoke_action",
    "defs": "defs",
    "libs": "libs",
    "filetypes": "filetypes",
}
