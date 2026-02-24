import math
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import orjson
import pytest

from hs_py.encoding.json import (
    JsonVersion,
    decode_grid,
    decode_val,
    encode_grid,
    encode_val,
)
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import (
    MARKER,
    NA,
    REMOVE,
    Coord,
    Number,
    Ref,
    Symbol,
    Uri,
    XStr,
)

# ---- V4 Scalar round-trips -----------------------------------------------


class TestV4EncodeDecodeScalars:
    def test_none(self) -> None:
        assert encode_val(None) is None
        assert decode_val(None) is None

    def test_bool(self) -> None:
        assert encode_val(True) is True
        assert encode_val(False) is False
        assert decode_val(True) is True
        assert decode_val(False) is False

    def test_str(self) -> None:
        assert encode_val("hello") == "hello"
        assert decode_val("hello") == "hello"

    def test_int(self) -> None:
        assert encode_val(42) == 42
        assert decode_val(42) == 42

    def test_float(self) -> None:
        assert encode_val(3.14) == 3.14
        assert decode_val(3.14) == 3.14


class TestV4EncodeDecodeSingletons:
    def test_marker(self) -> None:
        encoded = encode_val(MARKER)
        assert encoded == {"_kind": "marker"}
        assert decode_val(encoded) is MARKER

    def test_na(self) -> None:
        encoded = encode_val(NA)
        assert encoded == {"_kind": "na"}
        assert decode_val(encoded) is NA

    def test_remove(self) -> None:
        encoded = encode_val(REMOVE)
        assert encoded == {"_kind": "remove"}
        assert decode_val(encoded) is REMOVE


class TestV4EncodeDecodeNumber:
    def test_unitless(self) -> None:
        encoded = encode_val(Number(72.5))
        assert encoded == 72.5

    def test_with_unit(self) -> None:
        encoded = encode_val(Number(72.5, "°F"))
        assert encoded == {"_kind": "number", "val": 72.5, "unit": "°F"}
        decoded = decode_val(encoded)
        assert isinstance(decoded, Number)
        assert decoded.val == 72.5
        assert decoded.unit == "°F"

    def test_nan(self) -> None:
        encoded = encode_val(Number(float("nan")))
        assert encoded == {"_kind": "number", "val": "NaN"}
        decoded = decode_val(encoded)
        assert isinstance(decoded, Number)
        assert math.isnan(decoded.val)

    def test_inf(self) -> None:
        encoded = encode_val(Number(float("inf")))
        assert encoded == {"_kind": "number", "val": "INF"}
        decoded = decode_val(encoded)
        assert isinstance(decoded, Number)
        assert decoded.val == float("inf")

    def test_neg_inf(self) -> None:
        encoded = encode_val(Number(float("-inf")))
        assert encoded == {"_kind": "number", "val": "-INF"}
        decoded = decode_val(encoded)
        assert isinstance(decoded, Number)
        assert decoded.val == float("-inf")


class TestV4EncodeDecodeRef:
    def test_basic(self) -> None:
        encoded = encode_val(Ref("abc-123"))
        assert encoded == {"_kind": "ref", "val": "abc-123"}
        decoded = decode_val(encoded)
        assert decoded == Ref("abc-123")

    def test_with_dis(self) -> None:
        encoded = encode_val(Ref("abc", "My Point"))
        assert encoded == {"_kind": "ref", "val": "abc", "dis": "My Point"}
        decoded = decode_val(encoded)
        assert decoded == Ref("abc", "My Point")


class TestV4EncodeDecodeSymbol:
    def test_roundtrip(self) -> None:
        encoded = encode_val(Symbol("elec-meter"))
        assert encoded == {"_kind": "symbol", "val": "elec-meter"}
        assert decode_val(encoded) == Symbol("elec-meter")


