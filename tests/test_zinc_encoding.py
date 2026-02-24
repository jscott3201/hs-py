"""Tests for Zinc encoding and decoding.

Covers scalar encode/decode, grid encode/decode, and roundtrip correctness.
"""

import datetime
import math
from zoneinfo import ZoneInfo

import pytest

from hs_py.encoding.scanner import city_to_tz, tz_to_city
from hs_py.encoding.trio import parse_zinc_val
from hs_py.encoding.zinc import decode_grid, decode_val, encode_grid, encode_val
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, NA, REMOVE, Coord, Number, Ref, Symbol, Uri, XStr

# ---------------------------------------------------------------------------
# Scalar encode
# ---------------------------------------------------------------------------


class TestZincEncodeScalar:
    def test_null(self) -> None:
        assert encode_val(None) == "N"

    def test_bool_true(self) -> None:
        assert encode_val(True) == "T"

    def test_bool_false(self) -> None:
        assert encode_val(False) == "F"

    def test_marker(self) -> None:
        assert encode_val(MARKER) == "M"

    def test_na(self) -> None:
        assert encode_val(NA) == "NA"

    def test_remove(self) -> None:
        assert encode_val(REMOVE) == "R"

    def test_number_int(self) -> None:
        assert encode_val(Number(42.0)) == "42"

    def test_number_float(self) -> None:
        assert encode_val(Number(3.14)) == "3.14"

    def test_number_negative(self) -> None:
        assert encode_val(Number(-10.0)) == "-10"

    def test_number_with_unit(self) -> None:
        assert encode_val(Number(72.0, "°F")) == "72°F"

    def test_number_inf(self) -> None:
        assert encode_val(Number(float("inf"))) == "INF"

    def test_number_neg_inf(self) -> None:
        assert encode_val(Number(float("-inf"))) == "-INF"

    def test_number_nan(self) -> None:
        assert encode_val(Number(float("nan"))) == "NaN"

    def test_string_simple(self) -> None:
        assert encode_val("hello") == '"hello"'

    def test_string_escapes(self) -> None:
        assert encode_val("line1\nline2") == r'"line1\nline2"'

    def test_string_tab_escape(self) -> None:
        assert encode_val("a\tb") == r'"a\tb"'

    def test_string_backslash_escape(self) -> None:
        assert encode_val("a\\b") == r'"a\\b"'

    def test_string_quote_escape(self) -> None:
        assert encode_val('say "hi"') == r'"say \"hi\""'

    def test_string_dollar_escape(self) -> None:
        assert encode_val("$100") == r'"\$100"'

    def test_string_backspace_escape(self) -> None:
        assert encode_val("a\bb") == r'"a\bb"'

    def test_string_formfeed_escape(self) -> None:
        assert encode_val("a\fb") == r'"a\fb"'

    def test_string_cr_escape(self) -> None:
        assert encode_val("a\rb") == r'"a\rb"'

    def test_ref_simple(self) -> None:
        assert encode_val(Ref("site-1")) == "@site-1"

    def test_ref_with_display(self) -> None:
        assert encode_val(Ref("site-1", "HQ")) == '@site-1 "HQ"'

    def test_symbol(self) -> None:
        assert encode_val(Symbol("elec-meter")) == "^elec-meter"

    def test_uri(self) -> None:
        assert encode_val(Uri("http://example.com")) == "`http://example.com`"

    def test_uri_with_backtick_escape(self) -> None:
        assert encode_val(Uri("a`b")) == r"`a\`b`"

    def test_coord(self) -> None:
        assert encode_val(Coord(37.55, -77.45)) == "C(37.55,-77.45)"

    def test_xstr(self) -> None:
        assert encode_val(XStr("Bin", "text/plain")) == 'Bin("text/plain")'

    def test_date(self) -> None:
        assert encode_val(datetime.date(2024, 1, 15)) == "2024-01-15"

    def test_time(self) -> None:
        assert encode_val(datetime.time(8, 30, 0)) == "08:30:00"

    def test_time_with_millis(self) -> None:
        assert encode_val(datetime.time(8, 30, 0, 123000)) == "08:30:00.123000"

    def test_datetime_utc(self) -> None:
        dt = datetime.datetime(2024, 1, 15, 8, 30, 0, tzinfo=datetime.UTC)
        result = encode_val(dt)
        assert "2024-01-15T08:30:00" in result

    def test_datetime_with_tz(self) -> None:
        tz = ZoneInfo("America/New_York")
        dt = datetime.datetime(2024, 1, 15, 8, 30, 0, tzinfo=tz)
        result = encode_val(dt)
        assert "New_York" in result
        assert "America/" not in result

    def test_list(self) -> None:
        assert encode_val([Number(1.0), Number(2.0)]) == "[1, 2]"

    def test_dict(self) -> None:
        result = encode_val({"dis": "Hello", "site": MARKER})
        assert 'dis:"Hello"' in result
        assert "site" in result

    def test_nested_grid(self) -> None:
        b = GridBuilder()
        b.add_col("a")
        b.add_row({"a": Number(1.0)})
        result = encode_val(b.to_grid())
        assert result.startswith("<<")
        assert result.endswith(">>")
        assert 'ver:"3.0"' in result

    def test_plain_int(self) -> None:
        assert encode_val(42) == "42"

    def test_plain_float(self) -> None:
        assert encode_val(3.14) == "3.14"

    def test_unsupported_type(self) -> None:
        with pytest.raises(TypeError, match="Cannot encode"):
            encode_val(object())


