"""Evaluate Haystack filter AST nodes against entity dicts.

Supports single-segment and multi-segment (dereference) path expressions.
Multi-segment paths require a resolver callback to look up Refs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hs_py.filter.ast import And, Cmp, CmpOp, Has, Missing, Node, Or
from hs_py.grid import Grid
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

    matching = tuple(row for row in grid if _eval(node, row, resolver))
    if not matching:
        return Grid.make_empty()

    return Grid(meta=grid.meta, cols=grid.cols, rows=matching)


# ---- Internal evaluation ----------------------------------------------------

#: Sentinel for missing tag values (distinct from None).
_MISSING = object()

# Pre-compute CmpOp members for identity comparison (avoids Enum __eq__).
_OP_EQ = CmpOp.EQ
_OP_NE = CmpOp.NE
_OP_LT = CmpOp.LT
_OP_LE = CmpOp.LE
_OP_GT = CmpOp.GT


def _eval_has(node: Has, entity: dict[str, Any], resolver: Resolver | None) -> bool:
    names = node.path.names
    if len(names) == 1:
        return names[0] in entity
    return _resolve_path_multi(names, entity, resolver) is not _MISSING


def _eval_missing(node: Missing, entity: dict[str, Any], resolver: Resolver | None) -> bool:
    names = node.path.names
    if len(names) == 1:
        return names[0] not in entity
    return _resolve_path_multi(names, entity, resolver) is _MISSING


def _eval_cmp(node: Cmp, entity: dict[str, Any], resolver: Resolver | None) -> bool:
    names = node.path.names
    if len(names) == 1:
        val = entity.get(names[0], _MISSING)
    else:
        val = _resolve_path_multi(names, entity, resolver)
    if val is _MISSING:
        return False
    return _compare(val, node.op, node.val)


def _eval_and(node: And, entity: dict[str, Any], resolver: Resolver | None) -> bool:
    return _eval(node.left, entity, resolver) and _eval(node.right, entity, resolver)


def _eval_or(node: Or, entity: dict[str, Any], resolver: Resolver | None) -> bool:
    return _eval(node.left, entity, resolver) or _eval(node.right, entity, resolver)


# Type → handler dispatch table (O(1) lookup instead of isinstance chain).
_EVAL_DISPATCH: dict[type, Callable[..., bool]] = {
    Has: _eval_has,
    Missing: _eval_missing,
    Cmp: _eval_cmp,
    And: _eval_and,
    Or: _eval_or,
}


def _eval(node: Node, entity: dict[str, Any], resolver: Resolver | None) -> bool:
    handler = _EVAL_DISPATCH.get(type(node))
    if handler is not None:
        return handler(node, entity, resolver)
    msg = f"Unknown node type: {type(node).__name__}"
    raise TypeError(msg)


def _resolve_path_multi(
    names: tuple[str, ...], entity: dict[str, Any], resolver: Resolver | None
) -> Any:
    """Walk a multi-segment path, returning _MISSING if any segment fails."""
    current: Any = entity
    last = len(names) - 1
    for i in range(last):
        if not isinstance(current, dict):
            return _MISSING
        val = current.get(names[i], _MISSING)
        if val is _MISSING:
            return _MISSING
        if not isinstance(val, Ref):
            return _MISSING
        if resolver is None:
            return _MISSING
        resolved = resolver(val)
        if resolved is None:
            return _MISSING
        current = resolved
    # Last segment — just a dict lookup.
    if not isinstance(current, dict):
        return _MISSING
    return current.get(names[last], _MISSING)


def _compare(left: Any, op: CmpOp, right: Any) -> bool:
    """Compare two values using a comparison operator.

    Number comparison uses numeric value only (unit-agnostic).
    """
    if op is _OP_EQ:
        return _eq(left, right)
    if op is _OP_NE:
        return not _eq(left, right)
    return _ordered_cmp(left, op, right)


def _eq(left: Any, right: Any) -> bool:
    """Equality check with Number-aware comparison."""
    if type(left) is Number and type(right) is Number:
        return left.val == right.val and left.unit == right.unit
    if type(left) is Ref and type(right) is Ref:
        return left.val == right.val
    return left == right  # type: ignore[no-any-return]


def _ordered_cmp(left: Any, op: CmpOp, right: Any) -> bool:
    """Ordered comparison (<, <=, >, >=)."""
    lv = left.val if type(left) is Number else left
    rv = right.val if type(right) is Number else right
    try:
        if op is _OP_LT:
            return lv < rv
        if op is _OP_LE:
            return lv <= rv
        if op is _OP_GT:
            return lv > rv
        return lv >= rv
    except TypeError:
        return False


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
