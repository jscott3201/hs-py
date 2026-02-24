"""RDF export for Project Haystack ontology.

Serializes a :class:`~hs_py.ontology.namespace.Namespace` to Turtle or
JSON-LD using the official Project Haystack def URI scheme
(``https://project-haystack.org/def/``).

Requires ``rdflib`` — install via ``pip install hs-py[rdf]``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rdflib import URIRef  # type: ignore[import-not-found]

    from hs_py.ontology.namespace import Namespace

__all__ = [
    "export_jsonld",
    "export_turtle",
]

# Internal constant — Haystack def base URI
_PH_BASE = "https://project-haystack.org/def/"

# Tags that are handled structurally (not as plain RDF properties)
_SKIP_TAGS = frozenset({"def", "is", "doc", "lib"})


def _def_uri(symbol_val: str) -> URIRef:
    """Convert a Haystack symbol like ``ph::site`` to a URI.

    :param symbol_val: Raw symbol value (e.g. ``ph::site`` or ``lib:ph``).
    :returns: A :class:`rdflib.URIRef` for the def.
    """
    from rdflib import URIRef

    cleaned = symbol_val.replace("::", "/")
    return URIRef(f"{_PH_BASE}{cleaned}")


def _val_to_rdf_node(val: Any) -> Any:
    """Convert a Haystack tag value to an rdflib node.

    :param val: A Haystack value (Marker, Number, Ref, Symbol, str, etc.).
    :returns: An rdflib Literal, URIRef, or ``None`` if the value cannot be
        represented in RDF.
    """
    from rdflib import Literal, URIRef

    from hs_py.kinds import Marker, Number, Ref, Symbol, Uri

    if isinstance(val, Marker):
        return Literal(True)
    if isinstance(val, Number):
        v = val.val
        if math.isnan(v) or math.isinf(v):
            return Literal(str(val))
        return Literal(v)
    if isinstance(val, (Ref, Symbol)):
        return _def_uri(val.val)
    if isinstance(val, Uri):
        return URIRef(val.val)
    if isinstance(val, bool):
        return Literal(val)
    if isinstance(val, (int, float)):
        return Literal(val)
    if isinstance(val, str):
        return Literal(val)
    return None


def _build_graph(ns: Namespace) -> Any:
    """Build an RDF graph from a Haystack Namespace.

    :param ns: Resolved :class:`~hs_py.ontology.namespace.Namespace` to export.
    :returns: Populated :class:`rdflib.Graph`.
    """
    from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef

    g = Graph()

    # Bind common prefixes
    ph_ns = Namespace(_PH_BASE + "ph/")
    g.bind("ph", ph_ns)
    g.bind("rdf", RDF)
    g.bind("rdfs", RDFS)

    # ph:Def type URI used to type every def
    ph_def_type = URIRef(f"{_PH_BASE}ph/Def")

    for d in ns.all_defs():
        subject = _def_uri(d.symbol.val)

        # Every def is an instance of ph:Def
        g.add((subject, RDF.type, ph_def_type))

        # is → rdfs:subClassOf
        for parent_sym in d.is_list:
            g.add((subject, RDFS.subClassOf, _def_uri(parent_sym.val)))

        # doc → rdfs:comment
        if d.doc:
            g.add((subject, RDFS.comment, Literal(d.doc)))

        # All other non-structural tags → ph:<tagname> properties
        for tag_name, tag_val in d.tags.items():
            if tag_name in _SKIP_TAGS:
                continue

            prop_uri = URIRef(f"{_PH_BASE}ph/{tag_name}")
            vals = tag_val if isinstance(tag_val, list) else [tag_val]
            for item in vals:
                node = _val_to_rdf_node(item)
                if node is not None:
                    g.add((subject, prop_uri, node))

    return g


def export_turtle(ns: Namespace) -> str:
    """Export a Haystack namespace as Turtle RDF.

    :param ns: Resolved :class:`~hs_py.ontology.namespace.Namespace` to export.
    :returns: Turtle-formatted RDF string.
    :raises ImportError: If ``rdflib`` is not installed.
    """
    return _build_graph(ns).serialize(format="turtle")  # type: ignore[no-any-return]


def export_jsonld(ns: Namespace) -> str:
    """Export a Haystack namespace as JSON-LD.

    :param ns: Resolved :class:`~hs_py.ontology.namespace.Namespace` to export.
    :returns: JSON-LD formatted string.
    :raises ImportError: If ``rdflib`` is not installed.
    """
    return _build_graph(ns).serialize(format="json-ld")  # type: ignore[no-any-return]
