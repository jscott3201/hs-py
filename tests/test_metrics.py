"""Tests for metrics hooks (metrics.py)."""

from __future__ import annotations

import asyncio
from typing import Any

from hs_py.grid import Grid
from hs_py.metrics import MetricsHooks, _fire
from hs_py.ops import HaystackOps
from hs_py.ws_client import WebSocketClient
from hs_py.ws_server import WebSocketServer

# ---------------------------------------------------------------------------
# _fire helper
# ---------------------------------------------------------------------------


class TestFire:
    def test_fire_invokes_callback(self) -> None:
        called: list[tuple[object, ...]] = []
        _fire(lambda *a: called.append(a), "hello", 42)
        assert called == [("hello", 42)]

    def test_fire_none_is_noop(self) -> None:
        _fire(None, "hello")  # Should not raise

    def test_fire_suppresses_callback_exceptions(self) -> None:
        def bad_callback(*_args: object) -> None:
            msg = "oops"
            raise RuntimeError(msg)

        _fire(bad_callback, "hello")  # Should not raise


# ---------------------------------------------------------------------------
# MetricsHooks dataclass
# ---------------------------------------------------------------------------


class TestMetricsHooks:
    def test_default_all_none(self) -> None:
        hooks = MetricsHooks()
        assert hooks.on_ws_connect is None
        assert hooks.on_ws_disconnect is None
        assert hooks.on_ws_message_sent is None
        assert hooks.on_ws_message_recv is None
        assert hooks.on_request is None
        assert hooks.on_error is None

    def test_frozen(self) -> None:
        hooks = MetricsHooks()
        try:
            hooks.on_ws_connect = lambda _: None  # type: ignore[misc]
            raise AssertionError("Should have raised")
        except AttributeError:
            pass

    def test_custom_callbacks(self) -> None:
        connect_calls: list[str] = []
        hooks = MetricsHooks(on_ws_connect=lambda addr: connect_calls.append(addr))
        _fire(hooks.on_ws_connect, "127.0.0.1")
        assert connect_calls == ["127.0.0.1"]


# ---------------------------------------------------------------------------
# Integration with WebSocket transport
# ---------------------------------------------------------------------------


class _MetricsTestOps(HaystackOps):
    async def about(self) -> Grid:
        return Grid.make_rows([{"serverName": "MetricsTest"}])


class TestMetricsIntegration:
    async def test_client_metrics_fired(self) -> None:
        """Metrics hooks fire on connect, send, request, disconnect."""
        events: list[tuple[str, Any]] = []

        def on_connect(addr: str) -> None:
            events.append(("connect", addr))

        def on_disconnect(addr: str) -> None:
            events.append(("disconnect", addr))

        def on_sent(op: str, size: int) -> None:
            events.append(("sent", op))

        def on_request(op: str, dur: float) -> None:
            events.append(("request", op))

        hooks = MetricsHooks(
            on_ws_connect=on_connect,
            on_ws_disconnect=on_disconnect,
            on_ws_message_sent=on_sent,
            on_request=on_request,
        )

        ops = _MetricsTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, metrics=hooks, pythonic=False) as client:
                await client.about()
        finally:
            await server.stop()

        event_types = [e[0] for e in events]
        assert "connect" in event_types
        assert "sent" in event_types
        assert "request" in event_types
        assert "disconnect" in event_types

    async def test_server_metrics_fired(self) -> None:
        """Server-side metrics hooks fire on connect and messages."""
        events: list[tuple[str, Any]] = []

        def on_connect(addr: str) -> None:
            events.append(("connect", addr))

        def on_disconnect(addr: str) -> None:
            events.append(("disconnect", addr))

        def on_recv(op: str, size: int) -> None:
            events.append(("recv", op))

        def on_sent(op: str, size: int) -> None:
            events.append(("sent", op))

        hooks = MetricsHooks(
            on_ws_connect=on_connect,
            on_ws_disconnect=on_disconnect,
            on_ws_message_recv=on_recv,
            on_ws_message_sent=on_sent,
        )

        ops = _MetricsTestOps()
        server = WebSocketServer(ops, host="127.0.0.1", port=0, metrics=hooks)
        await server.start()
        try:
            url = f"ws://127.0.0.1:{server.port}"
            async with WebSocketClient(url, pythonic=False) as client:
                await client.about()
        finally:
            await server.stop()
            await asyncio.sleep(0.05)

        event_types = [e[0] for e in events]
        assert "connect" in event_types
        assert "recv" in event_types
        assert "sent" in event_types
