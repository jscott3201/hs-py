import datetime
import math
from zoneinfo import ZoneInfo

import pytest

from hs_py.filter import (
    And,
    Cmp,
    CmpOp,
    Has,
    Missing,
    Or,
    ParseError,
    Path,
    evaluate,
    evaluate_grid,
    parse,
)
from hs_py.grid import Grid
from hs_py.kinds import MARKER, Number, Ref, Symbol, Uri

# ---- Path -------------------------------------------------------------------


class TestPath:
    def test_single_segment(self) -> None:
        p = Path(("point",))
        assert str(p) == "point"

    def test_multi_segment(self) -> None:
        p = Path(("equipRef", "siteRef", "dis"))
        assert str(p) == "equipRef->siteRef->dis"

    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one name"):
            Path(())


# ---- Parser: simple expressions ---------------------------------------------


class TestParserSimple:
    def test_has(self) -> None:
        node = parse("point")
        assert node == Has(Path(("point",)))

    def test_missing(self) -> None:
        node = parse("not point")
        assert node == Missing(Path(("point",)))

    def test_and(self) -> None:
        node = parse("point and sensor")
        assert node == And(Has(Path(("point",))), Has(Path(("sensor",))))

    def test_or(self) -> None:
        node = parse("point or equip")
        assert node == Or(Has(Path(("point",))), Has(Path(("equip",))))

    def test_and_or_precedence(self) -> None:
        node = parse("a and b or c and d")
        expected = Or(
            And(Has(Path(("a",))), Has(Path(("b",)))),
            And(Has(Path(("c",))), Has(Path(("d",)))),
        )
        assert node == expected

    def test_parens(self) -> None:
        node = parse("a and (b or c)")
        expected = And(
            Has(Path(("a",))),
            Or(Has(Path(("b",))), Has(Path(("c",)))),
        )
        assert node == expected


# ---- Parser: comparison expressions -----------------------------------------


class TestParserCmp:
    def test_eq_string(self) -> None:
        node = parse('dis == "Main Site"')
        assert node == Cmp(Path(("dis",)), CmpOp.EQ, "Main Site")

    def test_ne_number(self) -> None:
        node = parse("val != 72")
        assert node == Cmp(Path(("val",)), CmpOp.NE, Number(72.0))

    def test_lt_number_with_unit(self) -> None:
        node = parse("temp < 100\u00b0F")
        assert isinstance(node, Cmp)
        assert node.op == CmpOp.LT
        assert node.val == Number(100.0, "\u00b0F")

    def test_ge_negative_number(self) -> None:
        node = parse("val >= -10")
        assert node == Cmp(Path(("val",)), CmpOp.GE, Number(-10.0))

    def test_eq_bool_true(self) -> None:
        node = parse("active == true")
        assert node == Cmp(Path(("active",)), CmpOp.EQ, True)

    def test_eq_bool_false(self) -> None:
        node = parse("active == false")
        assert node == Cmp(Path(("active",)), CmpOp.EQ, False)

    def test_eq_ref(self) -> None:
        node = parse("siteRef == @site-1")
        assert node == Cmp(Path(("siteRef",)), CmpOp.EQ, Ref("site-1"))

    def test_eq_uri(self) -> None:
        node = parse("uri == `http://example.com`")
        assert node == Cmp(Path(("uri",)), CmpOp.EQ, Uri("http://example.com"))

    def test_eq_symbol(self) -> None:
        node = parse("kind == ^elec-meter")
        assert node == Cmp(Path(("kind",)), CmpOp.EQ, Symbol("elec-meter"))

    def test_eq_date(self) -> None:
        node = parse("date == 2024-01-15")
        assert node == Cmp(Path(("date",)), CmpOp.EQ, datetime.date(2024, 1, 15))

    def test_eq_time(self) -> None:
        node = parse("time == 08:30:00")
        assert node == Cmp(Path(("time",)), CmpOp.EQ, datetime.time(8, 30, 0))

    def test_eq_datetime_utc(self) -> None:
        node = parse("ts == 2024-01-15T08:30:00Z")
        assert isinstance(node, Cmp)
        expected = datetime.datetime(2024, 1, 15, 8, 30, 0, tzinfo=datetime.UTC)
        assert node.val == expected

    def test_eq_datetime_with_tz(self) -> None:
        node = parse("ts == 2024-01-15T08:30:00-05:00 America/New_York")
        assert isinstance(node, Cmp)
        assert node.val.tzinfo == ZoneInfo("America/New_York")

    def test_inf(self) -> None:
        node = parse("val == INF")
        assert isinstance(node, Cmp)
        assert math.isinf(node.val.val) and node.val.val > 0

    def test_neg_inf(self) -> None:
        node = parse("val > -INF")
        assert isinstance(node, Cmp)
        assert math.isinf(node.val.val) and node.val.val < 0

    def test_nan(self) -> None:
        node = parse("val != NaN")
        assert isinstance(node, Cmp)
        assert math.isnan(node.val.val)


