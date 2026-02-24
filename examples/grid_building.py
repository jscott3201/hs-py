"""Grid building and manipulation — the core data exchange unit.

Demonstrates creating grids programmatically with GridBuilder,
iterating rows, accessing metadata, and converting to pythonic dicts.

Usage::

    uv run python examples/grid_building.py
"""

from __future__ import annotations

from hs_py import MARKER, NA, Grid, GridBuilder, Number, Ref, Coord, Uri, Symbol
from hs_py import grid_to_pythonic


def main() -> None:
    # ── Build a grid with GridBuilder ──
    print("=== GridBuilder ===\n")

    grid = (
        GridBuilder()
        .set_meta({"ver": "3.0", "title": "Building Sites"})
        .add_col("id")
        .add_col("dis", meta={"format": "text"})
        .add_col("site")
        .add_col("area")
        .add_col("geoCoord")
        .add_col("tz")
        .add_row(
            {
                "id": Ref("site-1", "HQ"),
                "dis": "Headquarters",
                "site": MARKER,
                "area": Number(50000, "ft²"),
                "geoCoord": Coord(37.7749, -122.4194),
                "tz": "Los_Angeles",
            }
        )
        .add_row(
            {
                "id": Ref("site-2", "DC"),
                "dis": "Data Center",
                "site": MARKER,
                "area": Number(25000, "ft²"),
                "geoCoord": Coord(47.6062, -122.3321),
                "tz": "Los_Angeles",
            }
        )
        .to_grid()
    )

    print(f"Grid: {len(grid)} rows × {len(grid.cols)} cols")
    print(f"Meta: {dict(grid.meta)}")
    print(f"Columns: {[c.name for c in grid.cols]}\n")

    # ── Iterate rows ──
    print("=== Row Iteration ===\n")

    for i, row in enumerate(grid):
        print(f"  Row {i}: {row['dis']} ({row['id']}) — {row['area']}")

    # ── Column access ──
    print(f"\n=== Column Metadata ===\n")
    for col in grid.cols:
        meta_str = f" meta={dict(col.meta)}" if col.meta else ""
        print(f"  {col.name}{meta_str}")

    # ── Quick grid from rows ──
    print("\n=== Grid.make_rows() ===\n")

    quick = Grid.make_rows(
        [
            {"name": "read", "summary": "Read entity records"},
            {"name": "nav", "summary": "Navigate site structure"},
            {"name": "about", "summary": "Server information"},
        ]
    )
    print(f"Quick grid: {len(quick)} rows")
    for row in quick:
        print(f"  {row['name']}: {row['summary']}")

    # ── Pythonic conversion ──
    print("\n=== Pythonic Conversion ===\n")

    pythonic = grid_to_pythonic(grid)
    for d in pythonic:
        print(f"  {d}")

    # ── Haystack scalar types ──
    print("\n=== Haystack Types ===\n")

    types = [
        ("Marker", MARKER),
        ("NA", NA),
        ("Number", Number(72.5, "°F")),
        ("Ref", Ref("ahu-1", "AHU-1")),
        ("Coord", Coord(40.7128, -74.0060)),
        ("Uri", Uri("https://example.com")),
        ("Symbol", Symbol("hot-water")),
    ]

    for name, val in types:
        print(f"  {name:10s}  repr={val!r}")


if __name__ == "__main__":
    main()