# ---------------------------------------------------------------------------
# Scalar decode (via scanner)
# ---------------------------------------------------------------------------


class TestZincDecodeScalar:
    def test_null(self) -> None:
        assert decode_val("N") is None

    def test_empty(self) -> None:
        assert decode_val("") is None

    def test_marker(self) -> None:
        assert decode_val("M") is MARKER

    def test_remove(self) -> None:
        assert decode_val("R") is REMOVE

    def test_na(self) -> None:
        assert decode_val("NA") is NA

    def test_bool_true(self) -> None:
        assert decode_val("T") is True

    def test_bool_false(self) -> None:
        assert decode_val("F") is False

    def test_number(self) -> None:
        assert decode_val("42") == Number(42.0)

    def test_number_with_unit(self) -> None:
        assert decode_val("72°F") == Number(72.0, "°F")

    def test_string(self) -> None:
        assert decode_val('"hello world"') == "hello world"

    def test_ref(self) -> None:
        assert decode_val("@site-1") == Ref("site-1")

    def test_symbol(self) -> None:
        assert decode_val("^elec-meter") == Symbol("elec-meter")

    def test_uri(self) -> None:
        assert decode_val("`http://example.com`") == Uri("http://example.com")

    def test_date(self) -> None:
        assert decode_val("2024-01-15") == datetime.date(2024, 1, 15)

    def test_time(self) -> None:
        assert decode_val("08:30:00") == datetime.time(8, 30, 0)

    def test_datetime_utc(self) -> None:
        dt = decode_val("2024-01-15T08:30:00Z")
        expected = datetime.datetime(2024, 1, 15, 8, 30, 0, tzinfo=datetime.UTC)
        assert dt == expected

    def test_datetime_city_tz(self) -> None:
        """Haystack spec uses city-only timezone names."""
        dt = decode_val("2024-01-15T08:30:00-05:00 New_York")
        assert dt.tzinfo == ZoneInfo("America/New_York")

    def test_datetime_full_iana_tz(self) -> None:
        dt = decode_val("2024-01-15T08:30:00-05:00 America/New_York")
        assert dt.tzinfo == ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Timezone city name utilities
# ---------------------------------------------------------------------------


class TestTzToCity:
    def test_continent_city(self) -> None:
        assert tz_to_city("America/New_York") == "New_York"

    def test_multi_level(self) -> None:
        assert tz_to_city("America/Indiana/Indianapolis") == "Indianapolis"

    def test_no_slash(self) -> None:
        assert tz_to_city("UTC") == "UTC"

    def test_etc(self) -> None:
        assert tz_to_city("Etc/UTC") == "UTC"


