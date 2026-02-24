"""Haystack JSON encoding and decoding.

Supports both Haystack 4 (v4) and Haystack 3 (v3) JSON formats, with an
optional pythonic decode mode that converts Haystack types to native Python
equivalents where possible.

See: https://project-haystack.org/doc/docHaystack/Json
"""

from __future__ import annotations

import datetime
import math
from enum import Enum
from typing import Any

import orjson

from hs_py.encoding.scanner import city_to_tz, format_num, tz_name
from hs_py.grid import Col, Grid
from hs_py.kinds import (
    MARKER,
    NA,
    REMOVE,
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
    "JsonVersion",
    "decode_grid",
    "decode_grid_dict",
    "decode_val",
    "encode_grid",
    "encode_grid_dict",
    "encode_val",
]


class JsonVersion(Enum):
    """Haystack JSON encoding version."""

    V3 = "v3"
    """Haystack 3 JSON — type-prefixed strings (e.g. ``"n:42 °F"``)."""

    V4 = "v4"
    """Haystack 4 JSON — ``_kind`` object wrappers."""


# Maximum recursion depth for JSON decoding to prevent stack overflow.
_MAX_DECODE_DEPTH = 64


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encode_val(val: Any, *, version: JsonVersion = JsonVersion.V4) -> Any:
    """Encode a single Haystack value to its JSON-compatible representation.

    :param val: Haystack value to encode.
    :param version: JSON encoding version to use.
    :returns: JSON-serializable Python object.
    """
    if version is JsonVersion.V3:
        return _encode_val_v3(val)
    return _encode_val_v4(val)


def decode_val(
    obj: Any,
    *,
    version: JsonVersion = JsonVersion.V4,
    pythonic: bool = False,
) -> Any:
    """Decode a JSON value to a Haystack kind.

    :param obj: JSON-deserialized value.
    :param version: JSON encoding version to decode.
    :param pythonic: If ``True``, convert to native Python types where possible.
        :class:`~hs_py.kinds.Marker` becomes ``True``, unitless
        :class:`~hs_py.kinds.Number` becomes ``float``,
        :class:`~hs_py.kinds.Symbol` and :class:`~hs_py.kinds.Uri` become ``str``.
    :returns: Decoded Haystack value.
    """
    result = _decode_val_v3(obj) if version is JsonVersion.V3 else _decode_val_v4(obj)
    if pythonic:
        return _to_pythonic(result)
    return result


def encode_grid(grid: Grid, *, version: JsonVersion = JsonVersion.V4) -> bytes:
    """Encode a :class:`~hs_py.grid.Grid` to Haystack JSON bytes.

    :param grid: Grid to encode.
    :param version: JSON encoding version to use.
    :returns: JSON-encoded bytes via :mod:`orjson`.
    """
    if version is JsonVersion.V3:
        return orjson.dumps(_encode_grid_v3(grid))
    return orjson.dumps(_encode_grid_v4(grid))


def encode_grid_dict(grid: Grid, *, version: JsonVersion = JsonVersion.V4) -> dict[str, Any]:
    """Encode a :class:`~hs_py.grid.Grid` to a JSON-compatible dict (no serialization).

    Use this when embedding a grid dict inside a larger JSON structure
    to avoid the overhead of serializing to bytes and back.

    :param grid: Grid to encode.
    :param version: JSON encoding version to use.
    :returns: JSON-serializable dict.
    """
    if version is JsonVersion.V3:
        return _encode_grid_v3(grid)
    return _encode_grid_v4(grid)


def decode_grid_dict(
    obj: dict[str, Any],
    *,
    version: JsonVersion = JsonVersion.V4,
    pythonic: bool = False,
) -> Grid:
    """Decode a pre-parsed JSON dict to a :class:`~hs_py.grid.Grid`.

    Use this when the JSON has already been deserialized (e.g. from a
    WebSocket message) to avoid an unnecessary ``orjson.dumps`` /
    ``orjson.loads`` round-trip.

    :param obj: JSON-deserialized dict representing a grid.
    :param version: JSON encoding version to decode.
    :param pythonic: If ``True``, convert values to native Python types.
    :returns: Decoded :class:`~hs_py.grid.Grid`.
    """
    grid = _decode_grid_v3(obj) if version is JsonVersion.V3 else _decode_grid_v4(obj)
    if pythonic:
        return _pythonic_grid(grid)
    return grid


