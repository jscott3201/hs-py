"""Tests for binary WebSocket frame codec (ws_codec.py)."""

from __future__ import annotations

import asyncio

import pytest

from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref
from hs_py.ops import HaystackOps
from hs_py.ws_client import WebSocketClient
from hs_py.ws_codec import (
    CODE_OPS,
    FLAG_ERROR,
    FLAG_PUSH,
    FLAG_RESPONSE,
    OP_CODES,
    decode_binary_frame,
    encode_binary_push,
    encode_binary_request,
    encode_binary_response,
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
        import struct

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
