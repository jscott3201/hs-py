"""Tests for WebSocket transport (ws.py, ws_client.py, ws_server.py)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import orjson
import pytest

from hs_py.encoding.json import decode_grid
from hs_py.errors import AuthError, CallError, HaystackError, NetworkError
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref
from hs_py.ops import HaystackOps
from hs_py.tls import TLSConfig, build_client_ssl_context, generate_test_certificates
from hs_py.ws import HaystackWebSocket
from hs_py.ws_client import WebSocketClient
from hs_py.ws_server import WebSocketServer

# ---------------------------------------------------------------------------
# Test ops implementation (mirrors test_server.py)
# ---------------------------------------------------------------------------


class _TestOps(HaystackOps):
    """Ops subclass for WebSocket testing."""

    async def about(self) -> Grid:
        return Grid.make_rows(
            [
                {
                    "haystackVersion": "4.0",
                    "serverName": "WsTestServer",
                    "productName": "hs-py-ws-test",
                }
            ]
        )

    async def read(self, grid: Grid) -> Grid:
        if grid.rows and "filter" in grid[0]:
            return Grid.make_rows(
                [
                    {"id": Ref("p1"), "dis": "Point 1", "point": MARKER},
                    {"id": Ref("p2"), "dis": "Point 2", "point": MARKER},
                ]
            )
        if grid.rows and "id" in grid[0]:
            rows = [{"id": row["id"], "dis": f"Entity {row['id'].val}"} for row in grid]
            return Grid.make_rows(rows)
        return Grid.make_empty()

    async def nav(self, grid: Grid) -> Grid:
        return Grid.make_rows(
            [
                {"navId": "site-1", "dis": "Site 1"},
                {"navId": "site-2", "dis": "Site 2"},
            ]
        )

    async def his_read(self, grid: Grid) -> Grid:
        meta: dict[str, Any] = {"id": grid[0]["id"], "hisStart": "start", "hisEnd": "end"}
        return (
            GridBuilder()
            .set_meta(meta)
            .add_col("ts")
            .add_col("val")
            .add_row({"ts": "2024-01-01T00:00:00Z", "val": Number(72.0, "\u00b0F")})
            .to_grid()
        )

    async def his_write(self, grid: Grid) -> Grid:
        return Grid.make_empty()

    async def invoke_action(self, grid: Grid) -> Grid:
        action = grid.meta.get("action", "unknown")
        return Grid.make_rows([{"result": f"Invoked {action}"}])

    async def watch_sub(self, grid: Grid) -> Grid:
        return Grid.make_rows([{"watchId": "w-1", "lease": Number(60.0, "s")}])

    async def watch_unsub(self, grid: Grid) -> Grid:
        return Grid.make_empty()

    async def watch_poll(self, grid: Grid) -> Grid:
        return Grid.make_rows([{"id": Ref("p1"), "curVal": Number(72.0, "\u00b0F")}])

    async def point_write(self, grid: Grid) -> Grid:
        return Grid.make_rows([{"level": Number(1.0), "val": Number(72.0)}])


class _ErrorOps(HaystackOps):
    """Ops that raise exceptions for testing error handling."""

    async def about(self) -> Grid:
        return Grid.make_rows([{"serverName": "ErrorServer"}])

    async def read(self, grid: Grid) -> Grid:
        raise HaystackError("Something went wrong")

    async def nav(self, grid: Grid) -> Grid:
        msg = "unexpected failure"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def ws_pair():
    """Start a WebSocketServer and yield (server, url), then stop."""
    ops = _TestOps()
    server = WebSocketServer(ops, host="127.0.0.1", port=0)
    await server.start()
    url = f"ws://127.0.0.1:{server.port}"
    yield server, url
    await server.stop()


@pytest.fixture
async def ws_auth_pair():
    """Start a WebSocketServer with auth and yield (server, url, token)."""
    ops = _TestOps()
    token = "test-secret-token"
    server = WebSocketServer(ops, host="127.0.0.1", port=0, auth_token=token)
    await server.start()
    url = f"ws://127.0.0.1:{server.port}"
    yield server, url, token
    await server.stop()


@pytest.fixture
async def error_ws_pair():
    """Start a WebSocketServer with error-raising ops."""
    ops = _ErrorOps()
    server = WebSocketServer(ops, host="127.0.0.1", port=0)
    await server.start()
    url = f"ws://127.0.0.1:{server.port}"
    yield server, url
    await server.stop()


# ---------------------------------------------------------------------------
# HaystackWebSocket (sans-I/O layer) tests
# ---------------------------------------------------------------------------


class TestHaystackWebSocket:
    async def test_connect_and_close(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        ws = await HaystackWebSocket.connect(url)
        assert ws.is_open
        await ws.close()

    async def test_send_recv_text(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        ws = await HaystackWebSocket.connect(url)
        try:
            # Send a valid op request
            msg = orjson.dumps({"id": "1", "op": "about"}).decode()
            await ws.send_text(msg)
            data = await ws.recv()
            response = orjson.loads(data)
            assert "grid" in response
            assert response["id"] == "1"
        finally:
            await ws.close()

    async def test_subprotocol(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        ws = await HaystackWebSocket.connect(url)
        try:
            # The server accepts "haystack" subprotocol
            assert ws.subprotocol == "haystack"
        finally:
            await ws.close()


# ---------------------------------------------------------------------------
# WebSocketClient standard ops
# ---------------------------------------------------------------------------


class TestWebSocketClientOps:
    async def test_about(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.about()
            assert grid[0]["serverName"] == "WsTestServer"

    async def test_ops(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.ops()
            names = [row["name"] for row in grid]
            assert "about" in names
            assert "ops" in names
            assert "read" in names

    async def test_formats(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.formats()
            assert grid[0]["mime"] == "application/json"

    async def test_read_filter(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.read("point")
            assert len(grid) == 2
            assert grid[0]["id"] == Ref("p1")
            assert grid[1]["id"] == Ref("p2")

    async def test_read_by_ids(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.read_by_ids([Ref("a"), Ref("b")])
            assert len(grid) == 2
            assert grid[0]["id"] == Ref("a")

    async def test_nav(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.nav()
            assert len(grid) == 2
            assert grid[0]["navId"] == "site-1"

    async def test_his_read(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.his_read(Ref("p1"), "today")
            assert len(grid) == 1
            assert grid.meta["id"] == Ref("p1")

    async def test_his_write(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            await client.his_write(
                Ref("p1"),
                [{"ts": "2024-01-01T00:00:00Z", "val": Number(72.0)}],
            )

    async def test_invoke_action(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.invoke_action(Ref("p1"), "toggle")
            assert grid[0]["result"] == "Invoked toggle"

    async def test_watch_sub(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.watch_sub([Ref("p1")], "test-watch")
            assert grid[0]["watchId"] == "w-1"

    async def test_watch_unsub(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            await client.watch_unsub("w-1", [Ref("p1")])

    async def test_watch_close(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            await client.watch_close("w-1")

    async def test_watch_poll(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.watch_poll("w-1")
            assert len(grid) == 1
            assert grid[0]["id"] == Ref("p1")

    async def test_point_write_array(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            grid = await client.point_write_array(Ref("p1"))
            assert len(grid) == 1

    async def test_point_write(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            await client.point_write(Ref("p1"), 8, Number(72.0), who="test")


# ---------------------------------------------------------------------------
# Concurrent requests
# ---------------------------------------------------------------------------


class TestConcurrentRequests:
    async def test_parallel_ops(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        """Multiple concurrent requests should all get correct responses."""
        _, url = ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            results = await asyncio.gather(
                client.about(),
                client.read("point"),
                client.formats(),
                client.ops(),
            )
            about, read, formats, ops = results
            assert about[0]["serverName"] == "WsTestServer"
            assert len(read) == 2
            assert formats[0]["mime"] == "application/json"
            names = [row["name"] for row in ops]
            assert "about" in names


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestWebSocketAuth:
    async def test_auth_success(self, ws_auth_pair: tuple[WebSocketServer, str, str]) -> None:
        _, url, token = ws_auth_pair
        async with WebSocketClient(url, auth_token=token, pythonic=False) as client:
            grid = await client.about()
            assert grid[0]["serverName"] == "WsTestServer"

    async def test_auth_failure(self, ws_auth_pair: tuple[WebSocketServer, str, str]) -> None:
        _, url, _ = ws_auth_pair
        async with WebSocketClient(url, auth_token="wrong-token", pythonic=False) as client:
            with pytest.raises((NetworkError, ConnectionError)):
                await client.about()

    async def test_no_auth_when_required(
        self, ws_auth_pair: tuple[WebSocketServer, str, str]
    ) -> None:
        _, url, _ = ws_auth_pair
        # Connect without auth token -- server expects auth message first
        async with WebSocketClient(url, pythonic=False) as client:
            with pytest.raises((NetworkError, ConnectionError)):
                await client.about()


# ---------------------------------------------------------------------------
# SCRAM-SHA-256 WebSocket auth
# ---------------------------------------------------------------------------


@pytest.fixture
async def ws_scram_pair():
    """Start a WebSocketServer with SCRAM auth and yield (server, url)."""
    from hs_py.auth_types import SimpleAuthenticator

    ops = _TestOps()
    authenticator = SimpleAuthenticator({"admin": "secret"}, iterations=4096)
    server = WebSocketServer(ops, host="127.0.0.1", port=0, authenticator=authenticator)
    await server.start()
    url = f"ws://127.0.0.1:{server.port}"
    yield server, url
    await server.stop()


class TestWebSocketScramAuth:
    async def test_scram_success(self, ws_scram_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_scram_pair
        async with WebSocketClient(
            url, username="admin", password="secret", pythonic=False
        ) as client:
            grid = await client.about()
            assert grid[0]["serverName"] == "WsTestServer"

    async def test_scram_wrong_password(self, ws_scram_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_scram_pair
        with pytest.raises(AuthError):
            async with WebSocketClient(
                url, username="admin", password="wrong", pythonic=False
            ) as client:
                await client.about()

    async def test_scram_unknown_user(self, ws_scram_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_scram_pair
        with pytest.raises(AuthError):
            async with WebSocketClient(
                url, username="nobody", password="secret", pythonic=False
            ) as client:
                await client.about()

    async def test_scram_multiple_ops(self, ws_scram_pair: tuple[WebSocketServer, str]) -> None:
        """After SCRAM auth, multiple ops should work normally."""
        _, url = ws_scram_pair
        async with WebSocketClient(
            url, username="admin", password="secret", pythonic=False
        ) as client:
            about = await client.about()
            assert about[0]["serverName"] == "WsTestServer"
            points = await client.read("point")
            assert len(points) == 2
            nav = await client.nav()
            assert len(nav) == 2

    async def test_scram_with_token_fallback(self) -> None:
        """Server with both SCRAM authenticator and auth_token accepts token."""
        from hs_py.auth_types import SimpleAuthenticator

        ops = _TestOps()
        authenticator = SimpleAuthenticator({"admin": "secret"}, iterations=4096)
        token = "fallback-token"
        server = WebSocketServer(
            ops, host="127.0.0.1", port=0, authenticator=authenticator, auth_token=token
        )
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, auth_token=token, pythonic=False) as client:
                grid = await client.about()
                assert grid[0]["serverName"] == "WsTestServer"
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestWebSocketErrors:
    async def test_haystack_error_returns_error_grid(
        self, error_ws_pair: tuple[WebSocketServer, str]
    ) -> None:
        _, url = error_ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            with pytest.raises(CallError) as exc_info:
                await client.read("point")
            assert "Something went wrong" in str(exc_info.value)

    async def test_runtime_error_returns_error_grid(
        self, error_ws_pair: tuple[WebSocketServer, str]
    ) -> None:
        _, url = error_ws_pair
        async with WebSocketClient(url, pythonic=False) as client:
            with pytest.raises(CallError) as exc_info:
                await client.nav()
            assert "Internal server error" in str(exc_info.value)

    async def test_unknown_op(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        ws = await HaystackWebSocket.connect(url)
        try:
            msg = orjson.dumps({"id": "99", "op": "bogus"}).decode()
            await ws.send_text(msg)
            data = await ws.recv()
            response = orjson.loads(data)
            grid_data = orjson.dumps(response["grid"])
            grid = decode_grid(grid_data)
            assert grid.is_error
        finally:
            await ws.close()

    async def test_client_not_open_raises(self) -> None:
        client = WebSocketClient("ws://127.0.0.1:9999", pythonic=False)
        with pytest.raises(RuntimeError, match="not open"):
            await client.about()


# ---------------------------------------------------------------------------
# Watch push
# ---------------------------------------------------------------------------


class TestWatchPush:
    async def test_push_watch(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        server, url = ws_pair
        received: list[tuple[str, Grid]] = []

        def on_push(watch_id: str, grid: Grid) -> None:
            received.append((watch_id, grid))

        async with WebSocketClient(url, pythonic=False) as client:
            client.on_watch_push(on_push)
            # Give the recv loop time to start
            await asyncio.sleep(0.05)

            # Push from server
            push_grid = Grid.make_rows([{"id": Ref("p1"), "curVal": Number(73.0)}])
            await server.push_watch("w-1", push_grid)

            # Wait for push to arrive
            for _ in range(20):
                await asyncio.sleep(0.05)
                if received:
                    break

            assert len(received) == 1
            watch_id, grid = received[0]
            assert watch_id == "w-1"
            assert len(grid) == 1
            assert grid[0]["id"] == Ref("p1")


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


class TestWebSocketServerLifecycle:
    async def test_start_stop(self) -> None:
        ops = _TestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        assert server.port != 0
        await server.stop()

    async def test_stop_idempotent(self) -> None:
        ops = _TestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        await server.stop()
        await server.stop()  # Should not raise

    async def test_port_property_before_start(self) -> None:
        ops = _TestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=8765)
        assert server.port == 8765

    async def test_multiple_connections(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        _, url = ws_pair
        async with (
            WebSocketClient(url, pythonic=False) as c1,
            WebSocketClient(url, pythonic=False) as c2,
        ):
            g1 = await c1.about()
            g2 = await c2.about()
            assert g1[0]["serverName"] == "WsTestServer"
            assert g2[0]["serverName"] == "WsTestServer"


# ---------------------------------------------------------------------------
# TLS over WebSocket
# ---------------------------------------------------------------------------


class TestWebSocketTLS:
    async def test_tls_13_websocket(self) -> None:
        """WebSocket client + server communicate over TLS 1.3."""
        with tempfile.TemporaryDirectory() as d:
            server_config = generate_test_certificates(d)
            client_config = TLSConfig(
                certificate_path=str(Path(d) / "client.pem"),
                private_key_path=str(Path(d) / "client.key"),
                ca_certificates_path=str(Path(d) / "ca.pem"),
            )

            ops = _TestOps()
            server = WebSocketServer(ops, host="127.0.0.1", port=0, tls=server_config)
            await server.start()
            try:
                url = f"wss://localhost:{server.port}"
                ssl_ctx = build_client_ssl_context(client_config)
                ws = await HaystackWebSocket.connect(url, ssl_ctx)
                try:
                    msg = orjson.dumps({"id": "1", "op": "about"}).decode()
                    await ws.send_text(msg)
                    data = await ws.recv()
                    response = orjson.loads(data)
                    grid_data = orjson.dumps(response["grid"])
                    grid = decode_grid(grid_data)
                    assert grid[0]["serverName"] == "WsTestServer"
                finally:
                    await ws.close()
            finally:
                await server.stop()

    async def test_non_tls_client_rejected_by_tls_server(self) -> None:
        """A plain ws:// client should fail to connect to a wss:// server."""
        with tempfile.TemporaryDirectory() as d:
            server_config = generate_test_certificates(d)
            ops = _TestOps()
            server = WebSocketServer(ops, host="127.0.0.1", port=0, tls=server_config)
            await server.start()
            try:
                url = f"ws://127.0.0.1:{server.port}"
                with pytest.raises((ConnectionError, OSError)):
                    ws = await HaystackWebSocket.connect(url)
                    await ws.recv()
            finally:
                await server.stop()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    async def test_client_heartbeat_sends_pings(self) -> None:
        """Client with short heartbeat interval should send pings without error."""
        ops = _TestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, heartbeat=0)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, heartbeat=0.1, pythonic=False) as client:
                # Let a few heartbeat pings fire
                await asyncio.sleep(0.35)
                # Connection should still work
                grid = await client.about()
                assert grid[0]["serverName"] == "WsTestServer"
        finally:
            await server.stop()

    async def test_server_heartbeat_sends_pings(self) -> None:
        """Server with short heartbeat should keep connection alive."""
        ops = _TestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, heartbeat=0.1)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, heartbeat=0, pythonic=False) as client:
                await asyncio.sleep(0.35)
                grid = await client.about()
                assert grid[0]["serverName"] == "WsTestServer"
        finally:
            await server.stop()

    async def test_heartbeat_disabled(self) -> None:
        """Heartbeat=0 should disable pings."""
        ops = _TestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, heartbeat=0)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, heartbeat=0, pythonic=False) as client:
                assert client._heartbeat_task is None
                grid = await client.about()
                assert grid[0]["serverName"] == "WsTestServer"
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Push via HaystackOps.push_watch
# ---------------------------------------------------------------------------