def decode_grid(
    data: bytes,
    *,
    version: JsonVersion = JsonVersion.V4,
    pythonic: bool = False,
) -> Grid:
    """Decode Haystack JSON bytes to a :class:`~hs_py.grid.Grid`.

    :param data: JSON bytes.
    :param version: JSON encoding version to decode.
    :param pythonic: If ``True``, convert values to native Python types.
    :returns: Decoded :class:`~hs_py.grid.Grid`.
    """
    obj = orjson.loads(data)
    grid = _decode_grid_v3(obj) if version is JsonVersion.V3 else _decode_grid_v4(obj)
    if pythonic:
        return _pythonic_grid(grid)
    return grid


# ---------------------------------------------------------------------------
# V4 Encoding (Haystack 4 — _kind objects)
# ---------------------------------------------------------------------------


def _encode_val_v4(val: Any) -> Any:
    """Encode a value using Haystack 4 JSON format."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, Marker):
        return {"_kind": "marker"}
    if isinstance(val, Na):
        return {"_kind": "na"}
    if isinstance(val, Remove):
        return {"_kind": "remove"}
    if isinstance(val, Number):
        return _encode_number_v4(val)
    if isinstance(val, Ref):
        d: dict[str, Any] = {"_kind": "ref", "val": val.val}
        if val.dis is not None:
            d["dis"] = val.dis
        return d
    if isinstance(val, Symbol):
        return {"_kind": "symbol", "val": val.val}
    if isinstance(val, Uri):
        return {"_kind": "uri", "val": val.val}
    if isinstance(val, Coord):
        return {"_kind": "coord", "lat": val.lat, "lng": val.lng}
    if isinstance(val, XStr):
        return {"_kind": "xstr", "type": val.type_name, "val": val.val}
    if isinstance(val, datetime.datetime):
        return _encode_datetime_v4(val)
    if isinstance(val, datetime.date):
        return {"_kind": "date", "val": val.isoformat()}
    if isinstance(val, datetime.time):
        return {"_kind": "time", "val": val.isoformat()}
    if isinstance(val, Grid):
        return _encode_grid_v4(val)
    if isinstance(val, list):
        return [_encode_val_v4(v) for v in val]
    if isinstance(val, dict):
        return {k: _encode_val_v4(v) for k, v in val.items()}
    if isinstance(val, str):
        return val
    if isinstance(val, int | float):
        return val
    msg = f"Cannot encode {type(val).__name__} as Haystack JSON"
    raise TypeError(msg)


def _encode_number_v4(n: Number) -> Any:
    """Encode a Number for v4, using plain JSON number when possible."""
    if n.unit is None and not math.isnan(n.val) and not math.isinf(n.val):
        return n.val
    d: dict[str, Any] = {"_kind": "number"}
    if math.isnan(n.val):
        d["val"] = "NaN"
    elif math.isinf(n.val):
        d["val"] = "INF" if n.val > 0 else "-INF"
    else:
        d["val"] = n.val
    if n.unit is not None:
        d["unit"] = n.unit
    return d


def _encode_datetime_v4(dt: datetime.datetime) -> dict[str, Any]:
    """Encode a datetime for v4."""
    d: dict[str, Any] = {"_kind": "dateTime", "val": dt.isoformat()}
    tz = tz_name(dt)
    if tz:
        d["tz"] = tz
    return d


def _encode_grid_v4(grid: Grid) -> dict[str, Any]:
    """Encode a Grid as a v4 JSON dict."""
    meta = {k: _encode_val_v4(v) for k, v in grid.meta.items()}
    cols = []
    for c in grid.cols:
        col_d: dict[str, Any] = {"name": c.name}
        if c.meta:
            col_d["meta"] = {k: _encode_val_v4(v) for k, v in c.meta.items()}
        cols.append(col_d)
    rows = [{k: _encode_val_v4(v) for k, v in row.items()} for row in grid.rows]
    return {"_kind": "grid", "meta": meta, "cols": cols, "rows": rows}


# ---------------------------------------------------------------------------
# V4 Decoding
# ---------------------------------------------------------------------------


def _decode_val_v4(obj: Any, _depth: int = 0) -> Any:
    """Decode a value from Haystack 4 JSON format."""
    if _depth > _MAX_DECODE_DEPTH:
        msg = "Maximum decoding depth exceeded"
        raise ValueError(msg)
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, int | float):
        return obj
    if isinstance(obj, list):
        return [_decode_val_v4(v, _depth + 1) for v in obj]
    if isinstance(obj, dict):
        kind = obj.get("_kind")
        if kind is not None:
            return _decode_kind_v4(kind, obj, _depth)
        return {k: _decode_val_v4(v, _depth + 1) for k, v in obj.items()}
    msg = f"Cannot decode {type(obj).__name__} as Haystack value"
    raise TypeError(msg)


def _decode_kind_v4(kind: str, obj: dict[str, Any], _depth: int = 0) -> Any:
    """Decode a typed JSON object by its ``_kind`` field."""
    decoder = _V4_KIND_DECODERS.get(kind)
    if decoder is not None:
        return decoder(obj, _depth)
    msg = f"Unknown _kind: {kind!r}"
    raise ValueError(msg)


def _decode_marker_v4(_obj: dict[str, Any], _depth: int = 0) -> Marker:
    return MARKER


def _decode_na_v4(_obj: dict[str, Any], _depth: int = 0) -> Na:
    return NA


def _decode_remove_v4(_obj: dict[str, Any], _depth: int = 0) -> Remove:
    return REMOVE


def _decode_number_v4(obj: dict[str, Any], _depth: int = 0) -> Number:
    val = obj["val"]
    unit = obj.get("unit")
    if isinstance(val, str):
        if val == "NaN":
            return Number(float("nan"), unit)
        if val == "INF":
            return Number(float("inf"), unit)
        if val == "-INF":
            return Number(float("-inf"), unit)
    return Number(float(val), unit)


def _decode_ref_v4(obj: dict[str, Any], _depth: int = 0) -> Ref:
    return Ref(obj["val"], obj.get("dis"))


def _decode_symbol_v4(obj: dict[str, Any], _depth: int = 0) -> Symbol:
    return Symbol(obj["val"])


def _decode_uri_v4(obj: dict[str, Any], _depth: int = 0) -> Uri:
    return Uri(obj["val"])


def _decode_coord_v4(obj: dict[str, Any], _depth: int = 0) -> Coord:
    return Coord(obj["lat"], obj["lng"])


def _decode_xstr_v4(obj: dict[str, Any], _depth: int = 0) -> XStr:
    return XStr(obj["type"], obj["val"])


def _decode_date_v4(obj: dict[str, Any], _depth: int = 0) -> datetime.date:
    return datetime.date.fromisoformat(obj["val"])


def _decode_time_v4(obj: dict[str, Any], _depth: int = 0) -> datetime.time:
    return datetime.time.fromisoformat(obj["val"])


def _decode_datetime_v4(obj: dict[str, Any], _depth: int = 0) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(obj["val"])
    tz_name = obj.get("tz")
    if tz_name:
        tz = city_to_tz(tz_name)
        dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
    return dt


def _decode_dict_v4(obj: dict[str, Any], _depth: int = 0) -> dict[str, Any]:
    """Decode a dict with explicit ``_kind: 'dict'``."""
    return {k: _decode_val_v4(v, _depth + 1) for k, v in obj.items() if k != "_kind"}


def _decode_grid_v4(obj: dict[str, Any], _depth: int = 0) -> Grid:
    meta_raw = obj.get("meta", {})
    meta = {k: _decode_val_v4(v, _depth + 1) for k, v in meta_raw.items()}
    cols_raw = obj.get("cols", [])
    cols = tuple(
        Col(
            name=c["name"],
            meta={k: _decode_val_v4(v, _depth + 1) for k, v in c.get("meta", {}).items()},
        )
        for c in cols_raw
    )
    rows_raw = obj.get("rows", [])
    rows = tuple({k: _decode_val_v4(v, _depth + 1) for k, v in row.items()} for row in rows_raw)
    return Grid(meta=meta, cols=cols, rows=rows)


_V4_KIND_DECODERS: dict[str, Any] = {
    "marker": _decode_marker_v4,
    "na": _decode_na_v4,
    "remove": _decode_remove_v4,
    "number": _decode_number_v4,
    "ref": _decode_ref_v4,
    "symbol": _decode_symbol_v4,
    "uri": _decode_uri_v4,
    "coord": _decode_coord_v4,
    "xstr": _decode_xstr_v4,
    "date": _decode_date_v4,
    "time": _decode_time_v4,
    "dateTime": _decode_datetime_v4,
    "grid": _decode_grid_v4,
    "dict": _decode_dict_v4,
}


# ---------------------------------------------------------------------------
# V3 Encoding (Haystack 3 — string prefixes)
# ---------------------------------------------------------------------------

# Characters that, when followed by ':', form a v3 type prefix.
_V3_TYPE_PREFIXES = frozenset("cdhmnrstuxyz-")


def _encode_val_v3(val: Any) -> Any:
    """Encode a value using Haystack 3 JSON format."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, Marker):
        return "m:"
    if isinstance(val, Na):
        return "z:"
    if isinstance(val, Remove):
        return "-:"
    if isinstance(val, Number):
        return _encode_number_v3(val)
    if isinstance(val, Ref):
        if val.dis is not None:
            return f"r:{val.val} {val.dis}"
        return f"r:{val.val}"
    if isinstance(val, Symbol):
        return f"y:{val.val}"
    if isinstance(val, Uri):
        return f"u:{val.val}"
    if isinstance(val, Coord):
        return f"c:{val.lat},{val.lng}"
    if isinstance(val, XStr):
        return f"x:{val.type_name}:{val.val}"
    if isinstance(val, datetime.datetime):
        tz = tz_name(val) or "UTC"
        return f"t:{val.isoformat()} {tz}"
    if isinstance(val, datetime.date):
        return f"d:{val.isoformat()}"
    if isinstance(val, datetime.time):
        return f"h:{val.isoformat()}"
    if isinstance(val, Grid):
        return _encode_grid_v3(val)
    if isinstance(val, list):
        return [_encode_val_v3(v) for v in val]
    if isinstance(val, dict):
        return {k: _encode_val_v3(v) for k, v in val.items()}
    if isinstance(val, str):
        return _encode_str_v3(val)
    if isinstance(val, int | float):
        return _encode_number_v3(Number(float(val)))
    msg = f"Cannot encode {type(val).__name__} as Haystack JSON"
    raise TypeError(msg)


