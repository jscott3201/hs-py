"""Tests for scanner.py and encoding edge-case coverage gaps."""

from __future__ import annotations

import datetime

import pytest

from hs_py.encoding.scanner import (
    MAX_SCAN_DEPTH,
    _scan_coord_body,
    _scan_nested_grid,
    scan_dict,
    scan_keyword,
    scan_list,
    scan_uri,
    scan_val,
    tz_name,
)
from hs_py.kinds import MARKER, Coord, Number, Uri, XStr


class TestTzName:
    """Cover tz_name edge cases."""

    def test_naive_datetime_returns_none(self) -> None:
        dt = datetime.datetime(2024, 1, 1, 12, 0, 0)
        assert tz_name(dt) is None

    def test_fixed_offset_tz_returns_none(self) -> None:
        tz = datetime.timezone(datetime.timedelta(hours=5))
        dt = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz)
        assert tz_name(dt) is None

    def test_utc_returns_utc(self) -> None:
        dt = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        assert tz_name(dt) == "UTC"


class TestScanList:
    """Cover scan_list comma handling and edge cases."""

    def test_empty_list(self) -> None:
        items, pos = scan_list("[]", 0)
        assert items == []
        assert pos == 2

    def test_single_item(self) -> None:
        items, _pos = scan_list("[42]", 0)
        assert len(items) == 1
        assert isinstance(items[0], Number)

    def test_multiple_items_with_commas(self) -> None:
        items, _pos = scan_list('[1, 2, "hello"]', 0)
        assert len(items) == 3

    def test_unterminated_list(self) -> None:
        # Scanner doesn't raise, just returns what it got
        items, _pos = scan_list("[1, 2", 0)
        assert len(items) == 2


class TestScanDict:
    """Cover scan_dict comma handling and pair parsing."""

    def test_empty_dict(self) -> None:
        result, _pos = scan_dict("{}", 0)
        assert result == {}

    def test_marker_tag(self) -> None:
        result, _pos = scan_dict("{site}", 0)
        assert result["site"] is MARKER

    def test_value_tag(self) -> None:
        result, _pos = scan_dict('{dis:"Hello"}', 0)
        assert result["dis"] == "Hello"

    def test_multiple_tags_with_commas(self) -> None:
        result, _pos = scan_dict('{dis:"A", site}', 0)
        assert result["dis"] == "A"
        assert result["site"] is MARKER


class TestScanUri:
    """Cover scan_uri escape and length handling."""

    def test_simple_uri(self) -> None:
        val, _pos = scan_uri("`http://example.com`", 0)
        assert val == Uri("http://example.com")

    def test_uri_with_escape(self) -> None:
        val, _pos = scan_uri(r"`hello\:world`", 0)
        assert isinstance(val, Uri)
        assert ":" in val.val


class TestScanKeyword:
    """Cover XStr parsing, Coord detection, bare identifiers."""

    def test_xstr(self) -> None:
        val, _pos = scan_keyword('Bin("text/plain")', 0)
        assert isinstance(val, XStr)
        assert val.type_name == "Bin"
        assert val.val == "text/plain"

    def test_coord(self) -> None:
        val, _pos = scan_keyword("C(37.55,-77.45)", 0)
        assert isinstance(val, Coord)
        assert val.lat == pytest.approx(37.55)
        assert val.lng == pytest.approx(-77.45)

    def test_bare_identifier(self) -> None:
        val, _pos = scan_keyword("customTag ", 0)
        assert val == "customTag"


class TestScanCoordBody:
    """Cover _scan_coord_body edge cases."""

    def test_basic_coord(self) -> None:
        val, _pos = _scan_coord_body("(37.55,-77.45)", 0)
        assert isinstance(val, Coord)
        assert val.lat == pytest.approx(37.55)
        assert val.lng == pytest.approx(-77.45)


class TestScanNestedGrid:
    """Cover nested grid scanning."""

    def test_basic_nested_grid(self) -> None:
        zinc_inner = 'ver:"3.0"\nempty'
        text = f"<<{zinc_inner}>>"
        _val, pos = _scan_nested_grid(text, 0)
        assert pos == len(text)

    def test_max_depth_exceeded(self) -> None:
        with pytest.raises(ValueError, match="depth exceeded"):
            _scan_nested_grid("<<foo>>", 0, _depth=MAX_SCAN_DEPTH + 1)

    def test_unterminated_nested_grid(self) -> None:
        with pytest.raises(ValueError, match="Unterminated"):
            _scan_nested_grid("<<foo", 0)


class TestScanValEdgeCases:
    """Cover scan_val edge cases."""

    def test_negative_inf(self) -> None:
        val, _pos = scan_val("-INF", 0)
        assert isinstance(val, Number)
        assert val.val == float("-inf")

    def test_max_depth_exceeded(self) -> None:
        with pytest.raises(ValueError, match="depth exceeded"):
            scan_val("[1]", 0, _depth=MAX_SCAN_DEPTH + 1)

    def test_unexpected_char(self) -> None:
        with pytest.raises(ValueError, match="Unexpected character"):
            scan_val("!", 0)

    def test_empty_text(self) -> None:
        val, _pos = scan_val("", 0)
        assert val is None