class TestV4EncodeDecodeUri:
    def test_roundtrip(self) -> None:
        encoded = encode_val(Uri("http://example.com"))
        assert encoded == {"_kind": "uri", "val": "http://example.com"}
        assert decode_val(encoded) == Uri("http://example.com")


class TestV4EncodeDecodeCoord:
    def test_roundtrip(self) -> None:
        encoded = encode_val(Coord(37.545, -77.449))
        assert encoded == {"_kind": "coord", "lat": 37.545, "lng": -77.449}
        assert decode_val(encoded) == Coord(37.545, -77.449)


class TestV4EncodeDecodeXStr:
    def test_roundtrip(self) -> None:
        encoded = encode_val(XStr("Color", "red"))
        assert encoded == {"_kind": "xstr", "type": "Color", "val": "red"}
        assert decode_val(encoded) == XStr("Color", "red")


class TestV4EncodeDecodeDate:
    def test_roundtrip(self) -> None:
        d = date(2024, 7, 17)
        encoded = encode_val(d)
        assert encoded == {"_kind": "date", "val": "2024-07-17"}
        assert decode_val(encoded) == d


class TestV4EncodeDecodeTime:
    def test_roundtrip(self) -> None:
        t = time(14, 30, 0)
        encoded = encode_val(t)
        assert encoded == {"_kind": "time", "val": "14:30:00"}
        assert decode_val(encoded) == t


class TestV4EncodeDecodeDateTime:
    def test_utc(self) -> None:
        dt = datetime(2024, 7, 17, 16, 55, 42, tzinfo=UTC)
        encoded = encode_val(dt)
        assert encoded["_kind"] == "dateTime"
        assert encoded["tz"] == "UTC"
        decoded = decode_val(encoded)
        assert isinstance(decoded, datetime)
        assert decoded == dt

    def test_zoneinfo(self) -> None:
        tz = ZoneInfo("America/New_York")
        dt = datetime(2024, 7, 17, 12, 0, 0, tzinfo=tz)
        encoded = encode_val(dt)
        assert encoded["tz"] == "New_York"
        decoded = decode_val(encoded)
        assert isinstance(decoded, datetime)
        assert decoded.tzinfo is not None

    def test_decode_city_only_tz(self) -> None:
        """Haystack spec uses city-only timezone names."""
        obj = {"_kind": "dateTime", "val": "2024-07-17T12:00:00-04:00", "tz": "New_York"}
        decoded = decode_val(obj)
        assert isinstance(decoded, datetime)
        assert decoded.tzinfo == ZoneInfo("America/New_York")

    def test_decode_full_iana_tz(self) -> None:
        """Also accept full IANA names for interoperability."""
        obj = {"_kind": "dateTime", "val": "2024-07-17T12:00:00-04:00", "tz": "America/New_York"}
        decoded = decode_val(obj)
        assert isinstance(decoded, datetime)
        assert decoded.tzinfo == ZoneInfo("America/New_York")

    def test_roundtrip_preserves_tz(self) -> None:
        tz = ZoneInfo("America/Chicago")
        dt = datetime(2024, 7, 17, 12, 0, 0, tzinfo=tz)
        encoded = encode_val(dt)
        assert encoded["tz"] == "Chicago"
        decoded = decode_val(encoded)
        assert decoded.tzinfo == ZoneInfo("America/Chicago")


class TestV4EncodeDecodeDict:
    def test_explicit_kind_dict(self) -> None:
        obj = {"_kind": "dict", "site": {"_kind": "marker"}, "dis": "Test"}
        decoded = decode_val(obj)
        assert isinstance(decoded, dict)
        assert decoded["site"] is MARKER
        assert decoded["dis"] == "Test"
        assert "_kind" not in decoded


# ---- V4 Collection round-trips -------------------------------------------