def _encode_str_v3(s: str) -> str:
    """Encode a string for v3, adding ``s:`` prefix if ambiguous."""
    if len(s) >= 2 and s[1] == ":" and s[0] in _V3_TYPE_PREFIXES:
        return f"s:{s}"
    return s


def _encode_number_v3(n: Number) -> str:
    """Encode a Number for v3 as a string with ``n:`` prefix."""
    if math.isnan(n.val):
        num_str = "NaN"
    elif math.isinf(n.val):
        num_str = "INF" if n.val > 0 else "-INF"
    else:
        num_str = format_num(n.val)
    if n.unit is not None:
        return f"n:{num_str} {n.unit}"
    return f"n:{num_str}"


def _encode_grid_v3(grid: Grid) -> dict[str, Any]:
    """Encode a Grid as a v3 JSON dict (no ``_kind``, flat col meta)."""
    meta = {k: _encode_val_v3(v) for k, v in grid.meta.items()}
    cols = []
    for c in grid.cols:
        col_d: dict[str, Any] = {"name": c.name}
        for k, v in c.meta.items():
            col_d[k] = _encode_val_v3(v)
        cols.append(col_d)
    rows = [{k: _encode_val_v3(v) for k, v in row.items()} for row in grid.rows]
    return {"meta": meta, "cols": cols, "rows": rows}


