"""Tests for binary WebSocket frame codec (ws_codec.py)."""

from __future__ import annotations

import asyncio
import struct
import time

import pytest

from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref
from hs_py.ops import HaystackOps
from hs_py.ws_client import WebSocketClient
from hs_py.ws_codec import (
    CODE_OPS,
    COMP_LZMA,
    COMP_ZLIB,
    COMPRESS_THRESHOLD,
    FLAG_CHUNKED,
    FLAG_COMPRESSED,
    FLAG_ERROR,
    FLAG_PUSH,
    FLAG_RESPONSE,
    OP_CODES,
    ChunkAssembler,
    compress_payload,
    decode_binary_frame,
    decompress_payload,
    encode_binary_push,
    encode_binary_request,
    encode_binary_response,
    encode_chunked_frames,
)
from hs_py.ws_server import WebSocketServer

# ---------------------------------------------------------------------------
# Op code mapping
# ---------------------------------------------------------------------------


class TestOpCodes:
    def test_all_ops_have_codes(self) -> None:
        expected = {
            "about",
            "ops",
            "formats",
            "close",
            "read",
            "nav",
            "hisRead",
            "hisWrite",
            "pointWrite",
            "watchSub",
            "watchUnsub",
            "watchPoll",
            "invokeAction",
        }
        assert set(OP_CODES.keys()) == expected

    def test_code_ops_inverse(self) -> None:
        for op, code in OP_CODES.items():
            assert CODE_OPS[code] == op


# ---------------------------------------------------------------------------
# Encode / decode round-trip
# ---------------------------------------------------------------------------


class TestBinaryFrameRoundTrip:
    def test_request_round_trip(self) -> None:
        grid = GridBuilder().add_col("filter").add_row({"filter": "point"}).to_grid()
        data = encode_binary_request(42, "read", grid)
        flags, req_id, op, grid_bytes = decode_binary_frame(data)
        assert flags == 0
        assert req_id == 42
        assert op == "read"
        assert len(grid_bytes) > 0

    def test_response_round_trip(self) -> None:
        grid = Grid.make_rows([{"id": Ref("p1"), "dis": "Point 1"}])
        data = encode_binary_response(42, "read", grid)
        flags, req_id, op, _grid_bytes = decode_binary_frame(data)
        assert flags == FLAG_RESPONSE
        assert req_id == 42
        assert op == "read"

    def test_error_response(self) -> None:
        grid = Grid.make_error("Something went wrong")
        data = encode_binary_response(7, "read", grid, is_error=True)
        flags, req_id, op, _grid_bytes = decode_binary_frame(data)
        assert flags == (FLAG_RESPONSE | FLAG_ERROR)
        assert req_id == 7
        assert op == "read"

    def test_push_round_trip(self) -> None:
        grid = Grid.make_rows([{"id": Ref("p1"), "curVal": Number(72.0)}])
        data = encode_binary_push("watchPoll", grid)
        flags, req_id, op, grid_bytes = decode_binary_frame(data)
        assert flags == FLAG_PUSH
        assert req_id == 0
        assert op == "watchPoll"
        assert len(grid_bytes) > 0


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestBinaryFrameErrors:
    def test_decode_too_short(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            decode_binary_frame(b"\x00\x00")

    def test_decode_unknown_opcode(self) -> None:
        # Construct a header with opcode 255 (not in mapping)
        header = struct.pack("!BHB", 0, 0, 255)
        with pytest.raises(ValueError, match="Unknown op code"):
            decode_binary_frame(header)

    def test_encode_unknown_op(self) -> None:
        with pytest.raises(ValueError, match="Unknown operation"):
            encode_binary_request(0, "bogus", Grid.make_empty())


# ---------------------------------------------------------------------------
# Binary mode integration
# ---------------------------------------------------------------------------


class _BinaryTestOps(HaystackOps):
    async def about(self) -> Grid:
        return Grid.make_rows([{"serverName": "BinaryTest"}])

    async def read(self, grid: Grid) -> Grid:
        return Grid.make_rows(
            [
                {"id": Ref("p1"), "dis": "Point 1", "point": MARKER},
                {"id": Ref("p2"), "dis": "Point 2", "point": MARKER},
            ]
        )


class TestBinaryModeIntegration:
    async def test_binary_about(self) -> None:
        """Binary mode client can execute about() and get correct response."""
        ops = _BinaryTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, binary=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, binary=True, pythonic=False) as client:
                grid = await client.about()
                assert grid[0]["serverName"] == "BinaryTest"
        finally:
            await server.stop()

    async def test_binary_read(self) -> None:
        """Binary mode client can execute read() and get correct response."""
        ops = _BinaryTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, binary=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, binary=True, pythonic=False) as client:
                grid = await client.read("point")
                assert len(grid) == 2
                assert grid[0]["id"] == Ref("p1")
        finally:
            await server.stop()

    async def test_binary_concurrent(self) -> None:
        """Multiple concurrent requests in binary mode."""
        ops = _BinaryTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, binary=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, binary=True, pythonic=False) as client:
                results = await asyncio.gather(
                    client.about(),
                    client.read("point"),
                )
                assert results[0][0]["serverName"] == "BinaryTest"
                assert len(results[1]) == 2
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------


