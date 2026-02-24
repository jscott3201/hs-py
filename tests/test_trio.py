import datetime
import math
from zoneinfo import ZoneInfo

import pytest

from hs_py.encoding.trio import encode_trio, parse_trio, parse_zinc_val
from hs_py.grid import Col, Grid
from hs_py.kinds import MARKER, NA, REMOVE, Coord, Number, Ref, Symbol, Uri, XStr

# ---- parse_zinc_val: scalars ------------------------------------------------


class TestZincValScalars:
    def test_bool_true(self) -> None:
        assert parse_zinc_val("T") is True

    def test_bool_false(self) -> None:
        assert parse_zinc_val("F") is False

    def test_marker(self) -> None:
        assert parse_zinc_val("M") is MARKER

    def test_na(self) -> None:
        assert parse_zinc_val("NA") is NA

    def test_remove(self) -> None:
        assert parse_zinc_val("R") is REMOVE

    def test_null(self) -> None:
        assert parse_zinc_val("N") is None

    def test_empty(self) -> None:
        assert parse_zinc_val("") is None

    def test_string(self) -> None:
        assert parse_zinc_val('"hello world"') == "hello world"

    def test_string_escapes(self) -> None:
        assert parse_zinc_val(r'"line1\nline2"') == "line1\nline2"

    def test_string_unicode_escape(self) -> None:
        assert parse_zinc_val(r'"caf\u00e9"') == "caf\u00e9"

    def test_ref_simple(self) -> None:
        assert parse_zinc_val("@p1") == Ref("p1")

    def test_ref_with_display(self) -> None:
        assert parse_zinc_val('@site-1 "Main Site"') == Ref("site-1", "Main Site")

    def test_symbol(self) -> None:
        assert parse_zinc_val("^elec-meter") == Symbol("elec-meter")

    def test_uri(self) -> None:
        assert parse_zinc_val("`http://example.com`") == Uri("http://example.com")

    def test_coord(self) -> None:
        assert parse_zinc_val("C(37.545,-77.449)") == Coord(37.545, -77.449)

    def test_xstr(self) -> None:
        assert parse_zinc_val('Bin("text/plain")') == XStr("Bin", "text/plain")


# ---- parse_zinc_val: numbers ------------------------------------------------


class TestZincValNumbers:
    def test_integer(self) -> None:
        assert parse_zinc_val("42") == Number(42.0)

    def test_float(self) -> None:
        assert parse_zinc_val("3.14") == Number(3.14)

    def test_negative(self) -> None:
        assert parse_zinc_val("-10") == Number(-10.0)

    def test_with_unit(self) -> None:
        assert parse_zinc_val("72\u00b0F") == Number(72.0, "\u00b0F")

    def test_inf(self) -> None:
        n = parse_zinc_val("INF")
        assert isinstance(n, Number) and math.isinf(n.val) and n.val > 0

    def test_neg_inf(self) -> None:
        n = parse_zinc_val("-INF")
        assert isinstance(n, Number) and math.isinf(n.val) and n.val < 0

    def test_nan(self) -> None:
        n = parse_zinc_val("NaN")
        assert isinstance(n, Number) and math.isnan(n.val)


# ---- parse_zinc_val: temporal ------------------------------------------------


class TestZincValTemporal:
    def test_date(self) -> None:
        assert parse_zinc_val("2024-01-15") == datetime.date(2024, 1, 15)

    def test_time(self) -> None:
        assert parse_zinc_val("08:30:00") == datetime.time(8, 30, 0)

    def test_time_with_millis(self) -> None:
        assert parse_zinc_val("08:30:00.123") == datetime.time(8, 30, 0, 123000)

    def test_datetime_utc(self) -> None:
        dt = parse_zinc_val("2024-01-15T08:30:00Z")
        expected = datetime.datetime(2024, 1, 15, 8, 30, 0, tzinfo=datetime.UTC)
        assert dt == expected

    def test_datetime_with_tz(self) -> None:
        dt = parse_zinc_val("2024-01-15T08:30:00-05:00 America/New_York")
        assert dt.tzinfo == ZoneInfo("America/New_York")


# ---- parse_zinc_val: collections ---------------------------------------------