class TestCityToTz:
    def test_city_only(self) -> None:
        tz = city_to_tz("New_York")
        assert tz == ZoneInfo("America/New_York")

    def test_full_iana(self) -> None:
        tz = city_to_tz("America/New_York")
        assert tz == ZoneInfo("America/New_York")

    def test_utc(self) -> None:
        tz = city_to_tz("UTC")
        assert tz == ZoneInfo("UTC")

    def test_chicago(self) -> None:
        tz = city_to_tz("Chicago")
        assert tz == ZoneInfo("America/Chicago")

    def test_london(self) -> None:
        tz = city_to_tz("London")
        assert tz == ZoneInfo("Europe/London")

    def test_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown timezone"):
            city_to_tz("Not_A_Real_City_12345")


# ---------------------------------------------------------------------------
# Scanner enhancements (Coord, XStr, underscores, escapes)
# ---------------------------------------------------------------------------


class TestScannerCoord:
    def test_coord_basic(self) -> None:
        c = decode_val("C(37.55,-77.45)")
        assert isinstance(c, Coord)
        assert c.lat == 37.55
        assert c.lng == -77.45

    def test_coord_positive_both(self) -> None:
        c = decode_val("C(0,0)")
        assert c == Coord(0.0, 0.0)

    def test_coord_via_parse_zinc_val(self) -> None:
        c = parse_zinc_val("C(37.55,-77.45)")
        assert c == Coord(37.55, -77.45)


class TestScannerXStr:
    def test_xstr_basic(self) -> None:
        x = decode_val('Bin("text/plain")')
        assert isinstance(x, XStr)
        assert x.type_name == "Bin"
        assert x.val == "text/plain"

    def test_xstr_with_escapes(self) -> None:
        x = decode_val('Type("hello\\nworld")')
        assert isinstance(x, XStr)
        assert x.val == "hello\nworld"

    def test_xstr_via_parse_zinc_val(self) -> None:
        x = parse_zinc_val('Hex("deadbeef")')
        assert x == XStr("Hex", "deadbeef")


class TestScannerUnderscoreNumbers:
    def test_integer_underscores(self) -> None:
        n = decode_val("10_000")
        assert isinstance(n, Number)
        assert n.val == 10_000

    def test_float_underscores(self) -> None:
        n = decode_val("1_000.50")
        assert isinstance(n, Number)
        assert n.val == 1000.5

    def test_via_parse_zinc_val(self) -> None:
        n = parse_zinc_val("100_000")
        assert isinstance(n, Number)
        assert n.val == 100_000


class TestScannerStringEscapes:
    def test_backspace(self) -> None:
        assert decode_val(r'"\b"') == "\b"

    def test_formfeed(self) -> None:
        assert decode_val(r'"\f"') == "\f"

    def test_carriage_return(self) -> None:
        assert decode_val(r'"\r"') == "\r"

    def test_dollar(self) -> None:
        assert decode_val(r'"\$"') == "$"

    def test_newline(self) -> None:
        assert decode_val(r'"\n"') == "\n"

    def test_tab(self) -> None:
        assert decode_val(r'"\t"') == "\t"

    def test_unicode(self) -> None:
        assert decode_val(r'"\u00e9"') == "\u00e9"


class TestScannerUriEscapes:
    def test_colon_escape(self) -> None:
        u = decode_val(r"`http\://example.com`")
        assert isinstance(u, Uri)
        assert u.val == "http://example.com"

    def test_hash_escape(self) -> None:
        u = decode_val(r"`/path\#anchor`")
        assert isinstance(u, Uri)
        assert u.val == "/path#anchor"

    def test_backslash_escape(self) -> None:
        u = decode_val(r"`a\\b`")
        assert isinstance(u, Uri)
        assert u.val == "a\\b"

    def test_backtick_escape(self) -> None:
        u = decode_val("`a\\`b`")
        assert isinstance(u, Uri)
        assert u.val == "a`b"