def _large_grid(n: int = 200) -> Grid:
    """Build a grid large enough to exceed COMPRESS_THRESHOLD."""
    rows = [
        {"id": Ref(f"p{i}"), "dis": f"Point {i} temperature sensor", "val": Number(68.5 + i)}
        for i in range(n)
    ]
    return Grid.make_rows(rows)


class TestCompressPayload:
    def test_below_threshold_returns_unchanged(self) -> None:
        data = b"small payload"
        result, algo = compress_payload(data, COMP_ZLIB)
        assert result is data
        assert algo is None

    def test_zlib_compresses_above_threshold(self) -> None:
        data = b"x" * (COMPRESS_THRESHOLD + 100)
        result, algo = compress_payload(data, COMP_ZLIB)
        assert algo == COMP_ZLIB
        assert len(result) < len(data)

    def test_lzma_compresses_above_threshold(self) -> None:
        data = b"y" * (COMPRESS_THRESHOLD + 100)
        result, algo = compress_payload(data, COMP_LZMA)
        assert algo == COMP_LZMA
        assert len(result) < len(data)

    def test_unknown_algorithm_raises(self) -> None:
        data = b"a" * (COMPRESS_THRESHOLD + 100)
        with pytest.raises(ValueError, match="Unknown compression algorithm"):
            compress_payload(data, algorithm=99)

    def test_custom_threshold(self) -> None:
        data = b"hello"
        _result, algo = compress_payload(data, COMP_ZLIB, threshold=1)
        assert algo == COMP_ZLIB


class TestDecompressPayload:
    def test_zlib_round_trip(self) -> None:
        original = b"test data " * 500
        compressed, _ = compress_payload(original, COMP_ZLIB, threshold=0)
        assert decompress_payload(compressed, COMP_ZLIB) == original

    def test_lzma_round_trip(self) -> None:
        original = b"test data " * 500
        compressed, _ = compress_payload(original, COMP_LZMA, threshold=0)
        assert decompress_payload(compressed, COMP_LZMA) == original

    def test_unknown_algorithm_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown compression algorithm"):
            decompress_payload(b"data", 99)


