"""Haystack Zinc encoding and decoding.

Zinc is the primary text format for Haystack data. It encodes grids as a
line-oriented text format with typed scalar values.

See: https://project-haystack.org/doc/docHaystack/Zinc
"""

from __future__ import annotations

import datetime
from typing import Any

from hs_py.encoding.scanner import (
    escape_str,
    format_number,
    format_ref,
    scan_str,
    scan_tag_name,
    scan_val,
    skip_ws,
    tz_name,
)
from hs_py.grid import Col, Grid
from hs_py.kinds import (
    MARKER,
    Coord,
    Marker,
    Na,
    Number,
    Ref,
    Remove,
    Symbol,
    Uri,
    XStr,
)

__all__ = [
    "decode_grid",
    "decode_val",
    "encode_grid",
    "encode_val",
]

_ZINC_VER = "3.0"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encode_val(val: Any) -> str:
    """Encode a single Haystack value as Zinc text.

    :param val: Haystack value to encode.
    :returns: Zinc-encoded string.
    """
    return _encode(val)


def decode_val(text: str) -> Any:
    """Decode a Zinc-encoded scalar value string.

    :param text: Zinc value text.
    :returns: Parsed Haystack value.
    """
    text = text.strip()
    if not text:
        return None
    val, _ = scan_val(text, 0)
    return val


def encode_grid(grid: Grid) -> str:
    """Encode a Grid as Zinc text.

    :param grid: Grid to encode.
    :returns: Zinc-encoded grid string.
    """
    lines: list[str] = []

    # Version + metadata line
    ver_line = f'ver:"{_ZINC_VER}"'
    for k, v in grid.meta.items():
        ver_line += " " + _encode_tag(k, v)
    lines.append(ver_line)

    # Column definitions
    if not grid.cols:
        lines.append("empty")
    else:
        col_parts: list[str] = []
        for col in grid.cols:
            part = col.name
            for mk, mv in col.meta.items():
                part += " " + _encode_tag(mk, mv)
            col_parts.append(part)
        lines.append(",".join(col_parts))

    # Data rows
    for row in grid.rows:
        cells: list[str] = []
        for col in grid.cols:
            val = row.get(col.name)
            cells.append(_encode(val))
        lines.append(",".join(cells))

    return "\n".join(lines)


def decode_grid(text: str) -> Grid:
    """Decode Zinc text into a Grid.

    :param text: Zinc grid text.
    :returns: Decoded Grid.
    """
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return Grid()

    # Parse version + metadata
    meta = _parse_ver_line(lines[0])

    if len(lines) < 2:
        return Grid(meta=meta)

    # Parse columns
    cols = _parse_cols_line(lines[1])

    # Check for empty grid marker
    if len(cols) == 1 and cols[0].name == "empty" and not cols[0].meta:
        return Grid(meta=meta)

    # Parse rows
    rows: list[dict[str, Any]] = []
    for line in lines[2:]:
        row = _parse_row_line(line, cols)
        rows.append(row)

    return Grid(meta=meta, cols=tuple(cols), rows=tuple(rows))


# ---------------------------------------------------------------------------
# Scalar encoding helpers
# ---------------------------------------------------------------------------


def _encode(val: Any) -> str:
    """Encode any Haystack value as Zinc text."""
    if val is None:
        return "N"
    if isinstance(val, bool):
        return "T" if val else "F"
    if isinstance(val, Marker):
        return "M"
    if isinstance(val, Na):
        return "NA"
    if isinstance(val, Remove):
        return "R"
    if isinstance(val, Number):
        return format_number(val)
    if isinstance(val, str):
        return _encode_str(val)
    if isinstance(val, Ref):
        return format_ref(val, zinc=True)
    if isinstance(val, Symbol):
        return f"^{val.val}"
    if isinstance(val, Uri):
        return _encode_uri(val)
    if isinstance(val, Coord):
        return f"C({val.lat},{val.lng})"
    if isinstance(val, XStr):
        return f'{val.type_name}("{escape_str(val.val)}")'
    if isinstance(val, datetime.datetime):
        return _encode_datetime(val)
    if isinstance(val, datetime.date):
        return val.isoformat()
    if isinstance(val, datetime.time):
        return val.isoformat()
    if isinstance(val, Grid):
        return _encode_nested_grid(val)
    if isinstance(val, list):
        items = ", ".join(_encode(v) for v in val)
        return f"[{items}]"
    if isinstance(val, dict):
        return _encode_dict(val)
    if isinstance(val, int | float):
        return format_number(Number(float(val)))
    msg = f"Cannot encode {type(val).__name__} as Zinc"
    raise TypeError(msg)


