"""Ontology — build a namespace, query the taxonomy, and reflect entities.

Demonstrates constructing Haystack ontology definitions, compiling a
namespace, querying the taxonomy tree, reflecting tagged dicts, and
exporting to RDF Turtle format.

Usage::

    uv run python examples/ontology_demo.py
"""

from __future__ import annotations

from hs_py import MARKER, Ref, Symbol
from hs_py.ontology.defs import Def, Lib
from hs_py.ontology.normalize import compile_namespace
from hs_py.ontology.reflect import fits, reflect
from hs_py.ontology.rdf import export_turtle


# ---------------------------------------------------------------------------
# Build a small ontology inline
# ---------------------------------------------------------------------------

def build_demo_lib() -> Lib:
    """Create a minimal Haystack-like ontology for demonstration."""
    defs = [
        Def(Symbol("entity"), {"def": Symbol("entity"), "doc": "Base entity"}),
        Def(Symbol("site"), {"def": Symbol("site"), "is": Symbol("entity"), "doc": "A building site"}),
        Def(Symbol("equip"), {"def": Symbol("equip"), "is": Symbol("entity"), "doc": "Equipment"}),
        Def(Symbol("point"), {"def": Symbol("point"), "is": Symbol("entity"), "doc": "A data point"}),
        Def(Symbol("ahu"), {"def": Symbol("ahu"), "is": Symbol("equip"), "doc": "Air handling unit"}),
        Def(Symbol("vav"), {"def": Symbol("vav"), "is": Symbol("equip"), "doc": "Variable air volume box"}),
        Def(Symbol("meter"), {"def": Symbol("meter"), "is": Symbol("equip"), "doc": "Metering device"}),
        Def(Symbol("sensor"), {"def": Symbol("sensor"), "is": Symbol("point"), "doc": "Sensor point"}),
        Def(Symbol("cmd"), {"def": Symbol("cmd"), "is": Symbol("point"), "doc": "Command point"}),
        Def(Symbol("temp"), {"def": Symbol("temp"), "doc": "Temperature"}),
        Def(Symbol("zone"), {"def": Symbol("zone"), "doc": "Zone space"}),
        Def(Symbol("air"), {"def": Symbol("air"), "doc": "Air substance"}),
        Def(Symbol("discharge"), {"def": Symbol("discharge"), "doc": "Discharge from equipment"}),
    ]
    return Lib(
        symbol=Symbol("lib:demo"),
        version="1.0.0",
        defs=tuple(defs),
    )


def main() -> None:
    # ── Build and compile namespace ──
    lib = build_demo_lib()
    print(f"Library: {lib.symbol.val} — {len(lib.defs)} defs")

    ns = compile_namespace([lib])
    print(f"Namespace: {ns.def_count} resolved defs\n")

    # ── Query the taxonomy ──
    print("=== Taxonomy Queries ===\n")

    equip_subtypes = ns.subtypes("equip")
    print(f"Subtypes of 'equip' ({len(equip_subtypes)}):")
    for d in sorted(equip_subtypes, key=lambda d: d.symbol.val):
        print(f"  {d.symbol.val}: {d.doc}")

    point_subtypes = ns.subtypes("point")
    print(f"\nSubtypes of 'point' ({len(point_subtypes)}):")
    for d in sorted(point_subtypes, key=lambda d: d.symbol.val):
        print(f"  {d.symbol.val}: {d.doc}")

    # Inheritance checks
    print(f"\nahu is subtype of equip? {ns.is_subtype('ahu', 'equip')}")
    print(f"site is subtype of entity? {ns.is_subtype('site', 'entity')}")
    print(f"sensor is subtype of point? {ns.is_subtype('sensor', 'point')}")
    print(f"ahu is subtype of point? {ns.is_subtype('ahu', 'point')}")

    # ── Reflect a tagged dict ──
    print("\n=== Reflection ===\n")

    entity = {
        "id": Ref("ahu-1"),
        "dis": "AHU-1",
        "equip": MARKER,
        "ahu": MARKER,
        "siteRef": Ref("site-1"),
    }

    reflected = reflect(ns, entity)
    marker_tags = sorted(k for k, v in entity.items() if v is MARKER)
    reflected_names = sorted(d.symbol.val for d in reflected)
    print(f"Entity marker tags: {marker_tags}")
    print(f"Reflected defs:     {reflected_names}")

    print(f"\nFits 'equip'? {fits(ns, entity, 'equip')}")
    print(f"Fits 'ahu'?   {fits(ns, entity, 'ahu')}")
    print(f"Fits 'point'? {fits(ns, entity, 'point')}")

    # ── RDF Export ──
    print("\n=== RDF Turtle Export (snippet) ===\n")

    turtle = export_turtle(ns)
    lines = turtle.split("\n")
    for line in lines[:15]:
        print(f"  {line}")
    if len(lines) > 15:
        print(f"  ... ({len(lines)} total lines)")


if __name__ == "__main__":
    main()