class TestCompressedFrameRoundTrip:
    def test_request_zlib_round_trip(self) -> None:
        grid = _large_grid()
        data = encode_binary_request(42, "read", grid, compression=COMP_ZLIB)
        assert data[0] & FLAG_COMPRESSED
        _flags, req_id, op, grid_bytes = decode_binary_frame(data)
        assert req_id == 42
        assert op == "read"
        assert len(grid_bytes) > 0
        from hs_py.encoding.json import decode_grid

        decoded = decode_grid(grid_bytes)
        assert len(decoded) == 200

    def test_response_lzma_round_trip(self) -> None:
        grid = _large_grid()
        data = encode_binary_response(7, "read", grid, compression=COMP_LZMA)
        assert data[0] & FLAG_COMPRESSED
        flags, req_id, _op, grid_bytes = decode_binary_frame(data)
        assert flags & FLAG_RESPONSE
        assert req_id == 7
        from hs_py.encoding.json import decode_grid

        decoded = decode_grid(grid_bytes)
        assert len(decoded) == 200

    def test_push_compressed(self) -> None:
        grid = _large_grid(100)
        data = encode_binary_push("watchPoll", grid, compression=COMP_ZLIB)
        assert data[0] & FLAG_COMPRESSED
        flags, _req_id, op, _grid_bytes = decode_binary_frame(data)
        assert flags & FLAG_PUSH
        assert op == "watchPoll"

    def test_error_response_compressed(self) -> None:
        grid = _large_grid()
        data = encode_binary_response(1, "read", grid, is_error=True, compression=COMP_ZLIB)
        flags, _, _, _ = decode_binary_frame(data)
        assert flags & FLAG_RESPONSE
        assert flags & FLAG_ERROR
        assert flags & FLAG_COMPRESSED

    def test_small_payload_not_compressed(self) -> None:
        """Payloads below threshold should NOT have FLAG_COMPRESSED set."""
        grid = Grid.make_rows([{"id": Ref("p1")}])
        data = encode_binary_request(1, "about", grid, compression=COMP_ZLIB)
        assert not (data[0] & FLAG_COMPRESSED)

    def test_no_compression_param_produces_v1_frame(self) -> None:
        """Without compression= param, frames are identical to v1 format."""
        grid = _large_grid()
        v1 = encode_binary_request(42, "read", grid)
        assert not (v1[0] & FLAG_COMPRESSED)
        _flags, req_id, _op, _grid_bytes = decode_binary_frame(v1)
        assert req_id == 42

    def test_mixed_compressed_uncompressed_stream(self) -> None:
        """Decoder handles a mix of compressed and uncompressed frames."""
        small_grid = Grid.make_rows([{"id": Ref("p1")}])
        large_grid = _large_grid()

        frame_uncompressed = encode_binary_request(1, "about", small_grid)
        frame_compressed = encode_binary_request(2, "read", large_grid, compression=COMP_ZLIB)

        assert not (frame_uncompressed[0] & FLAG_COMPRESSED)
        assert frame_compressed[0] & FLAG_COMPRESSED

        _, r1, _, _ = decode_binary_frame(frame_uncompressed)
        _, r2, _, _gb = decode_binary_frame(frame_compressed)
        assert r1 == 1
        assert r2 == 2

    def test_compressed_frame_missing_algo_byte(self) -> None:
        """Compressed flag set but no algo byte should raise."""
        header = struct.pack("!BHB", FLAG_COMPRESSED, 0, 1)
        with pytest.raises(ValueError, match="missing algorithm byte"):
            decode_binary_frame(header)


# ---------------------------------------------------------------------------
# Chunked encoding/decoding
# ---------------------------------------------------------------------------


class TestEncodedChunkedFrames:
    def test_single_chunk_for_small_payload(self) -> None:
        payload = b"small"
        frames = encode_chunked_frames(0, 1, "read", payload, chunk_size=1024)
        assert len(frames) == 1
        flags, _req_id, _op, _chunk_data = decode_binary_frame(frames[0])
        assert flags & FLAG_CHUNKED

    def test_multiple_chunks(self) -> None:
        payload = b"x" * 1000
        frames = encode_chunked_frames(0, 1, "read", payload, chunk_size=300)
        assert len(frames) == 4  # ceil(1000/300)
        for f in frames:
            flags, _, _, _ = decode_binary_frame(f)
            assert flags & FLAG_CHUNKED

    def test_chunked_with_compression(self) -> None:
        payload = b"y" * 2000
        frames = encode_chunked_frames(
            FLAG_RESPONSE,
            5,
            "read",
            payload,
            compression=COMP_ZLIB,
            chunk_size=500,
        )
        assert len(frames) == 4
        for f in frames:
            flags = f[0]
            assert flags & FLAG_CHUNKED
            assert flags & FLAG_COMPRESSED

    def test_empty_payload_produces_one_chunk(self) -> None:
        frames = encode_chunked_frames(0, 1, "about", b"", chunk_size=256)
        assert len(frames) == 1

    def test_chunk_preserves_response_flags(self) -> None:
        payload = b"data" * 100
        frames = encode_chunked_frames(
            FLAG_RESPONSE | FLAG_ERROR,
            10,
            "read",
            payload,
            chunk_size=100,
        )
        for f in frames:
            flags = f[0]
            assert flags & FLAG_RESPONSE
            assert flags & FLAG_ERROR
            assert flags & FLAG_CHUNKED