def _encode_str(s: str) -> str:
    return f'"{escape_str(s)}"'


def _encode_uri(uri: Uri) -> str:
    escaped = uri.val.replace("\\", "\\\\").replace("`", "\\`")
    return f"`{escaped}`"


def _encode_datetime(dt: datetime.datetime) -> str:
    iso = dt.isoformat()
    tz = tz_name(dt)
    if tz is not None:
        return f"{iso} {tz}"
    return iso


def _encode_dict(d: dict[str, Any]) -> str:
    parts: list[str] = []
    for k, v in d.items():
        parts.append(_encode_tag(k, v))
    return "{" + " ".join(parts) + "}"


def _encode_nested_grid(grid: Grid) -> str:
    inner = encode_grid(grid)
    return f"<<\n{inner}\n>>"


def _encode_tag(name: str, val: Any) -> str:
    """Encode a tag as ``name`` (marker) or ``name:value``."""
    if isinstance(val, Marker):
        return name
    return f"{name}:{_encode(val)}"


# ---------------------------------------------------------------------------
# Grid decoding helpers
# ---------------------------------------------------------------------------


def _parse_ver_line(line: str) -> dict[str, Any]:
    """Parse the ``ver:"3.0" tag1:val tag2`` metadata line."""
    if not line.startswith("ver:"):
        msg = f"Zinc grid must start with 'ver:', got: {line!r}"
        raise ValueError(msg)
    pos = 4
    # Parse and discard the version string
    _, pos = scan_str(line, pos)

    # Parse metadata tags
    meta: dict[str, Any] = {}
    while pos < len(line):
        pos = skip_ws(line, pos)
        if pos >= len(line):
            break
        name, pos = _scan_tag_name(line, pos)
        if not name:
            break
        if pos < len(line) and line[pos] == ":":
            pos += 1
            val, pos = scan_val(line, pos)
            meta[name] = val
        else:
            meta[name] = MARKER
    return meta


def _parse_cols_line(line: str) -> list[Col]:
    """Parse the column definitions line."""
    cols: list[Col] = []
    pos = 0

    while pos < len(line):
        pos = skip_ws(line, pos)
        if pos >= len(line):
            break

        # Parse column name
        name, pos = _scan_tag_name(line, pos)
        if not name:
            break

        # Parse column metadata until comma or end of line
        meta: dict[str, Any] = {}
        while pos < len(line) and line[pos] != ",":
            pos = skip_ws(line, pos)
            if pos >= len(line) or line[pos] == ",":
                break
            # Bare display string → implicit dis tag
            if line[pos] == '"':
                dis_val, pos = scan_str(line, pos)
                meta["dis"] = dis_val
                continue
            mname, pos = _scan_tag_name(line, pos)
            if not mname:
                break
            if pos < len(line) and line[pos] == ":":
                pos += 1
                val, pos = scan_val(line, pos)
                meta[mname] = val
            else:
                meta[mname] = MARKER

        cols.append(Col(name=name, meta=meta))

        # Skip comma separator
        if pos < len(line) and line[pos] == ",":
            pos += 1

    return cols


def _parse_row_line(line: str, cols: list[Col]) -> dict[str, Any]:
    """Parse a data row line into a dict keyed by column names."""
    row: dict[str, Any] = {}
    pos = 0

    for i, col in enumerate(cols):
        pos = skip_ws(line, pos)

        if pos >= len(line):
            break

        # Empty cell (consecutive comma or trailing)
        if line[pos] == ",":
            if i < len(cols) - 1:
                pos += 1
            continue

        # Parse value
        val, pos = scan_val(line, pos)
        if val is not None:
            row[col.name] = val

        # Skip comma after value
        pos = skip_ws(line, pos)
        if pos < len(line) and line[pos] == ",":
            pos += 1

    return row


_scan_tag_name = scan_tag_name
