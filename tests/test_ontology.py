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