class TestZincValCollections:
    def test_empty_list(self) -> None:
        assert parse_zinc_val("[]") == []

    def test_list_of_symbols(self) -> None:
        result = parse_zinc_val("[^hot, ^water]")
        assert result == [Symbol("hot"), Symbol("water")]

    def test_list_of_numbers(self) -> None:
        result = parse_zinc_val("[1, 2, 3]")
        assert result == [Number(1.0), Number(2.0), Number(3.0)]

    def test_list_mixed(self) -> None:
        result = parse_zinc_val('[^site, "hello", 42]')
        assert result == [Symbol("site"), "hello", Number(42.0)]

    def test_empty_dict(self) -> None:
        assert parse_zinc_val("{}") == {}

    def test_dict_markers(self) -> None:
        result = parse_zinc_val("{point sensor}")
        assert result == {"point": MARKER, "sensor": MARKER}

    def test_dict_with_values(self) -> None:
        result = parse_zinc_val('{dis:"Hello" val:42}')
        assert result == {"dis": "Hello", "val": Number(42.0)}

    def test_nested_list_in_dict(self) -> None:
        result = parse_zinc_val("{tags:[^a, ^b]}")
        assert result == {"tags": [Symbol("a"), Symbol("b")]}


# ---- parse_trio: basic records -----------------------------------------------


class TestParseTrio:
    def test_empty(self) -> None:
        assert parse_trio("") == []

    def test_single_record(self) -> None:
        text = 'point\nsensor\ndis: "Test Point"\n'
        records = parse_trio(text)
        assert len(records) == 1
        assert records[0]["point"] is MARKER
        assert records[0]["sensor"] is MARKER
        assert records[0]["dis"] == "Test Point"

    def test_multiple_records(self) -> None:
        text = "---\ndef: ^site\nis: ^entity\n---\ndef: ^equip\nis: ^entity\n"
        records = parse_trio(text)
        assert len(records) == 2
        assert records[0]["def"] == Symbol("site")
        assert records[1]["def"] == Symbol("equip")

    def test_multiline_string(self) -> None:
        text = "def: ^site\ndoc:\n  Line one.\n  Line two.\n"
        records = parse_trio(text)
        assert records[0]["doc"] == "Line one.\nLine two."

    def test_multiline_string_with_blank(self) -> None:
        text = "doc:\n  Line one.\n\n  Line three.\n"
        records = parse_trio(text)
        assert records[0]["doc"] == "Line one.\n\nLine three."

    def test_comments(self) -> None:
        text = "// This is a comment\ndef: ^site\n// Another comment\nis: ^entity\n"
        records = parse_trio(text)
        assert len(records) == 1
        assert records[0]["def"] == Symbol("site")

    def test_inline_comment(self) -> None:
        text = 'dis: "Hello" // display name\npoint\n'
        records = parse_trio(text)
        assert records[0]["dis"] == "Hello"
        assert records[0]["point"] is MARKER

    def test_record_without_separator(self) -> None:
        text = 'def: ^site\nis: ^entity\ndoc: "A site."\n'
        records = parse_trio(text)
        assert len(records) == 1

    def test_list_value(self) -> None:
        text = "def: ^hot-water\nis: [^hot, ^water]\n"
        records = parse_trio(text)
        assert records[0]["is"] == [Symbol("hot"), Symbol("water")]

    def test_number_value(self) -> None:
        text = "val: 72\u00b0F\n"
        records = parse_trio(text)
        assert records[0]["val"] == Number(72.0, "\u00b0F")

    def test_ref_value(self) -> None:
        text = "siteRef: @site-1\n"
        records = parse_trio(text)
        assert records[0]["siteRef"] == Ref("site-1")

    def test_typical_def_record(self) -> None:
        text = """\
---
def: ^site
is: ^entity
doc:
  Site is a geographic location of a facility
  such as a building, campus, or data center.
marker
---
def: ^equip
is: ^entity
doc: "Equipment asset."
"""
        records = parse_trio(text)
        assert len(records) == 2
        assert records[0]["def"] == Symbol("site")
        assert records[0]["is"] == Symbol("entity")
        assert "Site is a geographic" in records[0]["doc"]
        assert "such as a building" in records[0]["doc"]
        assert records[0]["marker"] is MARKER
        assert records[1]["def"] == Symbol("equip")
        assert records[1]["doc"] == "Equipment asset."