class TestV4EncodeDecodeCollections:
    def test_list(self) -> None:
        val = [1, "two", MARKER, Ref("a")]
        encoded = encode_val(val)
        assert encoded == [1, "two", {"_kind": "marker"}, {"_kind": "ref", "val": "a"}]
        decoded = decode_val(encoded)
        assert decoded == [1, "two", MARKER, Ref("a")]

    def test_dict(self) -> None:
        val = {"site": MARKER, "area": Number(5000, "ft²")}
        encoded = encode_val(val)
        assert encoded["site"] == {"_kind": "marker"}
        assert encoded["area"] == {"_kind": "number", "val": 5000, "unit": "ft²"}
        decoded = decode_val(encoded)
        assert decoded["site"] is MARKER
        assert decoded["area"] == Number(5000, "ft²")

    def test_nested(self) -> None:
        val = {"points": [Ref("a"), Ref("b")], "coord": Coord(0, 0)}
        encoded = encode_val(val)
        decoded = decode_val(encoded)
        assert decoded["points"] == [Ref("a"), Ref("b")]
        assert decoded["coord"] == Coord(0, 0)


# ---- V4 Grid encoding/decoding -------------------------------------------


class TestV4GridEncoding:
    def test_empty_grid(self) -> None:
        g = Grid.make_empty()
        data = encode_grid(g)
        decoded = decode_grid(data)
        assert decoded.is_empty
        assert decoded.cols == ()

    def test_simple_grid(self) -> None:
        g = Grid.make_rows(
            [
                {"id": Ref("a"), "dis": "Alpha", "site": MARKER},
                {"id": Ref("b"), "dis": "Beta"},
            ]
        )
        data = encode_grid(g)
        decoded = decode_grid(data)
        assert len(decoded) == 2
        assert decoded[0]["id"] == Ref("a")
        assert decoded[0]["site"] is MARKER
        assert decoded[1]["dis"] == "Beta"

    def test_error_grid(self) -> None:
        g = Grid.make_error("test error", trace="some trace")
        data = encode_grid(g)
        decoded = decode_grid(data)
        assert decoded.is_error
        assert decoded.meta["dis"] == "test error"
        assert decoded.meta["errTrace"] == "some trace"

    def test_grid_with_col_meta(self) -> None:
        g = (
            GridBuilder()
            .add_col("temp", meta={"unit": "°F"})
            .add_row({"temp": Number(72.5, "°F")})
            .to_grid()
        )
        data = encode_grid(g)
        decoded = decode_grid(data)
        assert decoded.cols[0].meta["unit"] == "°F"
        assert decoded[0]["temp"] == Number(72.5, "°F")

    def test_grid_with_meta(self) -> None:
        g = (
            GridBuilder()
            .add_meta("watchId", "w-123")
            .add_col("id")
            .add_row({"id": Ref("p1")})
            .to_grid()
        )
        data = encode_grid(g)
        decoded = decode_grid(data)
        assert decoded.meta["watchId"] == "w-123"

    def test_nested_grid(self) -> None:
        inner = Grid.make_rows([{"x": 1}])
        outer = Grid.make_rows([{"nested": inner}])
        data = encode_grid(outer)
        decoded = decode_grid(data)
        nested = decoded[0]["nested"]
        assert isinstance(nested, Grid)
        assert nested[0]["x"] == 1

    def test_json_bytes_valid(self) -> None:
        g = Grid.make_rows([{"a": 1}])
        data = encode_grid(g)
        parsed = orjson.loads(data)
        assert parsed["_kind"] == "grid"
        assert "meta" in parsed
        assert "cols" in parsed
        assert "rows" in parsed


class TestV4Errors:
    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown _kind"):
            decode_val({"_kind": "bogus", "val": 1})

    def test_encode_unsupported_raises(self) -> None:
        with pytest.raises(TypeError, match="Cannot encode"):
            encode_val(object())


# ===========================================================================
# V3 Tests
# ===========================================================================


V3 = JsonVersion.V3


