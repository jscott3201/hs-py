"""Shared Zinc value scanning utilities.

Position-based scanner functions for Zinc-encoded scalar values.
Used by both the Trio parser and the filter lexer to avoid duplicating
regex constants and parsing logic.

All scan functions use the ``(text, pos) -> (value, end_pos)`` signature.
"""

from __future__ import annotations

import datetime
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

from hs_py.kinds import MARKER, NA, REMOVE, Coord, Number, Ref, Symbol, Uri, XStr

__all__ = [
    "DATETIME_RE",
    "DATE_RE",
    "DIGIT_CHARS",
    "IDENT_CHARS",
    "REF_CHARS",
    "STR_ESCAPES",
    "SYMBOL_CHARS",
    "TIME_RE",
    "UNIT_STOP_BASE",
    "city_to_tz",
    "escape_str",
    "format_num",
    "format_number",
    "format_ref",
    "parse_datetime",
    "scan_dict",
    "scan_keyword",
    "scan_list",
    "scan_number",
    "scan_number_or_temporal",
    "scan_ref",
    "scan_str",
    "scan_symbol",
    "scan_tag_name",
    "scan_uri",
    "scan_val",
    "skip_ws",
    "tz_name",
    "tz_to_city",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum nesting depth for recursive value scanning (lists, dicts, grids).
MAX_SCAN_DEPTH = 64

#: Maximum string/URI length in scanned values (1 MB).
MAX_STRING_LENGTH = 1_048_576

#: Regex for Zinc datetime values.
DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:\d{2})"
    r"(?:\s+[A-Z][a-zA-Z0-9_/]+)?"
)

#: Regex for Zinc date values.
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Regex for Zinc time values.
TIME_RE = re.compile(r"\d{2}:\d{2}:\d{2}(?:\.\d+)?")

#: Characters valid in a Ref id.
REF_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:.~")

#: Base characters that terminate a number unit (whitespace only).
#: Consumers extend this with context-specific delimiters.
UNIT_STOP_BASE = frozenset(" \t\n\r")

#: Characters valid in tag names and identifiers (alphanumeric + underscore).
IDENT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")

#: Characters valid in symbol names (alphanumeric + hyphen, underscore, colon, dot).
SYMBOL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:.")

#: Digit characters and underscore (for numeric scanning).
DIGIT_CHARS = frozenset("0123456789_")

