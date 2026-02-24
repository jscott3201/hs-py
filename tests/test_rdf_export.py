"""Tests for the RDF ontology export module (hs_py.ontology.rdf).

These tests are skipped automatically when rdflib is not installed.
"""

from __future__ import annotations

import pytest

from hs_py.kinds import Symbol
from hs_py.ontology.defs import Def, Lib
from hs_py.ontology.namespace import Namespace

try:
    import rdflib  # noqa: F401

    _HAS_RDFLIB = True
except ImportError:
    _HAS_RDFLIB = False

pytestmark = [
    pytest.mark.skipif(not _HAS_RDFLIB, reason="rdflib not installed"),
]


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ns() -> Namespace:
    marker_def = Def.from_tags(
        {
            "def": Symbol("ph::marker"),
            "is": [],
            "doc": "Marker tag",
        }
    )
    entity_def = Def.from_tags(
        {
            "def": Symbol("ph::entity"),
            "is": [Symbol("ph::marker")],
            "doc": "Base entity type",
        }
    )
    site_def = Def.from_tags(
        {
            "def": Symbol("ph::site"),
            "is": [Symbol("ph::entity")],
            "doc": "A geographic site",
            "dis": "Site",
        }
    )
    equip_def = Def.from_tags(
        {
            "def": Symbol("ph::equip"),
            "is": [Symbol("ph::entity")],
            "doc": "Equipment asset",
        }
    )
    lib = Lib.from_meta(
        {"def": Symbol("lib:ph"), "version": "4.0"},
        [marker_def, entity_def, site_def, equip_def],
    )
    return Namespace([lib])


@pytest.fixture()
def empty_ns() -> Namespace:
    return Namespace()


# ---------------------------------------------------------------------------
# Turtle tests
# ---------------------------------------------------------------------------


def test_export_turtle_contains_prefixes(ns: Namespace) -> None:
    from hs_py.ontology.rdf import export_turtle

    output = export_turtle(ns)
    assert "@prefix" in output


def test_export_turtle_contains_defs(ns: Namespace) -> None:
    from hs_py.ontology.rdf import export_turtle

    output = export_turtle(ns)
    assert "ph/site" in output
    assert "ph/equip" in output


def test_export_turtle_subclass(ns: Namespace) -> None:
    from hs_py.ontology.rdf import export_turtle

    output = export_turtle(ns)
    assert "subClassOf" in output


def test_export_turtle_doc(ns: Namespace) -> None:
    from hs_py.ontology.rdf import export_turtle

    output = export_turtle(ns)
    assert "A geographic site" in output


# ---------------------------------------------------------------------------
# JSON-LD tests
# ---------------------------------------------------------------------------


def test_export_jsonld_valid_json(ns: Namespace) -> None:
    import orjson

    from hs_py.ontology.rdf import export_jsonld

    output = export_jsonld(ns)
    parsed = orjson.loads(output)
    assert parsed is not None


def test_export_jsonld_has_types(ns: Namespace) -> None:
    import orjson

    from hs_py.ontology.rdf import export_jsonld

    output = export_jsonld(ns)
    parsed = orjson.loads(output)

    # JSON-LD may be a list of objects or a single object with @graph
    all_items = parsed if isinstance(parsed, list) else parsed.get("@graph", [parsed])

    type_values = []
    for item in all_items:
        if isinstance(item, dict):
            t = item.get("@type")
            if t is not None:
                type_values.append(t)

    assert type_values, "No @type entries found in JSON-LD output"


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


def test_empty_namespace(empty_ns: Namespace) -> None:
    import orjson

    from hs_py.ontology.rdf import export_jsonld, export_turtle

    turtle_out = export_turtle(empty_ns)
    assert isinstance(turtle_out, str)

    jsonld_out = export_jsonld(empty_ns)
    assert isinstance(jsonld_out, str)

    parsed = orjson.loads(jsonld_out)
    assert parsed is not None


def test_def_tags_as_properties(ns: Namespace) -> None:
    from hs_py.ontology.rdf import export_turtle

    # "dis" tag on site_def should appear as a property
    output = export_turtle(ns)
    # The "dis" tag with value "Site" should be present somewhere in output
    assert "Site" in output


# ---------------------------------------------------------------------------
# Top-level import convenience
# ---------------------------------------------------------------------------


def test_top_level_import() -> None:
    import hs_py

    assert callable(hs_py.export_turtle)
    assert callable(hs_py.export_jsonld)


def test_ontology_package_import() -> None:
    import hs_py.ontology as ont

    assert callable(ont.export_turtle)
    assert callable(ont.export_jsonld)
