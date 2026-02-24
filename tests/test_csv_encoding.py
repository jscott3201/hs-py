import datetime

from hs_py.encoding.csv import encode_grid
from hs_py.grid import Col, Grid
from hs_py.kinds import MARKER, NA, REMOVE, Coord, Number, Ref, Symbol, Uri, XStr

# ---- Column headers ----------------------------------------------------------


class TestCsvHeaders:
    def test_programmatic_names_as_default(self) -> None:
        grid = Grid(cols=(Col("id"), Col("val")), rows=())
        csv = encode_grid(grid)
        assert csv.startswith("id,val\n")

    def test_display_names_from_meta(self) -> None:
        grid = Grid(
            cols=(
                Col("id", meta={"dis": "Identifier"}),
                Col("equipName", meta={"dis": "Equip Name"}),
            ),
            rows=(),
        )
        csv = encode_grid(grid)
        assert csv.startswith("Identifier,Equip Name\n")

    def test_mixed_dis_and_programmatic(self) -> None:
        grid = Grid(
            cols=(
                Col("id", meta={"dis": "ID"}),
                Col("val"),
            ),
            rows=(),
        )
        csv = encode_grid(grid)
        assert csv.startswith("ID,val\n")

    def test_empty_grid_no_cols(self) -> None:
        grid = Grid(cols=(), rows=())
        csv = encode_grid(grid)
        assert csv == "\n"


# ---- Value encoding ----------------------------------------------------------


class TestCsvValues:
    def _cell(self, val: object) -> str:
        """Encode a single value as a CSV cell."""
        grid = Grid(cols=(Col("x"),), rows=({"x": val},))
        line = encode_grid(grid).split("\n")[1]
        return line

    def test_null(self) -> None:
        grid = Grid(cols=(Col("x"),), rows=({},))
        line = encode_grid(grid).split("\n")[1]
        assert line == ""

    def test_marker(self) -> None:
        assert self._cell(MARKER) == "\u2713"

    def test_na(self) -> None:
        assert self._cell(NA) == ""

    def test_remove(self) -> None:
        assert self._cell(REMOVE) == ""

    def test_bool_true(self) -> None:
        assert self._cell(True) == "true"

    def test_bool_false(self) -> None:
        assert self._cell(False) == "false"

    def test_string(self) -> None:
        assert self._cell("hello") == "hello"

    def test_string_unescaped(self) -> None:
        """Strings are unescaped (no Zinc quotes)."""
        assert self._cell("hello world") == "hello world"

    def test_uri(self) -> None:
        assert self._cell(Uri("http://example.com")) == "http://example.com"

    def test_ref_without_dis(self) -> None:
        assert self._cell(Ref("p1")) == "@p1"

    def test_ref_with_dis(self) -> None:
        assert self._cell(Ref("site-1", "Main Site")) == "@site-1 Main Site"

    def test_number_integer(self) -> None:
        assert self._cell(Number(42.0)) == "42"

    def test_number_float(self) -> None:
        assert self._cell(Number(3.14)) == "3.14"

    def test_number_with_unit(self) -> None:
        assert self._cell(Number(72.0, "°F")) == "72°F"

    def test_number_negative(self) -> None:
        assert self._cell(Number(-10.0)) == "-10"

    def test_number_inf(self) -> None:
        assert self._cell(Number(float("inf"))) == "INF"

    def test_number_neg_inf(self) -> None:
        assert self._cell(Number(float("-inf"))) == "-INF"

    def test_number_nan(self) -> None:
        assert self._cell(Number(float("nan"))) == "NaN"

    def test_symbol_uses_zinc(self) -> None:
        assert self._cell(Symbol("site")) == "^site"

    def test_coord_uses_zinc(self) -> None:
        cell = self._cell(Coord(37.545, -77.449))
        # Zinc encoding contains comma → RFC 4180 quotes the cell
        assert cell == '"C(37.545,-77.449)"'

    def test_date_uses_zinc(self) -> None:
        assert self._cell(datetime.date(2024, 1, 15)) == "2024-01-15"

    def test_time_uses_zinc(self) -> None:
        assert self._cell(datetime.time(8, 30, 0)) == "08:30:00"

    def test_datetime_uses_zinc(self) -> None:
        dt = datetime.datetime(2024, 1, 15, 8, 30, 0, tzinfo=datetime.UTC)
        cell = self._cell(dt)
        assert "2024-01-15" in cell

    def test_xstr_uses_zinc(self) -> None:
        cell = self._cell(XStr("Bin", "text/plain"))
        # Zinc encoding contains quotes → RFC 4180 doubles them
        assert cell == '"Bin(""text/plain"")"'


# ---- RFC 4180 escaping -------------------------------------------------------