class TestV3EncodeDecodeScalars:
    def test_none(self) -> None:
        assert encode_val(None, version=V3) is None
        assert decode_val(None, version=V3) is None

    def test_bool(self) -> None:
        assert encode_val(True, version=V3) is True
        assert encode_val(False, version=V3) is False
        assert decode_val(True, version=V3) is True
        assert decode_val(False, version=V3) is False

    def test_bare_int_passthrough(self) -> None:
        assert decode_val(42, version=V3) == 42

    def test_bare_float_passthrough(self) -> None:
        assert decode_val(3.14, version=V3) == 3.14


class TestV3EncodeDecodeSingletons:
    def test_marker(self) -> None:
        encoded = encode_val(MARKER, version=V3)
        assert encoded == "m:"
        assert decode_val(encoded, version=V3) is MARKER

    def test_na(self) -> None:
        encoded = encode_val(NA, version=V3)
        assert encoded == "z:"
        assert decode_val(encoded, version=V3) is NA

    def test_remove(self) -> None:
        encoded = encode_val(REMOVE, version=V3)
        assert encoded == "-:"
        assert decode_val(encoded, version=V3) is REMOVE


class TestV3EncodeDecodeNumber:
    def test_unitless(self) -> None:
        encoded = encode_val(Number(72.5), version=V3)
        assert encoded == "n:72.5"
        decoded = decode_val(encoded, version=V3)
        assert isinstance(decoded, Number)
        assert decoded.val == 72.5
        assert decoded.unit is None

    def test_integer_value(self) -> None:
        encoded = encode_val(Number(42.0), version=V3)
        assert encoded == "n:42"
        decoded = decode_val(encoded, version=V3)
        assert decoded.val == 42.0

    def test_with_unit(self) -> None:
        encoded = encode_val(Number(72.5, "°F"), version=V3)
        assert encoded == "n:72.5 °F"
        decoded = decode_val(encoded, version=V3)
        assert isinstance(decoded, Number)
        assert decoded.val == 72.5
        assert decoded.unit == "°F"

    def test_nan(self) -> None:
        encoded = encode_val(Number(float("nan")), version=V3)
        assert encoded == "n:NaN"
        decoded = decode_val(encoded, version=V3)
        assert isinstance(decoded, Number)
        assert math.isnan(decoded.val)

    def test_inf(self) -> None:
        encoded = encode_val(Number(float("inf")), version=V3)
        assert encoded == "n:INF"
        decoded = decode_val(encoded, version=V3)
        assert decoded.val == float("inf")

    def test_neg_inf(self) -> None:
        encoded = encode_val(Number(float("-inf")), version=V3)
        assert encoded == "n:-INF"
        decoded = decode_val(encoded, version=V3)
        assert decoded.val == float("-inf")

    def test_plain_int_encodes_as_number(self) -> None:
        encoded = encode_val(42, version=V3)
        assert encoded == "n:42"

    def test_plain_float_encodes_as_number(self) -> None:
        encoded = encode_val(3.14, version=V3)
        assert encoded == "n:3.14"


class TestV3EncodeDecodeRef:
    def test_basic(self) -> None:
        encoded = encode_val(Ref("abc-123"), version=V3)
        assert encoded == "r:abc-123"
        decoded = decode_val(encoded, version=V3)
        assert decoded == Ref("abc-123")

    def test_with_dis(self) -> None:
        encoded = encode_val(Ref("abc", "My Point"), version=V3)
        assert encoded == "r:abc My Point"
        decoded = decode_val(encoded, version=V3)
        assert decoded == Ref("abc", "My Point")


class TestV3EncodeDecodeSymbol:
    def test_roundtrip(self) -> None:
        encoded = encode_val(Symbol("elec-meter"), version=V3)
        assert encoded == "y:elec-meter"
        assert decode_val(encoded, version=V3) == Symbol("elec-meter")


class TestV3EncodeDecodeUri:
    def test_roundtrip(self) -> None:
        encoded = encode_val(Uri("http://example.com"), version=V3)
        assert encoded == "u:http://example.com"
        assert decode_val(encoded, version=V3) == Uri("http://example.com")


