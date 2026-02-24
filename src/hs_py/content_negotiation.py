"""Content negotiation for Haystack HTTP servers.

Provides Accept header parsing, request body decoding, and response body
encoding across all supported Haystack wire formats (JSON, Zinc, Trio, CSV).
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from hs_py.encoding import csv as _csv
from hs_py.encoding import json as _json
from hs_py.encoding import zinc as _zinc

if TYPE_CHECKING:
    from hs_py.grid import Grid

_log = logging.getLogger(__name__)

__all__ = [
    "decode_request",
    "encode_response",
    "negotiate_format",
]

# MIME type to internal format name mapping
_MIME_TO_FORMAT: dict[str, str] = {
    "application/json": "json",
    "text/zinc": "zinc",
    "text/trio": "trio",
    "text/csv": "csv",
}

# Format name to MIME type mapping (for response Content-Type)
_FORMAT_TO_MIME: dict[str, str] = {v: k for k, v in _MIME_TO_FORMAT.items()}

# Wildcard MIME types that map to the default format
_WILDCARD_MIMES: frozenset[str] = frozenset({"*/*", "application/*"})

_DEFAULT_FORMAT = "json"


def negotiate_format(accept: str) -> str:
    """Parse an HTTP Accept header and return the best matching format name.

    Parses quality parameters (``q=...``) from each media type, sorts
    candidates by descending quality, and returns the best known format.
    Wildcards ``*/*`` and ``application/*`` resolve to ``"json"``.
    Falls back to ``"json"`` when no match is found.

    :param accept: Value of the HTTP ``Accept`` header.
    :returns: Format name: ``"json"``, ``"zinc"``, ``"trio"``, or ``"csv"``.
    """
    if not accept or not accept.strip():
        return _DEFAULT_FORMAT

    candidates: list[tuple[float, int, str]] = []
    for idx, token in enumerate(accept.split(",")):
        parts = token.strip().split(";")
        mime = parts[0].strip().lower()
        if not mime:
            continue
        # Extract quality value (default 1.0)
        q = 1.0
        for param in parts[1:]:
            param = param.strip()
            if param.startswith("q="):
                with contextlib.suppress(ValueError):
                    q = float(param[2:])
        candidates.append((q, -idx, mime))

    # Sort by quality descending, then by original order
    candidates.sort(reverse=True)

    for _q, _idx, mime in candidates:
        if mime in _WILDCARD_MIMES:
            return _DEFAULT_FORMAT
        fmt = _MIME_TO_FORMAT.get(mime)
        if fmt is not None:
            return fmt

    return _DEFAULT_FORMAT


def encode_response(grid: Grid, fmt: str) -> tuple[bytes, str]:
    """Encode a Grid to the specified wire format.

    :param grid: Grid to encode.
    :param fmt: Format name — one of ``"json"``, ``"zinc"``, ``"trio"``, or
        ``"csv"``.
    :returns: A ``(body_bytes, content_type)`` tuple.
    :raises ValueError: If *fmt* is not a recognised format name.
    """
    if fmt == "json":
        body = _json.encode_grid(grid)
        return body, "application/json"

    if fmt == "zinc":
        body = _zinc.encode_grid(grid).encode("utf-8")
        return body, "text/zinc"

    if fmt == "csv":
        body = _csv.encode_grid(grid).encode("utf-8")
        return body, "text/csv"

    if fmt == "trio":
        # Trio encodes records (list[dict]), not grids directly.  Fall back to
        # JSON to preserve full type fidelity for server responses.
        _log.debug(
            "Trio format requested but not supported for grid encoding; falling back to JSON"
        )
        body = _json.encode_grid(grid)
        return body, "application/json"

    msg = f"Unknown format: {fmt!r}"
    raise ValueError(msg)


def decode_request(body: bytes, content_type: str) -> Grid:
    """Decode a request body based on its Content-Type header.

    Only JSON and Zinc are supported for decode; all other content types fall
    back to a JSON parse attempt.  An empty body returns an empty Grid.

    :param body: Raw request body bytes.
    :param content_type: Value of the HTTP ``Content-Type`` header.
    :returns: Decoded :class:`~hs_py.grid.Grid`.
    """
    from hs_py.grid import Grid

    if not body or not body.strip():
        return Grid.make_empty()

    # Strip parameters (e.g. "; charset=utf-8") from the content type.
    mime = content_type.split(";")[0].strip().lower()
    fmt = _MIME_TO_FORMAT.get(mime, _DEFAULT_FORMAT)

    if fmt == "zinc":
        return _zinc.decode_grid(body.decode("utf-8"))

    # JSON is the default for "json", "trio", "csv", or anything unknown.
    return _json.decode_grid(body)
