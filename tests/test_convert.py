"""Tests for the pythonic conversion helper (convert.py)."""

from __future__ import annotations

import datetime

from hs_py.convert import grid_to_pythonic
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, NA, REMOVE, Coord, Marker, Na, Number, Ref, Remove, Symbol, Uri


class TestGridToPythonic:
    """Tests for grid_to_pythonic()."""

    def _make_grid(self, rows: list[dict]) -> Grid:
        return Grid.make_rows(rows)

    # ---- Marker ----------------------------------------------------------------

    def test_marker_becomes_true(self) -> None:
        grid = self._make_grid([{"point": MARKER}])
        result = grid_to_pythonic(grid)
        assert result == [{"point": True}]

    def test_marker_singleton_identity(self) -> None:
        grid = self._make_grid([{"sensor": Marker()}])
        result = grid_to_pythonic(grid)
        assert result[0]["sensor"] is True

    # ---- Na --------------------------------------------------------------------

    def test_na_becomes_none(self) -> None:
        grid = self._make_grid([{"val": NA}])
        result = grid_to_pythonic(grid)
        assert result == [{"val": None}]

    def test_na_singleton_identity(self) -> None:
        grid = self._make_grid([{"x": Na()}])
        result = grid_to_pythonic(grid)
        assert result[0]["x"] is None

    # ---- Remove ----------------------------------------------------------------

    def test_remove_omits_key(self) -> None:
        grid = self._make_grid([{"keep": "yes", "gone": REMOVE}])
        result = grid_to_pythonic(grid)
        assert result == [{"keep": "yes"}]
        assert "gone" not in result[0]

    def test_remove_singleton_omits_key(self) -> None:
        grid = self._make_grid([{"a": Remove(), "b": "b"}])
        result = grid_to_pythonic(grid)
        assert list(result[0].keys()) == ["b"]

    # ---- Number ----------------------------------------------------------------

    def test_unitless_number_becomes_float(self) -> None:
        grid = self._make_grid([{"val": Number(72.5)}])
        result = grid_to_pythonic(grid)
        assert result[0]["val"] == 72.5
        assert isinstance(result[0]["val"], float)

    def test_number_with_unit_kept(self) -> None:
        grid = self._make_grid([{"temp": Number(72.5, "°F")}])
        result = grid_to_pythonic(grid)
        assert result[0]["temp"] == Number(72.5, "°F")
        assert isinstance(result[0]["temp"], Number)

    def test_number_zero_unitless(self) -> None:
        grid = self._make_grid([{"count": Number(0.0)}])
        result = grid_to_pythonic(grid)
        assert result[0]["count"] == 0.0
        assert isinstance(result[0]["count"], float)

    def test_number_with_empty_unit_becomes_float(self) -> None:
        # Number with unit=None (default) collapses to float
        grid = self._make_grid([{"level": Number(1.0, None)}])
        result = grid_to_pythonic(grid)
        assert result[0]["level"] == 1.0
        assert isinstance(result[0]["level"], float)

    # ---- Symbol ----------------------------------------------------------------

    def test_symbol_becomes_str(self) -> None:
        grid = self._make_grid([{"kind": Symbol("site")}])
        result = grid_to_pythonic(grid)
        assert result[0]["kind"] == "site"
        assert isinstance(result[0]["kind"], str)

    def test_symbol_val_preserved(self) -> None:
        grid = self._make_grid([{"def": Symbol("ph::site")}])
        result = grid_to_pythonic(grid)
        assert result[0]["def"] == "ph::site"

    # ---- Uri -------------------------------------------------------------------

    def test_uri_becomes_str(self) -> None:
        grid = self._make_grid([{"href": Uri("http://example.com/api")}])
        result = grid_to_pythonic(grid)
        assert result[0]["href"] == "http://example.com/api"
        assert isinstance(result[0]["href"], str)

    # ---- Ref -------------------------------------------------------------------

    def test_ref_kept_as_ref(self) -> None:
        ref = Ref("abc-123")
        grid = self._make_grid([{"id": ref}])
        result = grid_to_pythonic(grid)
        assert result[0]["id"] == Ref("abc-123")
        assert isinstance(result[0]["id"], Ref)

    def test_ref_identity_preserved(self) -> None:
        ref = Ref("p1", "Point 1")
        grid = self._make_grid([{"id": ref}])
        result = grid_to_pythonic(grid)
        assert result[0]["id"] is ref

    # ---- Pass-throughs ---------------------------------------------------------

    def test_str_passthrough(self) -> None:
        grid = self._make_grid([{"name": "hello"}])
        result = grid_to_pythonic(grid)
        assert result[0]["name"] == "hello"

    def test_bool_passthrough(self) -> None:
        grid = self._make_grid([{"flag": True}, {"flag": False}])
        result = grid_to_pythonic(grid)
        assert result[0]["flag"] is True
        assert result[1]["flag"] is False

    def test_int_passthrough(self) -> None:
        grid = self._make_grid([{"count": 42}])
        result = grid_to_pythonic(grid)
        assert result[0]["count"] == 42
        assert isinstance(result[0]["count"], int)

    def test_float_passthrough(self) -> None:
        grid = self._make_grid([{"ratio": 3.14}])
        result = grid_to_pythonic(grid)
        assert result[0]["ratio"] == 3.14

    def test_none_passthrough(self) -> None:
        grid = self._make_grid([{"val": None}])
        result = grid_to_pythonic(grid)
        assert result[0]["val"] is None

    def test_datetime_passthrough(self) -> None:
        ts = datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
        grid = self._make_grid([{"ts": ts}])
        result = grid_to_pythonic(grid)
        assert result[0]["ts"] == ts

    def test_date_passthrough(self) -> None:
        d = datetime.date(2024, 1, 1)
        grid = self._make_grid([{"date": d}])
        result = grid_to_pythonic(grid)
        assert result[0]["date"] == d

    def test_time_passthrough(self) -> None:
        t = datetime.time(12, 30, 0)
        grid = self._make_grid([{"time": t}])
        result = grid_to_pythonic(grid)
        assert result[0]["time"] == t

    def test_coord_passthrough(self) -> None:
        c = Coord(lat=51.5074, lng=-0.1278)
        grid = self._make_grid([{"geoCoord": c}])
        result = grid_to_pythonic(grid)
        assert result[0]["geoCoord"] == c

    # ---- Nested Grid -----------------------------------------------------------

    def test_nested_grid_converted_recursively(self) -> None:
        inner = Grid.make_rows([{"point": MARKER, "id": Ref("p1")}])
        outer = Grid.make_rows([{"name": "site", "children": inner}])
        result = grid_to_pythonic(outer)
        assert isinstance(result[0]["children"], list)
        children = result[0]["children"]
        assert len(children) == 1
        # Nested Marker should also be converted
        assert children[0]["point"] is True
        # Nested Ref should be kept
        assert children[0]["id"] == Ref("p1")

    def test_nested_grid_deeply_nested(self) -> None:
        inner2 = Grid.make_rows([{"val": Number(1.0)}])
        inner1 = Grid.make_rows([{"sub": inner2}])
        outer = Grid.make_rows([{"top": inner1}])
        result = grid_to_pythonic(outer)
        top = result[0]["top"]
        assert isinstance(top, list)
        sub = top[0]["sub"]
        assert isinstance(sub, list)
        assert sub[0]["val"] == 1.0

    # ---- Multiple rows ---------------------------------------------------------

    def test_multiple_rows(self) -> None:
        grid = Grid.make_rows(
            [
                {"id": Ref("p1"), "point": MARKER, "val": Number(72.0)},
                {"id": Ref("p2"), "point": MARKER, "val": Number(73.5, "°F")},
            ]
        )
        result = grid_to_pythonic(grid)
        assert len(result) == 2
        assert result[0]["point"] is True
        assert result[0]["val"] == 72.0
        assert result[1]["val"] == Number(73.5, "°F")

    # ---- Empty grid ------------------------------------------------------------

    def test_empty_grid_returns_empty_list(self) -> None:
        grid = Grid.make_empty()
        result = grid_to_pythonic(grid)
        assert result == []

    # ---- Mixed row with remove -------------------------------------------------

    def test_row_with_multiple_removes(self) -> None:
        grid = self._make_grid([{"id": Ref("p1"), "old1": REMOVE, "old2": REMOVE, "dis": "Point"}])
        result = grid_to_pythonic(grid)
        assert result == [{"id": Ref("p1"), "dis": "Point"}]

    # ---- GridBuilder compatibility ---------------------------------------------

    def test_grid_builder_output(self) -> None:
        grid = (
            GridBuilder()
            .add_col("id")
            .add_col("dis")
            .add_col("point")
            .add_row({"id": Ref("p1"), "dis": "Sensor", "point": MARKER})
            .to_grid()
        )
        result = grid_to_pythonic(grid)
        assert result == [{"id": Ref("p1"), "dis": "Sensor", "point": True}]
