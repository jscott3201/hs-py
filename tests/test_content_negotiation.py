"""Tests for content negotiation helpers."""

import pytest

from hs_py.content_negotiation import (
    UnsupportedContentTypeError,
    decode_request,
    encode_response,
    negotiate_format,
)
from hs_py.encoding.json import encode_grid
from hs_py.encoding.trio import encode_trio
from hs_py.encoding.zinc import encode_grid as zinc_encode_grid
from hs_py.grid import Grid

# ---------------------------------------------------------------------------
# negotiate_format
# ---------------------------------------------------------------------------


class TestNegotiateFormat:
    def test_empty_string_returns_default(self) -> None:
        assert negotiate_format("") == "json"

    def test_whitespace_only_returns_default(self) -> None:
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

    def test_unknown_mime_returns_none(self) -> None:
        assert negotiate_format("text/html") is None

    def test_completely_unknown_returns_none(self) -> None:
        assert negotiate_format("application/x-custom") is None

    def test_quality_parameters_respected(self) -> None:
        assert negotiate_format("text/zinc;q=0.9, application/json;q=1.0") == "json"

    def test_mime_case_insensitive(self) -> None:
        assert negotiate_format("Application/JSON") == "json"
        assert negotiate_format("TEXT/ZINC") == "zinc"

    def test_multiple_unknowns_then_known(self) -> None:
        assert negotiate_format("text/html, text/plain, text/zinc") == "zinc"

    def test_wildcard_among_tokens(self) -> None:
        assert negotiate_format("*/*, text/zinc") == "json"

    # Vendor MIME types (Haystack spec).
    def test_vendor_json_v4(self) -> None:
        assert negotiate_format("application/vnd.haystack+json;version=4") == "json"

    def test_vendor_json_v3(self) -> None:
        assert negotiate_format("application/vnd.haystack+json;version=3") == "json_v3"

    # RDF formats.
    def test_text_turtle(self) -> None:
        assert negotiate_format("text/turtle") == "turtle"

    def test_application_ld_json(self) -> None:
        assert negotiate_format("application/ld+json") == "jsonld"

    # Configurable default.
    def test_custom_default_for_empty(self) -> None:
        assert negotiate_format("", default="zinc") == "zinc"

    def test_custom_default_for_wildcard(self) -> None:
        assert negotiate_format("*/*", default="zinc") == "zinc"


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

    def test_json_v3_format(self) -> None:
        body, ct = encode_response(self._simple_grid(), "json_v3")
        assert isinstance(body, bytes)
        assert ct == "application/json"

    def test_zinc_format_returns_bytes(self) -> None:
        body, ct = encode_response(self._simple_grid(), "zinc")
        assert isinstance(body, bytes)
        assert ct == "text/zinc; charset=utf-8"

    def test_zinc_body_is_text(self) -> None:
        body, _ = encode_response(self._simple_grid(), "zinc")
        text = body.decode("utf-8")
        assert "ver:" in text

    def test_csv_format_returns_bytes(self) -> None:
        body, ct = encode_response(self._simple_grid(), "csv")
        assert isinstance(body, bytes)
        assert ct == "text/csv; charset=utf-8"

    def test_csv_body_contains_header(self) -> None:
        body, _ = encode_response(self._simple_grid(), "csv")
        text = body.decode("utf-8")
        assert "name" in text

    def test_trio_format_encodes_records(self) -> None:
        body, ct = encode_response(self._simple_grid(), "trio")
        assert isinstance(body, bytes)
        assert ct == "text/trio; charset=utf-8"
        text = body.decode("utf-8")
        assert "name" in text

    def test_turtle_format(self) -> None:
        body, ct = encode_response(self._simple_grid(), "turtle")
        assert isinstance(body, bytes)
        assert ct == "text/turtle; charset=utf-8"

    def test_jsonld_format(self) -> None:
        body, ct = encode_response(self._simple_grid(), "jsonld")
        assert isinstance(body, bytes)
        assert ct == "application/ld+json"

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

    def test_trio_content_type_decodes(self) -> None:
        records = [{"name": "test"}]
        body = encode_trio(records).encode("utf-8")
        grid = decode_request(body, "text/trio")
        assert len(grid.rows) == 1
        assert grid.rows[0]["name"] == "test"

    def test_unknown_content_type_raises(self) -> None:
        source = Grid.make_rows([{"val": 42}])
        body = encode_grid(source)
        with pytest.raises(UnsupportedContentTypeError):
            decode_request(body, "application/x-custom")

    def test_csv_content_type_not_decodable(self) -> None:
        with pytest.raises(UnsupportedContentTypeError):
            decode_request(b"name\ntest", "text/csv")

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

    def test_vendor_json_v4_decodes(self) -> None:
        source = Grid.make_rows([{"name": "test"}])
        body = encode_grid(source)
        grid = decode_request(body, "application/vnd.haystack+json;version=4")
        assert len(grid.rows) == 1

    def test_vendor_json_v3_decodes(self) -> None:
        from hs_py.encoding.json import JsonVersion
        from hs_py.encoding.json import encode_grid as json_encode

        source = Grid.make_rows([{"name": "test"}])
        body = json_encode(source, version=JsonVersion.V3)
        grid = decode_request(body, "application/vnd.haystack+json;version=3")
        assert len(grid.rows) == 1


# ---------------------------------------------------------------------------
# Coverage gaps
# ---------------------------------------------------------------------------


class TestNegotiateFormatEdgeCases:
    def test_empty_mime_token_skipped(self) -> None:
        """Cover content_negotiation.py L107: empty MIME token in Accept."""
        fmt = negotiate_format(",application/json,")
        assert fmt == "json"

    def test_vendor_mime_without_version_returns_406(self) -> None:
        """Cover L133-135: vendor MIME without version → no match (406)."""
        fmt = negotiate_format("application/vnd.haystack+json")
        assert fmt is None


class TestEncodeResponseUnknownFormat:
    def test_unknown_format_raises(self) -> None:
        """Cover content_negotiation.py L186-187: unknown format ValueError."""
        grid = Grid.make_rows([{"name": "test"}])
        with pytest.raises(ValueError, match="Unknown format"):
            encode_response(grid, "xml_nonexistent")

    def test_rdflib_import_error(self) -> None:
        """Cover content_negotiation.py L267-268: rdflib not available."""
        import sys
        from unittest.mock import patch

        grid = Grid.make_rows([{"id": "p1"}])
        # Temporarily hide rdflib
        with patch.dict(sys.modules, {"rdflib": None}):
            from hs_py.content_negotiation import _encode_grid_rdf

            result = _encode_grid_rdf(grid, "turtle")
            assert "rdflib is required" in result


class TestRDFEncodeEdgeCases:
    def test_row_without_id_uses_bnode(self) -> None:
        """Cover content_negotiation.py L277: row without 'id' → BNode."""
        grid = Grid.make_rows([{"dis": "No ID entity"}])
        body, _ct = encode_response(grid, "turtle")
        text = body.decode("utf-8")
        assert "No ID entity" in text

    def test_rdf_marker_ref_and_other(self) -> None:
        """Cover L290-293: Marker, Ref, and other values in RDF."""
        from hs_py.kinds import MARKER, Ref

        grid = Grid.make_rows(
            [
                {
                    "id": Ref("p1"),
                    "site": MARKER,
                    "siteRef": Ref("s1"),
                    "dis": "My Point",
                }
            ]
        )
        body, _ct = encode_response(grid, "turtle")
        text = body.decode("utf-8")
        assert "urn:haystack:" in text
        assert "urn:haystack:s1" in text or "urn:haystack:@s1" in text
        assert "My Point" in text