class TestChunkAssembler:
    def test_reassemble_in_order(self) -> None:
        payload = b"hello world, this is a chunked payload test!"
        frames = encode_chunked_frames(0, 1, "read", payload, chunk_size=10)
        assembler = ChunkAssembler()
        result = None
        for f in frames:
            flags, req_id, op, chunk_data = decode_binary_frame(f)
            result = assembler.feed(flags, req_id, op, chunk_data)
        assert result == payload

    def test_reassemble_out_of_order(self) -> None:
        payload = b"abcdefghijklmnopqrstuvwxyz"
        frames = encode_chunked_frames(0, 1, "read", payload, chunk_size=5)
        shuffled = [frames[-1], *frames[:-1]]
        assembler = ChunkAssembler()
        result = None
        for f in shuffled:
            flags, req_id, op, chunk_data = decode_binary_frame(f)
            result = assembler.feed(flags, req_id, op, chunk_data)
        assert result == payload

    def test_reassemble_with_compression(self) -> None:
        payload = b"repeated data " * 200
        frames = encode_chunked_frames(
            0,
            3,
            "hisRead",
            payload,
            compression=COMP_ZLIB,
            chunk_size=500,
        )
        assembler = ChunkAssembler()
        result = None
        for f in frames:
            flags, req_id, op, chunk_data = decode_binary_frame(f)
            result = assembler.feed(flags, req_id, op, chunk_data)
        assert result == payload

    def test_reassemble_lzma_compressed_chunks(self) -> None:
        payload = b"lzma test data " * 200
        frames = encode_chunked_frames(
            FLAG_RESPONSE,
            7,
            "read",
            payload,
            compression=COMP_LZMA,
            chunk_size=500,
        )
        assembler = ChunkAssembler()
        result = None
        for f in frames:
            flags, req_id, op, chunk_data = decode_binary_frame(f)
            result = assembler.feed(flags, req_id, op, chunk_data)
        assert result == payload

    def test_pending_count(self) -> None:
        payload = b"x" * 100
        frames = encode_chunked_frames(0, 1, "read", payload, chunk_size=30)
        assembler = ChunkAssembler()
        for f in frames[:-1]:
            flags, req_id, op, chunk_data = decode_binary_frame(f)
            result = assembler.feed(flags, req_id, op, chunk_data)
            assert result is None
        assert assembler.pending_count == 1
        flags, req_id, op, chunk_data = decode_binary_frame(frames[-1])
        result = assembler.feed(flags, req_id, op, chunk_data)
        assert result is not None
        assert assembler.pending_count == 0

    def test_cleanup_expired_buffers(self) -> None:
        payload = b"x" * 100
        frames = encode_chunked_frames(0, 1, "read", payload, chunk_size=30)
        assembler = ChunkAssembler(ttl_seconds=5.0)
        flags, req_id, op, chunk_data = decode_binary_frame(frames[0])
        assembler.feed(flags, req_id, op, chunk_data)
        assert assembler.pending_count == 1
        assembler.cleanup(time.monotonic() + 10.0)
        assert assembler.pending_count == 0

    def test_cleanup_retains_fresh_buffers(self) -> None:
        payload = b"x" * 100
        frames = encode_chunked_frames(0, 1, "read", payload, chunk_size=30)
        assembler = ChunkAssembler(ttl_seconds=60.0)
        flags, req_id, op, chunk_data = decode_binary_frame(frames[0])
        assembler.feed(flags, req_id, op, chunk_data)
        assembler.cleanup(time.monotonic())
        assert assembler.pending_count == 1

    def test_multiple_concurrent_sequences(self) -> None:
        """Two different req_ids being assembled concurrently."""
        payload_a = b"aaaa" * 50
        payload_b = b"bbbb" * 50
        frames_a = encode_chunked_frames(0, 1, "read", payload_a, chunk_size=40)
        frames_b = encode_chunked_frames(FLAG_RESPONSE, 2, "read", payload_b, chunk_size=40)

        assembler = ChunkAssembler()
        results: dict[int, bytes] = {}
        for fa, fb in zip(frames_a, frames_b, strict=False):
            for f in (fa, fb):
                flags, req_id, op, chunk_data = decode_binary_frame(f)
                result = assembler.feed(flags, req_id, op, chunk_data)
                if result is not None:
                    results[req_id] = result

        remaining = frames_a[len(frames_b) :] + frames_b[len(frames_a) :]
        for f in remaining:
            flags, req_id, op, chunk_data = decode_binary_frame(f)
            result = assembler.feed(flags, req_id, op, chunk_data)
            if result is not None:
                results[req_id] = result

        assert results[1] == payload_a
        assert results[2] == payload_b