# ---- Parser: path expressions -----------------------------------------------


class TestParserPath:
    def test_dereference(self) -> None:
        node = parse("equipRef->siteRef == @s1")
        assert isinstance(node, Cmp)
        assert node.path == Path(("equipRef", "siteRef"))

    def test_triple_dereference(self) -> None:
        node = parse('equipRef->siteRef->dis == "Main"')
        assert isinstance(node, Cmp)
        assert node.path == Path(("equipRef", "siteRef", "dis"))

    def test_has_with_dereference(self) -> None:
        node = parse("equipRef->siteRef")
        assert node == Has(Path(("equipRef", "siteRef")))


# ---- Parser: complex expressions --------------------------------------------


class TestParserComplex:
    def test_full_expression(self) -> None:
        node = parse('point and sensor and equipRef->siteRef->dis == "Main"')
        assert isinstance(node, And)

    def test_nested_parens(self) -> None:
        node = parse("((a and b))")
        assert node == And(Has(Path(("a",))), Has(Path(("b",))))

    def test_mixed(self) -> None:
        node = parse("point and (val > 72 or val < 32)")
        assert isinstance(node, And)
        assert isinstance(node.right, Or)


# ---- Parser: errors ---------------------------------------------------------


class TestParserErrors:
    def test_empty_string(self) -> None:
        with pytest.raises(ParseError):
            parse("")

    def test_trailing_and(self) -> None:
        with pytest.raises(ParseError):
            parse("point and")

    def test_unmatched_paren(self) -> None:
        with pytest.raises(ParseError):
            parse("(point and sensor")

    def test_invalid_char(self) -> None:
        with pytest.raises(ParseError):
            parse("val # 5")

    def test_unexpected_token(self) -> None:
        with pytest.raises(ParseError):
            parse("point )")


# ---- Evaluator: basic -------------------------------------------------------


class TestEvaluate:
    def test_has_present(self) -> None:
        node = parse("point")
        assert evaluate(node, {"point": MARKER})

    def test_has_absent(self) -> None:
        node = parse("point")
        assert not evaluate(node, {"equip": MARKER})

    def test_missing_present(self) -> None:
        node = parse("not point")
        assert not evaluate(node, {"point": MARKER})

    def test_missing_absent(self) -> None:
        node = parse("not point")
        assert evaluate(node, {"equip": MARKER})

    def test_and_both_true(self) -> None:
        node = parse("point and sensor")
        assert evaluate(node, {"point": MARKER, "sensor": MARKER})

    def test_and_one_false(self) -> None:
        node = parse("point and sensor")
        assert not evaluate(node, {"point": MARKER})

    def test_or_one_true(self) -> None:
        node = parse("point or equip")
        assert evaluate(node, {"equip": MARKER})

    def test_or_both_false(self) -> None:
        node = parse("point or equip")
        assert not evaluate(node, {"sensor": MARKER})


# ---- Evaluator: comparisons -------------------------------------------------