class TestScannerNestedGrid:
    def test_nested_grid_decode(self) -> None:
        text = '<<\nver:"3.0"\na\n1\n>>'
        val = decode_val(text)
        assert isinstance(val, Grid)
        assert len(val.rows) == 1

    def test_nested_grid_in_list(self) -> None:
        text = '[<<\nver:"3.0"\na\n1\n>>]'
        val = decode_val(text)
        assert isinstance(val, list)
        assert len(val) == 1
        assert isinstance(val[0], Grid)


# ---------------------------------------------------------------------------
# Scalar roundtrip (encode → decode)
# ---------------------------------------------------------------------------


class TestZincScalarRoundtrip:
    @pytest.mark.parametrize(
        "val",
        [
            None,
            True,
            False,
            MARKER,
            NA,
            REMOVE,
            Number(42.0),
            Number(3.14),
            Number(-10.0),
            Number(72.0, "°F"),
            "hello world",
            Ref("site-1"),
            Ref("site-1", "Main HQ"),
            Symbol("elec-meter"),
            Uri("http://example.com"),
            Coord(37.55, -77.45),
            XStr("Bin", "text/plain"),
            datetime.date(2024, 1, 15),
            datetime.time(8, 30, 0),
        ],
    )
    def test_roundtrip(self, val: object) -> None:
        encoded = encode_val(val)
        decoded = decode_val(encoded)
        assert decoded == val

    def test_roundtrip_inf(self) -> None:
        n = Number(float("inf"))
        decoded = decode_val(encode_val(n))
        assert isinstance(decoded, Number)
        assert math.isinf(decoded.val) and decoded.val > 0

    def test_roundtrip_neg_inf(self) -> None:
        n = Number(float("-inf"))
        decoded = decode_val(encode_val(n))
        assert isinstance(decoded, Number)
        assert math.isinf(decoded.val) and decoded.val < 0

    def test_roundtrip_nan(self) -> None:
        n = Number(float("nan"))
        decoded = decode_val(encode_val(n))
        assert isinstance(decoded, Number)
        assert math.isnan(decoded.val)

    def test_roundtrip_datetime_utc(self) -> None:
        dt = datetime.datetime(2024, 1, 15, 8, 30, 0, tzinfo=datetime.UTC)
        encoded = encode_val(dt)
        decoded = decode_val(encoded)
        assert decoded == dt

    def test_roundtrip_datetime_zoneinfo(self) -> None:
        tz = ZoneInfo("America/New_York")
        dt = datetime.datetime(2024, 7, 17, 12, 0, 0, tzinfo=tz)
        encoded = encode_val(dt)
        assert "New_York" in encoded
        decoded = decode_val(encoded)
        assert decoded == dt
        assert decoded.tzinfo == tz

    def test_roundtrip_list(self) -> None:
        val = [Number(1.0), "hello", Ref("a")]
        decoded = decode_val(encode_val(val))
        assert decoded == val

    def test_roundtrip_dict(self) -> None:
        val = {"dis": "Hello", "site": MARKER, "area": Number(35000.0, "ft²")}
        decoded = decode_val(encode_val(val))
        assert decoded == val


# ---------------------------------------------------------------------------
# Grid encode
# ---------------------------------------------------------------------------


