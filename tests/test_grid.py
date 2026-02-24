from hs_py.grid import Col, Grid, GridBuilder
from hs_py.kinds import MARKER, Ref


class TestCol:
    def test_basic(self) -> None:
        c = Col("name")
        assert c.name == "name"
        assert c.meta == {}

    def test_with_meta(self) -> None:
        c = Col("temp", meta={"unit": "°F"})
        assert c.meta["unit"] == "°F"

    def test_empty_name_raises(self) -> None:
        try:
            Col("")
            raise AssertionError("should raise")
        except ValueError:
            pass

    def test_frozen(self) -> None:
        c = Col("x")
        try:
            c.name = "y"  # type: ignore[misc]
            raise AssertionError("should be frozen")
        except AttributeError:
            pass


class TestGrid:
    def test_empty(self) -> None:
        g = Grid.make_empty()
        assert g.is_empty
        assert len(g) == 0
        assert not g.is_error

    def test_empty_singleton(self) -> None:
        assert Grid.make_empty() is Grid.make_empty()

    def test_error_grid(self) -> None:
        g = Grid.make_error("something broke", trace="line 1\nline 2")
        assert g.is_error
        assert g.meta["dis"] == "something broke"
        assert g.meta["err"] is MARKER
        assert "errTrace" in g.meta

    def test_error_grid_no_trace(self) -> None:
        g = Grid.make_error("oops")
        assert g.is_error
        assert "errTrace" not in g.meta

    def test_make_rows(self) -> None:
        rows = [
            {"id": Ref("a"), "dis": "Alpha"},
            {"id": Ref("b"), "dis": "Beta", "extra": 42},
        ]
        g = Grid.make_rows(rows)
        assert len(g) == 2
        assert g.col_names == ("id", "dis", "extra")
        assert g[0]["id"] == Ref("a")
        assert g[1]["extra"] == 42

    def test_make_rows_empty(self) -> None:
        g = Grid.make_rows([])
        assert g.is_empty

    def test_col_lookup(self) -> None:
        g = Grid.make_rows([{"a": 1, "b": 2}])
        assert g.col("a").name == "a"
        try:
            g.col("missing")
            raise AssertionError("should raise")
        except KeyError:
            pass

    def test_iteration(self) -> None:
        rows = [{"x": 1}, {"x": 2}, {"x": 3}]
        g = Grid.make_rows(rows)
        vals = [r["x"] for r in g]
        assert vals == [1, 2, 3]

    def test_indexing(self) -> None:
        g = Grid.make_rows([{"a": 10}, {"a": 20}])
        assert g[0]["a"] == 10
        assert g[1]["a"] == 20


class TestGridBuilder:
    def test_build_empty(self) -> None:
        g = GridBuilder().to_grid()
        assert len(g) == 0
        assert g.cols == ()

    def test_build_with_data(self) -> None:
        g = (
            GridBuilder()
            .set_meta({"ver": "3.0"})
            .add_col("id")
            .add_col("dis")
            .add_row({"id": Ref("a"), "dis": "Alpha"})
            .add_row({"id": Ref("b"), "dis": "Beta"})
            .to_grid()
        )
        assert g.meta["ver"] == "3.0"
        assert len(g.cols) == 2
        assert len(g) == 2

    def test_add_meta(self) -> None:
        g = GridBuilder().add_meta("watchId", "w-123").add_meta("refresh").to_grid()
        assert g.meta["watchId"] == "w-123"
        assert g.meta["refresh"] is MARKER

    def test_col_with_meta(self) -> None:
        g = GridBuilder().add_col("temp", meta={"unit": "°F"}).to_grid()
        assert g.cols[0].meta["unit"] == "°F"