# ---- parse_trio: Trio-specific value handling --------------------------------


class TestTrioValues:
    """Test Trio-specific value parsing extensions beyond Zinc."""

    def test_true_keyword(self) -> None:
        records = parse_trio("active: true\n")
        assert records[0]["active"] is True

    def test_false_keyword(self) -> None:
        records = parse_trio("deleted: false\n")
        assert records[0]["deleted"] is False

    def test_zinc_bool_true(self) -> None:
        records = parse_trio("active: T\n")
        assert records[0]["active"] is True

    def test_zinc_bool_false(self) -> None:
        records = parse_trio("deleted: F\n")
        assert records[0]["deleted"] is False

    def test_unquoted_string(self) -> None:
        records = parse_trio("dis: My Building\n")
        assert records[0]["dis"] == "My Building"

    def test_unquoted_string_with_special_chars(self) -> None:
        records = parse_trio("dis: Building #1 (Main)\n")
        assert records[0]["dis"] == "Building #1 (Main)"

    def test_quoted_string(self) -> None:
        records = parse_trio('dis: "My Building"\n')
        assert records[0]["dis"] == "My Building"

    def test_zinc_values_preferred_over_unquoted(self) -> None:
        """Zinc scalar syntax takes priority over unquoted string."""
        records = parse_trio("val: 42\n")
        assert records[0]["val"] == Number(42.0)

    def test_ref_not_unquoted_string(self) -> None:
        records = parse_trio("siteRef: @site-1\n")
        assert records[0]["siteRef"] == Ref("site-1")

    def test_symbol_not_unquoted_string(self) -> None:
        records = parse_trio("def: ^site\n")
        assert records[0]["def"] == Symbol("site")

    def test_uri_not_unquoted_string(self) -> None:
        records = parse_trio("link: `http://example.com`\n")
        assert records[0]["link"] == Uri("http://example.com")

    def test_coord_value(self) -> None:
        records = parse_trio("geoCoord: C(37.545,-77.449)\n")
        assert records[0]["geoCoord"] == Coord(37.545, -77.449)

    def test_na_value(self) -> None:
        records = parse_trio("val: NA\n")
        assert records[0]["val"] is NA

    def test_remove_value(self) -> None:
        records = parse_trio("old: R\n")
        assert records[0]["old"] is REMOVE

    def test_null_value(self) -> None:
        records = parse_trio("val: N\n")
        assert records[0]["val"] is None

    def test_date_value(self) -> None:
        records = parse_trio("date: 2024-01-15\n")
        assert records[0]["date"] == datetime.date(2024, 1, 15)

    def test_time_value(self) -> None:
        records = parse_trio("time: 08:30:00\n")
        assert records[0]["time"] == datetime.time(8, 30, 0)

    def test_datetime_value(self) -> None:
        records = parse_trio("ts: 2024-01-15T08:30:00Z\n")
        expected = datetime.datetime(2024, 1, 15, 8, 30, 0, tzinfo=datetime.UTC)
        assert records[0]["ts"] == expected

    def test_list_value(self) -> None:
        records = parse_trio("tags: [^hot, ^water]\n")
        assert records[0]["tags"] == [Symbol("hot"), Symbol("water")]

    def test_dict_value(self) -> None:
        records = parse_trio('meta: {point sensor dis:"AHU"}\n')
        assert records[0]["meta"] == {
            "point": MARKER,
            "sensor": MARKER,
            "dis": "AHU",
        }


# ---- parse_trio: separators --------------------------------------------------


class TestTrioSeparators:
    """Test flexible record separator handling per spec."""

    def test_three_dashes(self) -> None:
        text = "---\ndef: ^a\n---\ndef: ^b\n"
        records = parse_trio(text)
        assert len(records) == 2

    def test_long_dash_line(self) -> None:
        text = "----------\ndef: ^a\n----------\ndef: ^b\n"
        records = parse_trio(text)
        assert len(records) == 2

    def test_single_dash(self) -> None:
        text = "-\ndef: ^a\n-\ndef: ^b\n"
        records = parse_trio(text)
        assert len(records) == 2

    def test_separator_with_leading_whitespace(self) -> None:
        text = "  ---\ndef: ^a\n  ---\ndef: ^b\n"
        records = parse_trio(text)
        assert len(records) == 2

    def test_multiple_separators_no_records(self) -> None:
        text = "---\n---\n---\n"
        records = parse_trio(text)
        assert len(records) == 0

    def test_separator_at_end(self) -> None:
        text = "def: ^site\n---\n"
        records = parse_trio(text)
        assert len(records) == 1
        assert records[0]["def"] == Symbol("site")