# ---------------------------------------------------------------------------
# Compressed binary mode integration tests
# ---------------------------------------------------------------------------


class _LargeReadOps(HaystackOps):
    """Ops that return a large grid to test compression on the wire."""

    async def about(self) -> Grid:
        return Grid.make_rows([{"serverName": "CompressTest"}])

    async def read(self, grid: Grid) -> Grid:
        return Grid.make_rows(
            [
                {
                    "id": Ref(f"p{i}"),
                    "dis": f"Point {i} temperature sensor for AHU-{i // 10}",
                    "point": MARKER,
                    "sensor": MARKER,
                    "cur": Number(68.5 + i * 0.1, "°F"),
                }
                for i in range(200)
            ]
        )


class TestCompressedBinaryIntegration:
    async def test_compressed_about(self) -> None:
        """Binary+compressed client can execute about() and get correct response."""
        ops = _LargeReadOps()
        server = WebSocketServer(
            ops,
            host="127.0.0.1",
            port=0,
            binary=True,
            binary_compression=COMP_ZLIB,
        )
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(
                url,
                binary=True,
                binary_compression=COMP_ZLIB,
                pythonic=False,
            ) as client:
                grid = await client.about()
                assert grid[0]["serverName"] == "CompressTest"
        finally:
            await server.stop()

    async def test_compressed_read_large_grid(self) -> None:
        """Compressed binary read returns all 200 rows correctly."""
        ops = _LargeReadOps()
        server = WebSocketServer(
            ops,
            host="127.0.0.1",
            port=0,
            binary=True,
            binary_compression=COMP_ZLIB,
        )
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(
                url,
                binary=True,
                binary_compression=COMP_ZLIB,
                pythonic=False,
            ) as client:
                grid = await client.read("point")
                assert len(grid) == 200
                assert grid[0]["id"] == Ref("p0")
                assert grid[199]["id"] == Ref("p199")
        finally:
            await server.stop()

    async def test_compressed_concurrent_requests(self) -> None:
        """Multiple concurrent compressed requests work correctly."""
        ops = _LargeReadOps()
        server = WebSocketServer(
            ops,
            host="127.0.0.1",
            port=0,
            binary=True,
            binary_compression=COMP_ZLIB,
        )
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(
                url,
                binary=True,
                binary_compression=COMP_ZLIB,
                pythonic=False,
            ) as client:
                results = await asyncio.gather(
                    client.about(),
                    client.read("point"),
                    client.about(),
                )
                assert results[0][0]["serverName"] == "CompressTest"
                assert len(results[1]) == 200
                assert results[2][0]["serverName"] == "CompressTest"
        finally:
            await server.stop()

    async def test_lzma_compression_integration(self) -> None:
        """LZMA compressed binary mode works end-to-end."""
        ops = _LargeReadOps()
        server = WebSocketServer(
            ops,
            host="127.0.0.1",
            port=0,
            binary=True,
            binary_compression=COMP_LZMA,
        )
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(
                url,
                binary=True,
                binary_compression=COMP_LZMA,
                pythonic=False,
            ) as client:
                grid = await client.read("point")
                assert len(grid) == 200
        finally:
            await server.stop()

    async def test_uncompressed_client_to_compressed_server(self) -> None:
        """Uncompressed client can talk to a server that has compression enabled.

        Server compresses responses but client sends uncompressed requests.
        Both sides must handle mixed frames.
        """
        ops = _LargeReadOps()
        server = WebSocketServer(
            ops,
            host="127.0.0.1",
            port=0,
            binary=True,
            binary_compression=COMP_ZLIB,
        )
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            # Client does NOT enable compression — but must still decode compressed responses
            async with WebSocketClient(url, binary=True, pythonic=False) as client:
                grid = await client.about()
                assert grid[0]["serverName"] == "CompressTest"
        finally:
            await server.stop()
