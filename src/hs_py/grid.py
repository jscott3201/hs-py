"""Haystack Grid data structure.

The Grid is the fundamental data exchange unit in the Haystack HTTP API:
a two-dimensional table with grid-level metadata, typed columns (each with
optional metadata), and rows of tag dictionaries.

See: https://project-haystack.org/doc/docHaystack/Kinds#grid
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

from hs_py.kinds import MARKER

__all__ = [
    "Col",
    "Grid",
    "GridBuilder",
]


@dataclass(frozen=True, slots=True)
class Col:
    """A single column definition within a :class:`Grid`."""

    name: str
    """Column name (must be a valid Haystack tag name)."""

    meta: dict[str, Any] = field(default_factory=dict)
    """Column-level metadata tags."""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Col name must not be empty")


@dataclass(frozen=True, slots=True)
class Grid:
    """Two-dimensional tabular data with metadata.

    Grids are immutable once constructed.  Use :class:`GridBuilder` for
    incremental construction.
    """

    meta: dict[str, Any] = field(default_factory=dict)
    """Grid-level metadata tags."""

    cols: tuple[Col, ...] = ()
    """Column definitions in display order."""

    rows: tuple[dict[str, Any], ...] = ()
    """Row data as tag dicts keyed by column name."""

    _col_map: dict[str, Col] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_col_map", {c.name: c for c in self.cols})

    @property
    def is_empty(self) -> bool:
        """``True`` if the grid has no rows."""
        return len(self.rows) == 0

    @property
    def is_error(self) -> bool:
        """``True`` if this is an error grid (meta contains ``err`` marker)."""
        return "err" in self.meta

    @property
    def col_names(self) -> tuple[str, ...]:
        """Column names in display order."""
        return tuple(self._col_map)

    def col(self, name: str) -> Col:
        """Look up a column by name.

        :param name: Column name to find.
        :returns: The matching :class:`Col` instance.
        :raises KeyError: If no column with *name* exists.
        """
        c = self._col_map.get(name)
        if c is None:
            raise KeyError(f"Column not found: {name!r}")
        return c

    def has_col(self, name: str) -> bool:
        """Check whether a column with *name* exists.

        :param name: Column name to check.
        :returns: ``True`` if the column exists.
        """
        return name in self._col_map

    def __len__(self) -> int:
        """Return the number of rows."""
        return len(self.rows)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """Iterate over rows as tag dicts."""
        return iter(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        """Return the row at *index*."""
        return self.rows[index]

    # ---- Factory methods ---------------------------------------------------

    @classmethod
    def make_empty(cls) -> Grid:
        """Return a cached empty grid with no columns or rows.

        :returns: Singleton empty :class:`Grid`.
        """
        return _EMPTY_GRID

    @classmethod
    def make_error(cls, dis: str, trace: str | None = None) -> Grid:
        """Create an error grid.

        :param dis: Human-readable error description.
        :param trace: Optional stack trace string.
        :returns: A :class:`Grid` with ``err`` marker in metadata.
        """
        meta: dict[str, Any] = {"err": MARKER, "dis": dis}
        if trace is not None:
            meta["errTrace"] = trace
        return cls(meta=meta)

    @classmethod
    def make_rows(cls, rows: list[dict[str, Any]]) -> Grid:
        """Create a grid by inferring columns from row dicts.

        Columns are ordered by first appearance across all rows.

        :param rows: List of tag dicts.
        :returns: A :class:`Grid` with columns inferred from keys.
        """
        if not rows:
            return cls.make_empty()
        seen: dict[str, None] = {}
        for row in rows:
            for key in row:
                if key not in seen:
                    seen[key] = None
        cols = tuple(Col(name=k) for k in seen)
        return cls(cols=cols, rows=tuple(rows))


_EMPTY_GRID = Grid()


class GridBuilder:
    """Mutable builder for constructing :class:`Grid` instances."""

    def __init__(self) -> None:
        self._meta: dict[str, Any] = {}
        self._cols: list[Col] = []
        self._rows: list[dict[str, Any]] = []

    def set_meta(self, meta: dict[str, Any]) -> GridBuilder:
        """Replace grid-level metadata tags.

        :param meta: Tag dict to use as grid metadata.
        :returns: *self* for chaining.
        """
        self._meta = meta
        return self

    def add_meta(self, key: str, val: Any = MARKER) -> GridBuilder:
        """Add a single metadata tag.

        :param key: Tag name.
        :param val: Tag value (defaults to :data:`~hs_py.kinds.MARKER`).
        :returns: *self* for chaining.
        """
        self._meta[key] = val
        return self

    def add_col(self, name: str, meta: dict[str, Any] | None = None) -> GridBuilder:
        """Append a column definition.

        :param name: Column name.
        :param meta: Optional column-level metadata.
        :returns: *self* for chaining.
        """
        self._cols.append(Col(name=name, meta=meta or {}))
        return self

    def add_row(self, row: dict[str, Any]) -> GridBuilder:
        """Append a data row.

        :param row: Tag dict keyed by column name.
        :returns: *self* for chaining.
        """
        self._rows.append(row)
        return self

    def to_grid(self) -> Grid:
        """Build and return an immutable :class:`Grid`.

        :returns: Constructed :class:`Grid` instance.
        """
        return Grid(
            meta=self._meta,
            cols=tuple(self._cols),
            rows=tuple(self._rows),
        )