# ---- parse_trio: multi-line strings ------------------------------------------


class TestTrioMultilineStrings:
    """Test multi-line string handling per Trio spec."""

    def test_basic(self) -> None:
        text = "doc:\n  Line one.\n  Line two.\n"
        records = parse_trio(text)
        assert records[0]["doc"] == "Line one.\nLine two."

    def test_with_blank_lines(self) -> None:
        text = "doc:\n  Line one.\n\n  Line three.\n"
        records = parse_trio(text)
        assert records[0]["doc"] == "Line one.\n\nLine three."

    def test_tab_indented(self) -> None:
        text = "doc:\n\tLine one.\n\tLine two.\n"
        records = parse_trio(text)
        assert records[0]["doc"] == "Line one.\nLine two."

    def test_deeper_indent(self) -> None:
        text = "doc:\n    Line one.\n    Line two.\n"
        records = parse_trio(text)
        assert records[0]["doc"] == "Line one.\nLine two."

    def test_mixed_indent_depths(self) -> None:
        text = "doc:\n  First.\n    Indented.\n  Back.\n"
        records = parse_trio(text)
        assert records[0]["doc"] == "First.\n  Indented.\nBack."

    def test_terminated_by_next_tag(self) -> None:
        text = "doc:\n  Hello.\npoint\n"
        records = parse_trio(text)
        assert records[0]["doc"] == "Hello."
        assert records[0]["point"] is MARKER

    def test_terminated_by_separator(self) -> None:
        text = "doc:\n  Hello.\n---\ndef: ^next\n"
        records = parse_trio(text)
        assert len(records) == 2
        assert records[0]["doc"] == "Hello."
        assert records[1]["def"] == Symbol("next")

    def test_terminated_by_eof(self) -> None:
        text = "doc:\n  Hello."
        records = parse_trio(text)
        assert records[0]["doc"] == "Hello."

    def test_empty_multiline(self) -> None:
        """Tag with colon and no value, followed by non-indented line."""
        text = "doc:\npoint\n"
        records = parse_trio(text)
        assert records[0]["doc"] == ""
        assert records[0]["point"] is MARKER

    def test_trailing_blank_lines_stripped(self) -> None:
        text = "doc:\n  Hello.\n\n\ndef: ^site\n"
        records = parse_trio(text)
        assert records[0]["doc"] == "Hello."


# ---- parse_trio: Zinc: multi-line --------------------------------------------


class TestTrioZincMultiline:
    """Test Zinc: multi-line mode for nested grids."""

    def test_nested_grid(self) -> None:
        text = 'data: Zinc:\n  ver:"3.0"\n  id,val\n  @p1,42\n'
        records = parse_trio(text)
        grid = records[0]["data"]
        assert isinstance(grid, Grid)
        assert len(grid.rows) == 1
        assert grid.rows[0]["id"] == Ref("p1")
        assert grid.rows[0]["val"] == Number(42.0)

    def test_nested_grid_with_meta(self) -> None:
        text = (
            'data: Zinc:\n  ver:"3.0" hisStart:2024-01-01\n  ts,val\n  2024-01-01T00:00:00Z,72\n'
        )
        records = parse_trio(text)
        grid = records[0]["data"]
        assert isinstance(grid, Grid)
        assert "hisStart" in grid.meta

    def test_zinc_followed_by_tag(self) -> None:
        text = 'data: Zinc:\n  ver:"3.0"\n  id\n  @p1\npoint\n'
        records = parse_trio(text)
        assert isinstance(records[0]["data"], Grid)
        assert records[0]["point"] is MARKER

    def test_zinc_followed_by_separator(self) -> None:
        text = 'data: Zinc:\n  ver:"3.0"\n  id\n  @p1\n---\ndef: ^next\n'
        records = parse_trio(text)
        assert len(records) == 2
        assert isinstance(records[0]["data"], Grid)
        assert records[1]["def"] == Symbol("next")


# ---- parse_trio: Trio: multi-line --------------------------------------------


