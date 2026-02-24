from hs_py.kinds import MARKER, Symbol
from hs_py.ontology import (
    Def,
    Lib,
    Namespace,
    compile_namespace,
    effective_tags,
    is_conjunct,
    marker_tags,
    resolve_conjunct_parts,
    tag_on_defs,
)

# ---- Conjunct utilities -----------------------------------------------------


class TestConjuncts:
    def test_is_conjunct_true(self) -> None:
        assert is_conjunct("hot-water")
        assert is_conjunct(Symbol("hot-water-plant"))

    def test_is_conjunct_false(self) -> None:
        assert not is_conjunct("site")
        assert not is_conjunct(Symbol("equip"))

    def test_resolve_parts(self) -> None:
        assert resolve_conjunct_parts("hot-water") == ["hot", "water"]
        assert resolve_conjunct_parts("hot-water-plant") == ["hot", "water", "plant"]

    def test_resolve_single(self) -> None:
        assert resolve_conjunct_parts("site") == ["site"]


# ---- Effective tags ---------------------------------------------------------


def _make_taxonomy_ns() -> Namespace:
    """Build a small taxonomy for testing."""
    defs = [
        Def(Symbol("marker"), {"def": Symbol("marker")}),
        Def(
            Symbol("entity"),
            {"def": Symbol("entity"), "is": Symbol("marker"), "doc": "Base entity."},
        ),
        Def(
            Symbol("site"),
            {
                "def": Symbol("site"),
                "is": Symbol("entity"),
                "doc": "A site.",
                "geoCity": MARKER,
            },
        ),
        Def(
            Symbol("campus"),
            {
                "def": Symbol("campus"),
                "is": Symbol("site"),
                "doc": "A campus site.",
            },
        ),
    ]
    lib = Lib(symbol=Symbol("lib:test"), defs=tuple(defs))
    return Namespace([lib])


class TestEffectiveTags:
    def test_own_tags(self) -> None:
        ns = _make_taxonomy_ns()
        tags = effective_tags(ns, "site")
        assert tags["doc"] == "A site."
        assert "geoCity" in tags

    def test_inherited_tags(self) -> None:
        ns = _make_taxonomy_ns()
        tags = effective_tags(ns, "campus")
        # Campus inherits geoCity from site
        assert "geoCity" in tags
        # Campus overrides doc
        assert tags["doc"] == "A campus site."

    def test_missing_def(self) -> None:
        ns = _make_taxonomy_ns()
        assert effective_tags(ns, "nonexistent") == {}

    def test_excludes_meta_tags(self) -> None:
        ns = _make_taxonomy_ns()
        tags = effective_tags(ns, "site")
        assert "def" not in tags
        assert "is" not in tags


# ---- Marker tags ------------------------------------------------------------


class TestMarkerTags:
    def test_includes_self(self) -> None:
        ns = _make_taxonomy_ns()
        markers = marker_tags(ns, "site")
        assert "site" in markers

    def test_includes_supertypes(self) -> None:
        ns = _make_taxonomy_ns()
        markers = marker_tags(ns, "campus")
        assert "campus" in markers
        assert "site" in markers
        assert "entity" in markers
        assert "marker" in markers

    def test_missing_def(self) -> None:
        ns = _make_taxonomy_ns()
        assert marker_tags(ns, "nonexistent") == set()


# ---- Tag-on ----------------------------------------------------------------


class TestTagOn:
    def test_tag_on_single(self) -> None:
        defs = [
            Def(Symbol("site"), {"def": Symbol("site")}),
            Def(
                Symbol("geoCity"),
                {"def": Symbol("geoCity"), "tagOn": Symbol("site")},
            ),
        ]
        lib = Lib(symbol=Symbol("lib:test"), defs=tuple(defs))
        ns = Namespace([lib])
        assert tag_on_defs(ns, "geoCity") == ["site"]

    def test_tag_on_multiple(self) -> None:
        defs = [
            Def(Symbol("site"), {"def": Symbol("site")}),
            Def(Symbol("equip"), {"def": Symbol("equip")}),
            Def(
                Symbol("dis"),
                {"def": Symbol("dis"), "tagOn": [Symbol("site"), Symbol("equip")]},
            ),
        ]
        lib = Lib(symbol=Symbol("lib:test"), defs=tuple(defs))
        ns = Namespace([lib])
        assert tag_on_defs(ns, "dis") == ["site", "equip"]

    def test_tag_on_missing(self) -> None:
        ns = _make_taxonomy_ns()
        assert tag_on_defs(ns, "nonexistent") == []


