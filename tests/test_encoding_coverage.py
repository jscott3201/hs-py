"""Tests for JSON and Zinc encoding edge cases to fill coverage gaps."""

from __future__ import annotations

import datetime

import pytest

from hs_py.encoding.json import (
    JsonVersion,
    _decode_val_v3,
    _decode_val_v4,
    _encode_val_v3,
    decode_grid,
    decode_grid_dict,
    encode_grid,
    encode_grid_dict,
)
from hs_py.encoding.zinc import decode_grid as zinc_decode
from hs_py.encoding.zinc import encode_grid as zinc_encode
from hs_py.encoding.zinc import encode_val as zinc_encode_val
from hs_py.grid import Col, Grid
from hs_py.kinds import NA, REMOVE, Coord, Marker, Number, Ref, Symbol, Uri, XStr


class TestJsonV3Encoding:
    """Cover V3 encoder/decoder paths."""

    def test_encode_v3_grid(self) -> None:
        grid = Grid.make_rows([{"id": Ref("s1"), "dis": "Site"}])
        data = encode_grid(grid, version=JsonVersion.V3)
        assert b"s1" in data

    def test_encode_grid_dict_v3(self) -> None:
        grid = Grid.make_rows([{"val": Number(42.0, "°F")}])
        d = encode_grid_dict(grid, version=JsonVersion.V3)
        assert "rows" in d

    def test_decode_v3_grid(self) -> None:
        d = {
            "meta": {"ver": "3.0"},
            "cols": [{"name": "id"}, {"name": "dis"}],
            "rows": [{"id": "r:s1", "dis": "s:Site"}],
        }
        grid = decode_grid_dict(d, version=JsonVersion.V3)
        assert len(grid.rows) == 1

    def test_decode_v3_types(self) -> None:
        # Marker
        assert isinstance(_decode_val_v3("m:"), Marker)
        # NA
        assert _decode_val_v3("z:") is NA
        # Remove
        assert _decode_val_v3("-:") is REMOVE
        # String
        assert _decode_val_v3("s:hello") == "hello"
        # Number
        val = _decode_val_v3("n:42.0")
        assert isinstance(val, Number)
        # Ref
        val = _decode_val_v3("r:abc")
        assert isinstance(val, Ref)
        # Symbol
        val = _decode_val_v3("y:site")
        assert isinstance(val, Symbol)
        # URI
        val = _decode_val_v3("u:http://example.com")
        assert isinstance(val, Uri)
        # Coord
        val = _decode_val_v3("c:37.55,-77.45")
        assert isinstance(val, Coord)
        # Date
        val = _decode_val_v3("d:2024-01-01")
        assert isinstance(val, datetime.date)
        # Time
        val = _decode_val_v3("h:12:30:00")
        assert isinstance(val, datetime.time)

    def test_decode_v3_xstr(self) -> None:
        val = _decode_val_v3("x:Bin:text/plain")
        assert isinstance(val, XStr)

    def test_decode_v3_dict(self) -> None:
        result = _decode_val_v3({"a": "n:1", "b": "s:hello"})
        assert isinstance(result, dict)
        assert isinstance(result["a"], Number)

    def test_decode_v3_list(self) -> None:
        result = _decode_val_v3(["n:1", "n:2"])
        assert isinstance(result, list)
        assert len(result) == 2

    def test_decode_v3_unsupported(self) -> None:
        with pytest.raises(TypeError, match="Cannot decode"):
            _decode_val_v3(object())

    def test_encode_v3_nested_grid(self) -> None:
        inner = Grid.make_rows([{"x": Number(1.0)}])
        grid = Grid.make_rows([{"nested": inner}])
        data = encode_grid(grid, version=JsonVersion.V3)
        assert data is not None

    def test_encode_v3_list_val(self) -> None:
        result = _encode_val_v3([Number(1.0), Number(2.0)])
        assert isinstance(result, list)

    def test_encode_v3_dict_val(self) -> None:
        result = _encode_val_v3({"a": Number(1.0)})
        assert isinstance(result, dict)

    def test_encode_v3_datetime(self) -> None:
        dt = datetime.datetime(2024, 1, 15, 10, 30, tzinfo=datetime.UTC)
        result = _encode_val_v3(dt)
        assert isinstance(result, str)
        assert result.startswith("t:")

    def test_encode_v3_date(self) -> None:
        d = datetime.date(2024, 1, 15)
        result = _encode_val_v3(d)
        assert result.startswith("d:")

    def test_encode_v3_time(self) -> None:
        t = datetime.time(10, 30)
        result = _encode_val_v3(t)
        assert result.startswith("h:")

    def test_encode_v3_plain_int(self) -> None:
        result = _encode_val_v3(42)
        assert isinstance(result, str)
        assert result.startswith("n:")


