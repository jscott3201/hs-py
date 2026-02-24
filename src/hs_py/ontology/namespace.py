"""Ontology namespace for resolved defs.

The :class:`Namespace` indexes defs from one or more libs, providing
symbol resolution and taxonomy queries (subtype, supertype, transitive
subtype checking).

See: https://project-haystack.org/doc/docHaystack/Namespaces
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from hs_py.encoding.trio import parse_trio
from hs_py.kinds import Symbol, sym_name
from hs_py.ontology.defs import Def, Lib

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "Namespace",
    "load_defs_from_trio",
    "load_lib_from_trio",
]


class Namespace:
    """Container for resolved ontology definitions.

    Indexes defs by both qualified (``ph::site``) and unqualified (``site``)
    symbol names.  Provides taxonomy queries over the ``is`` tag hierarchy.

    :param libs: List of :class:`Lib` instances to include.
    """

    def __init__(self, libs: list[Lib] | None = None) -> None:
        """Initialise the namespace, optionally loading initial libs.

        :param libs: :class:`~hs_py.ontology.defs.Lib` instances to include.
        """
        self._libs: list[Lib] = []
        self._by_name: dict[str, Def] = {}
        self._subtypes: dict[str, list[str]] = {}
        # Caches (invalidated on add_lib)
        self._all_defs_cache: list[Def] | None = None
        self._supertypes_cache: dict[str, list[Def]] = {}
        if libs:
            for lib in libs:
                self.add_lib(lib)

    def add_lib(self, lib: Lib) -> None:
        """Add a lib and its defs to the namespace.

        :param lib: Library to add.
        """
        self._libs.append(lib)
        for d in lib.defs:
            # Index by qualified name
            self._by_name[d.symbol.val] = d
            # Index by unqualified name (first wins)
            if d.name not in self._by_name:
                self._by_name[d.name] = d
        # Rebuild subtype index and invalidate caches
        self._rebuild_subtypes()
        self._all_defs_cache = None
        self._supertypes_cache.clear()

    def _rebuild_subtypes(self) -> None:
        self._subtypes.clear()
        for d in self._by_name.values():
            for parent_sym in d.is_list:
                parent = parent_sym.val
                subs = self._subtypes.setdefault(parent, [])
                if d.symbol.val not in subs:
                    subs.append(d.symbol.val)

    # ---- Lookup -------------------------------------------------------------

    def get(self, symbol: str | Symbol) -> Def | None:
        """Look up a def by symbol name.

        Accepts both qualified (``ph::site``) and unqualified (``site``) names.

        :param symbol: Qualified or unqualified symbol name.
        :returns: The matching :class:`~hs_py.ontology.defs.Def`, or ``None``.
        """
        return self._by_name.get(sym_name(symbol))

    def has(self, symbol: str | Symbol) -> bool:
        """Check whether a def with *symbol* exists in the namespace.

        :param symbol: Qualified or unqualified symbol name.
        :returns: ``True`` if the def exists.
        """
        return self.get(symbol) is not None

    # ---- Iteration ----------------------------------------------------------

    def all_defs(self) -> Iterator[Def]:
        """Iterate all unique defs (deduplicated by qualified name)."""
        return iter(self._get_all_defs())

    def _get_all_defs(self) -> list[Def]:
        """Return cached list of unique defs."""
        if self._all_defs_cache is None:
            seen: set[str] = set()
            result: list[Def] = []
            for d in self._by_name.values():
                key = d.symbol.val
                if key not in seen:
                    seen.add(key)
                    result.append(d)
            self._all_defs_cache = result
        return self._all_defs_cache

    def all_libs(self) -> Iterator[Lib]:
        """Iterate all libs."""
        return iter(self._libs)

    @property
    def def_count(self) -> int:
        """Number of unique defs."""
        return len(self._get_all_defs())

    # ---- Taxonomy -----------------------------------------------------------

    def subtypes(self, symbol: str | Symbol) -> list[Def]:
        """Return direct subtypes of a def.

        :param symbol: Def symbol to look up.
        :returns: List of :class:`~hs_py.ontology.defs.Def` instances.
        """
        name = sym_name(symbol)
        sub_names = self._subtypes.get(name, [])
        result: list[Def] = []
        for sn in sub_names:
            d = self._by_name.get(sn)
            if d is not None:
                result.append(d)
        return result

    def supertypes(self, symbol: str | Symbol) -> list[Def]:
        """Return direct supertypes of a def (from its ``is`` tag).

        :param symbol: Def symbol to look up.
        :returns: List of parent :class:`~hs_py.ontology.defs.Def` instances.
        """
        d = self.get(symbol)
        if d is None:
            return []
        result: list[Def] = []
        for parent_sym in d.is_list:
            parent = self.get(parent_sym.val)
            if parent is not None:
                result.append(parent)
        return result

    def is_subtype(self, sub: str | Symbol, sup: str | Symbol) -> bool:
        """Check whether *sub* is a transitive subtype of *sup*.

        Also returns ``True`` if *sub* equals *sup*.

        :param sub: Candidate subtype symbol.
        :param sup: Candidate supertype symbol.
        """
        sub_name = sym_name(sub)
        sup_name = sym_name(sup)
        if sub_name == sup_name:
            return True
        visited: set[str] = set()
        queue = deque([sub_name])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            d = self._by_name.get(current)
            if d is None:
                continue
            for parent_sym in d.is_list:
                pname = parent_sym.val
                if pname == sup_name:
                    return True
                queue.append(pname)
        return False

    def all_supertypes(self, symbol: str | Symbol) -> list[Def]:
        """Return all transitive supertypes of a def (cached).

        :param symbol: Def symbol to look up.
        :returns: All ancestor :class:`~hs_py.ontology.defs.Def` instances.
        """
        name = sym_name(symbol)
        cached = self._supertypes_cache.get(name)
        if cached is not None:
            return cached
        result: list[Def] = []
        visited: set[str] = set()
        queue = deque([name])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            d = self._by_name.get(current)
            if d is None:
                continue
            for parent_sym in d.is_list:
                pname = parent_sym.val
                parent = self._by_name.get(pname)
                if parent is not None and pname not in visited:
                    result.append(parent)
                    queue.append(pname)
        self._supertypes_cache[name] = result
        return result


def load_defs_from_trio(text: str) -> list[Def]:
    """Parse Trio text and return :class:`~hs_py.ontology.defs.Def` objects for each record with a ``def`` tag.

    :param text: Trio-formatted text.
    :returns: List of parsed defs.
    """
    defs: list[Def] = []
    for tags in parse_trio(text):
        if "def" in tags:
            defs.append(Def.from_tags(tags))
    return defs


def load_lib_from_trio(
    lib_trio: str,
    def_trios: list[str] | None = None,
) -> Lib:
    """Load a Lib from lib.trio metadata and optional def trio strings.

    :param lib_trio: Trio text for the lib metadata (must have one record with ``def``).
    :param def_trios: Optional list of Trio text strings for def records.
    :returns: Fully constructed Lib.
    """
    records = parse_trio(lib_trio)
    lib_meta: dict[str, Any] = {}
    defs: list[Def] = []
    for rec in records:
        if "def" in rec:
            sym = rec["def"]
            if isinstance(sym, Symbol) and sym.val.startswith("lib:"):
                lib_meta = rec
            else:
                defs.append(Def.from_tags(rec))
    if not lib_meta:
        msg = "No lib record found (expected def tag starting with 'lib:')"
        raise ValueError(msg)

    if def_trios:
        for text in def_trios:
            defs.extend(load_defs_from_trio(text))

    return Lib.from_meta(lib_meta, defs)