# ---- Compile namespace (normalization) --------------------------------------


class TestCompileNamespace:
    def test_basic_compilation(self) -> None:
        defs = [
            Def(Symbol("marker"), {"def": Symbol("marker")}),
            Def(Symbol("entity"), {"def": Symbol("entity"), "is": Symbol("marker")}),
            Def(Symbol("site"), {"def": Symbol("site"), "is": Symbol("entity")}),
        ]
        lib = Lib(symbol=Symbol("lib:test"), defs=tuple(defs))
        ns = compile_namespace([lib])
        assert ns.has("site")
        assert ns.is_subtype("site", "marker")

    def test_conjunct_taxonify(self) -> None:
        defs = [
            Def(Symbol("hot"), {"def": Symbol("hot")}),
            Def(Symbol("water"), {"def": Symbol("water")}),
            Def(Symbol("hot-water"), {"def": Symbol("hot-water")}),
        ]
        lib = Lib(symbol=Symbol("lib:test"), defs=tuple(defs))
        ns = compile_namespace([lib])
        hw = ns.get("hot-water")
        assert hw is not None
        # After taxonify, hot-water should have hot and water as supertypes
        is_names = {s.val for s in hw.is_list}
        assert "hot" in is_names
        assert "water" in is_names

    def test_conjunct_preserves_existing_is(self) -> None:
        defs = [
            Def(Symbol("marker"), {"def": Symbol("marker")}),
            Def(Symbol("hot"), {"def": Symbol("hot"), "is": Symbol("marker")}),
            Def(Symbol("water"), {"def": Symbol("water"), "is": Symbol("marker")}),
            Def(
                Symbol("hot-water"),
                {"def": Symbol("hot-water"), "is": Symbol("marker")},
            ),
        ]
        lib = Lib(symbol=Symbol("lib:test"), defs=tuple(defs))
        ns = compile_namespace([lib])
        hw = ns.get("hot-water")
        assert hw is not None
        is_names = {s.val for s in hw.is_list}
        # Should have marker (original) + hot + water (from taxonify)
        assert "marker" in is_names
        assert "hot" in is_names
        assert "water" in is_names

    def test_multiple_libs(self) -> None:
        lib1 = Lib(
            symbol=Symbol("lib:core"),
            defs=(Def(Symbol("entity"), {"def": Symbol("entity")}),),
        )
        lib2 = Lib(
            symbol=Symbol("lib:ext"),
            defs=(Def(Symbol("site"), {"def": Symbol("site"), "is": Symbol("entity")}),),
        )
        ns = compile_namespace([lib1, lib2])
        assert ns.is_subtype("site", "entity")


class TestCompileNamespaceValidation:
    def test_missing_supertype_raises(self) -> None:
        defs = [
            Def(Symbol("site"), {"def": Symbol("site"), "is": Symbol("nonexistent")}),
        ]
        lib = Lib(symbol=Symbol("lib:test"), defs=tuple(defs))
        try:
            compile_namespace([lib])
            raise AssertionError("should raise NormalizeError")
        except Exception as e:
            assert "nonexistent" in str(e)

    def test_cycle_detection(self) -> None:
        defs = [
            Def(Symbol("a"), {"def": Symbol("a"), "is": Symbol("b")}),
            Def(Symbol("b"), {"def": Symbol("b"), "is": Symbol("a")}),
        ]
        lib = Lib(symbol=Symbol("lib:test"), defs=tuple(defs))
        try:
            compile_namespace([lib])
            raise AssertionError("should raise NormalizeError")
        except Exception as e:
            assert "cycle" in str(e)
