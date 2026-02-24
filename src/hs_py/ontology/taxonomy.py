"""Taxonomy tree operations over the def hierarchy.

Provides utilities for working with the ``is`` tag inheritance tree,
conjunct detection, and tag inheritance through the taxonomy.

See: https://project-haystack.org/doc/docHaystack/Subtyping
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hs_py.kinds import Symbol, sym_name

if TYPE_CHECKING:
    from hs_py.ontology.namespace import Namespace

__all__ = [
    "effective_tags",
    "is_conjunct",
    "marker_tags",
    "resolve_conjunct_parts",
    "tag_on_defs",
]


def is_conjunct(symbol: str | Symbol) -> bool:
    """Check if a symbol is a conjunct (compound term with ``-``).

    Conjuncts like ``hot-water`` are composed from their dash-separated parts.
    """
    return "-" in sym_name(symbol)


def resolve_conjunct_parts(symbol: str | Symbol) -> list[str]:
    """Split a conjunct symbol into its component part names.

    :param symbol: e.g. ``hot-water-plant``
    :returns: e.g. ``["hot", "water", "plant"]``
    """
    return sym_name(symbol).split("-")


def effective_tags(ns: Namespace, symbol: str | Symbol) -> dict[str, Any]:
    """Compute the effective tag set for a def, inheriting from supertypes.

    Walks up the taxonomy tree and merges tags from all supertypes.
    Tags defined on the def itself take precedence over inherited tags.

    :param ns: Namespace to resolve symbols in.
    :param symbol: Def symbol to compute tags for.
    :returns: Merged tag dict.
    """
    d = ns.get(symbol)
    if d is None:
        return {}

    # Collect all supertypes (most-specific first via BFS)
    all_supers = ns.all_supertypes(symbol)

    # Start with inherited tags (least-specific first = reverse order)
    merged: dict[str, Any] = {}
    for sup in reversed(all_supers):
        for key, val in sup.tags.items():
            if key not in ("def", "is", "lib"):
                merged[key] = val

    # Apply own tags last (highest precedence)
    for key, val in d.tags.items():
        if key not in ("def", "is", "lib"):
            merged[key] = val

    return merged


def marker_tags(ns: Namespace, symbol: str | Symbol) -> set[str]:
    """Return the set of marker tag names for a def and all its supertypes.

    This is useful for reflection: determining which marker defs an entity
    implements based on its tag set.

    :param ns: Namespace to resolve symbols in.
    :param symbol: Def symbol.
    :returns: Set of marker tag names.
    """
    markers: set[str] = set()
    d = ns.get(symbol)
    if d is None:
        return markers
    markers.add(d.name)
    for sup in ns.all_supertypes(symbol):
        markers.add(sup.name)
    return markers


def tag_on_defs(ns: Namespace, tag: str | Symbol) -> list[str]:
    """Return the entity def names that a tag applies to via ``tagOn``.

    :param ns: Namespace to resolve symbols in.
    :param tag: Tag name to look up.
    :returns: List of entity def names.
    """
    tag_name = sym_name(tag)
    d = ns.get(tag_name)
    if d is None:
        return []
    tag_on = d.tags.get("tagOn")
    if tag_on is None:
        return []
    if isinstance(tag_on, Symbol):
        return [tag_on.val]
    if isinstance(tag_on, list):
        return [s.val for s in tag_on if isinstance(s, Symbol)]
    return []
