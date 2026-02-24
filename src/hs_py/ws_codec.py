"""Binary WebSocket frame codec for Haystack.

Provides a compact binary frame format for high-frequency watch pushes and
other latency-sensitive operations.  The binary header eliminates the JSON
envelope overhead.

Frame layout (v1 — bits 3-4 clear)::

    Byte 0:    flags (bit 0 = response, bit 1 = error, bit 2 = push)
    Bytes 1-2: request ID (uint16 big-endian, 0 for push)
    Byte 3:    op code (uint8, mapped from op name)
    Bytes 4-N: grid payload (JSON-encoded bytes)

Frame layout (v2 — compression/chunking)::

    Byte 0:    flags (bits 0-2 same as v1, bit 3 = compressed, bit 4 = chunked)
    Bytes 1-2: request ID (uint16 big-endian)
    Byte 3:    op code (uint8)

    IF compressed (bit 3 set):
        Byte 4: compression algorithm (0 = zlib, 1 = lzma)

    IF chunked (bit 4 set):
        Next 2 bytes: chunk index (uint16 big-endian)
        Next 2 bytes: total chunks (uint16 big-endian)

    Remaining: payload bytes (compressed or raw)

Total v1 header: 4 bytes.  v2 adds 1 byte for compression, 4 for chunking.
"""

from __future__ import annotations

import lzma
import struct
import time
import zlib
from typing import TYPE_CHECKING

from hs_py.encoding.json import encode_grid

if TYPE_CHECKING:
    from hs_py.grid import Grid

__all__ = [
    "CHUNK_SIZE",
    "CHUNK_THRESHOLD",
    "COMP_LZMA",
    "COMP_ZLIB",
    "OP_CODES",
    "ChunkAssembler",
    "compress_payload",
    "decode_binary_frame",
    "decompress_payload",
    "encode_binary_push",
    "encode_binary_request",
    "encode_binary_response",
    "encode_chunked_frames",
]

# -- Flags -------------------------------------------------------------------

FLAG_RESPONSE: int = 0x01
"""Bit 0 — frame is a server response."""

FLAG_ERROR: int = 0x02
"""Bit 1 — response is an error grid."""

FLAG_PUSH: int = 0x04
"""Bit 2 — frame is a server-initiated push."""

FLAG_COMPRESSED: int = 0x08
"""Bit 3 — payload is compressed (v2)."""

FLAG_CHUNKED: int = 0x10
"""Bit 4 — frame is part of a chunked sequence (v2)."""

# -- Compression algorithms --------------------------------------------------

COMP_ZLIB: int = 0
"""Compression algorithm ID for zlib (level 1)."""

COMP_LZMA: int = 1
"""Compression algorithm ID for LZMA (raw, preset 0)."""

_LZMA_FILTERS: list[dict[str, int]] = [{"id": lzma.FILTER_LZMA2, "preset": 0}]

#: Minimum payload size (bytes) to attempt compression.
COMPRESS_THRESHOLD: int = 1024

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
_CHUNK_HEADER = struct.Struct("!HH")  # chunk_index(2) + total_chunks(2) = 4 bytes

#: Default chunk size in bytes (before compression).
CHUNK_SIZE: int = 256 * 1024

#: Minimum payload size to trigger chunking.
CHUNK_THRESHOLD: int = 256 * 1024


# -- Compression -------------------------------------------------------------


def compress_payload(
    data: bytes,
    algorithm: int = COMP_ZLIB,
    threshold: int = COMPRESS_THRESHOLD,
) -> tuple[bytes, int | None]:
    """Compress *data* if it exceeds *threshold*.

    :param data: Raw payload bytes.
    :param algorithm: ``COMP_ZLIB`` or ``COMP_LZMA``.
    :param threshold: Minimum size to compress. Payloads smaller than this
        are returned unchanged.
    :returns: ``(payload, algo_id)`` — *algo_id* is ``None`` when the payload
        was not compressed (below threshold).
    """
    if len(data) < threshold:
        return data, None
    if algorithm == COMP_ZLIB:
        return zlib.compress(data, 1), COMP_ZLIB
    if algorithm == COMP_LZMA:
        return lzma.compress(data, format=lzma.FORMAT_RAW, filters=_LZMA_FILTERS), COMP_LZMA
    msg = f"Unknown compression algorithm: {algorithm}"
    raise ValueError(msg)


def decompress_payload(data: bytes, algorithm: int) -> bytes:
    """Decompress *data* using the given algorithm.

    :param data: Compressed payload bytes.
    :param algorithm: ``COMP_ZLIB`` or ``COMP_LZMA``.
    :returns: Decompressed bytes.
    :raises ValueError: If *algorithm* is not recognised.
    """
    if algorithm == COMP_ZLIB:
        return zlib.decompress(data)
    if algorithm == COMP_LZMA:
        return lzma.decompress(data, format=lzma.FORMAT_RAW, filters=_LZMA_FILTERS)
    msg = f"Unknown compression algorithm: {algorithm}"
    raise ValueError(msg)


