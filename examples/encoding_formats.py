"""Encoding formats — convert grids between JSON, Zinc, Trio, and CSV.

Demonstrates encoding and decoding Haystack data in all four wire
formats supported by the library.

Usage::

    uv run python examples/encoding_formats.py
"""

from __future__ import annotations

from hs_py import MARKER, Grid, GridBuilder, Number, Ref
from hs_py.encoding import csv as hs_csv
from hs_py.encoding import json as hs_json
from hs_py.encoding import trio as hs_trio
from hs_py.encoding import zinc as hs_zinc
from hs_py.encoding.json import JsonVersion


def build_sample_grid() -> Grid:
    """Build a sample grid with building data."""
    return (
        GridBuilder()
        .set_meta({"ver": "3.0", "view": "sites"})
        .add_col("id")
        .add_col("dis")
        .add_col("site")
        .add_col("area")
        .add_col("geoAddr")
        .add_row(
            {
                "id": Ref("site-1", "Main Office"),
                "dis": "Main Office",
                "site": MARKER,
                "area": Number(50000, "ft²"),
                "geoAddr": "123 Main St",
            }
        )
        .add_row(
            {
                "id": Ref("site-2", "Warehouse"),
                "dis": "Warehouse",
                "site": MARKER,
                "area": Number(120000, "ft²"),
                "geoAddr": "456 Industrial Ave",
            }
        )
        .to_grid()
    )


def main() -> None:
    grid = build_sample_grid()
    print(f"Grid: {len(grid)} rows × {len(grid.cols)} cols\n")

    # ── JSON v4 (default) ──
    json_bytes = hs_json.encode_grid(grid)
    print("=== JSON v4 ===")
    print(json_bytes.decode()[:200], "...\n")

    decoded = hs_json.decode_grid(json_bytes)
    print(f"Round-trip: {len(decoded)} rows ✓\n")

    # ── JSON v3 ──
    json3_bytes = hs_json.encode_grid(grid, version=JsonVersion.V3)
    print("=== JSON v3 ===")
    print(json3_bytes.decode()[:200], "...\n")

    # ── Zinc ──
    zinc_text = hs_zinc.encode_grid(grid)
    print("=== Zinc ===")
    print(zinc_text[:200], "...\n")

    zinc_decoded = hs_zinc.decode_grid(zinc_text)
    print(f"Round-trip: {len(zinc_decoded)} rows ✓\n")

    # ── Trio ──
    # Trio encodes list of dicts (records), not full grids
    records = list(grid)
    trio_text = hs_trio.encode_trio(records)
    print("=== Trio ===")
    print(trio_text[:200], "...\n")

    trio_decoded = hs_trio.parse_trio(trio_text)
    print(f"Round-trip: {len(trio_decoded)} records ✓\n")

    # ── CSV (encode-only, lossy) ──
    csv_text = hs_csv.encode_grid(grid)
    print("=== CSV ===")
    print(csv_text)

    # ── Scalar values ──
    print("=== Scalar Encoding ===")
    for val in [MARKER, Number(72.5, "°F"), Ref("ahu-1", "AHU-1")]:
        j = hs_json.encode_val(val)
        z = hs_zinc.encode_val(val)
        print(f"  {val!r:40s}  json={j!r:30s}  zinc={z}")


if __name__ == "__main__":
    main()
