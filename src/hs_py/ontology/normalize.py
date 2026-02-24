"""Normalization pipeline for compiling raw defs into a resolved namespace.

Implements a simplified version of the Haystack normalization pipeline:

1. Parse: convert Trio text to raw tag dicts
2. Resolve: create Def objects from parsed records
3. Taxonify: compute conjunct supertypes
4. Inherit: propagate tags down the taxonomy tree
5. Validate: check for missing references and cycles

See: https://project-haystack.org/doc/docHaystack/Normalization
"""

from __future__ import annotations

from collections import deque

from hs_py.kinds import Symbol
from hs_py.ontology.defs import Def, Lib
from hs_py.ontology.namespace import Namespace
from hs_py.ontology.taxonomy import is_conjunct, resolve_conjunct_parts

__all__ = [
    "NormalizeError",
    "compile_namespace",
]


class NormalizeError(ValueError):
    """Raised when normalization encounters an error."""


def compile_namespace(libs: list[Lib]) -> Namespace:
    """Run the normalization pipeline on a set of libs.

    :param libs: Libraries to compile.
    :returns: Fully resolved Namespace.
    :raises NormalizeError: If validation fails.
    """
    # Step 1: Collect all defs across all libs
    all_defs: list[Def] = []
    for lib in libs:
        all_defs.extend(lib.defs)

    # Step 2: Build initial name index
    by_name: dict[str, Def] = {}
    for d in all_defs:
        by_name[d.symbol.val] = d
        if d.name not in by_name:
            by_name[d.name] = d

    # Step 3: Taxonify — generate conjunct supertypes
    all_defs = _taxonify(all_defs, by_name)

    # Step 4: Rebuild libs with updated defs
    normalized_libs = _rebuild_libs(libs, all_defs)

    # Step 5: Build namespace
    ns = Namespace(normalized_libs)

    # Step 6: Validate
    _validate(ns)

    return ns


def _taxonify(defs: list[Def], by_name: dict[str, Def]) -> list[Def]:
    """Compute conjunct supertypes for compound terms.

    For a conjunct like ``hot-water``, its ``is`` tag should include the
    individual parts (``hot``, ``water``) as supertypes if they exist as defs.
    """
    updated: list[Def] = []
    for d in defs:
        if is_conjunct(d.symbol.val):
            parts = resolve_conjunct_parts(d.symbol.val)
            existing_is = d.is_list
            existing_names = {s.val for s in existing_is}
            new_supers = list(existing_is)
            for part in parts:
                if part in by_name and part not in existing_names:
                    new_supers.append(Symbol(part))
                    existing_names.add(part)
            if len(new_supers) != len(existing_is):
                new_tags = dict(d.tags)
                new_tags["is"] = new_supers if len(new_supers) > 1 else new_supers[0]
                updated.append(Def(symbol=d.symbol, tags=new_tags))
            else:
                updated.append(d)
        else:
            updated.append(d)
    return updated


def _rebuild_libs(original_libs: list[Lib], all_defs: list[Def]) -> list[Lib]:
    """Rebuild libs using potentially updated defs."""
    # Index updated defs by symbol
    updated: dict[str, Def] = {d.symbol.val: d for d in all_defs}

    rebuilt: list[Lib] = []
    for lib in original_libs:
        new_defs = [updated.get(d.symbol.val, d) for d in lib.defs]
        rebuilt.append(
            Lib(
                symbol=lib.symbol,
                version=lib.version,
                depends=lib.depends,
                base_uri=lib.base_uri,
                defs=tuple(new_defs),
            )
        )
    return rebuilt


def _validate(ns: Namespace) -> None:
    """Validate the namespace for common errors."""
    errors: list[str] = []

    for d in ns.all_defs():
        # Check that all supertypes exist
        for parent_sym in d.is_list:
            if not ns.has(parent_sym.val):
                errors.append(f"{d.symbol.val}: supertype {parent_sym.val!r} not found")

    # Check for cycles in the taxonomy
    for d in ns.all_defs():
        if _has_cycle(ns, d.symbol.val):
            errors.append(f"{d.symbol.val}: cycle detected in is-hierarchy")

    if errors:
        msg = "Normalization errors:\n" + "\n".join(f"  - {e}" for e in errors)
        raise NormalizeError(msg)


def _has_cycle(ns: Namespace, start: str) -> bool:
    """Check if following the is-chain from *start* leads back to *start*."""
    visited: set[str] = set()
    queue = deque([start])
    first = True
    while queue:
        current = queue.popleft()
        if current in visited:
            if current == start and not first:
                return True
            continue
        visited.add(current)
        first = False
        d = ns.get(current)
        if d is None:
            continue
        for parent_sym in d.is_list:
            queue.append(parent_sym.val)
    return False