class TestTrioTrioMultiline:
    """Test Trio: multi-line mode for nested records."""

    def test_nested_records(self) -> None:
        text = (
            "name: Parent\n"
            "children: Trio:\n"
            "  ---\n"
            "  def: ^child1\n"
            "  val: 1\n"
            "  ---\n"
            "  def: ^child2\n"
            "  val: 2\n"
        )
        records = parse_trio(text)
        assert len(records) == 1
        children = records[0]["children"]
        assert isinstance(children, list)
        assert len(children) == 2
        assert children[0]["def"] == Symbol("child1")
        assert children[0]["val"] == Number(1.0)
        assert children[1]["def"] == Symbol("child2")
        assert children[1]["val"] == Number(2.0)

    def test_nested_followed_by_tag(self) -> None:
        text = "children: Trio:\n  ---\n  def: ^child1\npoint\n"
        records = parse_trio(text)
        assert len(records[0]["children"]) == 1
        assert records[0]["point"] is MARKER

    def test_nested_followed_by_separator(self) -> None:
        text = "children: Trio:\n  ---\n  def: ^child1\n---\ndef: ^next\n"
        records = parse_trio(text)
        assert len(records) == 2
        assert len(records[0]["children"]) == 1
        assert records[1]["def"] == Symbol("next")

    def test_nested_without_indented_separator(self) -> None:
        """Nested Trio records without separator — just indented tags."""
        text = "items: Trio:\n  def: ^only\n  val: 1\n"
        records = parse_trio(text)
        items = records[0]["items"]
        assert len(items) == 1
        assert items[0]["def"] == Symbol("only")


# ---- parse_trio: edge cases --------------------------------------------------


class TestParseTrioEdgeCases:
    def test_blank_lines_ignored(self) -> None:
        text = "\n\ndef: ^site\n\nis: ^entity\n\n"
        records = parse_trio(text)
        assert len(records) == 1

    def test_only_comments(self) -> None:
        text = "// just a comment\n// another\n"
        records = parse_trio(text)
        assert len(records) == 0

    def test_separator_only(self) -> None:
        text = "---\n---\n"
        records = parse_trio(text)
        assert len(records) == 0

    def test_multiline_then_next_tag(self) -> None:
        text = "doc:\n  Hello.\npoint\n"
        records = parse_trio(text)
        assert records[0]["doc"] == "Hello."
        assert records[0]["point"] is MARKER

    def test_bool_values(self) -> None:
        text = "active: T\ndeleted: F\n"
        records = parse_trio(text)
        assert records[0]["active"] is True
        assert records[0]["deleted"] is False

    def test_comment_in_string_preserved(self) -> None:
        text = 'dis: "Hello // World"\n'
        records = parse_trio(text)
        assert records[0]["dis"] == "Hello // World"

    def test_uri_value(self) -> None:
        text = "baseUri: `https://project-haystack.org/def/ph/`\n"
        records = parse_trio(text)
        assert records[0]["baseUri"] == Uri("https://project-haystack.org/def/ph/")

    def test_unterminated_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Unterminated"):
            parse_zinc_val('"hello')

    def test_comment_in_uri_preserved(self) -> None:
        text = "link: `http://example.com/path//extra`\n"
        records = parse_trio(text)
        assert records[0]["link"] == Uri("http://example.com/path//extra")

    def test_whitespace_around_colon(self) -> None:
        text = "dis : value here\n"
        records = parse_trio(text)
        assert records[0]["dis"] == "value here"

    def test_multiple_tags_various_types(self) -> None:
        text = (
            "def: ^ahu\n"
            "is: [^equip, ^airHandlingEquip]\n"
            "dis: Air Handling Unit\n"
            "geoCoord: C(37.545,-77.449)\n"
            "area: 1000ft²\n"
            "marker\n"
        )
        records = parse_trio(text)
        r = records[0]
        assert r["def"] == Symbol("ahu")
        assert r["is"] == [Symbol("equip"), Symbol("airHandlingEquip")]
        assert r["dis"] == "Air Handling Unit"
        assert r["geoCoord"] == Coord(37.545, -77.449)
        assert r["area"] == Number(1000.0, "ft²")
        assert r["marker"] is MARKER


# ---- encode_trio -------------------------------------------------------------