class TestZincEncodeGrid:
    def test_empty_grid(self) -> None:
        text = encode_grid(Grid())
        assert 'ver:"3.0"' in text
        assert "empty" in text

    def test_simple_grid(self) -> None:
        b = GridBuilder()
        b.add_col("name")
        b.add_col("val")
        b.add_row({"name": "a", "val": Number(1.0)})
        b.add_row({"name": "b", "val": Number(2.0)})
        text = encode_grid(b.to_grid())
        lines = text.split("\n")
        assert lines[0] == 'ver:"3.0"'
        assert lines[1] == "name,val"
        assert lines[2] == '"a",1'
        assert lines[3] == '"b",2'

    def test_grid_with_meta(self) -> None:
        b = GridBuilder()
        b.add_meta("database", "test")
        b.add_meta("dis", "Test Grid")
        b.add_col("a")
        b.add_row({"a": Number(1.0)})
        text = encode_grid(b.to_grid())
        assert 'database:"test"' in text.split("\n")[0]
        assert 'dis:"Test Grid"' in text.split("\n")[0]

    def test_grid_with_col_meta(self) -> None:
        b = GridBuilder()
        b.add_col("val", {"dis": "Value", "unit": "kW"})
        b.add_row({"val": Number(100.0)})
        text = encode_grid(b.to_grid())
        assert 'dis:"Value"' in text.split("\n")[1]
        assert 'unit:"kW"' in text.split("\n")[1]

    def test_grid_with_marker_meta(self) -> None:
        b = GridBuilder()
        b.add_meta("hisRead")
        b.add_col("ts")
        b.add_row({"ts": datetime.date(2024, 1, 1)})
        text = encode_grid(b.to_grid())
        assert "hisRead" in text.split("\n")[0]

    def test_sparse_grid(self) -> None:
        b = GridBuilder()
        b.add_col("a")
        b.add_col("b")
        b.add_row({"a": Number(1.0)})
        b.add_row({"b": Number(2.0)})
        text = encode_grid(b.to_grid())
        lines = text.split("\n")
        assert lines[2] == "1,N"
        assert lines[3] == "N,2"


# ---------------------------------------------------------------------------
# Grid decode
# ---------------------------------------------------------------------------


