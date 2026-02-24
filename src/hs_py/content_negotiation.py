"""Content negotiation for Haystack HTTP servers.

Provides Accept header parsing, request body decoding, and response body
encoding across all supported Haystack wire formats (JSON, Zinc, Trio, CSV,
Turtle, JSON-LD).

See: https://project-haystack.org/doc/docHaystack/HttpApi#contentNegotiation
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from hs_py.encoding import csv as _csv
from hs_py.encoding import json as _json
from hs_py.encoding import trio as _trio
from hs_py.encoding import zinc as _zinc
from hs_py.encoding.json import JsonVersion

if TYPE_CHECKING:
    from hs_py.grid import Grid

_log = logging.getLogger(__name__)

__all__ = [
    "UnsupportedContentTypeError",
    "decode_request",
    "encode_response",
    "negotiate_format",
]

# ---------------------------------------------------------------------------
# MIME type mappings
# ---------------------------------------------------------------------------

# MIME type to internal format name mapping.
# Vendor MIME types per Haystack spec §contentNegotiation.
_MIME_TO_FORMAT: dict[str, str] = {
    "application/json": "json",
    "application/vnd.haystack+json;version=4": "json",
    "application/vnd.haystack+json;version=3": "json_v3",
    "text/zinc": "zinc",
    "text/trio": "trio",
    "text/csv": "csv",
    "text/turtle": "turtle",
    "application/ld+json": "jsonld",
}

# Format name to MIME type mapping (for response Content-Type).
_FORMAT_TO_CONTENT_TYPE: dict[str, str] = {
    "json": "application/json",
    "json_v3": "application/json",
    "zinc": "text/zinc; charset=utf-8",
    "trio": "text/trio; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "turtle": "text/turtle; charset=utf-8",
    "jsonld": "application/ld+json",
}

# Wildcard MIME types that map to the default format.
_WILDCARD_MIMES: frozenset[str] = frozenset({"*/*", "application/*", "text/*"})

# Formats that can decode request bodies.
_DECODABLE_FORMATS: frozenset[str] = frozenset({"json", "json_v3", "zinc", "trio"})

_DEFAULT_FORMAT = "json"


class UnsupportedContentTypeError(Exception):
    """Raised when a POST Content-Type is not a supported Haystack format."""

    def __init__(self, mime: str) -> None:
        self.mime = mime
        super().__init__(f"Unsupported Content-Type: {mime}")


# ---------------------------------------------------------------------------
# Accept header negotiation
# ---------------------------------------------------------------------------


def negotiate_format(accept: str, *, default: str = _DEFAULT_FORMAT) -> str | None:
    """Parse an HTTP Accept header and return the best matching format name.

    Parses quality parameters (``q=...``) from each media type, sorts
    candidates by descending quality, and returns the best known format.
    Wildcards ``*/*`` and ``application/*`` resolve to *default*.

    Returns ``None`` when the Accept header contains only explicit MIME types
    and none of them are supported — the caller should return HTTP 406.

    :param accept: Value of the HTTP ``Accept`` header.
    :param default: Format to use for wildcards and empty Accept (default
        ``"json"``).
    :returns: Format name, or ``None`` if no supported format matches.
    """
    if not accept or not accept.strip():
        return default

    candidates: list[tuple[float, int, str, dict[str, str]]] = []
    for idx, token in enumerate(accept.split(",")):
        parts = token.strip().split(";")
        mime = parts[0].strip().lower()
        if not mime:
            continue
        # Extract quality value and other parameters.
        q = 1.0
        params: dict[str, str] = {}
        for param in parts[1:]:
            param = param.strip()
            if param.startswith("q="):
                with contextlib.suppress(ValueError):
                    q = float(param[2:])
            elif "=" in param:
                k, _, v = param.partition("=")
                params[k.strip().lower()] = v.strip().lower()
        candidates.append((q, -idx, mime, params))

    # Sort by quality descending, then by original order.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)

    has_explicit = False
    for _q, _idx, mime, params in candidates:
        if mime in _WILDCARD_MIMES:
            return default
        has_explicit = True
        # Reconstruct vendor MIME with version parameter for lookup.
        if "version" in params:
            versioned = f"{mime};version={params['version']}"
            fmt = _MIME_TO_FORMAT.get(versioned)
            if fmt is not None:
                return fmt
        fmt = _MIME_TO_FORMAT.get(mime)
        if fmt is not None:
            return fmt

    # Only return None (406) when there were explicit non-wildcard types.
    return None if has_explicit else default


# ---------------------------------------------------------------------------
# Response encoding
# ---------------------------------------------------------------------------


def encode_response(grid: Grid, fmt: str) -> tuple[bytes, str]:
    """Encode a Grid to the specified wire format.

    :param grid: Grid to encode.
    :param fmt: Format name — one of ``"json"``, ``"json_v3"``, ``"zinc"``,
        ``"trio"``, ``"csv"``, ``"turtle"``, or ``"jsonld"``.
    :returns: A ``(body_bytes, content_type)`` tuple.
    :raises ValueError: If *fmt* is not a recognised format name.
    """
    ct = _FORMAT_TO_CONTENT_TYPE.get(fmt)
    if ct is None:
        msg = f"Unknown format: {fmt!r}"
        raise ValueError(msg)

    if fmt == "json":
        return _json.encode_grid(grid), ct

    if fmt == "json_v3":
        return _json.encode_grid(grid, version=JsonVersion.V3), ct

    if fmt == "zinc":
        return _zinc.encode_grid(grid).encode("utf-8"), ct

    if fmt == "csv":
        return _csv.encode_grid(grid).encode("utf-8"), ct

    if fmt == "trio":
        body = _trio.encode_trio(list(grid.rows)).encode("utf-8")
        return body, ct

    if fmt == "turtle":
        body = _encode_grid_rdf(grid, "turtle").encode("utf-8")
        return body, ct

    if fmt == "jsonld":
        body = _encode_grid_rdf(grid, "json-ld").encode("utf-8")
        return body, ct

    msg = f"Unknown format: {fmt!r}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Request decoding
# ---------------------------------------------------------------------------


def decode_request(body: bytes, content_type: str) -> Grid:
    """Decode a request body based on its Content-Type header.

    Supports JSON (v3 and v4), Zinc, and Trio for decoding.  An empty body
    returns an empty Grid.

    :param body: Raw request body bytes.
    :param content_type: Value of the HTTP ``Content-Type`` header.
    :returns: Decoded :class:`~hs_py.grid.Grid`.
    :raises UnsupportedContentTypeError: If the Content-Type is not a supported
        Haystack format.
    """
    from hs_py.grid import Grid

    if not body or not body.strip():
        return Grid.make_empty()

    # Strip parameters (e.g. "; charset=utf-8") but preserve version for vendor types.
    raw = content_type.strip().lower()
    base_mime = raw.split(";")[0].strip()

    # Try the base MIME first.
    fmt = _MIME_TO_FORMAT.get(base_mime)

    # If base MIME is a vendor type, try with version parameter.
    if fmt is None and base_mime.startswith("application/vnd.haystack"):
        for param in raw.split(";")[1:]:
            param = param.strip()
            if param.startswith("version="):
                versioned = f"{base_mime};{param}"
                fmt = _MIME_TO_FORMAT.get(versioned)
                break

    if fmt is None:
        raise UnsupportedContentTypeError(base_mime)

    if fmt not in _DECODABLE_FORMATS:
        raise UnsupportedContentTypeError(base_mime)

    if fmt == "zinc":
        return _zinc.decode_grid(body.decode("utf-8"))

    if fmt == "json_v3":
        return _json.decode_grid(body, version=JsonVersion.V3)

    if fmt == "trio":
        records = _trio.parse_trio(body.decode("utf-8"))
        return Grid.make_rows(records) if records else Grid.make_empty()

    # JSON v4 (default).
    return _json.decode_grid(body)


# ---------------------------------------------------------------------------
# RDF helpers
# ---------------------------------------------------------------------------


def _encode_grid_rdf(grid: Grid, rdf_format: str) -> str:
    """Encode a Grid as RDF (Turtle or JSON-LD).

    Builds a minimal rdflib Graph from the grid's rows and columns, then
    serializes to the requested RDF format.

    :param grid: Grid to encode.
    :param rdf_format: rdflib serialization format (``"turtle"`` or ``"json-ld"``).
    :returns: Serialized RDF string.
    """
    try:
        import rdflib
        from rdflib import RDF, BNode, Literal, URIRef
        from rdflib import Namespace as RdfNs
    except ImportError:
        return f"# rdflib is required for {rdf_format} output\n"

    ph = RdfNs("https://project-haystack.org/def/ph/")
    g = rdflib.Graph()
    g.bind("ph", ph)

    for row in grid.rows:
        ref_val = row.get("id")
        if ref_val is not None:
            subject: URIRef | BNode = URIRef(f"urn:haystack:{ref_val}")
        else:
            subject = BNode()

        g.add((subject, RDF.type, ph["entity"]))

        for key, val in row.items():
            if key == "id":
                continue
            pred = ph[key]
            from hs_py.kinds import Marker, Ref

            if isinstance(val, Marker):
                g.add((subject, pred, Literal(True)))
            elif isinstance(val, Ref):
                g.add((subject, pred, URIRef(f"urn:haystack:{val.val}")))
            elif val is not None:
                g.add((subject, pred, Literal(str(val))))

    return g.serialize(format=rdf_format)