class TestEncodeTrio:
    """Test Trio encoding."""

    def test_marker_tag(self) -> None:
        encoded = encode_trio([{"point": MARKER}])
        assert "point\n" in encoded
        assert "point:" not in encoded

    def test_scalar_value(self) -> None:
        encoded = encode_trio([{"val": Number(42.0, "°F")}])
        assert "val: 42°F\n" in encoded

    def test_string_value(self) -> None:
        encoded = encode_trio([{"dis": "Hello"}])
        assert 'dis: "Hello"\n' in encoded

    def test_ref_value(self) -> None:
        encoded = encode_trio([{"siteRef": Ref("site-1")}])
        assert "siteRef: @site-1\n" in encoded

    def test_symbol_value(self) -> None:
        encoded = encode_trio([{"def": Symbol("site")}])
        assert "def: ^site\n" in encoded

    def test_bool_values(self) -> None:
        encoded = encode_trio([{"active": True, "deleted": False}])
        assert "active: T\n" in encoded
        assert "deleted: F\n" in encoded

    def test_multiline_string(self) -> None:
        encoded = encode_trio([{"doc": "Line one.\nLine two."}])
        assert "doc:\n" in encoded
        assert "  Line one.\n" in encoded
        assert "  Line two.\n" in encoded

    def test_multiline_string_with_blank(self) -> None:
        encoded = encode_trio([{"doc": "Line one.\n\nLine three."}])
        lines = encoded.split("\n")
        # Find the blank line in the output
        doc_start = next(i for i, ln in enumerate(lines) if ln == "doc:")
        assert lines[doc_start + 1] == "  Line one."
        assert lines[doc_start + 2] == ""
        assert lines[doc_start + 3] == "  Line three."

    def test_separator_between_records(self) -> None:
        encoded = encode_trio(
            [
                {"def": Symbol("a")},
                {"def": Symbol("b")},
            ]
        )
        assert encoded.count("---") == 2

    def test_nested_grid_zinc_mode(self) -> None:
        grid = Grid(
            cols=(Col("id"), Col("val")),
            rows=({"id": Ref("p1"), "val": Number(42.0)},),
        )
        encoded = encode_trio([{"data": grid}])
        assert "data: Zinc:\n" in encoded
        assert 'ver:"3.0"' in encoded

    def test_nested_records_trio_mode(self) -> None:
        children = [
            {"def": Symbol("child1"), "val": Number(1.0)},
            {"def": Symbol("child2"), "val": Number(2.0)},
        ]
        encoded = encode_trio([{"children": children}])
        assert "children: Trio:\n" in encoded
        assert "^child1" in encoded
        assert "^child2" in encoded

    def test_empty_records(self) -> None:
        encoded = encode_trio([])
        assert encoded == "\n"

    def test_list_value(self) -> None:
        encoded = encode_trio([{"tags": [Symbol("hot"), Symbol("water")]}])
        assert "tags: [^hot, ^water]\n" in encoded

    def test_none_value(self) -> None:
        encoded = encode_trio([{"val": None}])
        assert "val: N\n" in encoded

    def test_na_value(self) -> None:
        encoded = encode_trio([{"val": NA}])
        assert "val: NA\n" in encoded

    def test_uri_value(self) -> None:
        encoded = encode_trio([{"link": Uri("http://example.com")}])
        assert "link: `http://example.com`\n" in encoded


# ---- encode_trio roundtrips --------------------------------------------------


