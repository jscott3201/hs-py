"""Pythonic conversion helpers for Haystack Grid rows.

Converts Haystack-specific value types to plain Python equivalents so that
callers can work with ordinary dicts and lists rather than Haystack kinds.

Conversion rules
----------------
- :class:`~hs_py.kinds.Marker`        → ``True``
- :class:`~hs_py.kinds.Na`            → ``None``
- :class:`~hs_py.kinds.Remove`        → key omitted from the output dict
- :class:`~hs_py.kinds.Number` (no unit) → ``float``
- :class:`~hs_py.kinds.Number` (with unit) → kept as :class:`~hs_py.kinds.Number`
- :class:`~hs_py.kinds.Symbol`        → ``str`` (.val)
- :class:`~hs_py.kinds.Uri`           → ``str`` (.val)
- :class:`~hs_py.kinds.Ref`           → kept as :class:`~hs_py.kinds.Ref`
- Nested :class:`~hs_py.grid.Grid`    → ``list[dict]`` (recursive)
- Everything else                     → passed through unchanged
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hs_py.kinds import Marker, Na, Number, Remove, Symbol, Uri

if TYPE_CHECKING:
    from hs_py.grid import Grid

__all__ = [
    "grid_to_pythonic",
]

# Sentinel used internally so that Remove values can be detected after conversion
# without an additional isinstance check in the hot loop.
_REMOVE_SENTINEL = object()


def _convert_val(val: Any) -> Any:
    """Convert a single Haystack value to its pythonic equivalent.

    :param val: Any Haystack or plain Python value.
    :returns: Converted value, or the module-private ``_REMOVE_SENTINEL`` object
        when *val* is :class:`~hs_py.kinds.Remove` (the caller must omit the key).
    """
    # Import locally to avoid circular import at module load time.
    from hs_py.grid import Grid

    if isinstance(val, Marker):
        return True
    if isinstance(val, Na):
        return None
    if isinstance(val, Remove):
        return _REMOVE_SENTINEL
    if isinstance(val, Number):
        # Unit-less numbers collapse to plain float; unit-bearing ones keep kind.
        return val.val if not val.unit else val
    if isinstance(val, Symbol):
        return val.val
    if isinstance(val, Uri):
        return val.val
    if isinstance(val, Grid):
        return grid_to_pythonic(val)
    return val


def grid_to_pythonic(grid: Grid) -> list[dict[str, Any]]:
    """Convert a :class:`~hs_py.grid.Grid` to a list of pythonic dicts.

    Each grid row becomes a plain :class:`dict`.  Keys whose values are
    :class:`~hs_py.kinds.Remove` are omitted from the output dict.

    :param grid: The :class:`~hs_py.grid.Grid` to convert.
    :returns: List of converted row dicts.
    """
    rows: list[dict[str, Any]] = []
    for row in grid:
        converted: dict[str, Any] = {}
        for k, v in row.items():
            result = _convert_val(v)
            if result is not _REMOVE_SENTINEL:
                converted[k] = result
        rows.append(converted)
    return rows