class TestV3EncodeDecodeCoord:
    def test_roundtrip(self) -> None:
        encoded = encode_val(Coord(37.545, -77.449), version=V3)
        assert encoded == "c:37.545,-77.449"
        assert decode_val(encoded, version=V3) == Coord(37.545, -77.449)


class TestV3EncodeDecodeXStr:
    def test_roundtrip(self) -> None:
        encoded = encode_val(XStr("Color", "red"), version=V3)
        assert encoded == "x:Color:red"
        assert decode_val(encoded, version=V3) == XStr("Color", "red")

    def test_value_with_colon(self) -> None:
        encoded = encode_val(XStr("Span", "2024-01-01:now"), version=V3)
        assert encoded == "x:Span:2024-01-01:now"
        decoded = decode_val(encoded, version=V3)
        assert decoded == XStr("Span", "2024-01-01:now")


class TestV3EncodeDecodeDate:
    def test_roundtrip(self) -> None:
        d = date(2024, 7, 17)
        encoded = encode_val(d, version=V3)
        assert encoded == "d:2024-07-17"
        assert decode_val(encoded, version=V3) == d


class TestV3EncodeDecodeTime:
    def test_roundtrip(self) -> None:
        t = time(14, 30, 0)
        encoded = encode_val(t, version=V3)
        assert encoded == "h:14:30:00"
        assert decode_val(encoded, version=V3) == t


class TestV3EncodeDecodeDateTime:
    def test_utc(self) -> None:
        dt = datetime(2024, 7, 17, 16, 55, 42, tzinfo=UTC)
        encoded = encode_val(dt, version=V3)
        assert encoded.startswith("t:")
        assert "UTC" in encoded
        decoded = decode_val(encoded, version=V3)
        assert isinstance(decoded, datetime)

    def test_zoneinfo(self) -> None:
        tz = ZoneInfo("America/New_York")
        dt = datetime(2024, 7, 17, 12, 0, 0, tzinfo=tz)
        encoded = encode_val(dt, version=V3)
        assert "New_York" in encoded
        assert "America/" not in encoded
        decoded = decode_val(encoded, version=V3)
        assert isinstance(decoded, datetime)
        assert decoded.tzinfo is not None

    def test_iso_without_timezone(self) -> None:
        decoded = decode_val("t:2024-07-17T12:00:00+00:00", version=V3)
        assert isinstance(decoded, datetime)

    def test_decode_city_only_tz(self) -> None:
        decoded = decode_val("t:2024-07-17T12:00:00-04:00 New_York", version=V3)
        assert isinstance(decoded, datetime)
        assert decoded.tzinfo == ZoneInfo("America/New_York")

    def test_roundtrip_preserves_tz(self) -> None:
        tz = ZoneInfo("America/Denver")
        dt = datetime(2024, 7, 17, 12, 0, 0, tzinfo=tz)
        encoded = encode_val(dt, version=V3)
        assert "Denver" in encoded
        decoded = decode_val(encoded, version=V3)
        assert decoded.tzinfo == ZoneInfo("America/Denver")


