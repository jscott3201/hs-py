"""Binary WebSocket frame codec for Haystack.

Provides a compact binary frame format for high-frequency watch pushes and
other latency-sensitive operations.  The binary header eliminates the JSON
envelope overhead.

Frame layout::

    Byte 0:    flags (bit 0 = response, bit 1 = error, bit 2 = push)
    Bytes 1-2: request ID (uint16 big-endian, 0 for push)
    Byte 3:    op code (uint8, mapped from op name)
    Bytes 4-N: grid payload (JSON-encoded bytes)

Total header: 4 bytes vs ~30+ bytes for the JSON envelope.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from hs_py.encoding.json import encode_grid

if TYPE_CHECKING:
    from hs_py.grid import Grid

__all__ = [
    "OP_CODES",
    "decode_binary_frame",
    "encode_binary_push",
    "encode_binary_request",
    "encode_binary_response",
]

# -- Flags -------------------------------------------------------------------

FLAG_RESPONSE: int = 0x01
"""Bit 0 — frame is a server response."""

FLAG_ERROR: int = 0x02
"""Bit 1 — response is an error grid."""

FLAG_PUSH: int = 0x04
"""Bit 2 — frame is a server-initiated push."""

# -- Op codes ----------------------------------------------------------------

#: Map Haystack operation names to binary op codes.
OP_CODES: dict[str, int] = {
    "about": 1,
    "ops": 2,
    "formats": 3,
    "close": 4,
    "read": 10,
    "nav": 11,
    "hisRead": 12,
    "hisWrite": 13,
    "pointWrite": 14,
    "watchSub": 15,
    "watchUnsub": 16,
    "watchPoll": 17,
    "invokeAction": 18,
}

#: Reverse lookup — binary op codes to operation names.
CODE_OPS: dict[int, str] = {v: k for k, v in OP_CODES.items()}

_HEADER = struct.Struct("!BHB")  # flags(1) + req_id(2) + opcode(1) = 4 bytes


# -- Encode ------------------------------------------------------------------


def encode_binary_request(req_id: int, op: str, grid: Grid) -> bytes:
    """Encode a client request as a binary frame.

    :param req_id: Request correlation ID (0-65535).
    :param op: Operation name (must be in :data:`OP_CODES`).
    :param grid: Request grid payload.
    :returns: Binary frame bytes.
    :raises ValueError: If *op* is not a known operation.
    """
    opcode = _require_opcode(op)
    header = _HEADER.pack(0, req_id, opcode)
    return header + encode_grid(grid)


def encode_binary_response(req_id: int, op: str, grid: Grid, *, is_error: bool = False) -> bytes:
    """Encode a server response as a binary frame.

    :param req_id: Correlated request ID.
    :param op: Operation name.
    :param grid: Response grid payload.
    :param is_error: ``True`` if this is an error response.
    :returns: Binary frame bytes.
    """
    opcode = _require_opcode(op)
    flags = FLAG_RESPONSE
    if is_error:
        flags |= FLAG_ERROR
    header = _HEADER.pack(flags, req_id, opcode)
    return header + encode_grid(grid)


def encode_binary_push(op: str, grid: Grid) -> bytes:
    """Encode a server-initiated push as a binary frame.

    :param op: Push type (e.g. ``"watchPoll"``).
    :param grid: Push grid payload.
    :returns: Binary frame bytes.
    """
    opcode = _require_opcode(op)
    header = _HEADER.pack(FLAG_PUSH, 0, opcode)
    return header + encode_grid(grid)


# -- Decode ------------------------------------------------------------------


def decode_binary_frame(data: bytes) -> tuple[int, int, str, bytes]:
    """Decode a binary frame into its components.

    :param data: Raw binary frame bytes.
    :returns: Tuple of ``(flags, req_id, op_name, grid_bytes)``.
    :raises ValueError: If frame is too short or op code is unknown.
    """
    if len(data) < _HEADER.size:
        msg = f"Binary frame too short: {len(data)} bytes"
        raise ValueError(msg)
    flags, req_id, opcode = _HEADER.unpack_from(data)
    op = CODE_OPS.get(opcode)
    if op is None:
        msg = f"Unknown op code: {opcode}"
        raise ValueError(msg)
    return flags, req_id, op, data[_HEADER.size :]


# -- Helpers -----------------------------------------------------------------


def _require_opcode(op: str) -> int:
    """Look up an op code or raise ValueError."""
    code = OP_CODES.get(op)
    if code is None:
        msg = f"Unknown operation: {op!r}"
        raise ValueError(msg)
    return code
