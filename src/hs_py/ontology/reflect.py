"""Reflection engine for mapping entity dicts to ontology defs.

Reflection determines which defs apply to an entity based on its tag set.
This includes detecting simple marker matches, conjunct matches, and
computing the full effective def set.

See: https://project-haystack.org/doc/docHaystack/Reflection
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hs_py.kinds import Symbol, sym_name
from hs_py.ontology.taxonomy import is_conjunct, resolve_conjunct_parts

if TYPE_CHECKING:
    from hs_py.ontology.defs import Def
    from hs_py.ontology.namespace import Namespace

__all__ = [
    "fits",
    "reflect",
]


def reflect(ns: Namespace, tags: dict[str, Any]) -> list[Def]:
    """Return all defs that apply to an entity with the given tags.

    The algorithm:
    1. Scan the entity's tags for marker tags (tags whose value is Marker).
    2. For each marker, look up the matching def in the namespace.
    3. Check for conjuncts (compound terms matching tag combinations).
    4. Include all transitive supertypes.

    :param ns: Namespace to resolve defs in.
    :param tags: Entity tag dict.
    :returns: List of applicable defs (most-specific first).
    """
    from hs_py.kinds import Marker

    # Step 1: Find directly matching marker defs
    direct: list[Def] = []
    marker_names: set[str] = set()
    for key, val in tags.items():
        if isinstance(val, Marker):
            marker_names.add(key)
            d = ns.get(key)
            if d is not None:
                direct.append(d)

    # Step 2: Check for conjuncts
    conjuncts = _find_conjuncts(ns, marker_names)
    direct.extend(conjuncts)

    # Step 3: Collect all supertypes
    seen: set[str] = set()
    result: list[Def] = []

    # Add direct matches first (most-specific)
    for d in direct:
        if d.symbol.val not in seen:
            seen.add(d.symbol.val)
            result.append(d)

    # Add all transitive supertypes
    for d in list(result):
        for sup in ns.all_supertypes(d.symbol.val):
            if sup.symbol.val not in seen:
                seen.add(sup.symbol.val)
                result.append(sup)

    return result


def fits(ns: Namespace, tags: dict[str, Any], def_symbol: str | Symbol) -> bool:
    """Check if an entity's tags fit a given def.

    An entity fits a def if the def (or one of its subtypes) appears in
    the entity's reflected def set.

    :param ns: Namespace to resolve defs in.
    :param tags: Entity tag dict.
    :param def_symbol: Def symbol to check against.
    :returns: ``True`` if the entity fits the def.
    """
    target = sym_name(def_symbol)
    defs = reflect(ns, tags)
    return any(d.symbol.val == target or d.name == target for d in defs)


def _find_conjuncts(ns: Namespace, marker_names: set[str]) -> list[Def]:
    """Find conjunct defs whose parts are all present in the marker set.

    For example, if ``marker_names`` contains ``hot`` and ``water``,
    and the namespace has a ``hot-water`` def, it will be returned.
    """
    conjuncts: list[Def] = []
    for d in ns.all_defs():
        name = d.symbol.val
        if not is_conjunct(name):
            continue
        # Already matched directly
        if name in marker_names:
            continue
        parts = resolve_conjunct_parts(name)
        if all(p in marker_names for p in parts):
            conjuncts.append(d)
    return conjuncts