class TestJsonV4EdgeCases:
    """Cover V4 decoder edge cases."""

    def test_decode_v4_depth_exceeded(self) -> None:
        with pytest.raises(ValueError, match="depth exceeded"):
            _decode_val_v4({"a": 1}, _depth=100)

    def test_decode_v4_unsupported_type(self) -> None:
        with pytest.raises(TypeError, match="Cannot decode"):
            _decode_val_v4(object())

    def test_decode_grid_dict_pythonic(self) -> None:
        d = {
            "cols": [{"name": "val"}],
            "rows": [{"val": {"_kind": "number", "val": 42.0}}],
        }
        grid = decode_grid_dict(d, pythonic=True)
        assert grid.rows[0]["val"] == 42.0

    def test_encode_decode_v4_roundtrip(self) -> None:
        grid = Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1", "val": Number(72.5, "°F")},
            ]
        )
        data = encode_grid(grid)
        decoded = decode_grid(data)
        assert decoded.rows[0]["dis"] == "Point 1"


class TestZincEncodingEdgeCases:
    """Cover Zinc encoding edge cases."""

    def test_encode_decode_empty_grid(self) -> None:
        grid = Grid.make_empty()
        zinc = zinc_encode(grid)
        decoded = zinc_decode(zinc)
        assert len(decoded.rows) == 0

    def test_encode_grid_with_col_meta(self) -> None:
        cols = (Col(name="val", meta={"dis": "Value", "unit": "°F"}),)
        grid = Grid(meta={}, cols=cols, rows=({"val": Number(72.5, "°F")},))
        zinc = zinc_encode(grid)
        assert "val" in zinc

    def test_decode_grid_meta_only(self) -> None:
        zinc = 'ver:"3.0" dis:"Test"'
        grid = zinc_decode(zinc)
        assert grid.meta.get("dis") == "Test"
        assert len(grid.rows) == 0

    def test_encode_datetime_with_tz(self) -> None:
        dt = datetime.datetime(2024, 1, 15, 10, 30, tzinfo=datetime.UTC)
        result = zinc_encode_val(dt)
        assert "2024" in result
        assert "UTC" in result

    def test_encode_datetime_without_tz(self) -> None:
        dt = datetime.datetime(2024, 1, 15, 10, 30)
        result = zinc_encode_val(dt)
        assert "2024" in result

    def test_encode_date(self) -> None:
        d = datetime.date(2024, 1, 15)
        result = zinc_encode_val(d)
        assert "2024-01-15" in result

    def test_encode_time(self) -> None:
        t = datetime.time(10, 30, 0)
        result = zinc_encode_val(t)
        assert "10:30" in result

    def test_encode_list(self) -> None:
        result = zinc_encode_val([Number(1.0), Number(2.0)])
        assert "[" in result and "]" in result

    def test_encode_xstr(self) -> None:
        result = zinc_encode_val(XStr("Bin", "text/plain"))
        assert "Bin" in result

    def test_encode_symbol(self) -> None:
        result = zinc_encode_val(Symbol("site"))
        assert "^site" in result

    def test_encode_na(self) -> None:
        result = zinc_encode_val(NA)
        assert "NA" in result

    def test_encode_remove(self) -> None:
        result = zinc_encode_val(REMOVE)
        assert "R" in result

    def test_encode_bool(self) -> None:
        assert zinc_encode_val(True) == "T"
        assert zinc_encode_val(False) == "F"

    def test_encode_dict(self) -> None:
        result = zinc_encode_val({"site": Marker(), "dis": "Hello"})
        assert "{" in result

    def test_decode_grid_with_empty_cells(self) -> None:
        zinc = 'ver:"3.0"\nid,dis\n@s1,"Site 1"\n,\n'
        grid = zinc_decode(zinc)
        assert len(grid.rows) >= 1
