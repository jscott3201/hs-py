"""Tests for RDF export coverage gaps."""

from __future__ import annotations

from hs_py.kinds import MARKER, Number, Ref, Symbol, Uri
from hs_py.ontology.defs import Def
from hs_py.ontology.rdf import _val_to_rdf_node, export_jsonld, export_turtle


class _FakeNamespace:
    """Minimal mock namespace with defs for RDF export."""

    def all_defs(self) -> list[Def]:
        return [
            Def(
                symbol=Symbol("ph::site"),
                tags={
                    "is": Symbol("ph::entity"),
                    "doc": "Site marker",
                    "mandatory": MARKER,
                    "area": Number(100.0, "ft²"),
                    "geoCoord": "string-tag",
                    "link": Uri("http://example.com"),
                    "siteRef": Ref("s1"),
                    "flag": True,
                    "count": 42,
                    "ratio": 3.14,
                    "special": Number(float("nan")),
                },
            ),
            Def(
                symbol=Symbol("ph::equip"),
                tags={"is": Symbol("ph::entity")},
            ),
        ]


class TestValToRdfNode:
    """Cover _val_to_rdf_node branches."""

    def test_marker(self) -> None:
        node = _val_to_rdf_node(MARKER)
        assert node.toPython() is True

    def test_number(self) -> None:
        node = _val_to_rdf_node(Number(42.0))
        assert node.toPython() == 42.0

    def test_nan_number(self) -> None:
        node = _val_to_rdf_node(Number(float("nan")))
        assert isinstance(node.toPython(), str)

    def test_inf_number(self) -> None:
        node = _val_to_rdf_node(Number(float("inf")))
        assert isinstance(node.toPython(), str)

    def test_ref(self) -> None:
        node = _val_to_rdf_node(Ref("abc"))
        assert "abc" in str(node)

    def test_symbol(self) -> None:
        node = _val_to_rdf_node(Symbol("site"))
        assert "site" in str(node)

    def test_uri(self) -> None:
        node = _val_to_rdf_node(Uri("http://example.com"))
        assert "example.com" in str(node)

    def test_bool(self) -> None:
        node = _val_to_rdf_node(True)
        assert node.toPython() is True

    def test_int(self) -> None:
        node = _val_to_rdf_node(42)
        assert node.toPython() == 42

    def test_float(self) -> None:
        node = _val_to_rdf_node(3.14)
        assert abs(node.toPython() - 3.14) < 0.01

    def test_string(self) -> None:
        node = _val_to_rdf_node("hello")
        assert node.toPython() == "hello"

    def test_unsupported_returns_none(self) -> None:
        assert _val_to_rdf_node(object()) is None


class TestRdfExport:
    """Cover export_turtle and export_jsonld."""

    def test_export_turtle(self) -> None:
        result = export_turtle(_FakeNamespace())  # type: ignore[arg-type]
        assert "site" in result
        assert "subClassOf" in result

    def test_export_jsonld(self) -> None:
        result = export_jsonld(_FakeNamespace())  # type: ignore[arg-type]
        assert "site" in result