class TestV3EncodeDecodeStr:
    def test_plain(self) -> None:
        assert encode_val("hello", version=V3) == "hello"
        assert decode_val("hello", version=V3) == "hello"

    def test_ambiguous_marker_prefix(self) -> None:
        encoded = encode_val("m:fake", version=V3)
        assert encoded == "s:m:fake"
        decoded = decode_val(encoded, version=V3)
        assert decoded == "m:fake"

    def test_ambiguous_number_prefix(self) -> None:
        encoded = encode_val("n:test", version=V3)
        assert encoded == "s:n:test"
        decoded = decode_val(encoded, version=V3)
        assert decoded == "n:test"

    def test_ambiguous_ref_prefix(self) -> None:
        encoded = encode_val("r:not-a-ref", version=V3)
        assert encoded == "s:r:not-a-ref"
        decoded = decode_val(encoded, version=V3)
        assert decoded == "r:not-a-ref"

    def test_safe_colon(self) -> None:
        # 'q' is not a v3 type prefix, so no s: needed
        assert encode_val("q:test", version=V3) == "q:test"
        assert decode_val("q:test", version=V3) == "q:test"

    def test_str_with_colon_not_at_pos2(self) -> None:
        assert encode_val("hello:world", version=V3) == "hello:world"
        assert decode_val("hello:world", version=V3) == "hello:world"

    def test_empty_string(self) -> None:
        assert encode_val("", version=V3) == ""
        assert decode_val("", version=V3) == ""

    def test_single_char(self) -> None:
        assert encode_val("a", version=V3) == "a"
        assert decode_val("a", version=V3) == "a"


# ---- V3 Collection round-trips -------------------------------------------


class TestV3EncodeDecodeCollections:
    def test_list(self) -> None:
        val = [MARKER, Number(42.0), "hello"]
        encoded = encode_val(val, version=V3)
        assert encoded == ["m:", "n:42", "hello"]
        decoded = decode_val(encoded, version=V3)
        assert decoded[0] is MARKER
        assert decoded[1] == Number(42.0)
        assert decoded[2] == "hello"

    def test_dict(self) -> None:
        val = {"site": MARKER, "area": Number(5000, "ft²"), "dis": "Site-A"}
        encoded = encode_val(val, version=V3)
        assert encoded["site"] == "m:"
        assert encoded["area"] == "n:5000 ft²"
        assert encoded["dis"] == "Site-A"
        decoded = decode_val(encoded, version=V3)
        assert decoded["site"] is MARKER
        assert decoded["area"] == Number(5000, "ft²")
        assert decoded["dis"] == "Site-A"


# ---- V3 Grid encoding/decoding -------------------------------------------


class TestV3GridEncoding:
    def test_empty_grid(self) -> None:
        g = Grid.make_empty()
        data = encode_grid(g, version=V3)
        decoded = decode_grid(data, version=V3)
        assert decoded.is_empty

    def test_simple_grid(self) -> None:
        g = Grid.make_rows(
            [
                {"id": Ref("a"), "dis": "Alpha", "site": MARKER},
                {"id": Ref("b"), "dis": "Beta"},
            ]
        )
        data = encode_grid(g, version=V3)
        decoded = decode_grid(data, version=V3)
        assert len(decoded) == 2
        assert decoded[0]["id"] == Ref("a")
        assert decoded[0]["site"] is MARKER
        assert decoded[1]["dis"] == "Beta"

    def test_no_kind_in_v3_grid(self) -> None:
        g = Grid.make_rows([{"x": Number(1.0)}])
        data = encode_grid(g, version=V3)
        raw = orjson.loads(data)
        assert "_kind" not in raw

    def test_col_meta_flattened(self) -> None:
        g = (
            GridBuilder()
            .add_col("temp", meta={"unit": "°F"})
            .add_row({"temp": Number(72.5, "°F")})
            .to_grid()
        )
        data = encode_grid(g, version=V3)
        raw = orjson.loads(data)
        # v3: col meta is flattened onto the col object
        assert raw["cols"][0]["unit"] == "°F"
        assert "meta" not in raw["cols"][0]

        decoded = decode_grid(data, version=V3)
        assert decoded.cols[0].meta["unit"] == "°F"

    def test_v3_grid_values_string_encoded(self) -> None:
        g = Grid.make_rows([{"site": MARKER, "area": Number(5000, "ft²")}])
        data = encode_grid(g, version=V3)
        raw = orjson.loads(data)
        assert raw["rows"][0]["site"] == "m:"
        assert raw["rows"][0]["area"] == "n:5000 ft²"