class TestZincDecodeGrid:
    def test_empty_grid(self) -> None:
        text = 'ver:"3.0"\nempty'
        g = decode_grid(text)
        assert g.cols == ()
        assert g.rows == ()

    def test_simple_grid(self) -> None:
        text = 'ver:"3.0"\nname,val\n"a",1\n"b",2'
        g = decode_grid(text)
        assert len(g.cols) == 2
        assert g.cols[0].name == "name"
        assert g.cols[1].name == "val"
        assert len(g.rows) == 2
        assert g.rows[0]["name"] == "a"
        assert g.rows[0]["val"] == Number(1.0)

    def test_grid_with_meta(self) -> None:
        text = 'ver:"3.0" database:"test" hisRead\nts\n2024-01-01'
        g = decode_grid(text)
        assert g.meta["database"] == "test"
        assert g.meta["hisRead"] is MARKER

    def test_grid_with_col_meta(self) -> None:
        text = 'ver:"3.0"\nval dis:"Value" unit:"kW"\n100'
        g = decode_grid(text)
        assert g.cols[0].meta["dis"] == "Value"
        assert g.cols[0].meta["unit"] == "kW"

    def test_sparse_row(self) -> None:
        text = 'ver:"3.0"\na,b\n1,N\nN,2'
        g = decode_grid(text)
        assert g.rows[0].get("a") == Number(1.0)
        assert "b" not in g.rows[0]
        assert "a" not in g.rows[1]
        assert g.rows[1].get("b") == Number(2.0)

    def test_missing_ver_raises(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            decode_grid("not a grid")

    def test_all_types_in_grid(self) -> None:
        text = (
            'ver:"3.0"\n'
            "str,num,ref,date,time,coord\n"
            '"hello",42°F,@site-1,2024-01-15,08:30:00,C(37.55,-77.45)'
        )
        g = decode_grid(text)
        row = g.rows[0]
        assert row["str"] == "hello"
        assert row["num"] == Number(42.0, "°F")
        assert row["ref"] == Ref("site-1")
        assert row["date"] == datetime.date(2024, 1, 15)
        assert row["time"] == datetime.time(8, 30, 0)
        assert row["coord"] == Coord(37.55, -77.45)


# ---------------------------------------------------------------------------
# Grid roundtrip
# ---------------------------------------------------------------------------


class TestZincGridRoundtrip:
    def test_simple_roundtrip(self) -> None:
        b = GridBuilder()
        b.add_col("name")
        b.add_col("val")
        b.add_row({"name": "a", "val": Number(1.0)})
        b.add_row({"name": "b", "val": Number(2.0)})
        original = b.to_grid()
        text = encode_grid(original)
        decoded = decode_grid(text)
        assert len(decoded.cols) == len(original.cols)
        assert len(decoded.rows) == len(original.rows)
        for i, row in enumerate(decoded.rows):
            for col in decoded.cols:
                assert row.get(col.name) == original.rows[i].get(col.name)

    def test_meta_roundtrip(self) -> None:
        b = GridBuilder()
        b.add_meta("database", "test")
        b.add_meta("hisRead")
        b.add_col("ts", {"dis": "Timestamp"})
        b.add_col("val", {"unit": "kW"})
        b.add_row({"ts": datetime.date(2024, 1, 1), "val": Number(100.0, "kW")})
        original = b.to_grid()
        decoded = decode_grid(encode_grid(original))
        assert decoded.meta == original.meta
        for i, col in enumerate(decoded.cols):
            assert col.name == original.cols[i].name
            assert col.meta == original.cols[i].meta

    def test_sparse_roundtrip(self) -> None:
        b = GridBuilder()
        b.add_col("a")
        b.add_col("b")
        b.add_col("c")
        b.add_row({"a": Number(1.0)})
        b.add_row({"b": Number(2.0)})
        b.add_row({"c": Number(3.0)})
        original = b.to_grid()
        decoded = decode_grid(encode_grid(original))
        assert len(decoded.rows) == 3
        assert decoded.rows[0].get("a") == Number(1.0)
        assert "b" not in decoded.rows[0]
        assert decoded.rows[1].get("b") == Number(2.0)
        assert decoded.rows[2].get("c") == Number(3.0)

    def test_empty_grid_roundtrip(self) -> None:
        original = Grid()
        decoded = decode_grid(encode_grid(original))
        assert decoded.cols == ()
        assert decoded.rows == ()

    def test_rich_types_roundtrip(self) -> None:
        b = GridBuilder()
        b.add_col("ref")
        b.add_col("sym")
        b.add_col("uri")
        b.add_col("coord")
        b.add_col("xstr")
        b.add_row(
            {
                "ref": Ref("site-1", "Main"),
                "sym": Symbol("elec-meter"),
                "uri": Uri("http://example.com"),
                "coord": Coord(37.55, -77.45),
                "xstr": XStr("Bin", "text/plain"),
            }
        )
        original = b.to_grid()
        decoded = decode_grid(encode_grid(original))
        row = decoded.rows[0]
        assert row["ref"] == Ref("site-1", "Main")
        assert row["sym"] == Symbol("elec-meter")
        assert row["uri"] == Uri("http://example.com")
        assert row["coord"] == Coord(37.55, -77.45)
        assert row["xstr"] == XStr("Bin", "text/plain")


# ---------------------------------------------------------------------------
# Spec-format examples from project-haystack.org/doc/docHaystack/Zinc
# ---------------------------------------------------------------------------


class TestZincSpecExamples:
    def test_spec_example_basic(self) -> None:
        """Example from spec: simple two-column grid."""
        text = 'ver:"3.0"\nfirstName,bday\n"Jack",1973-07-23\n"Jill",1975-11-15'
        g = decode_grid(text)
        assert len(g.rows) == 2
        assert g.rows[0]["firstName"] == "Jack"
        assert g.rows[0]["bday"] == datetime.date(1973, 7, 23)
        assert g.rows[1]["firstName"] == "Jill"

    def test_spec_example_with_meta(self) -> None:
        """Example from spec: grid with metadata and column metadata."""
        text = (
            'ver:"3.0" projName:"test"\n'
            'dis "Equip Name",equip,siteRef\n'
            '"RTU-1",M,@153c-699a "HQ"\n'
            '"RTU-2",M,@153c-699a "HQ"'
        )
        g = decode_grid(text)
        assert g.meta["projName"] == "test"
        assert g.cols[0].name == "dis"
        assert g.cols[0].meta.get("dis") == "Equip Name"
        assert g.cols[1].name == "equip"
        assert len(g.rows) == 2
        assert g.rows[0]["dis"] == "RTU-1"
        assert g.rows[0]["equip"] is MARKER
        assert g.rows[0]["siteRef"] == Ref("153c-699a", "HQ")
