from hs_py.kinds import MARKER, Number, Ref, Symbol
from hs_py.ontology import Def, Lib, Namespace, fits, reflect


def _make_ns() -> Namespace:
    """Build a test namespace with a realistic ontology fragment."""
    defs = [
        Def(Symbol("marker"), {"def": Symbol("marker")}),
        Def(Symbol("entity"), {"def": Symbol("entity"), "is": Symbol("marker")}),
        Def(Symbol("site"), {"def": Symbol("site"), "is": Symbol("entity")}),
        Def(Symbol("equip"), {"def": Symbol("equip"), "is": Symbol("entity")}),
        Def(Symbol("point"), {"def": Symbol("point"), "is": Symbol("entity")}),
        Def(Symbol("sensor"), {"def": Symbol("sensor"), "is": Symbol("point")}),
        Def(Symbol("cmd"), {"def": Symbol("cmd"), "is": Symbol("point")}),
        Def(Symbol("hot"), {"def": Symbol("hot"), "is": Symbol("marker")}),
        Def(Symbol("water"), {"def": Symbol("water"), "is": Symbol("marker")}),
        Def(
            Symbol("hot-water"),
            {"def": Symbol("hot-water"), "is": [Symbol("hot"), Symbol("water")]},
        ),
        Def(Symbol("ahu"), {"def": Symbol("ahu"), "is": Symbol("equip")}),
    ]
    lib = Lib(symbol=Symbol("lib:test"), defs=tuple(defs))
    return Namespace([lib])


# ---- reflect ----------------------------------------------------------------


class TestReflect:
    def test_simple_marker(self) -> None:
        ns = _make_ns()
        defs = reflect(ns, {"site": MARKER, "dis": "Main"})
        names = {d.symbol.val for d in defs}
        assert "site" in names
        assert "entity" in names
        assert "marker" in names

    def test_multiple_markers(self) -> None:
        ns = _make_ns()
        defs = reflect(ns, {"point": MARKER, "sensor": MARKER})
        names = {d.symbol.val for d in defs}
        assert "sensor" in names
        assert "point" in names
        assert "entity" in names

    def test_conjunct_detection(self) -> None:
        ns = _make_ns()
        defs = reflect(ns, {"hot": MARKER, "water": MARKER, "equip": MARKER})
        names = {d.symbol.val for d in defs}
        # Should detect hot-water conjunct
        assert "hot-water" in names
        assert "hot" in names
        assert "water" in names
        assert "equip" in names

    def test_no_markers(self) -> None:
        ns = _make_ns()
        defs = reflect(ns, {"dis": "Hello", "val": Number(72.0)})
        assert defs == []

    def test_unknown_marker(self) -> None:
        ns = _make_ns()
        defs = reflect(ns, {"unknown": MARKER})
        # Unknown marker has no def, so no results
        assert defs == []

    def test_most_specific_first(self) -> None:
        ns = _make_ns()
        defs = reflect(ns, {"sensor": MARKER})
        # sensor should come before point and entity
        names = [d.symbol.val for d in defs]
        assert names.index("sensor") < names.index("point")
        assert names.index("point") < names.index("entity")

    def test_non_marker_tags_ignored(self) -> None:
        ns = _make_ns()
        defs = reflect(ns, {"site": MARKER, "siteRef": Ref("s1"), "dis": "Foo"})
        names = {d.symbol.val for d in defs}
        assert "site" in names
        # siteRef and dis are not marker tags, so not reflected


# ---- fits -------------------------------------------------------------------


class TestFits:
    def test_fits_direct(self) -> None:
        ns = _make_ns()
        assert fits(ns, {"site": MARKER}, "site")

    def test_fits_supertype(self) -> None:
        ns = _make_ns()
        assert fits(ns, {"site": MARKER}, "entity")
        assert fits(ns, {"sensor": MARKER}, "point")

    def test_fits_false(self) -> None:
        ns = _make_ns()
        assert not fits(ns, {"site": MARKER}, "equip")

    def test_fits_conjunct(self) -> None:
        ns = _make_ns()
        assert fits(ns, {"hot": MARKER, "water": MARKER}, "hot-water")

    def test_fits_with_symbol(self) -> None:
        ns = _make_ns()
        assert fits(ns, {"site": MARKER}, Symbol("entity"))

    def test_fits_empty_tags(self) -> None:
        ns = _make_ns()
        assert not fits(ns, {}, "site")
