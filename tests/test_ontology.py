import pytest

from hs_py.kinds import Symbol, Uri
from hs_py.ontology import Def, Lib, Namespace, load_defs_from_trio, load_lib_from_trio

# ---- Def --------------------------------------------------------------------


class TestDef:
    def test_from_tags(self) -> None:
        d = Def.from_tags({"def": Symbol("site"), "is": Symbol("entity")})
        assert d.symbol == Symbol("site")
        assert d.name == "site"

    def test_qualified_name(self) -> None:
        d = Def(symbol=Symbol("ph::site"), tags={"def": Symbol("ph::site")})
        assert d.name == "site"
        assert d.lib_prefix == "ph"

    def test_unqualified_name(self) -> None:
        d = Def(symbol=Symbol("site"), tags={})
        assert d.name == "site"
        assert d.lib_prefix is None

    def test_doc(self) -> None:
        d = Def(symbol=Symbol("site"), tags={"doc": "A site."})
        assert d.doc == "A site."

    def test_doc_missing(self) -> None:
        d = Def(symbol=Symbol("site"), tags={})
        assert d.doc == ""

    def test_is_list_single(self) -> None:
        d = Def(symbol=Symbol("site"), tags={"is": Symbol("entity")})
        assert d.is_list == [Symbol("entity")]

    def test_is_list_multiple(self) -> None:
        d = Def(
            symbol=Symbol("hot-water"),
            tags={"is": [Symbol("hot"), Symbol("water")]},
        )
        assert d.is_list == [Symbol("hot"), Symbol("water")]

    def test_is_list_empty(self) -> None:
        d = Def(symbol=Symbol("marker"), tags={})
        assert d.is_list == []

    def test_from_tags_missing_def_raises(self) -> None:
        with pytest.raises(ValueError, match="def tag must be a Symbol"):
            Def.from_tags({"is": Symbol("entity")})


# ---- Lib --------------------------------------------------------------------


class TestLib:
    def test_from_meta(self) -> None:
        defs = [Def.from_tags({"def": Symbol("site"), "is": Symbol("entity")})]
        meta = {
            "def": Symbol("lib:ph"),
            "version": "4.0",
            "depends": [Symbol("lib:sys")],
            "baseUri": Uri("https://project-haystack.org/def/ph/"),
        }
        lib = Lib.from_meta(meta, defs)
        assert lib.symbol == Symbol("lib:ph")
        assert lib.version == "4.0"
        assert lib.depends == (Symbol("lib:sys"),)
        assert lib.base_uri == Uri("https://project-haystack.org/def/ph/")
        assert len(lib.defs) == 1

    def test_from_meta_minimal(self) -> None:
        lib = Lib.from_meta({"def": Symbol("lib:test")}, [])
        assert lib.symbol == Symbol("lib:test")
        assert lib.version == ""
        assert lib.depends == ()
        assert lib.base_uri is None

    def test_from_meta_missing_def_raises(self) -> None:
        with pytest.raises(ValueError, match="lib def tag must be a Symbol"):
            Lib.from_meta({"version": "1.0"}, [])


# ---- Namespace: lookup ------------------------------------------------------


class TestNamespaceLookup:
    def _make_ns(self) -> Namespace:
        defs = [
            Def(Symbol("entity"), {"def": Symbol("entity")}),
            Def(Symbol("site"), {"def": Symbol("site"), "is": Symbol("entity")}),
            Def(Symbol("equip"), {"def": Symbol("equip"), "is": Symbol("entity")}),
            Def(
                Symbol("ahu"),
                {"def": Symbol("ahu"), "is": [Symbol("equip"), Symbol("marker")]},
            ),
        ]
        lib = Lib(symbol=Symbol("lib:ph"), defs=tuple(defs))
        return Namespace([lib])

    def test_get_by_name(self) -> None:
        ns = self._make_ns()
        d = ns.get("site")
        assert d is not None
        assert d.symbol == Symbol("site")

    def test_get_by_symbol(self) -> None:
        ns = self._make_ns()
        d = ns.get(Symbol("site"))
        assert d is not None

    def test_get_missing(self) -> None:
        ns = self._make_ns()
        assert ns.get("nonexistent") is None

    def test_has(self) -> None:
        ns = self._make_ns()
        assert ns.has("site")
        assert not ns.has("nonexistent")

    def test_def_count(self) -> None:
        ns = self._make_ns()
        assert ns.def_count == 4

    def test_all_defs(self) -> None:
        ns = self._make_ns()
        names = {d.symbol.val for d in ns.all_defs()}
        assert names == {"entity", "site", "equip", "ahu"}


# ---- Namespace: taxonomy ----------------------------------------------------


