"""Haystack ontology model.

Data model for defs, libs, and namespace resolution over the
Haystack taxonomy.

Usage::

    from hs_py.ontology import Def, Lib, Namespace, load_lib_from_trio

    lib = load_lib_from_trio(lib_trio_text, [defs_trio_text])
    ns = Namespace([lib])
    site_def = ns.get("site")
"""

from hs_py.ontology.defs import Def, Lib
from hs_py.ontology.namespace import (
    Namespace,
    load_defs_from_trio,
    load_lib_from_trio,
)
from hs_py.ontology.normalize import NormalizeError, compile_namespace
from hs_py.ontology.reflect import fits, reflect
from hs_py.ontology.taxonomy import (
    effective_tags,
    is_conjunct,
    marker_tags,
    resolve_conjunct_parts,
    tag_on_defs,
)

__all__ = [
    "Def",
    "Lib",
    "Namespace",
    "NormalizeError",
    "compile_namespace",
    "effective_tags",
    "export_jsonld",
    "export_turtle",
    "fits",
    "is_conjunct",
    "load_defs_from_trio",
    "load_lib_from_trio",
    "marker_tags",
    "reflect",
    "resolve_conjunct_parts",
    "tag_on_defs",
]

_RDF_NAMES = frozenset({"export_turtle", "export_jsonld"})


def __getattr__(name: str) -> object:
    """Lazily import optional RDF functions to avoid hard rdflib dependency."""
    if name in _RDF_NAMES:
        from hs_py.ontology.rdf import export_jsonld, export_turtle

        if name == "export_turtle":
            return export_turtle
        return export_jsonld
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
