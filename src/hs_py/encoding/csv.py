"""Haystack CSV encoding.

CSV is a lossy text format for grids — metadata, column meta, and type
information are discarded. It is useful for exporting grid data to
spreadsheets and other tools that consume RFC 4180 CSV.

See: https://project-haystack.org/doc/docHaystack/Csv
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hs_py.encoding.scanner import format_number, format_ref
from hs_py.kinds import Marker, Na, Number, Ref, Remove, Uri

if TYPE_CHECKING:
    from hs_py.grid import Grid

__all__ = [
    "encode_grid",
]


def encode_grid(grid: Grid) -> str:
    """Encode a Grid as CSV text.

    Column headers use the ``dis`` metadata value when present,
    otherwise the programmatic column name. Grid and column metadata
    are discarded. Type information is simplified per the Haystack
    CSV spec.

    :param grid: Grid to encode.
    :returns: CSV-formatted string (with trailing newline).
    """
    lines: list[str] = []

    # Header row: display names
    headers: list[str] = []
    for col in grid.cols:
        dis = col.meta.get("dis", col.name)
        headers.append(_escape_cell(str(dis)))
    lines.append(",".join(headers))

    # Data rows
    for row in grid.rows:
        cells: list[str] = []
        for col in grid.cols:
            val = row.get(col.name)
            cells.append(_escape_cell(_encode_val(val)))
        lines.append(",".join(cells))

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Value encoding
# ---------------------------------------------------------------------------


def _encode_val(val: Any) -> str:
    """Encode a Haystack value for CSV output."""
    if val is None:
        return ""
    if isinstance(val, Marker):
        return "\u2713"
    if isinstance(val, Na):
        return ""
    if isinstance(val, Remove):
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, str):
        return val
    if isinstance(val, Uri):
        return val.val
    if isinstance(val, Ref):
        return format_ref(val)
    if isinstance(val, Number):
        return format_number(val)

    # Fall back to Zinc encoding for all other types
    from hs_py.encoding.zinc import encode_val as _zinc_encode_val

    return _zinc_encode_val(val)


# ---------------------------------------------------------------------------
# RFC 4180 cell escaping
# ---------------------------------------------------------------------------


def _escape_cell(val: str) -> str:
    """Escape a CSV cell per RFC 4180.

    Cells containing commas, double quotes, or newlines are wrapped
    in double quotes. Any internal double quotes are doubled.
    """
    if not val:
        return val
    if "," in val or '"' in val or "\n" in val or "\r" in val:
        return '"' + val.replace('"', '""') + '"'
    return val