class TestNamespaceTaxonomy:
    def _make_ns(self) -> Namespace:
        defs = [
            Def(Symbol("marker"), {"def": Symbol("marker")}),
            Def(Symbol("entity"), {"def": Symbol("entity"), "is": Symbol("marker")}),
            Def(Symbol("site"), {"def": Symbol("site"), "is": Symbol("entity")}),
            Def(Symbol("equip"), {"def": Symbol("equip"), "is": Symbol("entity")}),
            Def(
                Symbol("ahu"),
                {"def": Symbol("ahu"), "is": Symbol("equip")},
            ),
        ]
        lib = Lib(symbol=Symbol("lib:ph"), defs=tuple(defs))
        return Namespace([lib])

    def test_subtypes(self) -> None:
        ns = self._make_ns()
        subs = ns.subtypes("entity")
        names = {d.symbol.val for d in subs}
        assert names == {"site", "equip"}

    def test_subtypes_empty(self) -> None:
        ns = self._make_ns()
        assert ns.subtypes("ahu") == []

    def test_supertypes(self) -> None:
        ns = self._make_ns()
        supers = ns.supertypes("site")
        assert len(supers) == 1
        assert supers[0].symbol == Symbol("entity")

    def test_supertypes_of_root(self) -> None:
        ns = self._make_ns()
        assert ns.supertypes("marker") == []

    def test_is_subtype_direct(self) -> None:
        ns = self._make_ns()
        assert ns.is_subtype("site", "entity")

    def test_is_subtype_transitive(self) -> None:
        ns = self._make_ns()
        assert ns.is_subtype("ahu", "entity")
        assert ns.is_subtype("ahu", "marker")

    def test_is_subtype_self(self) -> None:
        ns = self._make_ns()
        assert ns.is_subtype("site", "site")

    def test_is_subtype_false(self) -> None:
        ns = self._make_ns()
        assert not ns.is_subtype("site", "equip")

    def test_all_supertypes(self) -> None:
        ns = self._make_ns()
        supers = ns.all_supertypes("ahu")
        names = {d.symbol.val for d in supers}
        assert names == {"equip", "entity", "marker"}


# ---- Namespace: add_lib -----------------------------------------------------


class TestNamespaceAddLib:
    def test_add_lib_incrementally(self) -> None:
        ns = Namespace()
        lib1 = Lib(
            symbol=Symbol("lib:a"),
            defs=(Def(Symbol("base"), {"def": Symbol("base")}),),
        )
        ns.add_lib(lib1)
        assert ns.has("base")

        lib2 = Lib(
            symbol=Symbol("lib:b"),
            defs=(Def(Symbol("child"), {"def": Symbol("child"), "is": Symbol("base")}),),
        )
        ns.add_lib(lib2)
        assert ns.has("child")
        assert ns.is_subtype("child", "base")


# ---- Load from Trio ---------------------------------------------------------


class TestLoadFromTrio:
    def test_load_defs(self) -> None:
        trio = '---\ndef: ^site\nis: ^entity\ndoc: "A site."\n---\ndef: ^equip\nis: ^entity\n'
        defs = load_defs_from_trio(trio)
        assert len(defs) == 2
        assert defs[0].symbol == Symbol("site")
        assert defs[1].symbol == Symbol("equip")

    def test_load_lib(self) -> None:
        lib_trio = 'def: ^lib:test\nversion: "1.0"\n'
        def_trio = "---\ndef: ^site\nis: ^entity\n---\ndef: ^equip\nis: ^entity\n"
        lib = load_lib_from_trio(lib_trio, [def_trio])
        assert lib.symbol == Symbol("lib:test")
        assert lib.version == "1.0"
        assert len(lib.defs) == 2

    def test_load_lib_no_lib_record_raises(self) -> None:
        with pytest.raises(ValueError, match="No lib record"):
            load_lib_from_trio("def: ^site\nis: ^entity\n")

    def test_load_lib_with_inline_defs(self) -> None:
        trio = """\
def: ^lib:test
version: "2.0"
---
def: ^myTag
is: ^marker
doc: "A custom tag."
"""
        lib = load_lib_from_trio(trio)
        assert lib.symbol == Symbol("lib:test")
        assert len(lib.defs) == 1
        assert lib.defs[0].symbol == Symbol("myTag")

    def test_roundtrip_to_namespace(self) -> None:
        lib_trio = 'def: ^lib:test\nversion: "1.0"\n'
        def_trio = """\
---
def: ^entity
---
def: ^site
is: ^entity
doc: "A site."
---
def: ^equip
is: ^entity
"""
        lib = load_lib_from_trio(lib_trio, [def_trio])
        ns = Namespace([lib])
        assert ns.has("site")
        assert ns.has("equip")
        assert ns.is_subtype("site", "entity")
        assert ns.get("site") is not None
        assert ns.get("site").doc == "A site."  # type: ignore[union-attr]