class TestOpsPushWatch:
    async def test_ops_push_watch(self, ws_pair: tuple[WebSocketServer, str]) -> None:
        """HaystackOps.push_watch should deliver via the wired server handler."""
        server, url = ws_pair
        received: list[tuple[str, Grid]] = []

        def on_push(watch_id: str, grid: Grid) -> None:
            received.append((watch_id, grid))

        async with WebSocketClient(url, pythonic=False) as client:
            client.on_watch_push(on_push)
            await asyncio.sleep(0.05)

            # Push through the ops instance (not server directly)
            push_grid = Grid.make_rows([{"id": Ref("p1"), "curVal": Number(74.0)}])
            await server._ops.push_watch("w-2", push_grid)

            for _ in range(20):
                await asyncio.sleep(0.05)
                if received:
                    break

            assert len(received) == 1
            assert received[0][0] == "w-2"

    async def test_push_watch_without_handler(self) -> None:
        """push_watch should be a no-op if no handler is wired."""
        ops = _TestOps()
        # No set_push_handler called -- should not raise
        await ops.push_watch("w-1", Grid.make_empty())


# ---------------------------------------------------------------------------
# FastAPI WebSocket endpoint
# ---------------------------------------------------------------------------


class TestFastapiWebSocket:
    """Test the /ws endpoint mounted on the FastAPI app."""

    def _make_app(self) -> Any:
        from hs_py.fastapi_server import create_fastapi_app

        return create_fastapi_app(ops=_TestOps())

    async def test_ws_about(self) -> None:
        from starlette.testclient import TestClient

        with (
            TestClient(self._make_app()) as tc,
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            ws.send_text(orjson.dumps({"id": "1", "op": "about"}).decode())
            data = orjson.loads(ws.receive_text())
            grid_data = orjson.dumps(data["grid"])
            grid = decode_grid(grid_data)
            assert grid[0]["serverName"] == "WsTestServer"
            assert data["id"] == "1"

    async def test_ws_read(self) -> None:
        from starlette.testclient import TestClient

        from hs_py.encoding.json import encode_grid as enc_grid

        req_grid = GridBuilder().add_col("filter").add_row({"filter": "point"}).to_grid()
        grid_json = orjson.loads(enc_grid(req_grid))

        with (
            TestClient(self._make_app()) as tc,
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            ws.send_text(orjson.dumps({"id": "2", "op": "read", "grid": grid_json}).decode())
            data = orjson.loads(ws.receive_text())
            grid = decode_grid(orjson.dumps(data["grid"]))
            assert len(grid) == 2
            assert grid[0]["id"] == Ref("p1")

    async def test_ws_unknown_op(self) -> None:
        from starlette.testclient import TestClient

        with (
            TestClient(self._make_app()) as tc,
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            ws.send_text(orjson.dumps({"id": "3", "op": "bogus"}).decode())
            data = orjson.loads(ws.receive_text())
            grid = decode_grid(orjson.dumps(data["grid"]))
            assert grid.is_error

    async def test_ws_ops(self) -> None:
        from starlette.testclient import TestClient

        with (
            TestClient(self._make_app()) as tc,
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            ws.send_text(orjson.dumps({"id": "4", "op": "ops"}).decode())
            data = orjson.loads(ws.receive_text())
            grid = decode_grid(orjson.dumps(data["grid"]))
            names = [row["name"] for row in grid]
            assert "about" in names
            assert "read" in names
