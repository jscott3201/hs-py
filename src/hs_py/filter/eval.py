"""Evaluate Haystack filter AST nodes against entity dicts.

Supports single-segment and multi-segment (dereference) path expressions.
Multi-segment paths require a resolver callback to look up Refs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hs_py.filter.ast import And, Cmp, CmpOp, Has, Missing, Node, Or, Path
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import Number, Ref

__all__ = [
    "Resolver",
    "evaluate",
    "evaluate_grid",
]

#: Callback type for resolving a Ref to an entity dict.
#: Returns None if the Ref cannot be resolved.
Resolver = Callable[[Ref], dict[str, Any] | None]


def evaluate(
    node: Node,
    entity: dict[str, Any],
    resolver: Resolver | None = None,
) -> bool:
    """Evaluate a filter AST against an entity dict.

    :param node: Root of the filter AST.
    :param entity: Tag dict to test.
    :param resolver: Optional callback to resolve Refs for multi-segment paths.
    :returns: ``True`` if the entity matches the filter.
    """
    return _eval(node, entity, resolver)


def evaluate_grid(
    node: Node,
    grid: Grid,
    resolver: Resolver | None = None,
) -> Grid:
    """Filter a grid, returning only rows that match the filter.

    :param node: Root of the filter AST.
    :param grid: Grid to filter.
    :param resolver: Optional callback to resolve Refs for multi-segment paths.
        If not provided and the grid has an ``id`` column, an auto-resolver
        is created from the grid's rows.
    :returns: New :class:`~hs_py.grid.Grid` containing only matching rows.
    """
    if resolver is None:
        resolver = _grid_resolver(grid)

    matching = [row for row in grid if _eval(node, row, resolver)]
    if not matching:
        return Grid.make_empty()

    builder = GridBuilder().set_meta(dict(grid.meta))
    for col in grid.cols:
        builder.add_col(col.name, dict(col.meta) if col.meta else None)
    for row in matching:
        builder.add_row(row)
    return builder.to_grid()


# ---- Internal evaluation ----------------------------------------------------


def _eval(node: Node, entity: dict[str, Any], resolver: Resolver | None) -> bool:
    if isinstance(node, Has):
        val = _resolve_path(node.path, entity, resolver)
        return val is not _MISSING
    if isinstance(node, Missing):
        val = _resolve_path(node.path, entity, resolver)
        return val is _MISSING
    if isinstance(node, Cmp):
        val = _resolve_path(node.path, entity, resolver)
        if val is _MISSING:
            return False
        return _compare(val, node.op, node.val)
    if isinstance(node, And):
        return _eval(node.left, entity, resolver) and _eval(node.right, entity, resolver)
    if isinstance(node, Or):
        return _eval(node.left, entity, resolver) or _eval(node.right, entity, resolver)
    msg = f"Unknown node type: {type(node).__name__}"
    raise TypeError(msg)


#: Sentinel for missing tag values (distinct from None).
_MISSING = object()


def _resolve_path(path: Path, entity: dict[str, Any], resolver: Resolver | None) -> Any:
    """Walk a path expression, returning _MISSING if any segment fails."""
    current: Any = entity
    for i, name in enumerate(path.names):
        if not isinstance(current, dict):
            return _MISSING
        if name not in current:
            return _MISSING
        val = current[name]
        # If not the last segment, we need to dereference through a Ref.
        if i < len(path.names) - 1:
            if not isinstance(val, Ref):
                return _MISSING
            if resolver is None:
                return _MISSING
            resolved = resolver(val)
            if resolved is None:
                return _MISSING
            current = resolved
        else:
            return val
    return _MISSING  # pragma: no cover — unreachable with non-empty path


def _compare(left: Any, op: CmpOp, right: Any) -> bool:
    """Compare two values using a comparison operator.

    Number comparison uses numeric value only (unit-agnostic).
    """
    if op == CmpOp.EQ:
        return _eq(left, right)
    if op == CmpOp.NE:
        return not _eq(left, right)
    return _ordered_cmp(left, op, right)


def _eq(left: Any, right: Any) -> bool:
    """Equality check with Number-aware comparison."""
    if isinstance(left, Number) and isinstance(right, Number):
        return _num_val(left) == _num_val(right) and left.unit == right.unit
    if isinstance(left, Ref) and isinstance(right, Ref):
        return left.val == right.val
    return left == right  # type: ignore[no-any-return]


def _ordered_cmp(left: Any, op: CmpOp, right: Any) -> bool:
    """Ordered comparison (<, <=, >, >=)."""
    lv = _num_val(left) if isinstance(left, Number) else left
    rv = _num_val(right) if isinstance(right, Number) else right
    try:
        if op == CmpOp.LT:
            return lv < rv
        if op == CmpOp.LE:
            return lv <= rv
        if op == CmpOp.GT:
            return lv > rv
        return lv >= rv
    except TypeError:
        return False


def _num_val(n: Number) -> float:
    return n.val


def _grid_resolver(grid: Grid) -> Resolver | None:
    """Build a resolver from grid rows that have an ``id`` column."""
    if "id" not in grid.col_names:
        return None
    index: dict[str, dict[str, Any]] = {}
    for row in grid:
        ref = row.get("id")
        if isinstance(ref, Ref):
            index[ref.val] = row
    if not index:
        return None

    def _resolve(ref: Ref) -> dict[str, Any] | None:
        return index.get(ref.val)

    return _resolve