# ---------------------------------------------------------------------------
# V3 Decoding (Haystack 3 — string prefixes)
# ---------------------------------------------------------------------------


def _decode_val_v3(obj: Any, _depth: int = 0) -> Any:
    """Decode a value from Haystack 3 JSON format."""
    if _depth > _MAX_DECODE_DEPTH:
        msg = "Maximum decoding depth exceeded"
        raise ValueError(msg)
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int | float):
        return obj
    if isinstance(obj, str):
        return _decode_str_v3(obj)
    if isinstance(obj, list):
        return [_decode_val_v3(v, _depth + 1) for v in obj]
    if isinstance(obj, dict):
        return {k: _decode_val_v3(v, _depth + 1) for k, v in obj.items()}
    msg = f"Cannot decode {type(obj).__name__} as Haystack value"
    raise TypeError(msg)


def _decode_str_v3(s: str) -> Any:
    """Decode a v3 type-prefixed string."""
    if len(s) >= 2 and s[1] == ":":
        decoder = _V3_STR_DECODERS.get(s[0])
        if decoder is not None:
            return decoder(s[2:])
    return s


def _v3_marker(_rest: str) -> Marker:
    return MARKER


def _v3_na(_rest: str) -> Na:
    return NA


def _v3_remove(_rest: str) -> Remove:
    return REMOVE


def _v3_str(rest: str) -> str:
    return rest


