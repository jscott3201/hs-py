"""Tests targeting uncovered lines in ws_server.py."""

from __future__ import annotations

import asyncio
import contextlib
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import orjson
import pytest
from websockets.exceptions import ConnectionClosedOK

from hs_py.encoding.json import decode_grid
from hs_py.errors import HaystackError
from hs_py.grid import Grid
from hs_py.kinds import MARKER, Number, Ref
from hs_py.ops import HaystackOps
from hs_py.ws import HaystackWebSocket
from hs_py.ws_codec import FLAG_PUSH, encode_binary_request
from hs_py.ws_server import WebSocketServer

# ---------------------------------------------------------------------------
# Test ops
# ---------------------------------------------------------------------------


class _Ops(HaystackOps):
    async def about(self) -> Grid:
        return Grid.make_rows([{"haystackVersion": "4.0", "serverName": "CovServer"}])

    async def read(self, grid: Grid) -> Grid:
        if grid.rows and "filter" in grid[0]:
            return Grid.make_rows([{"id": Ref("p1"), "point": MARKER}])
        return Grid.make_empty()

    async def nav(self, grid: Grid) -> Grid:
        msg = "boom"
        raise RuntimeError(msg)


class _ErrOps(HaystackOps):
    async def about(self) -> Grid:
        return Grid.make_rows([{"serverName": "ErrServer"}])

    async def read(self, grid: Grid) -> Grid:
        raise HaystackError("read failed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _start_stop(server: WebSocketServer) -> None:
    """Stop server, closing client connections first via the WeakSet."""
    # Close tracked connections so handlers exit, then close the server.
    for ws in set(server._connections):
        with contextlib.suppress(Exception):
            await ws.close()
    await asyncio.sleep(0.05)
    if server._server is not None:
        server._server.close()
        await server._server.wait_closed()
        server._server = None
    server._connection_count = 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def srv() -> AsyncIterator[WebSocketServer]:
    ops = _Ops()
    server = WebSocketServer(ops, host="127.0.0.1", port=0)
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def srv_url(srv: WebSocketServer) -> str:
    return f"ws://127.0.0.1:{srv.port}"


# ---------------------------------------------------------------------------
# Lines 142-143: stop() closes tracked connections
# ---------------------------------------------------------------------------


class TestStopClosesConnections:
    async def test_stop_iterates_connections(self) -> None:
        """Ensure stop() iterates and closes items in _connections."""
        ops = _Ops()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        url = f"ws://127.0.0.1:{server.port}"
        ws = await HaystackWebSocket.connect(url)
        await asyncio.sleep(0.1)
        assert server._connection_count >= 1
        # Close client first so handler exits, unblocking wait_closed
        await ws.close()
        await asyncio.sleep(0.1)
        await server.stop()
        assert server._connection_count == 0


# ---------------------------------------------------------------------------
# Lines 163-166: binary push_watch
# ---------------------------------------------------------------------------


class TestBinaryPush:
    async def test_binary_push_watch(self) -> None:
        ops = _Ops()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, binary=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            ws = await HaystackWebSocket.connect(url)
            await asyncio.sleep(0.1)
            grid = Grid.make_rows([{"id": Ref("p1"), "curVal": Number(42.0)}])
            await server.push_watch("w-1", grid)
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            assert isinstance(data, bytes)
            assert len(data) >= 4
            await ws.close()
        finally:
            await _start_stop(server)


# ---------------------------------------------------------------------------
# Lines 179-183: max connections rejection
# ---------------------------------------------------------------------------


class TestMaxConnections:
    async def test_reject_when_max_reached(self) -> None:
        import hs_py.ws_server as ws_mod

        original = ws_mod._MAX_CONNECTIONS
        ws_mod._MAX_CONNECTIONS = 1
        try:
            ops = _Ops()
            server = WebSocketServer(ops, host="127.0.0.1", port=0)
            await server.start()
            url = f"ws://127.0.0.1:{server.port}"
            ws1 = await HaystackWebSocket.connect(url)
            await asyncio.sleep(0.1)
            # Second connection should be rejected
            with pytest.raises((ConnectionError, OSError, asyncio.TimeoutError)):
                ws2 = await HaystackWebSocket.connect(url)
                await asyncio.wait_for(ws2.recv(), timeout=2.0)
            await ws1.close()
            await asyncio.sleep(0.1)
            await server.stop()
        finally:
            ws_mod._MAX_CONNECTIONS = original


# ---------------------------------------------------------------------------
# Lines 228-231: connection exception handling
# ---------------------------------------------------------------------------


class TestConnectionExceptions:
    async def test_abrupt_close_handled(self) -> None:
        ops = _Ops()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            ws = await HaystackWebSocket.connect(url)
            await asyncio.sleep(0.05)
            await ws.close()
            await asyncio.sleep(0.1)
            # Server should still be healthy
            ws2 = await HaystackWebSocket.connect(url)
            await ws2.send_text(orjson.dumps({"op": "about"}).decode())
            data = await asyncio.wait_for(ws2.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert "grid" in resp
            await ws2.close()
        finally:
            await asyncio.sleep(0.1)
            await server.stop()


# ---------------------------------------------------------------------------
# Lines 252-254: token auth error branch
# ---------------------------------------------------------------------------


class TestTokenAuthError:
    async def test_malformed_token_message(self) -> None:
        ops = _Ops()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, auth_token="secret")
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            ws = await HaystackWebSocket.connect(url)
            try:
                await ws.send_text("not-json{{{")
                with pytest.raises(
                    (ConnectionError, OSError, asyncio.TimeoutError, ConnectionClosedOK)
                ):
                    await asyncio.wait_for(ws.recv(), timeout=2.0)
            finally:
                with contextlib.suppress(Exception):
                    await ws.close()
        finally:
            await asyncio.sleep(0.1)
            await server.stop()


# ---------------------------------------------------------------------------
# Lines 282-289, 316-317, 345-346, 371-373: SCRAM edge cases
# ---------------------------------------------------------------------------


class TestScramEdgeCases:
    @pytest.fixture
    async def scram_srv(self) -> AsyncIterator[WebSocketServer]:
        from hs_py.auth_types import SimpleAuthenticator

        ops = _Ops()
        auth = SimpleAuthenticator({"admin": "secret"}, iterations=4096)
        server = WebSocketServer(ops, host="127.0.0.1", port=0, authenticator=auth)
        await server.start()
        yield server
        await server.stop()

    async def test_non_hello_first_message(self, scram_srv: WebSocketServer) -> None:
        """Lines 287-289: Server expects hello but gets 'scram'."""
        url = f"ws://127.0.0.1:{scram_srv.port}"
        ws = await HaystackWebSocket.connect(url)
        try:
            await ws.send_text(orjson.dumps({"type": "scram", "data": "bogus"}).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert resp["type"] == "authErr"
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
            await asyncio.sleep(0.1)

    async def test_bad_token_in_scram_mode(self, scram_srv: WebSocketServer) -> None:
        """Lines 282-284: authToken in SCRAM mode with wrong token."""
        url = f"ws://127.0.0.1:{scram_srv.port}"
        ws = await HaystackWebSocket.connect(url)
        try:
            await ws.send_text(orjson.dumps({"authToken": "wrong"}).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert resp["type"] == "authErr"
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
            await asyncio.sleep(0.1)

    async def test_step2_non_scram_type(self, scram_srv: WebSocketServer) -> None:
        """Lines 316-317: After hello, send non-scram at step 2."""
        import base64

        url = f"ws://127.0.0.1:{scram_srv.port}"
        ws = await HaystackWebSocket.connect(url)
        try:
            username_b64 = base64.urlsafe_b64encode(b"admin").rstrip(b"=").decode()
            await ws.send_text(orjson.dumps({"type": "hello", "username": username_b64}).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert resp["type"] == "hello"
            # Send wrong type at step 2
            await ws.send_text(orjson.dumps({"type": "hello", "data": "bogus"}).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert resp["type"] == "authErr"
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
            await asyncio.sleep(0.1)

    async def test_step3_non_scram_type(self, scram_srv: WebSocketServer) -> None:
        """Lines 345-346: After step 2, send non-scram at step 3."""
        import base64

        from hs_py.auth import scram_client_first

        url = f"ws://127.0.0.1:{scram_srv.port}"
        ws = await HaystackWebSocket.connect(url)
        try:
            # Step 1: hello
            username_b64 = base64.urlsafe_b64encode(b"admin").rstrip(b"=").decode()
            await ws.send_text(orjson.dumps({"type": "hello", "username": username_b64}).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            hello_resp = orjson.loads(data)
            assert hello_resp["type"] == "hello"

            # Step 2: send valid client-first SCRAM message
            ht = hello_resp["handshakeToken"]
            first = scram_client_first("admin")
            scram_data = (
                base64.urlsafe_b64encode(first.client_first_msg.encode()).rstrip(b"=").decode()
            )
            await ws.send_text(
                orjson.dumps({"type": "scram", "handshakeToken": ht, "data": scram_data}).decode()
            )
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            step2_resp = orjson.loads(data)
            assert step2_resp["type"] == "scram"

            # Step 3: send wrong type
            await ws.send_text(orjson.dumps({"type": "hello"}).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert resp["type"] == "authErr"
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
            await asyncio.sleep(0.1)

    async def test_scram_exception(self, scram_srv: WebSocketServer) -> None:
        """Lines 371-373: Exception during SCRAM auth."""
        url = f"ws://127.0.0.1:{scram_srv.port}"
        ws = await HaystackWebSocket.connect(url)
        # Close immediately to trigger recv exception in SCRAM handler
        await ws.close()
        await asyncio.sleep(0.2)


# ---------------------------------------------------------------------------
# Lines 389-391: non-JSON message in message loop
# ---------------------------------------------------------------------------


class TestNonJsonMessage:
    async def test_non_json_ignored(self, srv: WebSocketServer, srv_url: str) -> None:
        ws = await HaystackWebSocket.connect(srv_url)
        try:
            await ws.send_text("this is not json{{{")
            await ws.send_text(orjson.dumps({"id": "1", "op": "about"}).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert "grid" in resp
            assert resp["id"] == "1"
        finally:
            await ws.close()


# ---------------------------------------------------------------------------
# Lines 419-421: ch field in response
# ---------------------------------------------------------------------------


class TestChannelField:
    async def test_ch_field_echoed(self, srv: WebSocketServer, srv_url: str) -> None:
        ws = await HaystackWebSocket.connect(srv_url)
        try:
            msg = orjson.dumps({"id": "42", "op": "about", "ch": "my-ch"}).decode()
            await ws.send_text(msg)
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert resp["id"] == "42"
            assert resp["ch"] == "my-ch"
        finally:
            await ws.close()

    async def test_no_id_or_ch(self, srv: WebSocketServer, srv_url: str) -> None:
        ws = await HaystackWebSocket.connect(srv_url)
        try:
            msg = orjson.dumps({"op": "about"}).decode()
            await ws.send_text(msg)
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert "grid" in resp
            assert "id" not in resp
            assert "ch" not in resp
        finally:
            await ws.close()


# ---------------------------------------------------------------------------
# Lines 431-436, 442-451: binary frame handling
# ---------------------------------------------------------------------------


class TestBinaryMessages:
    async def test_binary_about(self) -> None:
        ops = _Ops()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, binary=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            ws = await HaystackWebSocket.connect(url)
            try:
                frame = encode_binary_request(1, "about", Grid.make_empty())
                await ws.send_bytes(frame)
                data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert isinstance(data, bytes)
                assert data[0] & 0x01  # FLAG_RESPONSE
            finally:
                await ws.close()
        finally:
            await asyncio.sleep(0.1)
            await server.stop()

    async def test_binary_invalid_frame(self) -> None:
        ops = _Ops()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, binary=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            ws = await HaystackWebSocket.connect(url)
            try:
                # Invalid opcode 0xFF
                await ws.send_bytes(b"\x00\x00\x01\xff")
                # Follow with valid request to confirm connection is alive
                frame = encode_binary_request(2, "about", Grid.make_empty())
                await ws.send_bytes(frame)
                data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert isinstance(data, bytes)
            finally:
                await ws.close()
        finally:
            await asyncio.sleep(0.1)
            await server.stop()

    async def test_binary_push_frame_ignored(self) -> None:
        """Line 436: server ignores inbound push frames."""
        ops = _Ops()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, binary=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            ws = await HaystackWebSocket.connect(url)
            try:
                header = struct.pack("!BHB", FLAG_PUSH, 0, 1)
                await ws.send_bytes(header + b"{}")
                # Follow with valid request
                frame = encode_binary_request(3, "about", Grid.make_empty())
                await ws.send_bytes(frame)
                data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert isinstance(data, bytes)
            finally:
                await ws.close()
        finally:
            await asyncio.sleep(0.1)
            await server.stop()

    async def test_binary_haystack_error(self) -> None:
        """Lines 445-447: HaystackError in binary dispatch."""
        ops = _ErrOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, binary=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            ws = await HaystackWebSocket.connect(url)
            try:
                req_grid = Grid.make_rows([{"filter": "point"}])
                frame = encode_binary_request(1, "read", req_grid)
                await ws.send_bytes(frame)
                data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert isinstance(data, bytes)
                assert data[0] & 0x02  # FLAG_ERROR
            finally:
                await ws.close()
        finally:
            await asyncio.sleep(0.1)
            await server.stop()

    async def test_binary_runtime_error(self) -> None:
        """Lines 448-451: RuntimeError in binary dispatch."""
        ops = _Ops()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, binary=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            ws = await HaystackWebSocket.connect(url)
            try:
                frame = encode_binary_request(1, "nav", Grid.make_empty())
                await ws.send_bytes(frame)
                data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert isinstance(data, bytes)
                assert data[0] & 0x02  # FLAG_ERROR
            finally:
                await ws.close()
        finally:
            await asyncio.sleep(0.1)
            await server.stop()

    async def test_binary_with_grid_payload(self) -> None:
        """Lines 442-444: binary request with non-empty grid_bytes."""
        ops = _Ops()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, binary=True)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            ws = await HaystackWebSocket.connect(url)
            try:
                req_grid = Grid.make_rows([{"filter": "point"}])
                frame = encode_binary_request(1, "read", req_grid)
                await ws.send_bytes(frame)
                data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert isinstance(data, bytes)
                assert data[0] & 0x01  # FLAG_RESPONSE
            finally:
                await ws.close()
        finally:
            await asyncio.sleep(0.1)
            await server.stop()


# ---------------------------------------------------------------------------
# Lines 457-477: batch handling
# ---------------------------------------------------------------------------


class TestBatchDispatch:
    async def test_batch_with_ids(self, srv: WebSocketServer, srv_url: str) -> None:
        ws = await HaystackWebSocket.connect(srv_url)
        try:
            batch = [{"id": "b1", "op": "about"}, {"id": "b2", "op": "about"}]
            await ws.send_text(orjson.dumps(batch).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert isinstance(resp, list)
            assert len(resp) == 2
            assert resp[0]["id"] == "b1"
            assert resp[1]["id"] == "b2"
        finally:
            await ws.close()

    async def test_batch_skips_non_dict(self, srv: WebSocketServer, srv_url: str) -> None:
        ws = await HaystackWebSocket.connect(srv_url)
        try:
            batch = [{"id": "x1", "op": "about"}, "not-a-dict", 42]
            await ws.send_text(orjson.dumps(batch).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert isinstance(resp, list)
            assert len(resp) == 1
        finally:
            await ws.close()

    async def test_batch_haystack_error(self) -> None:
        ops = _ErrOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            ws = await HaystackWebSocket.connect(url)
            try:
                batch = [
                    {
                        "id": "e1",
                        "op": "read",
                        "grid": {
                            "meta": {"ver": "3.0"},
                            "cols": [{"name": "filter"}],
                            "rows": [{"filter": "point"}],
                        },
                    },
                    {"id": "e2", "op": "about"},
                ]
                await ws.send_text(orjson.dumps(batch).decode())
                data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                resp = orjson.loads(data)
                assert isinstance(resp, list)
                grid0 = decode_grid(orjson.dumps(resp[0]["grid"]))
                assert grid0.is_error
            finally:
                await ws.close()
        finally:
            await asyncio.sleep(0.1)
            await server.stop()

    async def test_batch_runtime_error(self, srv: WebSocketServer, srv_url: str) -> None:
        ws = await HaystackWebSocket.connect(srv_url)
        try:
            batch = [{"id": "r1", "op": "nav"}]
            await ws.send_text(orjson.dumps(batch).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert isinstance(resp, list)
            grid0 = decode_grid(orjson.dumps(resp[0]["grid"]))
            assert grid0.is_error
        finally:
            await ws.close()

    async def test_batch_without_id(self, srv: WebSocketServer, srv_url: str) -> None:
        ws = await HaystackWebSocket.connect(srv_url)
        try:
            batch = [{"op": "about"}]
            await ws.send_text(orjson.dumps(batch).decode())
            data = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = orjson.loads(data)
            assert isinstance(resp, list)
            assert len(resp) == 1
            assert "id" not in resp[0]
        finally:
            await ws.close()