class TestTrioRoundtrip:
    """Test encode_trio → parse_trio roundtrips."""

    def test_simple_record(self) -> None:
        original = [{"def": Symbol("site"), "is": Symbol("entity"), "marker": MARKER}]
        decoded = parse_trio(encode_trio(original))
        assert len(decoded) == 1
        assert decoded[0]["def"] == Symbol("site")
        assert decoded[0]["is"] == Symbol("entity")
        assert decoded[0]["marker"] is MARKER

    def test_multiple_records(self) -> None:
        original = [
            {"def": Symbol("site"), "is": Symbol("entity")},
            {"def": Symbol("equip"), "is": Symbol("entity")},
        ]
        decoded = parse_trio(encode_trio(original))
        assert len(decoded) == 2
        assert decoded[0]["def"] == Symbol("site")
        assert decoded[1]["def"] == Symbol("equip")

    def test_multiline_string(self) -> None:
        original = [{"doc": "Line one.\nLine two.\nLine three."}]
        decoded = parse_trio(encode_trio(original))
        assert decoded[0]["doc"] == "Line one.\nLine two.\nLine three."

    def test_multiline_string_with_blank(self) -> None:
        original = [{"doc": "Para one.\n\nPara two."}]
        decoded = parse_trio(encode_trio(original))
        assert decoded[0]["doc"] == "Para one.\n\nPara two."

    def test_mixed_value_types(self) -> None:
        original = [
            {
                "def": Symbol("ahu"),
                "marker": MARKER,
                "val": Number(72.0, "°F"),
                "siteRef": Ref("site-1"),
                "active": True,
                "deleted": False,
                "link": Uri("http://example.com"),
            }
        ]
        decoded = parse_trio(encode_trio(original))
        r = decoded[0]
        assert r["def"] == Symbol("ahu")
        assert r["marker"] is MARKER
        assert r["val"] == Number(72.0, "°F")
        assert r["siteRef"] == Ref("site-1")
        assert r["active"] is True
        assert r["deleted"] is False
        assert r["link"] == Uri("http://example.com")

    def test_nested_grid_roundtrip(self) -> None:
        grid = Grid(
            cols=(Col("id"), Col("val")),
            rows=({"id": Ref("p1"), "val": Number(42.0)},),
        )
        original = [{"name": "test", "data": grid}]
        decoded = parse_trio(encode_trio(original))
        result_grid = decoded[0]["data"]
        assert isinstance(result_grid, Grid)
        assert len(result_grid.rows) == 1
        assert result_grid.rows[0]["id"] == Ref("p1")

    def test_nested_trio_roundtrip(self) -> None:
        children = [
            {"def": Symbol("child1"), "val": Number(1.0)},
            {"def": Symbol("child2"), "val": Number(2.0)},
        ]
        original = [{"name": "parent", "children": children}]
        decoded = parse_trio(encode_trio(original))
        result_children = decoded[0]["children"]
        assert len(result_children) == 2
        assert result_children[0]["def"] == Symbol("child1")
        assert result_children[1]["def"] == Symbol("child2")


# ---- Trio spec examples from project-haystack.org ---------------------------


class TestTrioSpecExamples:
    """Test against examples from the Trio specification."""

    def test_spec_example_basic(self) -> None:
        """From the spec: basic record with markers, strings, refs."""
        text = """\
---
dis: "Site 1"
site
area: 3500ft²
geoAddr: "100 Main St, Richmond, VA"
geoCoord: C(37.5458,-77.4491)
strTag: OK if unquoted
---
dis: "Site 2"
site
area: 4000ft²
"""
        records = parse_trio(text)
        assert len(records) == 2
        r0 = records[0]
        assert r0["dis"] == "Site 1"
        assert r0["site"] is MARKER
        assert r0["area"] == Number(3500.0, "ft²")
        assert r0["geoAddr"] == "100 Main St, Richmond, VA"
        assert r0["geoCoord"] == Coord(37.5458, -77.4491)
        assert r0["strTag"] == "OK if unquoted"
        r1 = records[1]
        assert r1["dis"] == "Site 2"
        assert r1["area"] == Number(4000.0, "ft²")

    def test_spec_example_multiline_string(self) -> None:
        """From the spec: multi-line string continuation."""
        text = """\
dis: "Example"
doc:
  This is a multi-line string value
  that spans two lines.
"""
        records = parse_trio(text)
        assert records[0]["doc"] == ("This is a multi-line string value\nthat spans two lines.")

    def test_spec_example_def_with_doc(self) -> None:
        """A typical def record as seen in Haystack libraries."""
        text = """\
---
def: ^site
is: ^geoPlace
doc:
  Site is a geographic location of a facility such as a
  building, campus, or data center.
mandatory
---
def: ^geoPlace
is: ^entity
doc: "Geographic place."
"""
        records = parse_trio(text)
        assert len(records) == 2
        assert records[0]["def"] == Symbol("site")
        assert records[0]["is"] == Symbol("geoPlace")
        assert "geographic location" in records[0]["doc"]
        assert records[0]["mandatory"] is MARKER
        assert records[1]["def"] == Symbol("geoPlace")
        assert records[1]["doc"] == "Geographic place."