def _v3_number(rest: str) -> Number:
    """Parse ``"45.5"`` or ``"45.5 °F"``."""
    parts = rest.split(" ", 1)
    num_str = parts[0]
    unit = parts[1] if len(parts) > 1 else None
    if num_str == "NaN":
        return Number(float("nan"), unit)
    if num_str == "INF":
        return Number(float("inf"), unit)
    if num_str == "-INF":
        return Number(float("-inf"), unit)
    return Number(float(num_str), unit)


def _v3_ref(rest: str) -> Ref:
    """Parse ``"abc-123"`` or ``"abc-123 Display Name"``."""
    parts = rest.split(" ", 1)
    return Ref(parts[0], parts[1] if len(parts) > 1 else None)


def _v3_symbol(rest: str) -> Symbol:
    return Symbol(rest)


def _v3_date(rest: str) -> datetime.date:
    return datetime.date.fromisoformat(rest)


def _v3_time(rest: str) -> datetime.time:
    return datetime.time.fromisoformat(rest)


def _v3_datetime(rest: str) -> datetime.datetime:
    """Parse ``"ISO8601 Timezone"``."""
    if " " in rest:
        iso_part, tz_name = rest.rsplit(" ", 1)
        dt = datetime.datetime.fromisoformat(iso_part)
        dt = dt.astimezone(city_to_tz(tz_name))
        return dt
    return datetime.datetime.fromisoformat(rest)


def _v3_uri(rest: str) -> Uri:
    return Uri(rest)


def _v3_coord(rest: str) -> Coord:
    """Parse ``"lat,lng"``."""
    lat_s, lng_s = rest.split(",")
    return Coord(float(lat_s), float(lng_s))


def _v3_xstr(rest: str) -> XStr:
    """Parse ``"Type:value"``."""
    idx = rest.index(":")
    return XStr(rest[:idx], rest[idx + 1 :])


_V3_STR_DECODERS: dict[str, Any] = {
    "m": _v3_marker,
    "z": _v3_na,
    "-": _v3_remove,
    "s": _v3_str,
    "n": _v3_number,
    "r": _v3_ref,
    "y": _v3_symbol,
    "d": _v3_date,
    "h": _v3_time,
    "t": _v3_datetime,
    "u": _v3_uri,
    "c": _v3_coord,
    "x": _v3_xstr,
}


def _decode_grid_v3(obj: dict[str, Any], _depth: int = 0) -> Grid:
    """Decode a v3 grid JSON object (flat col meta, no ``_kind``)."""
    meta_raw = obj.get("meta", {})
    meta = {k: _decode_val_v3(v, _depth + 1) for k, v in meta_raw.items()}
    cols_raw = obj.get("cols", [])
    cols = tuple(
        Col(
            name=c["name"],
            meta={k: _decode_val_v3(v, _depth + 1) for k, v in c.items() if k != "name"},
        )
        for c in cols_raw
    )
    rows_raw = obj.get("rows", [])
    rows = tuple({k: _decode_val_v3(v, _depth + 1) for k, v in row.items()} for row in rows_raw)
    return Grid(meta=meta, cols=cols, rows=rows)


# ---------------------------------------------------------------------------
# Pythonic transform (decode-only)
# ---------------------------------------------------------------------------


def _to_pythonic(val: Any) -> Any:
    """Convert Haystack types to native Python equivalents where possible.

    - Marker → True
    - Number (unitless) → float
    - Symbol → str
    - Uri → str
    """
    if isinstance(val, Marker):
        return True
    if isinstance(val, Number):
        return val.val if val.unit is None else val
    if isinstance(val, Symbol):
        return val.val
    if isinstance(val, Uri):
        return val.val
    if isinstance(val, list):
        return [_to_pythonic(v) for v in val]
    if isinstance(val, dict):
        return {k: _to_pythonic(v) for k, v in val.items()}
    if isinstance(val, Grid):
        return _pythonic_grid(val)
    return val


def _pythonic_grid(grid: Grid) -> Grid:
    """Apply pythonic transform to all values in a Grid."""
    meta = {k: _to_pythonic(v) for k, v in grid.meta.items()}
    cols = tuple(
        Col(name=c.name, meta={k: _to_pythonic(v) for k, v in c.meta.items()}) for c in grid.cols
    )
    rows = tuple({k: _to_pythonic(v) for k, v in row.items()} for row in grid.rows)
    return Grid(meta=meta, cols=cols, rows=rows)
