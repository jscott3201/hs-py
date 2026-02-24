"""Haystack data type kinds.

Implements all Project Haystack scalar and singleton value types as
immutable Python objects. Collection types (list, dict) and passthrough
types (bool, str, datetime.date, datetime.time, datetime.datetime) use
their native Python equivalents directly.

See: https://project-haystack.org/doc/docHaystack/Kinds
"""

from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MARKER",
    "NA",
    "REMOVE",
    "Coord",
    "Marker",
    "Na",
    "Number",
    "Ref",
    "Remove",
    "Symbol",
    "Uri",
    "XStr",
    "is_haystack_type",
    "sym_name",
]


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------


class _Singleton:
    """Base for singleton kind types."""

    _instance: _Singleton | None = None

    def __new__(cls) -> _Singleton:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return self.__class__.__name__

    def __bool__(self) -> bool:
        return True

    def __hash__(self) -> int:
        return hash(self.__class__)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, self.__class__)


class Marker(_Singleton):
    """Marker tag singleton.

    Marker is a label type used to express typing information.
    """

    _instance: Marker | None = None

    def __str__(self) -> str:
        return "\u2713"


class Na(_Singleton):
    """NA (not available) singleton.

    Represents missing or invalid data, analogous to R's ``NA``.
    """

    _instance: Na | None = None

    def __str__(self) -> str:
        return "NA"


class Remove(_Singleton):
    """Remove singleton.

    Indicates a tag should be removed from a dict.
    """

    _instance: Remove | None = None

    def __str__(self) -> str:
        return "remove"


#: Canonical Marker instance.
MARKER: Marker = Marker()

#: Canonical NA instance.
NA: Na = Na()

#: Canonical Remove instance.
REMOVE: Remove = Remove()


# ---------------------------------------------------------------------------
# Scalar types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Number:
    """Numeric value with optional unit of measurement.

    Supports special IEEE 754 values: ``NaN``, ``INF``, ``-INF``.
    """

    val: float
    """Numeric value (may be ``NaN``, ``INF``, or ``-INF``)."""

    unit: str | None = None
    """Optional unit of measurement (e.g. ``"°F"``, ``"kW"``)."""

    def __post_init__(self) -> None:
        if self.unit is not None and (self.unit == "" or math.isnan(self.val)):
            object.__setattr__(self, "unit", None)

    def __str__(self) -> str:
        if math.isnan(self.val):
            return "NaN"
        if math.isinf(self.val):
            return "INF" if self.val > 0 else "-INF"
        s = _format_float(self.val)
        if self.unit is not None:
            s += self.unit
        return s

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Number):
            if math.isnan(self.val) and math.isnan(other.val):
                return self.unit == other.unit
            return self.val == other.val and self.unit == other.unit
        return NotImplemented

    def __hash__(self) -> int:
        v = 0 if math.isnan(self.val) else self.val
        return hash((v, self.unit))


# Haystack Ref identifier: ASCII letters, digits, underbar, colon, dash, dot, tilde.
_REF_VAL_RE = re.compile(r"^[a-zA-Z0-9_:\-.~]+$")


@dataclass(frozen=True, slots=True)
class Ref:
    """Entity reference identifier.

    ``val`` is an opaque identifier string containing only ASCII letters,
    digits, underbar, colon, dash, period, and tilde.
    """

    val: str
    """Opaque identifier string (ASCII letters, digits, ``_:-.~``)."""

    dis: str | None = None
    """Optional human-readable display name."""

    def __post_init__(self) -> None:
        if not self.val:
            raise ValueError("Ref val must not be empty")
        if not _REF_VAL_RE.match(self.val):
            raise ValueError(f"Ref val contains invalid characters: {self.val!r}")

    def __str__(self) -> str:
        if self.dis is not None:
            return f"@{self.val} {self.dis!r}"
        return f"@{self.val}"


@dataclass(frozen=True, slots=True)
class Symbol:
    """Definition name constant (e.g. ``^elec-meter``)."""

    val: str
    """Symbol name without the leading ``^``."""

    def __post_init__(self) -> None:
        if not self.val:
            raise ValueError("Symbol val must not be empty")

    def __str__(self) -> str:
        return f"^{self.val}"


@dataclass(frozen=True, slots=True)
class Uri:
    """Universal Resource Identifier per RFC 3986."""

    val: str
    """URI string value."""

    def __str__(self) -> str:
        return f"`{self.val}`"


@dataclass(frozen=True, slots=True)
class Coord:
    """Geographic coordinate as latitude/longitude in decimal degrees."""

    lat: float
    """Latitude in decimal degrees (``-90`` to ``90``)."""

    lng: float
    """Longitude in decimal degrees (``-180`` to ``180``)."""

    def __post_init__(self) -> None:
        if not (-90 <= self.lat <= 90):
            raise ValueError(f"lat must be -90..90, got {self.lat}")
        if not (-180 <= self.lng <= 180):
            raise ValueError(f"lng must be -180..180, got {self.lng}")

    def __str__(self) -> str:
        return f"C({self.lat},{self.lng})"


@dataclass(frozen=True, slots=True)
class XStr:
    """Extended string: a typed string tuple.

    ``type_name`` must start with an uppercase ASCII letter.
    """

    type_name: str
    """Type name (must start with an uppercase ASCII letter)."""

    val: str
    """String payload."""

    def __post_init__(self) -> None:
        if not self.type_name or not self.type_name[0].isupper():
            raise ValueError(
                f"XStr type_name must start with uppercase letter, got {self.type_name!r}"
            )

    def __str__(self) -> str:
        return f'{self.type_name}("{self.val}")'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sym_name(s: str | Symbol) -> str:
    """Normalize a symbol argument to its string name.

    :param s: A :class:`Symbol` or plain string.
    :returns: The bare symbol name string.
    """
    return s.val if isinstance(s, Symbol) else s


def _format_float(v: float) -> str:
    """Format a float, dropping trailing zeros."""
    if v == int(v) and not math.isinf(v):
        return str(int(v))
    return f"{v:g}"


_HAYSTACK_TYPES: tuple[type, ...] | None = None


def is_haystack_type(val: Any) -> bool:
    """Return ``True`` if *val* is a valid Haystack value kind.

    :param val: Value to check.
    :returns: Whether *val* is ``None`` or an instance of a Haystack kind.
    """
    global _HAYSTACK_TYPES
    if _HAYSTACK_TYPES is None:
        from hs_py.grid import Grid

        _HAYSTACK_TYPES = (
            Marker,
            Na,
            Remove,
            Number,
            Ref,
            Symbol,
            Uri,
            Coord,
            XStr,
            bool,
            int,
            float,
            str,
            datetime.date,
            datetime.time,
            datetime.datetime,
            list,
            dict,
            Grid,
        )
    return val is None or isinstance(val, _HAYSTACK_TYPES)
