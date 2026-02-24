"""Filter expressions — parse, inspect, and evaluate Haystack filters.

Demonstrates the filter engine for querying tagged entity data using
the standard Haystack filter syntax.

Usage::

    uv run python examples/filter_eval.py
"""

from __future__ import annotations

from hs_py import MARKER, Number, Ref
from hs_py.filter.eval import evaluate, evaluate_grid
from hs_py.filter.parser import parse
from hs_py.grid import GridBuilder

# ---------------------------------------------------------------------------
# Sample data — a small building model
# ---------------------------------------------------------------------------

ENTITIES = [
    {
        "id": Ref("site-1"),
        "dis": "Main Office",
        "site": MARKER,
        "area": Number(50000, "ft²"),
    },
    {
        "id": Ref("ahu-1"),
        "dis": "AHU-1",
        "equip": MARKER,
        "ahu": MARKER,
        "siteRef": Ref("site-1"),
    },
    {
        "id": Ref("znt-1"),
        "dis": "Zone Temp",
        "point": MARKER,
        "sensor": MARKER,
        "temp": MARKER,
        "zone": MARKER,
        "air": MARKER,
        "kind": "Number",
        "unit": "°F",
        "curVal": Number(72.4, "°F"),
        "siteRef": Ref("site-1"),
        "equipRef": Ref("ahu-1"),
    },
    {
        "id": Ref("dat-1"),
        "dis": "Discharge Air Temp",
        "point": MARKER,
        "sensor": MARKER,
        "temp": MARKER,
        "discharge": MARKER,
        "air": MARKER,
        "kind": "Number",
        "unit": "°F",
        "curVal": Number(55.0, "°F"),
        "siteRef": Ref("site-1"),
        "equipRef": Ref("ahu-1"),
    },
    {
        "id": Ref("cmd-1"),
        "dis": "Fan Command",
        "point": MARKER,
        "cmd": MARKER,
        "fan": MARKER,
        "kind": "Bool",
        "siteRef": Ref("site-1"),
        "equipRef": Ref("ahu-1"),
    },
]


def main() -> None:
    print("=== Filter Parsing ===\n")

    filters = [
        "site",
        "point and temp",
        "point and sensor and not cmd",
        'equip and siteRef == @site-1',
        "point and curVal > 60°F",
    ]

    for filt_str in filters:
        ast = parse(filt_str)
        print(f"  {filt_str!r}")
        print(f"    AST: {ast}\n")

    print("=== Evaluate Against Dicts ===\n")

    for filt_str in filters:
        ast = parse(filt_str)
        matches = [e for e in ENTITIES if evaluate(ast, e)]
        names = [m.get("dis", "?") for m in matches]
        print(f"  {filt_str!r}  →  {names}")

    print("\n=== Evaluate Against Grid ===\n")

    # Build a grid from the entities
    all_cols = sorted({k for e in ENTITIES for k in e})
    builder = GridBuilder()
    for col in all_cols:
        builder.add_col(col)
    for entity in ENTITIES:
        builder.add_row(entity)
    grid = builder.to_grid()

    result = evaluate_grid(parse("point and temp and curVal > 60°F"), grid)
    print(f"  'point and temp and curVal > 60°F'  →  {len(result)} rows")
    for row in result:
        print(f"    {row['dis']} = {row.get('curVal', '?')}")


if __name__ == "__main__":
    main()