# -- Encode ------------------------------------------------------------------


def _encode_payload(
    flags: int,
    req_id: int,
    opcode: int,
    payload: bytes,
    compression: int | None = None,
    threshold: int = COMPRESS_THRESHOLD,
) -> bytes:
    """Build a binary frame, optionally compressing the payload."""
    if compression is not None and len(payload) >= threshold:
        compressed, algo = compress_payload(payload, compression, threshold)
        if algo is not None:
            flags |= FLAG_COMPRESSED
            header = _HEADER.pack(flags, req_id, opcode)
            return header + bytes([algo]) + compressed
    header = _HEADER.pack(flags, req_id, opcode)
    return header + payload


def encode_binary_request(
    req_id: int,
    op: str,
    grid: Grid,
    *,
    compression: int | None = None,
) -> bytes:
    """Encode a client request as a binary frame.

    :param req_id: Request correlation ID (0-65535).
    :param op: Operation name (must be in :data:`OP_CODES`).
    :param grid: Request grid payload.
    :param compression: Optional compression algorithm (``COMP_ZLIB`` or
        ``COMP_LZMA``).  ``None`` disables compression.
    :returns: Binary frame bytes.
    :raises ValueError: If *op* is not a known operation.
    """
    opcode = _require_opcode(op)
    payload = encode_grid(grid)
    return _encode_payload(0, req_id, opcode, payload, compression)


def encode_binary_response(
    req_id: int,
    op: str,
    grid: Grid,
    *,
    is_error: bool = False,
    compression: int | None = None,
) -> bytes:
    """Encode a server response as a binary frame.

    :param req_id: Correlated request ID.
    :param op: Operation name.
    :param grid: Response grid payload.
    :param is_error: ``True`` if this is an error response.
    :param compression: Optional compression algorithm.
    :returns: Binary frame bytes.
    """
    opcode = _require_opcode(op)
    flags = FLAG_RESPONSE
    if is_error:
        flags |= FLAG_ERROR
    payload = encode_grid(grid)
    return _encode_payload(flags, req_id, opcode, payload, compression)


def encode_binary_push(
    op: str,
    grid: Grid,
    *,
    compression: int | None = None,
) -> bytes:
    """Encode a server-initiated push as a binary frame.

    :param op: Push type (e.g. ``"watchPoll"``).
    :param grid: Push grid payload.
    :param compression: Optional compression algorithm.
    :returns: Binary frame bytes.
    """
    opcode = _require_opcode(op)
    payload = encode_grid(grid)
    return _encode_payload(FLAG_PUSH, 0, opcode, payload, compression)


# -- Decode ------------------------------------------------------------------