class TestCsvEscaping:
    def _cell(self, val: object) -> str:
        grid = Grid(cols=(Col("x"),), rows=({"x": val},))
        line = encode_grid(grid).split("\n")[1]
        return line

    def test_string_with_comma(self) -> None:
        assert self._cell("hello, world") == '"hello, world"'

    def test_string_with_quotes(self) -> None:
        assert self._cell('say "hi"') == '"say ""hi"""'

    def test_string_with_newline(self) -> None:
        grid = Grid(cols=(Col("x"),), rows=({"x": "line1\nline2"},))
        csv = encode_grid(grid)
        # Full output: header + quoted cell with embedded newline + trailing \n
        assert csv == 'x\n"line1\nline2"\n'

    def test_string_with_carriage_return(self) -> None:
        grid = Grid(cols=(Col("x"),), rows=({"x": "line1\rline2"},))
        csv = encode_grid(grid)
        assert csv == 'x\n"line1\rline2"\n'

    def test_header_with_comma(self) -> None:
        grid = Grid(cols=(Col("x", meta={"dis": "A, B"}),), rows=())
        header = encode_grid(grid).split("\n")[0]
        assert header == '"A, B"'

    def test_ref_dis_with_comma(self) -> None:
        cell = self._cell(Ref("site-1", "Main, Site"))
        assert cell == '"@site-1 Main, Site"'

    def test_uri_with_comma(self) -> None:
        cell = self._cell(Uri("http://example.com/a,b"))
        assert cell == '"http://example.com/a,b"'

    def test_plain_string_no_escaping(self) -> None:
        assert self._cell("hello") == "hello"


# ---- Multi-row grids ---------------------------------------------------------


class TestCsvGrids:
    def test_single_row(self) -> None:
        grid = Grid(
            cols=(Col("id"), Col("val")),
            rows=({"id": Ref("p1"), "val": Number(42.0)},),
        )
        csv = encode_grid(grid)
        lines = csv.strip().split("\n")
        assert len(lines) == 2
        assert lines[0] == "id,val"
        assert lines[1] == "@p1,42"

    def test_multiple_rows(self) -> None:
        grid = Grid(
            cols=(Col("id"), Col("val")),
            rows=(
                {"id": Ref("p1"), "val": Number(1.0)},
                {"id": Ref("p2"), "val": Number(2.0)},
                {"id": Ref("p3"), "val": Number(3.0)},
            ),
        )
        csv = encode_grid(grid)
        lines = csv.strip().split("\n")
        assert len(lines) == 4
        assert lines[1] == "@p1,1"
        assert lines[2] == "@p2,2"
        assert lines[3] == "@p3,3"

    def test_sparse_rows(self) -> None:
        """Missing values produce empty cells."""
        grid = Grid(
            cols=(Col("a"), Col("b"), Col("c")),
            rows=({"a": Number(1.0), "c": Number(3.0)},),
        )
        csv = encode_grid(grid)
        lines = csv.strip().split("\n")
        assert lines[1] == "1,,3"

    def test_all_null_row(self) -> None:
        grid = Grid(
            cols=(Col("a"), Col("b")),
            rows=({},),
        )
        csv = encode_grid(grid)
        lines = csv.strip().split("\n")
        assert lines[1] == ","

    def test_metadata_discarded(self) -> None:
        """Grid and column metadata should not appear in CSV output."""
        grid = Grid(
            meta={"hisStart": datetime.date(2024, 1, 1)},
            cols=(Col("ts", meta={"tz": "New_York"}), Col("val")),
            rows=({"ts": "2024-01-01", "val": Number(72.0)},),
        )
        csv = encode_grid(grid)
        assert "hisStart" not in csv
        assert "tz" not in csv
        # Headers use programmatic names (no dis meta set)
        assert csv.startswith("ts,val\n")


# ---- Spec-style examples ----------------------------------------------------


class TestCsvSpecExample:
    def test_equip_grid(self) -> None:
        """Reproduce the CSV spec example grid."""
        grid = Grid(
            cols=(
                Col("dis", meta={"dis": "Equip Name"}),
                Col("equip"),
                Col("siteRef"),
                Col("installed"),
            ),
            rows=(
                {
                    "dis": "RTU-1",
                    "equip": MARKER,
                    "siteRef": Ref("153c-699a", "HQ"),
                    "installed": datetime.date(2005, 6, 1),
                },
                {
                    "dis": "RTU-2",
                    "equip": MARKER,
                    "siteRef": Ref("153c-699a", "HQ"),
                    "installed": datetime.date(2999, 7, 12),
                },
            ),
        )
        csv = encode_grid(grid)
        lines = csv.strip().split("\n")
        assert lines[0] == "Equip Name,equip,siteRef,installed"
        assert lines[1] == "RTU-1,\u2713,@153c-699a HQ,2005-06-01"
        assert lines[2] == "RTU-2,\u2713,@153c-699a HQ,2999-07-12"

    def test_his_data_grid(self) -> None:
        """Typical history data grid."""
        ts1 = datetime.datetime(2024, 1, 15, 0, 0, 0, tzinfo=datetime.UTC)
        ts2 = datetime.datetime(2024, 1, 15, 1, 0, 0, tzinfo=datetime.UTC)
        grid = Grid(
            cols=(Col("ts"), Col("val")),
            rows=(
                {"ts": ts1, "val": Number(72.0, "°F")},
                {"ts": ts2, "val": Number(73.5, "°F")},
            ),
        )
        csv = encode_grid(grid)
        lines = csv.strip().split("\n")
        assert lines[0] == "ts,val"
        assert "72°F" in lines[1]
        assert "73.5°F" in lines[2]

    def test_mixed_types_grid(self) -> None:
        """Grid with diverse value types."""
        grid = Grid(
            cols=(Col("name"), Col("point"), Col("val"), Col("uri"), Col("active")),
            rows=(
                {
                    "name": "Sensor 1",
                    "point": MARKER,
                    "val": Number(42.0, "°F"),
                    "uri": Uri("http://example.com"),
                    "active": True,
                },
            ),
        )
        csv = encode_grid(grid)
        lines = csv.strip().split("\n")
        assert lines[0] == "name,point,val,uri,active"
        assert lines[1] == "Sensor 1,\u2713,42°F,http://example.com,true"