class TestV3Errors:
    def test_encode_unsupported_raises(self) -> None:
        with pytest.raises(TypeError, match="Cannot encode"):
            encode_val(object(), version=V3)


# ===========================================================================
# Pythonic Decode Tests
# ===========================================================================


class TestPythonicV4:
    def test_marker_becomes_true(self) -> None:
        encoded = encode_val(MARKER)
        decoded = decode_val(encoded, pythonic=True)
        assert decoded is True

    def test_unitless_number_becomes_float(self) -> None:
        # Number with _kind object → decoded to Number → pythonic to float
        encoded = {"_kind": "number", "val": 42.0}
        decoded = decode_val(encoded, pythonic=True)
        assert decoded == 42.0
        assert isinstance(decoded, float)

    def test_number_with_unit_preserved(self) -> None:
        encoded = encode_val(Number(72.5, "°F"))
        decoded = decode_val(encoded, pythonic=True)
        assert isinstance(decoded, Number)
        assert decoded.unit == "°F"

    def test_symbol_becomes_str(self) -> None:
        encoded = encode_val(Symbol("site"))
        decoded = decode_val(encoded, pythonic=True)
        assert decoded == "site"
        assert isinstance(decoded, str)

    def test_uri_becomes_str(self) -> None:
        encoded = encode_val(Uri("http://example.com"))
        decoded = decode_val(encoded, pythonic=True)
        assert decoded == "http://example.com"
        assert isinstance(decoded, str)

    def test_ref_preserved(self) -> None:
        encoded = encode_val(Ref("abc"))
        decoded = decode_val(encoded, pythonic=True)
        assert isinstance(decoded, Ref)

    def test_na_preserved(self) -> None:
        encoded = encode_val(NA)
        decoded = decode_val(encoded, pythonic=True)
        assert decoded is NA

    def test_remove_preserved(self) -> None:
        encoded = encode_val(REMOVE)
        decoded = decode_val(encoded, pythonic=True)
        assert decoded is REMOVE

    def test_coord_preserved(self) -> None:
        encoded = encode_val(Coord(37.545, -77.449))
        decoded = decode_val(encoded, pythonic=True)
        assert isinstance(decoded, Coord)

    def test_xstr_preserved(self) -> None:
        encoded = encode_val(XStr("Color", "red"))
        decoded = decode_val(encoded, pythonic=True)
        assert isinstance(decoded, XStr)

    def test_date_preserved(self) -> None:
        d = date(2024, 7, 17)
        encoded = encode_val(d)
        decoded = decode_val(encoded, pythonic=True)
        assert decoded == d

    def test_dict_recursive(self) -> None:
        encoded = {
            "site": {"_kind": "marker"},
            "name": "hello",
            "uri": {"_kind": "uri", "val": "/api"},
        }
        decoded = decode_val(encoded, pythonic=True)
        assert decoded["site"] is True
        assert decoded["name"] == "hello"
        assert decoded["uri"] == "/api"

    def test_list_recursive(self) -> None:
        encoded = [{"_kind": "marker"}, {"_kind": "symbol", "val": "site"}]
        decoded = decode_val(encoded, pythonic=True)
        assert decoded == [True, "site"]

    def test_none_passthrough(self) -> None:
        assert decode_val(None, pythonic=True) is None

    def test_bool_passthrough(self) -> None:
        assert decode_val(True, pythonic=True) is True
        assert decode_val(False, pythonic=True) is False

    def test_str_passthrough(self) -> None:
        assert decode_val("hello", pythonic=True) == "hello"