#: String escape sequences per Zinc spec.
STR_ESCAPES: dict[str, str] = {
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "\\": "\\",
    '"': '"',
    "$": "$",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def skip_ws(text: str, pos: int) -> int:
    """Advance *pos* past whitespace.

    :param text: Source text.
    :param pos: Current position.
    :returns: Position of the first non-whitespace character.
    """
    while pos < len(text) and text[pos] in " \t":
        pos += 1
    return pos


def tz_to_city(tz_key: str) -> str:
    """Extract the Haystack city name from an IANA timezone key.

    Haystack uses city-only timezone names per the zoneinfo convention::

        "America/New_York" → "New_York"
        "UTC"              → "UTC"

    :param tz_key: Full IANA timezone key.
    :returns: City-only timezone name.
    """
    if "/" in tz_key:
        return tz_key.rsplit("/", 1)[1]
    return tz_key


def tz_name(dt: datetime.datetime) -> str | None:
    """Extract the Haystack city timezone name from a datetime.

    :param dt: Timezone-aware datetime.
    :returns: City-only name, or ``None`` if the datetime has no timezone
        or uses a fixed-offset timezone without an IANA key.
    """
    tz = dt.tzinfo
    if tz is None:
        return None
    if hasattr(tz, "key"):
        return tz_to_city(str(tz.key))
    if tz == datetime.UTC:
        return "UTC"
    return None


# Lazy-built mapping from city names to full IANA timezone keys.
_tz_city_map: dict[str, str] | None = None


def _get_tz_city_map() -> dict[str, str]:
    """Build (once) a mapping of city name → full IANA timezone key."""
    global _tz_city_map
    if _tz_city_map is None:
        from zoneinfo import available_timezones

        mapping: dict[str, str] = {}
        for name in available_timezones():
            if "/" in name:
                city = name.rsplit("/", 1)[1]
                mapping[city] = name
            else:
                mapping[name] = name
        _tz_city_map = mapping
    return _tz_city_map


_tz_cache: dict[str, ZoneInfo] = {}


def city_to_tz(name: str) -> ZoneInfo:
    """Resolve a Haystack timezone name to a :class:`~zoneinfo.ZoneInfo`.

    Accepts both city-only names (``"New_York"``) and full IANA names
    (``"America/New_York"``).  Results are cached to avoid repeated
    filesystem lookups from :class:`~zoneinfo.ZoneInfo`.

    :param name: Haystack city name or full IANA timezone key.
    :returns: Resolved :class:`~zoneinfo.ZoneInfo` instance.
    :raises KeyError: If *name* cannot be resolved.
    """
    cached = _tz_cache.get(name)
    if cached is not None:
        return cached
    try:
        zi = ZoneInfo(name)
    except KeyError:
        full_name = _get_tz_city_map().get(name)
        if full_name is not None:
            zi = ZoneInfo(full_name)
        else:
            msg = f"Unknown timezone: {name!r}"
            raise KeyError(msg) from None
    _tz_cache[name] = zi
    return zi


def parse_datetime(s: str) -> datetime.datetime:
    """Parse a Zinc datetime string into a Python datetime.

    :param s: Zinc datetime text (e.g. ``"2024-01-15T10:30:00-05:00 New_York"``).
    :returns: Parsed timezone-aware datetime.
    """
    parts = s.split()
    iso_part = parts[0]
    dt = datetime.datetime.fromisoformat(iso_part)
    if len(parts) > 1:
        tz = city_to_tz(parts[1])
        dt = dt.astimezone(tz)
    return dt


def format_num(val: float) -> str:
    """Format a float, dropping unnecessary trailing zeros.

    :param val: Numeric value.
    :returns: String representation without redundant decimal places.
    """
    if val == int(val) and not math.isinf(val):
        return str(int(val))
    return f"{val:g}"


def format_number(n: Number) -> str:
    """Format a :class:`~hs_py.kinds.Number` as a string with optional unit.

    Handles ``NaN``, ``INF``, ``-INF``, and appends the unit if present.

    :param n: Number to format.
    :returns: Zinc-formatted number string.
    """
    if math.isnan(n.val):
        s = "NaN"
    elif math.isinf(n.val):
        s = "INF" if n.val > 0 else "-INF"
    else:
        s = format_num(n.val)
    if n.unit:
        s += n.unit
    return s


# Reverse mapping of STR_ESCAPES for encoding: char → escape sequence.
_STR_ESCAPE_ENC: dict[str, str] = {v: f"\\{k}" for k, v in STR_ESCAPES.items()}
_ESCAPE_CHARS = frozenset(_STR_ESCAPE_ENC)


def escape_str(s: str) -> str:
    """Escape a string per the Zinc string escape spec.

    :param s: Raw string.
    :returns: Escaped string safe for Zinc encoding.
    """
    # Fast path: no escaping needed
    if not _ESCAPE_CHARS.intersection(s):
        return s
    chars: list[str] = []
    for ch in s:
        esc = _STR_ESCAPE_ENC.get(ch)
        if esc is not None:
            chars.append(esc)
        else:
            chars.append(ch)
    return "".join(chars)


def format_ref(ref: Ref, *, zinc: bool = False) -> str:
    """Format a :class:`~hs_py.kinds.Ref` as ``@id dis`` or ``@id "dis"``.

    :param ref: Ref to format.
    :param zinc: If ``True``, quote the display string per Zinc syntax.
    :returns: Formatted ref string.
    """
    if ref.dis is not None:
        if zinc:
            return f'@{ref.val} "{escape_str(ref.dis)}"'
        return f"@{ref.val} {ref.dis}"
    return f"@{ref.val}"


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------

#: Default unit-stop set (Trio context: collection delimiters).
_TRIO_UNIT_STOP = frozenset(" \t\n\r,]}>)")


def scan_val(text: str, pos: int, *, _depth: int = 0) -> tuple[Any, int]:
    """Scan a Zinc value starting at *pos*.

    :param text: Source text.
    :param pos: Starting position.
    :returns: ``(value, end_pos)`` tuple.
    :raises ValueError: If an unexpected character is encountered or nesting
        depth exceeds :data:`MAX_SCAN_DEPTH`.
    """
    if _depth > MAX_SCAN_DEPTH:
        msg = "Maximum value nesting depth exceeded"
        raise ValueError(msg)
    pos = skip_ws(text, pos)
    if pos >= len(text):
        return None, pos

    ch = text[pos]

    if ch == '"':
        return scan_str(text, pos)
    if ch == "@":
        return scan_ref(text, pos)
    if ch == "^":
        return scan_symbol(text, pos)
    if ch == "`":
        return scan_uri(text, pos)
    if ch == "[":
        return scan_list(text, pos, _depth=_depth + 1)
    if ch == "{":
        return scan_dict(text, pos, _depth=_depth + 1)
    if ch == "<" and pos + 1 < len(text) and text[pos + 1] == "<":
        return _scan_nested_grid(text, pos, _depth=_depth + 1)
    if ch == "-":
        rest = text[pos:]
        if rest.startswith("-INF") and (len(rest) == 4 or not rest[4].isalnum()):
            return Number(float("-inf")), pos + 4
        if pos + 1 < len(text) and text[pos + 1].isdigit():
            return scan_number_or_temporal(text, pos)
    if ch.isdigit():
        return scan_number_or_temporal(text, pos)
    if ch.isalpha():
        return scan_keyword(text, pos)

    msg = f"Unexpected character {ch!r} at position {pos}"
    raise ValueError(msg)


def scan_str(text: str, pos: int) -> tuple[str, int]:
    """Scan a Zinc quoted string starting at the opening ``"``.

    :param text: Source text.
    :param pos: Position of the opening quote.
    :returns: ``(string_value, end_pos)`` tuple.
    :raises ValueError: If the string is unterminated.
    """
    pos += 1  # skip opening "
    chars: list[str] = []
    length = 0
    while pos < len(text):
        ch = text[pos]
        if ch == "\\":
            pos += 1
            if pos >= len(text):
                raise ValueError("Unterminated string escape")
            esc = text[pos]
            if esc == "u" and pos + 4 < len(text):
                code = text[pos + 1 : pos + 5]
                chars.append(chr(int(code, 16)))
                pos += 5
            else:
                chars.append(STR_ESCAPES.get(esc, esc))
                pos += 1
        elif ch == '"':
            return "".join(chars), pos + 1
        else:
            chars.append(ch)
            pos += 1
        length += 1
        if length > MAX_STRING_LENGTH:
            msg = f"String exceeds maximum length of {MAX_STRING_LENGTH}"
            raise ValueError(msg)
    raise ValueError("Unterminated string")


def scan_ref(text: str, pos: int) -> tuple[Ref, int]:
    """Scan a Zinc :class:`~hs_py.kinds.Ref` literal starting at ``@``.

    :param text: Source text.
    :param pos: Position of the ``@`` character.
    :returns: ``(ref, end_pos)`` tuple.
    """
    pos += 1  # skip @
    start = pos
    while pos < len(text) and text[pos] in REF_CHARS:
        pos += 1
    ref_id = text[start:pos]
    # Check for display name (space then quoted string)
    save = pos
    ws_end = skip_ws(text, pos)
    if ws_end < len(text) and text[ws_end] == '"':
        dis, pos = scan_str(text, ws_end)
        return Ref(ref_id, dis), pos
    return Ref(ref_id), save


def scan_symbol(text: str, pos: int) -> tuple[Symbol, int]:
    """Scan a Zinc :class:`~hs_py.kinds.Symbol` literal starting at ``^``.

    :param text: Source text.
    :param pos: Position of the ``^`` character.
    :returns: ``(symbol, end_pos)`` tuple.
    """
    pos += 1  # skip ^
    start = pos
    while pos < len(text) and text[pos] in SYMBOL_CHARS:
        pos += 1
    return Symbol(text[start:pos]), pos


def scan_uri(text: str, pos: int) -> tuple[Uri, int]:
    """Scan a Zinc :class:`~hs_py.kinds.Uri` literal starting at back-tick.

    :param text: Source text.
    :param pos: Position of the opening back-tick.
    :returns: ``(uri, end_pos)`` tuple.
    :raises ValueError: If the URI is unterminated.
    """
    pos += 1  # skip `
    chars: list[str] = []
    length = 0
    while pos < len(text):
        ch = text[pos]
        if ch == "\\" and pos + 1 < len(text):
            chars.append(text[pos + 1])
            pos += 2
        elif ch == "`":
            return Uri("".join(chars)), pos + 1
        else:
            chars.append(ch)
            pos += 1
        length += 1
        if length > MAX_STRING_LENGTH:
            msg = f"URI exceeds maximum length of {MAX_STRING_LENGTH}"
            raise ValueError(msg)
    raise ValueError("Unterminated URI")


def scan_list(text: str, pos: int, *, _depth: int = 0) -> tuple[list[Any], int]:
    """Scan a Zinc list literal starting at ``[``.

    :param text: Source text.
    :param pos: Position of the opening ``[``.
    :returns: ``(list, end_pos)`` tuple.
    """
    pos += 1  # skip [
    items: list[Any] = []
    pos = skip_ws(text, pos)
    while pos < len(text) and text[pos] != "]":
        val, pos = scan_val(text, pos, _depth=_depth)
        items.append(val)
        pos = skip_ws(text, pos)
        if pos < len(text) and text[pos] == ",":
            pos += 1
            pos = skip_ws(text, pos)
    if pos < len(text):
        pos += 1  # skip ]
    return items, pos


def scan_dict(text: str, pos: int, *, _depth: int = 0) -> tuple[dict[str, Any], int]:
    """Scan a Zinc dict literal starting at ``{``.

    :param text: Source text.
    :param pos: Position of the opening ``{``.
    :returns: ``(dict, end_pos)`` tuple.
    """
    pos += 1  # skip {
    result: dict[str, Any] = {}
    pos = skip_ws(text, pos)
    while pos < len(text) and text[pos] != "}":
        # Read tag name
        name, pos = scan_tag_name(text, pos)
        if not name:
            msg = f"Expected tag name in dict at position {pos}"
            raise ValueError(msg)
        # Check for colon → value follows
        if pos < len(text) and text[pos] == ":":
            pos += 1
            val, pos = scan_val(text, pos, _depth=_depth)
            result[name] = val
        else:
            result[name] = MARKER
        pos = skip_ws(text, pos)
        if pos < len(text) and text[pos] == ",":
            pos += 1
            pos = skip_ws(text, pos)
    if pos < len(text):
        pos += 1  # skip }
    return result, pos


def scan_number_or_temporal(
    text: str, pos: int, *, unit_stop: frozenset[str] | None = None
) -> tuple[Any, int]:
    """Disambiguate and scan a number, date, time, or datetime.

    :param text: Source text.
    :param pos: Starting position.
    :param unit_stop: Characters that terminate a number unit.
    :returns: ``(value, end_pos)`` tuple.
    """
    rest = text[pos:]

    m = DATETIME_RE.match(rest)
    if m:
        dt = parse_datetime(m.group(0).strip())
        return dt, pos + len(m.group(0))

    m = DATE_RE.match(rest)
    if m and (pos + 10 >= len(text) or text[pos + 10] not in "0123456789T"):
        return datetime.date.fromisoformat(m.group(0)), pos + 10

    m = TIME_RE.match(rest)
    if m and len(rest) >= 3 and rest[2] == ":":
        return datetime.time.fromisoformat(m.group(0)), pos + len(m.group(0))

    return scan_number(text, pos, unit_stop=unit_stop)


def scan_number(
    text: str, pos: int, *, unit_stop: frozenset[str] | None = None
) -> tuple[Number, int]:
    """Scan a numeric literal with optional unit.

    Supports underscore digit separators per the Zinc spec (e.g. ``10_000``).

    :param text: Source text.
    :param pos: Starting position.
    :param unit_stop: Characters that terminate the unit string.
    :returns: ``(number, end_pos)`` tuple.
    """
    if unit_stop is None:
        unit_stop = _TRIO_UNIT_STOP
    start = pos
    if text[pos] == "-":
        pos += 1
    while pos < len(text) and text[pos] in DIGIT_CHARS:
        pos += 1
    if pos < len(text) and text[pos] == ".":
        pos += 1
        while pos < len(text) and text[pos] in DIGIT_CHARS:
            pos += 1
    if pos < len(text) and text[pos] in "eE":
        pos += 1
        if pos < len(text) and text[pos] in "+-":
            pos += 1
        while pos < len(text) and text[pos] in DIGIT_CHARS:
            pos += 1

    num_str = text[start:pos].replace("_", "")
    val = float(num_str)

    # Optional unit
    unit_start = pos
    while pos < len(text) and text[pos] not in unit_stop:
        pos += 1
    unit = text[unit_start:pos] if unit_start < pos else None

    return Number(val, unit), pos


def scan_tag_name(text: str, pos: int) -> tuple[str, int]:
    """Scan a tag name (alphanumeric + underscore) starting at *pos*.

    :param text: Source text.
    :param pos: Starting position.
    :returns: ``(name, end_pos)`` tuple; *name* may be empty.
    """
    start = pos
    while pos < len(text) and text[pos] in IDENT_CHARS:
        pos += 1
    return text[start:pos], pos


def scan_keyword(text: str, pos: int) -> tuple[Any, int]:
    """Scan a keyword (``T``/``F``/``M``/``NA``/…), :class:`~hs_py.kinds.Coord`, :class:`~hs_py.kinds.XStr`, or bare identifier.

    :param text: Source text.
    :param pos: Starting position (must be an alpha character).
    :returns: ``(value, end_pos)`` tuple.
    """
    name, pos = scan_tag_name(text, pos)

    # Coord: C(lat,lng) — distinguished from XStr by no quote after (
    if (
        name == "C"
        and pos < len(text)
        and text[pos] == "("
        and (pos + 1 >= len(text) or text[pos + 1] != '"')
    ):
        return _scan_coord_body(text, pos)

    # XStr: TypeName("value") — uppercase-starting name followed by ("
    if (
        name
        and name[0].isupper()
        and pos + 1 < len(text)
        and text[pos] == "("
        and text[pos + 1] == '"'
    ):
        pos += 1  # skip (
        val_str, pos = scan_str(text, pos)
        if pos < len(text) and text[pos] == ")":
            pos += 1
        return XStr(name, val_str), pos

    return _SCAN_KEYWORDS.get(name, name), pos


_SCAN_KEYWORDS: dict[str, Any] = {
    "T": True,
    "F": False,
    "M": MARKER,
    "NA": NA,
    "R": REMOVE,
    "N": None,
    "INF": Number(float("inf")),
    "NaN": Number(float("nan")),
}


# ---------------------------------------------------------------------------
# Complex type scanners
# ---------------------------------------------------------------------------


def _scan_coord_body(text: str, pos: int) -> tuple[Coord, int]:
    """Parse the ``(lat,lng)`` portion of a Coord literal."""
    pos += 1  # skip (
    lat_start = pos
    while pos < len(text) and text[pos] not in ",)":
        pos += 1
    lat = float(text[lat_start:pos])
    if pos < len(text) and text[pos] == ",":
        pos += 1  # skip ,
    lng_start = pos
    while pos < len(text) and text[pos] != ")":
        pos += 1
    lng = float(text[lng_start:pos])
    if pos < len(text):
        pos += 1  # skip )
    return Coord(lat, lng), pos


def _scan_nested_grid(text: str, pos: int, *, _depth: int = 0) -> tuple[Any, int]:
    """Scan a nested grid literal between ``<<`` and ``>>``."""
    if _depth > MAX_SCAN_DEPTH:
        msg = "Maximum nested grid depth exceeded"
        raise ValueError(msg)
    pos += 2  # skip <<
    depth = 1
    start = pos
    while pos < len(text) - 1:
        if text[pos] == "<" and text[pos + 1] == "<":
            depth += 1
            pos += 2
        elif text[pos] == ">" and text[pos + 1] == ">":
            depth -= 1
            if depth == 0:
                inner = text[start:pos].strip()
                from hs_py.encoding.zinc import decode_grid

                grid = decode_grid(inner)
                return grid, pos + 2
            pos += 2
        else:
            pos += 1
    msg = "Unterminated nested grid"
    raise ValueError(msg)
