import datetime

from hs_py.grid import Grid
from hs_py.kinds import (
    MARKER,
    NA,
    REMOVE,
    Coord,
    Marker,
    Na,
    Number,
    Ref,
    Remove,
    Symbol,
    Uri,
    XStr,
    is_haystack_type,
)

# ---- Singletons -----------------------------------------------------------


class TestMarker:
    def test_singleton(self) -> None:
        assert Marker() is MARKER
        assert Marker() is Marker()

    def test_str(self) -> None:
        assert str(MARKER) == "\u2713"

    def test_repr(self) -> None:
        assert repr(MARKER) == "Marker"

    def test_bool(self) -> None:
        assert bool(MARKER) is True

    def test_equality(self) -> None:
        assert Marker() == MARKER
        assert MARKER != NA

    def test_hash(self) -> None:
        assert hash(MARKER) == hash(Marker())
        s: set[Marker] = {MARKER, Marker()}
        assert len(s) == 1


class TestNa:
    def test_singleton(self) -> None:
        assert Na() is NA

    def test_str(self) -> None:
        assert str(NA) == "NA"

    def test_equality(self) -> None:
        assert Na() == NA
        assert NA != MARKER


class TestRemove:
    def test_singleton(self) -> None:
        assert Remove() is REMOVE

    def test_str(self) -> None:
        assert str(REMOVE) == "remove"


# ---- Number ----------------------------------------------------------------


class TestNumber:
    def test_basic(self) -> None:
        n = Number(72.5, "°F")
        assert n.val == 72.5
        assert n.unit == "°F"

    def test_unitless(self) -> None:
        n = Number(42)
        assert n.unit is None

    def test_empty_unit_normalizes(self) -> None:
        n = Number(1.0, "")
        assert n.unit is None

    def test_str_with_unit(self) -> None:
        assert str(Number(72.5, "°F")) == "72.5°F"

    def test_str_integer(self) -> None:
        assert str(Number(42.0)) == "42"

    def test_str_nan(self) -> None:
        assert str(Number(float("nan"))) == "NaN"

    def test_str_inf(self) -> None:
        assert str(Number(float("inf"))) == "INF"
        assert str(Number(float("-inf"))) == "-INF"

    def test_equality(self) -> None:
        assert Number(1.0) == Number(1.0)
        assert Number(1.0, "m") == Number(1.0, "m")
        assert Number(1.0, "m") != Number(1.0, "ft")
        assert Number(1.0) != Number(2.0)

    def test_nan_equality(self) -> None:
        assert Number(float("nan")) == Number(float("nan"))

    def test_nan_strips_unit(self) -> None:
        """Spec: NaN cannot include unit — unit is silently discarded."""
        n = Number(float("nan"), "m")
        assert n.unit is None

    def test_hash(self) -> None:
        assert hash(Number(1.0)) == hash(Number(1.0))
        s = {Number(1.0, "m"), Number(1.0, "m")}
        assert len(s) == 1

    def test_frozen(self) -> None:
        n = Number(1.0)
        try:
            n.val = 2.0  # type: ignore[misc]
            raise AssertionError("should be frozen")
        except AttributeError:
            pass


# ---- Ref -------------------------------------------------------------------


class TestRef:
    def test_basic(self) -> None:
        r = Ref("abc-123")
        assert r.val == "abc-123"
        assert r.dis is None

    def test_with_dis(self) -> None:
        r = Ref("abc-123", "My Point")
        assert r.dis == "My Point"

    def test_str(self) -> None:
        assert str(Ref("abc")) == "@abc"
        assert str(Ref("abc", "Foo")) == "@abc 'Foo'"

    def test_empty_val_raises(self) -> None:
        try:
            Ref("")
            raise AssertionError("should raise")
        except ValueError:
            pass

    def test_equality(self) -> None:
        assert Ref("a") == Ref("a")
        assert Ref("a") != Ref("b")
        # dis is part of equality
        assert Ref("a", "X") != Ref("a", "Y")

    def test_hash(self) -> None:
        s = {Ref("a"), Ref("a")}
        assert len(s) == 1


# ---- Symbol ----------------------------------------------------------------


class TestSymbol:
    def test_basic(self) -> None:
        s = Symbol("elec-meter")
        assert s.val == "elec-meter"

    def test_str(self) -> None:
        assert str(Symbol("elec-meter")) == "^elec-meter"

    def test_empty_raises(self) -> None:
        try:
            Symbol("")
            raise AssertionError("should raise")
        except ValueError:
            pass


# ---- Uri -------------------------------------------------------------------


class TestUri:
    def test_basic(self) -> None:
        u = Uri("http://example.com")
        assert u.val == "http://example.com"

    def test_str(self) -> None:
        assert str(Uri("http://example.com")) == "`http://example.com`"


# ---- Coord -----------------------------------------------------------------


class TestCoord:
    def test_basic(self) -> None:
        c = Coord(37.545, -77.449)
        assert c.lat == 37.545
        assert c.lng == -77.449

    def test_str(self) -> None:
        assert str(Coord(37.545, -77.449)) == "C(37.545,-77.449)"

    def test_lat_out_of_range(self) -> None:
        try:
            Coord(91.0, 0.0)
            raise AssertionError("should raise")
        except ValueError:
            pass

    def test_lng_out_of_range(self) -> None:
        try:
            Coord(0.0, 181.0)
            raise AssertionError("should raise")
        except ValueError:
            pass


# ---- XStr ------------------------------------------------------------------


class TestXStr:
    def test_basic(self) -> None:
        x = XStr("Color", "red")
        assert x.type_name == "Color"
        assert x.val == "red"

    def test_str(self) -> None:
        assert str(XStr("Color", "red")) == 'Color("red")'

    def test_lowercase_type_raises(self) -> None:
        try:
            XStr("color", "red")
            raise AssertionError("should raise")
        except ValueError:
            pass

    def test_empty_type_raises(self) -> None:
        try:
            XStr("", "red")
            raise AssertionError("should raise")
        except ValueError:
            pass


# ---- is_haystack_type ------------------------------------------------------


class TestIsHaystackType:
    def test_singletons(self) -> None:
        assert is_haystack_type(MARKER)
        assert is_haystack_type(NA)
        assert is_haystack_type(REMOVE)

    def test_scalars(self) -> None:
        assert is_haystack_type(Number(1.0))
        assert is_haystack_type(Ref("a"))
        assert is_haystack_type(Symbol("x"))
        assert is_haystack_type(Uri("http://x"))
        assert is_haystack_type(Coord(0, 0))
        assert is_haystack_type(XStr("Foo", "bar"))

    def test_python_natives(self) -> None:
        assert is_haystack_type(True)
        assert is_haystack_type("hello")
        assert is_haystack_type(42)
        assert is_haystack_type(3.14)
        assert is_haystack_type(None)

    def test_temporals(self) -> None:
        assert is_haystack_type(datetime.date(2024, 1, 1))
        assert is_haystack_type(datetime.time(8, 30))
        assert is_haystack_type(datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC))

    def test_collections(self) -> None:
        assert is_haystack_type([1, 2])
        assert is_haystack_type({"a": 1})
        assert is_haystack_type(Grid())

    def test_non_haystack(self) -> None:
        assert not is_haystack_type(b"bytes")
        assert not is_haystack_type(object())
