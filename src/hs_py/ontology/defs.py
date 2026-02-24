"""Ontology definition and library data model.

A :class:`Def` is a named term in the Haystack ontology, carrying metadata
tags parsed from Trio records. A :class:`Lib` groups related defs into a
versioned, distributable package.

See: https://project-haystack.org/doc/docHaystack/Defs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hs_py.kinds import Symbol, Uri

__all__ = [
    "Def",
    "Lib",
]


@dataclass(frozen=True, slots=True)
class Def:
    """A single definition in the Haystack ontology.

    :param symbol: Qualified or unqualified symbol (e.g. ``^ph::site`` or ``^site``).
    :param tags: Full tag dict from the Trio record.
    """

    symbol: Symbol
    """Qualified or unqualified symbol (e.g. ``^ph::site`` or ``^site``)."""

    tags: dict[str, Any] = field(default_factory=dict)
    """Full tag dict from the Trio record."""

    @property
    def name(self) -> str:
        """Unqualified def name (e.g. ``site`` from ``ph::site``)."""
        val = self.symbol.val
        return val.rsplit("::", 1)[-1] if "::" in val else val

    @property
    def lib_prefix(self) -> str | None:
        """Library prefix if qualified (e.g. ``ph`` from ``ph::site``)."""
        val = self.symbol.val
        if "::" in val:
            return val.rsplit("::", 1)[0]
        return None

    @property
    def doc(self) -> str:
        """Documentation string from ``doc`` tag."""
        val = self.tags.get("doc", "")
        return val if isinstance(val, str) else ""

    @property
    def is_list(self) -> list[Symbol]:
        """Supertype symbols from the ``is`` tag."""
        is_val = self.tags.get("is")
        if isinstance(is_val, Symbol):
            return [is_val]
        if isinstance(is_val, list):
            return [s for s in is_val if isinstance(s, Symbol)]
        return []

    @classmethod
    def from_tags(cls, tags: dict[str, Any]) -> Def:
        """Create a Def from a parsed Trio tag dict.

        :param tags: Must contain a ``def`` tag with a Symbol value.
        :raises ValueError: If ``def`` tag is missing or not a Symbol.
        """
        def_sym = tags.get("def")
        if not isinstance(def_sym, Symbol):
            msg = f"def tag must be a Symbol, got {type(def_sym).__name__}"
            raise ValueError(msg)
        return cls(symbol=def_sym, tags=tags)


@dataclass(frozen=True, slots=True)
class Lib:
    """A library of ontology definitions.

    :param symbol: Lib symbol (e.g. ``^lib:ph``).
    :param version: Semantic version string.
    :param depends: Required library symbols.
    :param base_uri: Base URI for RDF export.
    :param defs: All defs defined by this lib.
    """

    symbol: Symbol
    """Lib symbol (e.g. ``^lib:ph``)."""

    version: str = ""
    """Semantic version string."""

    depends: tuple[Symbol, ...] = ()
    """Required library symbols."""

    base_uri: Uri | None = None
    """Base URI for RDF export."""

    defs: tuple[Def, ...] = ()
    """All :class:`Def` instances defined by this lib."""

    @classmethod
    def from_meta(cls, meta: dict[str, Any], defs: list[Def]) -> Lib:
        """Create a Lib from lib.trio metadata and a list of defs.

        :param meta: Parsed lib.trio record (must have ``def`` tag).
        :param defs: All defs belonging to this lib.
        """
        lib_sym = meta.get("def")
        if not isinstance(lib_sym, Symbol):
            msg = f"lib def tag must be a Symbol, got {type(lib_sym).__name__}"
            raise ValueError(msg)

        version = meta.get("version", "")
        if not isinstance(version, str):
            version = str(version)

        depends_val = meta.get("depends")
        depends: list[Symbol] = []
        if isinstance(depends_val, Symbol):
            depends = [depends_val]
        elif isinstance(depends_val, list):
            depends = [s for s in depends_val if isinstance(s, Symbol)]

        base_uri = meta.get("baseUri")

        return cls(
            symbol=lib_sym,
            version=version,
            depends=tuple(depends),
            base_uri=base_uri if isinstance(base_uri, Uri) else None,
            defs=tuple(defs),
        )
