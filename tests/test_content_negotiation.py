"""Tests for content negotiation helpers."""

import pytest

from hs_py.content_negotiation import decode_request, encode_response, negotiate_format
from hs_py.encoding.json import encode_grid
from hs_py.encoding.zinc import encode_grid as zinc_encode_grid
from hs_py.grid import Grid

# ---------------------------------------------------------------------------
# negotiate_format
# ---------------------------------------------------------------------------


class TestNegotiateFormat:
    def test_empty_string_returns_json(self) -> None:
        assert negotiate_format("") == "json"

    def test_whitespace_only_returns_json(self) -> None:
        assert negotiate_format("   ") == "json"

    def test_application_json(self) -> None:
        assert negotiate_format("application/json") == "json"

    def test_text_zinc(self) -> None:
        assert negotiate_format("text/zinc") == "zinc"

    def test_text_csv(self) -> None:
        assert negotiate_format("text/csv") == "csv"

    def test_text_trio(self) -> None:
        assert negotiate_format("text/trio") == "trio"

    def test_wildcard_any(self) -> None:
        assert negotiate_format("*/*") == "json"

    def test_wildcard_application(self) -> None:
        assert negotiate_format("application/*") == "json"

    def test_zinc_before_json_first_match_wins(self) -> None:
        assert negotiate_format("text/zinc, application/json") == "zinc"

    def test_json_before_zinc_first_match_wins(self) -> None:
        assert negotiate_format("application/json, text/zinc") == "json"

    def test_unknown_mime_falls_back_to_json(self) -> None:
        assert negotiate_format("text/html") == "json"

    def test_completely_unknown_falls_back_to_json(self) -> None:
        assert negotiate_format("application/x-custom") == "json"

    def test_quality_parameters_respected(self) -> None:
        # Higher quality value wins regardless of position.
        assert negotiate_format("text/zinc;q=0.9, application/json;q=1.0") == "json"

    def test_mime_case_insensitive(self) -> None:
        assert negotiate_format("Application/JSON") == "json"
        assert negotiate_format("TEXT/ZINC") == "zinc"

    def test_multiple_unknowns_then_known(self) -> None:
        assert negotiate_format("text/html, text/plain, text/zinc") == "zinc"

    def test_wildcard_among_tokens(self) -> None:
        # Wildcard appears before a concrete known type — wildcard wins.
        assert negotiate_format("*/*, text/zinc") == "json"


# ---------------------------------------------------------------------------
# encode_response
# ---------------------------------------------------------------------------


class TestEncodeResponse:
    def _simple_grid(self) -> Grid:
        return Grid.make_rows([{"name": "test"}])

    def test_json_format_returns_bytes(self) -> None:
        body, ct = encode_response(self._simple_grid(), "json")
        assert isinstance(body, bytes)
        assert ct == "application/json"

    def test_json_body_is_valid_json(self) -> None:
        body, _ = encode_response(self._simple_grid(), "json")
        import json

        parsed = json.loads(body)
        assert isinstance(parsed, dict)

    def test_zinc_format_returns_bytes(self) -> None:
        body, ct = encode_response(self._simple_grid(), "zinc")
        assert isinstance(body, bytes)
        assert ct == "text/zinc"

    def test_zinc_body_is_text(self) -> None:
        body, _ = encode_response(self._simple_grid(), "zinc")
        text = body.decode("utf-8")
        assert "ver:" in text

    def test_csv_format_returns_bytes(self) -> None:
        body, ct = encode_response(self._simple_grid(), "csv")
        assert isinstance(body, bytes)
        assert ct == "text/csv"

    def test_csv_body_contains_header(self) -> None:
        body, _ = encode_response(self._simple_grid(), "csv")
        text = body.decode("utf-8")
        assert "name" in text

    def test_trio_format_falls_back_to_json(self) -> None:
        # Trio is not supported for grid encoding; falls back to JSON.
        body, ct = encode_response(self._simple_grid(), "trio")
        assert isinstance(body, bytes)
        assert ct == "application/json"

    def test_unknown_format_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown format"):
            encode_response(self._simple_grid(), "msgpack")

    def test_empty_grid_json(self) -> None:
        body, ct = encode_response(Grid.make_empty(), "json")
        assert isinstance(body, bytes)
        assert ct == "application/json"


# ---------------------------------------------------------------------------
# decode_request
# ---------------------------------------------------------------------------


class TestDecodeRequest:
    def test_empty_body_returns_empty_grid(self) -> None:
        grid = decode_request(b"", "application/json")
        assert grid.is_empty
        assert len(grid.cols) == 0

    def test_whitespace_body_returns_empty_grid(self) -> None:
        grid = decode_request(b"   ", "application/json")
        assert grid.is_empty

    def test_json_content_type_decodes(self) -> None:
        source = Grid.make_rows([{"name": "test"}])
        body = encode_grid(source)
        grid = decode_request(body, "application/json")
        assert len(grid.rows) == 1
        assert grid.rows[0]["name"] == "test"

    def test_zinc_content_type_decodes(self) -> None:
        source = Grid.make_rows([{"name": "test"}])
        zinc_text = zinc_encode_grid(source)
        body = zinc_text.encode("utf-8")
        grid = decode_request(body, "text/zinc")
        assert len(grid.rows) == 1
        assert grid.rows[0]["name"] == "test"

    def test_unknown_content_type_falls_back_to_json(self) -> None:
        source = Grid.make_rows([{"val": 42}])
        body = encode_grid(source)
        grid = decode_request(body, "application/x-custom")
        assert len(grid.rows) == 1

    def test_content_type_with_charset_stripped(self) -> None:
        source = Grid.make_rows([{"name": "test"}])
        body = encode_grid(source)
        grid = decode_request(body, "application/json; charset=utf-8")
        assert len(grid.rows) == 1
        assert grid.rows[0]["name"] == "test"

    def test_zinc_content_type_with_params(self) -> None:
        source = Grid.make_rows([{"name": "test"}])
        zinc_text = zinc_encode_grid(source)
        body = zinc_text.encode("utf-8")
        grid = decode_request(body, "text/zinc; charset=utf-8")
        assert len(grid.rows) == 1