class TestPythonicV3:
    def test_marker_becomes_true(self) -> None:
        decoded = decode_val("m:", version=V3, pythonic=True)
        assert decoded is True

    def test_unitless_number_becomes_float(self) -> None:
        decoded = decode_val("n:42", version=V3, pythonic=True)
        assert decoded == 42.0
        assert isinstance(decoded, float)

    def test_number_with_unit_preserved(self) -> None:
        decoded = decode_val("n:72.5 °F", version=V3, pythonic=True)
        assert isinstance(decoded, Number)
        assert decoded.unit == "°F"

    def test_symbol_becomes_str(self) -> None:
        decoded = decode_val("y:site", version=V3, pythonic=True)
        assert decoded == "site"
        assert isinstance(decoded, str)

    def test_uri_becomes_str(self) -> None:
        decoded = decode_val("u:http://example.com", version=V3, pythonic=True)
        assert decoded == "http://example.com"
        assert isinstance(decoded, str)


class TestPythonicGrid:
    def test_grid_values_transformed(self) -> None:
        g = Grid.make_rows(
            [
                {
                    "id": Ref("a"),
                    "site": MARKER,
                    "area": Number(5000, "ft²"),
                    "uri": Uri("/api"),
                },
            ]
        )
        data = encode_grid(g)
        decoded = decode_grid(data, pythonic=True)

        row = decoded[0]
        assert isinstance(row["id"], Ref)
        assert row["site"] is True
        assert isinstance(row["area"], Number)
        assert row["uri"] == "/api"

    def test_grid_meta_transformed(self) -> None:
        g = (
            GridBuilder()
            .add_meta("hisURI", Uri("/api/his"))
            .add_col("ts")
            .add_row({"ts": "2024-01-01"})
            .to_grid()
        )
        data = encode_grid(g)
        decoded = decode_grid(data, pythonic=True)
        assert decoded.meta["hisURI"] == "/api/his"

    def test_grid_col_meta_transformed(self) -> None:
        g = (
            GridBuilder()
            .add_col("temp", meta={"kind": Symbol("number")})
            .add_row({"temp": Number(72.5, "°F")})
            .to_grid()
        )
        data = encode_grid(g)
        decoded = decode_grid(data, pythonic=True)
        assert decoded.cols[0].meta["kind"] == "number"

    def test_v3_grid_pythonic(self) -> None:
        g = Grid.make_rows([{"site": MARKER, "dis": "Test"}])
        data = encode_grid(g, version=V3)
        decoded = decode_grid(data, version=V3, pythonic=True)
        assert decoded[0]["site"] is True
        assert decoded[0]["dis"] == "Test"


# ===========================================================================
# Cross-version tests
# ===========================================================================


class TestCrossVersion:
    """Test that values can be encoded in one version and decoded correctly."""

    @pytest.mark.parametrize(
        "val",
        [
            MARKER,
            NA,
            REMOVE,
            Number(42.0),
            Number(72.5, "°F"),
            Number(float("nan")),
            Number(float("inf")),
            Number(float("-inf")),
            Ref("abc-123"),
            Ref("abc", "Display"),
            Symbol("site"),
            Uri("http://example.com"),
            Coord(37.545, -77.449),
            XStr("Color", "red"),
            date(2024, 7, 17),
            time(14, 30, 0),
            True,
            False,
            None,
            "hello",
        ],
        ids=lambda v: type(v).__name__,
    )
    def test_v3_roundtrip(self, val: object) -> None:
        encoded = encode_val(val, version=V3)
        decoded = decode_val(encoded, version=V3)
        if isinstance(val, Number) and math.isnan(val.val):
            assert isinstance(decoded, Number)
            assert math.isnan(decoded.val)
        else:
            assert decoded == val

    def test_v4_to_v3_grid(self) -> None:
        """Encode grid as v4, decode values manually, re-encode as v3."""
        g = Grid.make_rows([{"id": Ref("a"), "site": MARKER, "area": Number(5000, "ft²")}])
        # Encode as v4 bytes, decode, then re-encode as v3
        v4_data = encode_grid(g)
        decoded = decode_grid(v4_data)
        v3_data = encode_grid(decoded, version=V3)
        re_decoded = decode_grid(v3_data, version=V3)
        assert re_decoded[0]["id"] == Ref("a")
        assert re_decoded[0]["site"] is MARKER
