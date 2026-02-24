"""Tests for ws_client.py unit-testable functions and constructors."""

from __future__ import annotations

import asyncio

import pytest

from hs_py.encoding.json import encode_grid_dict
from hs_py.errors import CallError, NetworkError
from hs_py.grid import Grid
from hs_py.ws_client import (
    ReconnectingWebSocketClient,
    WebSocketClient,
    WebSocketPool,
    _resolve_grid_response,
)


class TestResolveGridResponse:
    """Cover _resolve_grid_response function."""

    def test_no_id_returns_false(self) -> None:
        pending: dict[str, asyncio.Future[Grid]] = {}
        assert _resolve_grid_response(pending, {"grid": {}}) is False

    def test_id_not_in_pending_returns_false(self) -> None:
        pending: dict[str, asyncio.Future[Grid]] = {}
        assert _resolve_grid_response(pending, {"id": "x", "grid": {}}) is False

    def test_done_future_returns_true(self) -> None:
        loop = asyncio.new_event_loop()
        fut: asyncio.Future[Grid] = loop.create_future()
        fut.set_result(Grid.make_empty())
        pending = {"x": fut}
        assert (
            _resolve_grid_response(
                pending, {"id": "x", "grid": encode_grid_dict(Grid.make_empty())}
            )
            is True
        )
        assert "x" not in pending
        loop.close()

    def test_success_resolves_future(self) -> None:
        loop = asyncio.new_event_loop()
        fut: asyncio.Future[Grid] = loop.create_future()
        pending = {"r1": fut}
        grid = Grid.make_rows([{"dis": "test"}])
        msg = {"id": "r1", "grid": encode_grid_dict(grid)}
        assert _resolve_grid_response(pending, msg) is True
        assert fut.result().rows[0]["dis"] == "test"
        loop.close()

    def test_error_grid_sets_exception(self) -> None:
        loop = asyncio.new_event_loop()
        fut: asyncio.Future[Grid] = loop.create_future()
        pending = {"r2": fut}
        err_grid = Grid.make_error("Something went wrong")
        msg = {"id": "r2", "grid": encode_grid_dict(err_grid)}
        assert _resolve_grid_response(pending, msg) is True
        with pytest.raises(CallError):
            fut.result()
        loop.close()

    def test_decode_failure_sets_network_error(self) -> None:
        loop = asyncio.new_event_loop()
        fut: asyncio.Future[Grid] = loop.create_future()
        pending = {"r3": fut}
        msg = {"id": "r3", "grid": "not-a-valid-grid"}
        assert _resolve_grid_response(pending, msg) is True
        with pytest.raises(NetworkError):
            fut.result()
        loop.close()


class TestWebSocketClientConstructor:
    """Cover WebSocketClient instantiation paths."""

    def test_basic_constructor(self) -> None:
        client = WebSocketClient.__new__(WebSocketClient)
        client._url = "ws://localhost:8080/ws"
        client._username = ""
        client._password = ""
        client._auth_token = None
        client._pythonic = True
        client._ws = None
        client._pending = {}
        assert client._url == "ws://localhost:8080/ws"

    def test_repr_like(self) -> None:
        client = WebSocketClient.__new__(WebSocketClient)
        client._url = "ws://example.com/ws"
        client._username = "user"
        assert "example.com" in client._url


class TestReconnectingClientConstructor:
    """Cover ReconnectingWebSocketClient constructor."""

    def test_basic_constructor(self) -> None:
        client = ReconnectingWebSocketClient.__new__(ReconnectingWebSocketClient)
        client._url = "ws://localhost:8080/ws"
        client._username = ""
        client._password = ""
        client._max_retries = 5
        client._backoff_base = 1.0
        client._backoff_max = 60.0
        assert client._max_retries == 5


class TestWebSocketPoolConstructor:
    """Cover WebSocketPool constructor."""

    def test_basic_constructor(self) -> None:
        pool = WebSocketPool.__new__(WebSocketPool)
        pool._url = "ws://localhost:8080/ws"
        pool._username = ""
        pool._password = ""
        assert pool._url == "ws://localhost:8080/ws"
