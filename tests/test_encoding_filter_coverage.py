"""Tests for filter lexer, eval, and encoding edge-case coverage gaps."""

from __future__ import annotations

import pytest

from hs_py.filter import evaluate, evaluate_grid, parse
from hs_py.filter.ast import CmpOp, Path
from hs_py.filter.eval import _compare, _eval, _resolve_path_multi
from hs_py.filter.lexer import Lexer, TokenType
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref


class TestLexerEdgeCases:
    """Cover filter lexer missing lines."""

    def test_uri_literal(self) -> None:
        tokens = list(Lexer("`http://example.com`").tokenize())
        assert any(t.type == TokenType.URI for t in tokens)

    def test_string_with_unicode_escape(self) -> None:
        tokens = list(Lexer('"hello\\u0041world"').tokenize())
        vals = [t.val for t in tokens if t.type == TokenType.STR]
        assert vals[0] == "helloAworld"

    def test_string_with_backslash_escape(self) -> None:
        tokens = list(Lexer('"line\\n"').tokenize())
        vals = [t.val for t in tokens if t.type == TokenType.STR]
        assert "\n" in vals[0]

    def test_unterminated_string(self) -> None:
        with pytest.raises(ValueError, match="Unterminated"):
            list(Lexer('"unclosed').tokenize())

    def test_unterminated_uri(self) -> None:
        with pytest.raises(ValueError, match="Unterminated"):
            list(Lexer("`unclosed").tokenize())

    def test_negative_inf(self) -> None:
        tokens = list(Lexer("val == -INF").tokenize())
        nums = [t for t in tokens if t.type == TokenType.NUMBER]
        assert len(nums) == 1
        assert nums[0].val[0] == float("-inf")

    def test_unexpected_minus(self) -> None:
        with pytest.raises(ValueError, match="Unexpected"):
            list(Lexer("val == -x").tokenize())

    def test_negative_number(self) -> None:
        tokens = list(Lexer("val == -42").tokenize())
        nums = [t for t in tokens if t.type == TokenType.NUMBER]
        assert nums[0].val[0] == -42.0

    def test_scientific_notation(self) -> None:
        tokens = list(Lexer("val == 1.5e+3").tokenize())
        nums = [t for t in tokens if t.type == TokenType.NUMBER]
        assert nums[0].val[0] == 1500.0


class TestFilterEvalEdgeCases:
    """Cover filter eval missing lines."""

    def test_compare_incompatible_types_returns_false(self) -> None:
        assert _compare("text", CmpOp.LT, Number(42.0)) is False

    def test_compare_le(self) -> None:
        assert _compare(Number(1.0), CmpOp.LE, Number(2.0)) is True
        assert _compare(Number(2.0), CmpOp.LE, Number(2.0)) is True

    def test_compare_gt(self) -> None:
        assert _compare(Number(3.0), CmpOp.GT, Number(2.0)) is True

    def test_compare_ge(self) -> None:
        assert _compare(Number(2.0), CmpOp.GE, Number(2.0)) is True

    def test_path_resolve_non_ref_intermediate(self) -> None:
        """Multi-segment path where intermediate is not a Ref → _MISSING."""
        from hs_py.filter.eval import _MISSING

        path = Path(("siteRef", "dis"))
        entity = {"siteRef": "not-a-ref"}
        assert _resolve_path_multi(path.names, entity, None) is _MISSING

    def test_path_resolve_no_resolver(self) -> None:
        """Multi-segment path with a Ref but no resolver → _MISSING."""
        from hs_py.filter.eval import _MISSING

        path = Path(("siteRef", "dis"))
        entity = {"siteRef": Ref("s1")}
        assert _resolve_path_multi(path.names, entity, None) is _MISSING

    def test_path_resolve_ref_not_found(self) -> None:
        """Multi-segment path where resolver returns None → _MISSING."""
        from hs_py.filter.eval import _MISSING

        path = Path(("siteRef", "dis"))
        entity = {"siteRef": Ref("s1")}
        assert _resolve_path_multi(path.names, entity, lambda r: None) is _MISSING

    def test_or_node_short_circuit(self) -> None:
        """Or node short-circuits on first True."""
        ast = parse("site or equip")
        assert evaluate(ast, {"site": MARKER}) is True

    def test_unknown_node_type_raises(self) -> None:
        """Eval raises TypeError for unknown AST node."""

        class FakeNode:
            pass

        with pytest.raises(TypeError, match="Unknown node type"):
            _eval(FakeNode(), {}, None)  # type: ignore[arg-type]

    def test_evaluate_grid_with_path_resolution(self) -> None:
        """evaluate_grid with multi-segment path uses grid-based resolver."""
        builder = GridBuilder().add_col("id").add_col("dis").add_col("site").add_col("siteRef")
        builder.add_row({"id": Ref("s1"), "dis": "Main Site", "site": MARKER})
        builder.add_row({"id": Ref("e1"), "dis": "AHU-1", "siteRef": Ref("s1")})
        grid = builder.to_grid()
        # Filter on siteRef->dis would use the grid resolver
        result = evaluate_grid(parse("siteRef->dis"), grid)
        assert len(result.rows) == 1

    def test_evaluate_grid_empty_result(self) -> None:
        """evaluate_grid returns empty grid when nothing matches."""
        grid = Grid.make_rows([{"dis": "test"}])
        result = evaluate_grid(parse("site"), grid)
        assert len(result.rows) == 0


class TestEncodingJsonEdgeCases:
    """Cover JSON encoding edge cases."""

    def test_decode_empty_grid(self) -> None:
        from hs_py.encoding.json import decode_grid

        grid = decode_grid(b'{"meta":{"ver":"3.0"},"cols":[{"name":"empty"}],"rows":[]}')
        assert len(grid.rows) == 0

    def test_encode_grid_v4_with_ref(self) -> None:
        from hs_py.encoding.json import JsonVersion, encode_grid

        grid = Grid.make_rows([{"id": Ref("abc"), "dis": "Test"}])
        data = encode_grid(grid, version=JsonVersion.V4)
        assert b"abc" in data


class TestEncodingZincEdgeCases:
    """Cover Zinc encoding edge cases."""

    def test_encode_number_with_unit(self) -> None:
        from hs_py.encoding.zinc import encode_val

        result = encode_val(Number(72.5, "°F"))
        assert "72.5" in result
        assert "°F" in result

    def test_encode_none(self) -> None:
        from hs_py.encoding.zinc import encode_val

        assert encode_val(None) == "N"

    def test_decode_grid_with_meta(self) -> None:
        from hs_py.encoding.zinc import decode_grid

        zinc = 'ver:"3.0" dis:"Test"\nempty\n'
        grid = decode_grid(zinc)
        assert grid.meta.get("dis") == "Test"

    def test_encode_coord(self) -> None:
        from hs_py.encoding.zinc import encode_val
        from hs_py.kinds import Coord

        result = encode_val(Coord(37.55, -77.45))
        assert "37.55" in result
        assert "-77.45" in result


class TestEncodingTrioEdgeCases:
    """Cover Trio encoding edge cases."""

    def test_parse_trio_with_marker(self) -> None:
        from hs_py.encoding.trio import parse_trio

        records = parse_trio("site\ndis:Main Office\n---\nequip\ndis:AHU-1")
        assert len(records) == 2
        assert records[0].get("site") is MARKER

    def test_encode_trio_with_ref(self) -> None:
        from hs_py.encoding.trio import encode_trio

        result = encode_trio([{"id": Ref("abc"), "dis": "Test"}])
        assert "@abc" in result
        assert "dis" in result