class TestEvaluateCmp:
    def test_eq_string(self) -> None:
        node = parse('dis == "Hello"')
        assert evaluate(node, {"dis": "Hello"})
        assert not evaluate(node, {"dis": "World"})

    def test_ne_string(self) -> None:
        node = parse('dis != "Hello"')
        assert evaluate(node, {"dis": "World"})
        assert not evaluate(node, {"dis": "Hello"})

    def test_lt_number(self) -> None:
        node = parse("val < 100")
        assert evaluate(node, {"val": Number(72.0)})
        assert not evaluate(node, {"val": Number(100.0)})

    def test_ge_number(self) -> None:
        node = parse("val >= 72")
        assert evaluate(node, {"val": Number(72.0)})
        assert evaluate(node, {"val": Number(100.0)})
        assert not evaluate(node, {"val": Number(50.0)})

    def test_eq_ref(self) -> None:
        node = parse("siteRef == @s1")
        assert evaluate(node, {"siteRef": Ref("s1")})
        assert not evaluate(node, {"siteRef": Ref("s2")})
        # Ref display name is ignored in comparison
        assert evaluate(node, {"siteRef": Ref("s1", "Site One")})

    def test_eq_number_with_unit(self) -> None:
        node = parse("temp == 72\u00b0F")
        assert evaluate(node, {"temp": Number(72.0, "\u00b0F")})
        # Different unit does not match
        assert not evaluate(node, {"temp": Number(72.0, "\u00b0C")})

    def test_missing_tag_comparison(self) -> None:
        node = parse("val > 10")
        assert not evaluate(node, {})

    def test_eq_bool(self) -> None:
        node = parse("active == true")
        assert evaluate(node, {"active": True})
        assert not evaluate(node, {"active": False})


# ---- Evaluator: path dereference --------------------------------------------


class TestEvaluatePath:
    def test_single_segment(self) -> None:
        node = parse('dis == "Hello"')
        assert evaluate(node, {"dis": "Hello"})

    def test_multi_segment_with_resolver(self) -> None:
        equip = {"id": Ref("e1"), "siteRef": Ref("s1"), "dis": "AHU-1"}
        site = {"id": Ref("s1"), "dis": "Main Site"}

        def resolver(ref: Ref) -> dict | None:
            if ref.val == "s1":
                return site
            return None

        node = parse('siteRef->dis == "Main Site"')
        assert evaluate(node, equip, resolver)

    def test_multi_segment_no_resolver(self) -> None:
        node = parse('equipRef->dis == "AHU"')
        assert not evaluate(node, {"equipRef": Ref("e1")})

    def test_multi_segment_ref_not_found(self) -> None:
        node = parse('equipRef->dis == "AHU"')
        assert not evaluate(node, {"equipRef": Ref("e1")}, resolver=lambda _: None)


# ---- Evaluator: evaluate_grid -----------------------------------------------


class TestEvaluateGrid:
    def test_filter_grid(self) -> None:
        grid = Grid.make_rows(
            [
                {"id": Ref("p1"), "point": MARKER, "sensor": MARKER, "val": Number(72.0)},
                {"id": Ref("p2"), "point": MARKER, "val": Number(68.0)},
                {"id": Ref("e1"), "equip": MARKER, "dis": "AHU-1"},
            ]
        )
        node = parse("point")
        result = evaluate_grid(node, grid)
        assert len(result) == 2
        assert result[0]["id"] == Ref("p1")
        assert result[1]["id"] == Ref("p2")

    def test_filter_grid_no_matches(self) -> None:
        grid = Grid.make_rows([{"id": Ref("e1"), "equip": MARKER}])
        node = parse("point")
        result = evaluate_grid(node, grid)
        assert result.is_empty

    def test_filter_grid_preserves_cols(self) -> None:
        grid = Grid.make_rows(
            [
                {"id": Ref("p1"), "point": MARKER, "val": Number(72.0)},
                {"id": Ref("p2"), "point": MARKER, "val": Number(68.0)},
            ]
        )
        node = parse("val > 70")
        result = evaluate_grid(node, grid)
        assert len(result) == 1
        assert result[0]["val"] == Number(72.0)

    def test_filter_grid_auto_resolver(self) -> None:
        grid = Grid.make_rows(
            [
                {"id": Ref("s1"), "site": MARKER, "dis": "Main"},
                {"id": Ref("e1"), "equip": MARKER, "siteRef": Ref("s1")},
            ]
        )
        node = parse('siteRef->dis == "Main"')
        result = evaluate_grid(node, grid)
        assert len(result) == 1
        assert result[0]["id"] == Ref("e1")