def decode_binary_frame(data: bytes) -> tuple[int, int, str, bytes]:
    """Decode a binary frame into its components.

    Transparently decompresses compressed payloads (``FLAG_COMPRESSED``)
    and returns the chunk metadata for chunked frames (``FLAG_CHUNKED``).

    :param data: Raw binary frame bytes.
    :returns: Tuple of ``(flags, req_id, op_name, grid_bytes)``.
        For chunked frames the *grid_bytes* is the raw (possibly compressed)
        chunk payload — use :class:`ChunkAssembler` to reassemble.
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

    offset = _HEADER.size
    algo: int | None = None

    # For chunked frames, return everything after the base header so that
    # ChunkAssembler can parse the algo byte (if compressed) and chunk header.
    if flags & FLAG_CHUNKED:
        return flags, req_id, op, data[offset:]

    if flags & FLAG_COMPRESSED:
        if offset >= len(data):
            msg = "Compressed frame missing algorithm byte"
            raise ValueError(msg)
        algo = data[offset]
        offset += 1

    payload = data[offset:]

    if algo is not None:
        payload = decompress_payload(payload, algo)

    return flags, req_id, op, payload


def _decode_chunk_header(data: bytes) -> tuple[int, int, bytes]:
    """Extract chunk index, total count, and payload from chunk data.

    :param data: The ``grid_bytes`` portion returned by :func:`decode_binary_frame`
        for a chunked frame (everything after the base header + optional algo byte).
    :returns: ``(chunk_index, total_chunks, chunk_payload)``.
    """
    if len(data) < _CHUNK_HEADER.size:
        msg = "Chunk data too short for chunk header"
        raise ValueError(msg)
    idx, total = _CHUNK_HEADER.unpack_from(data)
    return idx, total, data[_CHUNK_HEADER.size :]


# -- Chunked encoding -------------------------------------------------------


def encode_chunked_frames(
    flags: int,
    req_id: int,
    op: str,
    payload: bytes,
    *,
    compression: int | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> list[bytes]:
    """Split *payload* into chunked binary frames.

    Each chunk is independently compressed (if *compression* is set) and
    wrapped with the chunked frame header.

    :param flags: Base flags for each frame (e.g. ``FLAG_RESPONSE``).
    :param req_id: Request correlation ID.
    :param op: Operation name.
    :param payload: Full payload bytes to chunk.
    :param compression: Optional compression algorithm.
    :param chunk_size: Maximum raw bytes per chunk before compression.
    :returns: List of binary frame bytes, one per chunk.
    """
    opcode = _require_opcode(op)
    chunks: list[bytes] = []
    total = (len(payload) + chunk_size - 1) // chunk_size
    if total == 0:
        total = 1  # empty payload still produces one chunk

    for i in range(total):
        start = i * chunk_size
        chunk_data = payload[start : start + chunk_size]

        frame_flags = flags | FLAG_CHUNKED
        algo_byte = b""

        if compression is not None:
            compressed, algo = compress_payload(chunk_data, compression, threshold=0)
            assert algo is not None  # threshold=0 guarantees compression
            frame_flags |= FLAG_COMPRESSED
            algo_byte = bytes([algo])
            chunk_data = compressed

        header = _HEADER.pack(frame_flags, req_id, opcode)
        chunk_hdr = _CHUNK_HEADER.pack(i, total)
        chunks.append(header + algo_byte + chunk_hdr + chunk_data)

    return chunks


# -- Chunk reassembly --------------------------------------------------------


class ChunkAssembler:
    """Reassemble chunked binary frames into complete payloads.

    Feed decoded chunk data via :meth:`feed` and receive the assembled
    payload when all chunks have arrived.

    :param ttl_seconds: Seconds before incomplete chunk sequences are discarded.
    """

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self._ttl = ttl_seconds
        # Key: (req_id, op) → {chunk_index: data}
        self._pending: dict[tuple[int, str], _ChunkBuffer] = {}

    def feed(
        self,
        flags: int,
        req_id: int,
        op: str,
        chunk_data: bytes,
    ) -> bytes | None:
        """Process a chunk frame and return the reassembled payload when complete.

        :param flags: Frame flags (must have ``FLAG_CHUNKED`` set).
        :param req_id: Request correlation ID.
        :param op: Operation name.
        :param chunk_data: The ``grid_bytes`` from :func:`decode_binary_frame`
            (includes chunk header + payload).
        :returns: Fully reassembled (and decompressed) payload when all chunks
            have been received, or ``None`` if still waiting for more.
        """
        compressed = bool(flags & FLAG_COMPRESSED)

        # Parse the algo byte from the front of chunk_data if compressed
        algo: int | None = None
        offset = 0
        if compressed:
            if not chunk_data:
                msg = "Compressed chunk missing algorithm byte"
                raise ValueError(msg)
            algo = chunk_data[0]
            offset = 1

        idx, total, payload = _decode_chunk_header(chunk_data[offset:])

        # Decompress individual chunk
        if algo is not None:
            payload = decompress_payload(payload, algo)

        key = (req_id, op)
        buf = self._pending.get(key)
        if buf is None:
            buf = _ChunkBuffer(total)
            self._pending[key] = buf

        buf.chunks[idx] = payload

        if len(buf.chunks) == buf.total:
            del self._pending[key]
            # Validate that all expected indices are present
            if set(buf.chunks.keys()) != set(range(buf.total)):
                msg = f"Chunk sequence has gaps: got indices {sorted(buf.chunks)} for total={buf.total}"
                raise ValueError(msg)
            parts = [buf.chunks[i] for i in range(buf.total)]
            return b"".join(parts)

        return None

    def cleanup(self, now: float) -> None:
        """Discard incomplete chunk buffers older than the TTL.

        :param now: Current monotonic timestamp (``time.monotonic()``).
        """
        expired = [key for key, buf in self._pending.items() if (now - buf.created) > self._ttl]
        for key in expired:
            del self._pending[key]

    @property
    def pending_count(self) -> int:
        """Number of incomplete chunk sequences being tracked."""
        return len(self._pending)


class _ChunkBuffer:
    """Internal buffer for partially-received chunk sequences."""

    __slots__ = ("chunks", "created", "total")

    def __init__(self, total: int) -> None:
        self.total = total
        self.chunks: dict[int, bytes] = {}
        self.created = time.monotonic()


# -- Helpers -----------------------------------------------------------------


def _require_opcode(op: str) -> int:
    """Look up an op code or raise ValueError."""
    code = OP_CODES.get(op)
    if code is None:
        msg = f"Unknown operation: {op!r}"
        raise ValueError(msg)
    return code