# ---- Ontology coverage gaps ------------------------------------------------


class TestOntologyModuleGetattr:
    """Cover ontology/__init__.py L61-62: unknown attribute → AttributeError."""

    def test_unknown_attr_raises(self) -> None:
        import hs_py.ontology as ont_mod

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = ont_mod.nonexistent_thing  # type: ignore[attr-defined]


class TestLibVersionCoercion:
    """Cover ontology/defs.py L121: non-string version → str()."""

    def test_non_string_version(self) -> None:
        lib = Lib.from_meta(
            {"def": Symbol("lib:test"), "version": 42},
            defs=(),
        )
        assert lib.version == "42"

    def test_single_symbol_depends(self) -> None:
        """Cover ontology/defs.py L126: single Symbol depends (not list)."""
        lib = Lib.from_meta(
            {"def": Symbol("lib:test"), "version": "1.0", "depends": Symbol("lib:ph")},
            defs=(),
        )
        assert len(lib.depends) == 1
        assert lib.depends[0] == Symbol("lib:ph")


class TestNamespaceEdgeCases:
    """Cover namespace.py uncovered branches."""

    def _make_ns(self) -> Namespace:
        defs = [
            Def(Symbol("marker"), {"def": Symbol("marker")}),
            Def(Symbol("entity"), {"def": Symbol("entity"), "is": Symbol("marker")}),
            Def(Symbol("site"), {"def": Symbol("site"), "is": Symbol("entity")}),
            # has a parent that doesn't exist in namespace
            Def(Symbol("orphan"), {"def": Symbol("orphan"), "is": Symbol("nonexistent")}),
            # diamond: both paths lead to entity
            Def(Symbol("tagged"), {"def": Symbol("tagged"), "is": Symbol("marker")}),
            Def(
                Symbol("diamond"),
                {"def": Symbol("diamond"), "is": [Symbol("entity"), Symbol("tagged")]},
            ),
        ]
        lib = Lib(symbol=Symbol("lib:ph"), defs=tuple(defs))
        return Namespace([lib])

    def test_all_libs(self) -> None:
        """Cover namespace.py L150: all_libs() iterator."""
        ns = self._make_ns()
        libs = list(ns.all_libs())
        assert len(libs) == 1
        assert libs[0].symbol == Symbol("lib:ph")

    def test_supertypes_unknown_parent(self) -> None:
        """Cover namespace.py L186: parent symbol not in namespace."""
        ns = self._make_ns()
        # orphan's parent 'nonexistent' is not in the namespace
        supers = ns.supertypes("orphan")
        assert len(supers) == 0

    def test_supertypes_unknown_def(self) -> None:
        """Cover namespace.py L182: def itself doesn't exist."""
        ns = self._make_ns()
        supers = ns.supertypes("totally_unknown")
        assert len(supers) == 0

    def test_is_subtype_unknown_def(self) -> None:
        """Cover namespace.py L207/211: unknown def in traversal chain."""
        ns = self._make_ns()
        assert not ns.is_subtype("orphan", "marker")

    def test_is_subtype_unknown_sub(self) -> None:
        """Cover namespace.py L211: sub is completely unknown → d is None."""
        ns = self._make_ns()
        assert not ns.is_subtype("totally_unknown", "marker")

    def test_is_subtype_diamond_revisit(self) -> None:
        """Cover namespace.py L207: visited node revisited in BFS."""
        ns = self._make_ns()
        # diamond→entity→marker AND diamond→tagged→marker
        # Both paths reach marker, so 'marker' gets queued twice
        assert ns.is_subtype("diamond", "marker")

    def test_all_supertypes_with_unknown_in_chain(self) -> None:
        """Cover namespace.py L239: all_supertypes unknown def in chain."""
        ns = self._make_ns()
        supers = ns.all_supertypes("orphan")
        assert len(supers) == 0

    def test_all_supertypes_diamond_revisit(self) -> None:
        """Cover namespace.py L234: visited check in all_supertypes BFS."""
        ns = self._make_ns()
        supers = ns.all_supertypes("diamond")
        names = {d.symbol.val for d in supers}
        # Should include entity, tagged, marker (each only once)
        assert "entity" in names
        assert "tagged" in names
        assert "marker" in names

    def test_all_supertypes_caching(self) -> None:
        """Cover namespace.py L228: cache hit on second call."""
        ns = self._make_ns()
        supers1 = ns.all_supertypes("site")
        supers2 = ns.all_supertypes("site")
        assert supers1 is supers2  # same object from cache
