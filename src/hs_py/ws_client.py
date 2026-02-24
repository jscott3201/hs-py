"""Async Haystack WebSocket client.

Provides the same operation API as :class:`~hs_py.client.Client` over a
persistent WebSocket connection.  Uses JSON-encoded request/response
envelopes with correlation IDs for concurrent request support.

Message format (client → server)::

    {"id": "1", "op": "read", "grid": {...}}

Message format (server → client)::

    {"id": "1", "grid": {...}}

Server-initiated push::

    {"type": "watch", "watchId": "w-1", "grid": {...}}
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, Literal, overload

import orjson

from hs_py.convert import grid_to_pythonic
from hs_py.encoding.json import decode_grid, decode_grid_dict, encode_grid_dict
from hs_py.errors import AuthError, CallError, NetworkError
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref
from hs_py.metrics import MetricsHooks, _fire
from hs_py.tls import TLSConfig, build_client_ssl_context
from hs_py.ws import HaystackWebSocket, cancel_task, heartbeat_loop
from hs_py.ws_codec import (
    FLAG_ERROR,
    FLAG_PUSH,
    FLAG_RESPONSE,
    decode_binary_frame,
    encode_binary_request,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = [
    "ChannelClient",
    "ReconnectingWebSocketClient",
    "WebSocketClient",
    "WebSocketPool",
]

_log = logging.getLogger(__name__)


def _resolve_grid_response(pending: dict[str, asyncio.Future[Grid]], msg: dict[str, Any]) -> bool:
    """Resolve a pending future from a JSON response envelope. Return True if matched."""
    req_id = msg.get("id")
    if req_id is None or req_id not in pending:
        return False
    fut = pending.pop(req_id)
    if fut.done():
        return True
    try:
        grid = decode_grid_dict(msg["grid"])
        if grid.is_error:
            fut.set_exception(CallError(grid.meta.get("dis", "Unknown error"), grid))
        else:
            fut.set_result(grid)
    except Exception as exc:
        fut.set_exception(NetworkError(f"Failed to decode response: {exc}"))
    return True


class WebSocketClient:
    """Async Haystack WebSocket client.

    Mirrors the :class:`~hs_py.client.Client` API over a persistent
    WebSocket connection.

    Usage::

        async with WebSocketClient("ws://host:8080/api/ws") as c:
            about = await c.about()  # returns list[dict] by default
            points = await c.read("point and sensor")
            raw_grid = await c.about(raw=True)  # returns Grid
    """

    def __init__(
        self,
        url: str,
        *,
        username: str = "",
        password: str = "",
        auth_token: str = "",
        tls: TLSConfig | None = None,
        timeout: float = 30.0,
        heartbeat: float = 30.0,
        metrics: MetricsHooks | None = None,
        compression: bool = False,
        binary: bool = False,
        pythonic: bool = True,
    ) -> None:
        """Initialise the WebSocket client.

        :param url: WebSocket URI (e.g. ``ws://host:8080/api/ws``).
        :param username: Username for SCRAM-SHA-256 authentication.
        :param password: Password for SCRAM-SHA-256 authentication.
        :param auth_token: Bearer token sent on connect (used when *username*
            is not provided).
        :param tls: Optional :class:`~hs_py.tls.TLSConfig` for ``wss://``.
        :param timeout: Per-request timeout in seconds.
        :param heartbeat: Ping interval in seconds (0 to disable).
        :param metrics: Optional :class:`~hs_py.metrics.MetricsHooks` callbacks.
        :param compression: Enable per-message deflate compression.
        :param binary: Use binary frame encoding instead of JSON envelopes.
        :param pythonic: When ``True`` (default) Grid-returning methods return
            ``list[dict[str, Any]]`` with Haystack kinds converted to plain Python
            values.  Pass ``False`` to always return raw :class:`~hs_py.grid.Grid`.
        """
        self._url = url
        self._username = username
        self._password = password
        self._auth_token = auth_token
        self._tls = tls
        self._timeout = timeout
        self._heartbeat = heartbeat
        self._metrics = metrics or MetricsHooks()
        self._compression = compression
        self._binary = binary
        self._pythonic = pythonic
        self._ws: HaystackWebSocket | None = None
        self._next_id = 0
        self._pending: dict[str, asyncio.Future[Grid]] = {}
        self._recv_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._watch_callback: Callable[[str, Grid], Any] | None = None

    async def __aenter__(self) -> WebSocketClient:
        ssl_ctx = build_client_ssl_context(self._tls) if self._tls else None
        self._ws = await HaystackWebSocket.connect(
            self._url, ssl_ctx, compression=self._compression
        )
        _fire(self._metrics.on_ws_connect, self._url)
        # Authenticate before starting recv loop
        if self._username:
            await self._scram_authenticate()
        elif self._auth_token:
            await self._ws.send_text(orjson.dumps({"authToken": self._auth_token}).decode())
        self._recv_task = asyncio.create_task(self._recv_loop(), name="hs-ws-recv")
        if self._heartbeat > 0:
            self._heartbeat_task = asyncio.create_task(
                heartbeat_loop(self._ws, self._heartbeat), name="hs-ws-heartbeat"
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the WebSocket connection."""
        await cancel_task(self._heartbeat_task)
        self._heartbeat_task = None
        await cancel_task(self._recv_task)
        self._recv_task = None
        if self._ws is not None:
            await self._ws.close()
            _fire(self._metrics.on_ws_disconnect, self._url)
            self._ws = None
        # Cancel any pending requests
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def _scram_authenticate(self) -> None:
        """Perform SCRAM-SHA-256 handshake over WebSocket messages."""
        from hs_py.auth import (
            _b64url_decode,
            _b64url_encode,
            scram_client_final,
            scram_client_first,
            verify_server_signature,
        )

        assert self._ws is not None

        # Step 1: HELLO
        first = scram_client_first(self._username)
        await self._ws.send_text(
            orjson.dumps(
                {"type": "hello", "username": _b64url_encode(self._username.encode())}
            ).decode()
        )
        data = await self._ws.recv()
        msg = orjson.loads(data)
        if msg.get("type") == "authErr":
            raise AuthError("Server rejected HELLO")
        if msg.get("type") != "hello":
            raise AuthError(f"Expected hello response, got: {msg.get('type')}")

        handshake_token = msg.get("handshakeToken", "")
        hash_name = msg.get("hash", "SHA-256")

        # Step 2: client-first → server-first
        await self._ws.send_text(
            orjson.dumps(
                {
                    "type": "scram",
                    "handshakeToken": handshake_token,
                    "data": _b64url_encode(first.client_first_msg.encode()),
                }
            ).decode()
        )
        data = await self._ws.recv()
        msg = orjson.loads(data)
        if msg.get("type") == "authErr":
            raise AuthError("SCRAM step 1 failed")
        if msg.get("type") != "scram":
            raise AuthError(f"Expected scram response, got: {msg.get('type')}")

        server_first_data = msg.get("data", "")
        server_first_msg = _b64url_decode(server_first_data).decode()
        new_ht = msg.get("handshakeToken", "")

        final = scram_client_final(self._password, first, server_first_msg, hash_name)

        # Step 3: client-final → authOk
        await self._ws.send_text(
            orjson.dumps(
                {
                    "type": "scram",
                    "handshakeToken": new_ht,
                    "data": _b64url_encode(final.client_final_msg.encode()),
                }
            ).decode()
        )
        data = await self._ws.recv()
        msg = orjson.loads(data)
        if msg.get("type") == "authErr":
            raise AuthError("SCRAM authentication failed")
        if msg.get("type") != "authOk":
            raise AuthError(f"Expected authOk, got: {msg.get('type')}")

        # Verify server signature
        server_data = msg.get("data", "")
        if server_data:
            verify_server_signature(final, _b64url_decode(server_data).decode())

        _log.debug("WebSocket SCRAM auth succeeded")

    # ---- Standard ops (mirrors Client) -------------------------------------

    @overload
    async def about(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def about(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def about(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def about(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query server information."""
        grid = await self._call("about", Grid.make_empty())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def ops(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def ops(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def ops(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def ops(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query available operations."""
        grid = await self._call("ops", Grid.make_empty())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def formats(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def formats(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def formats(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def formats(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query supported data formats."""
        grid = await self._call("formats", Grid.make_empty())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def read(self, filter: str, limit: int | None = ..., *, raw: Literal[True]) -> Grid: ...
    @overload
    async def read(
        self, filter: str, limit: int | None = ..., *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def read(
        self, filter: str, limit: int | None = ..., *, raw: bool
    ) -> Grid | list[dict[str, Any]]: ...
    async def read(
        self, filter: str, limit: int | None = None, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read entities matching a filter expression."""
        row: dict[str, Any] = {"filter": filter}
        if limit is not None:
            row["limit"] = Number(float(limit))
        grid_req = GridBuilder().add_col("filter").add_col("limit").add_row(row).to_grid()
        grid = await self._call("read", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def read_by_ids(self, ids: list[Ref], *, raw: Literal[True]) -> Grid: ...
    @overload
    async def read_by_ids(
        self, ids: list[Ref], *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def read_by_ids(self, ids: list[Ref], *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def read_by_ids(
        self, ids: list[Ref], *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read entities by their identifiers."""
        builder = GridBuilder().add_col("id")
        for ref in ids:
            builder.add_row({"id": ref})
        grid = await self._call("read", builder.to_grid())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def nav(self, nav_id: str | None = ..., *, raw: Literal[True]) -> Grid: ...
    @overload
    async def nav(
        self, nav_id: str | None = ..., *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def nav(self, nav_id: str | None = ..., *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def nav(
        self, nav_id: str | None = None, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Navigate the entity tree."""
        row: dict[str, Any] = {"navId": nav_id}
        grid_req = GridBuilder().add_col("navId").add_row(row).to_grid()
        grid = await self._call("nav", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    # ---- History ops -------------------------------------------------------

    @overload
    async def his_read(self, id: Ref, range: str, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def his_read(
        self, id: Ref, range: str, *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def his_read(self, id: Ref, range: str, *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def his_read(
        self, id: Ref, range: str, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read time-series data for a single point."""
        grid_req = (
            GridBuilder()
            .add_col("id")
            .add_col("range")
            .add_row({"id": id, "range": range})
            .to_grid()
        )
        grid = await self._call("hisRead", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def his_read_batch(self, ids: list[Ref], range: str, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def his_read_batch(
        self, ids: list[Ref], range: str, *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def his_read_batch(
        self, ids: list[Ref], range: str, *, raw: bool
    ) -> Grid | list[dict[str, Any]]: ...
    async def his_read_batch(
        self, ids: list[Ref], range: str, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read time-series data for multiple points."""
        builder = GridBuilder().set_meta({"range": range}).add_col("id")
        for ref in ids:
            builder.add_row({"id": ref})
        grid = await self._call("hisRead", builder.to_grid())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    async def his_write(self, id: Ref, items: list[dict[str, Any]]) -> None:
        """Write time-series data to a single point."""
        builder = GridBuilder().set_meta({"id": id}).add_col("ts").add_col("val")
        for item in items:
            builder.add_row(item)
        await self._call("hisWrite", builder.to_grid())

    async def his_write_batch(self, grid: Grid) -> None:
        """Write time-series data for multiple points."""
        await self._call("hisWrite", grid)

    # ---- Point write ops ---------------------------------------------------

    @overload
    async def point_write_array(self, id: Ref, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def point_write_array(self, id: Ref, *, raw: Literal[False]) -> list[dict[str, Any]]: ...
    @overload
    async def point_write_array(self, id: Ref, *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def point_write_array(
        self, id: Ref, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read the priority array of a writable point."""
        grid_req = GridBuilder().add_col("id").add_row({"id": id}).to_grid()
        grid = await self._call("pointWrite", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    async def point_write(
        self,
        id: Ref,
        level: int,
        val: Any,
        who: str = "",
        duration: Number | None = None,
    ) -> None:
        """Write to a priority array level."""
        row: dict[str, Any] = {
            "id": id,
            "level": Number(float(level)),
            "val": val,
            "who": who,
        }
        if duration is not None:
            row["duration"] = duration
        cols = ["id", "level", "val", "who", "duration"]
        builder = GridBuilder()
        for col in cols:
            builder.add_col(col)
        builder.add_row(row)
        await self._call("pointWrite", builder.to_grid())

    # ---- Watch ops ---------------------------------------------------------

    @overload
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = ...,
        *,
        filter: str | None = ...,
        raw: Literal[True],
    ) -> Grid: ...
    @overload
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = ...,
        *,
        filter: str | None = ...,
        raw: Literal[False],
    ) -> list[dict[str, Any]]: ...
    @overload
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = ...,
        *,
        filter: str | None = ...,
        raw: bool,
    ) -> Grid | list[dict[str, Any]]: ...
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = None,
        *,
        filter: str | None = None,
        raw: bool = False,
    ) -> Grid | list[dict[str, Any]]:
        """Create a new watch or add entities to an existing one.

        :param filter: Optional Haystack filter for server-side filtering.
        :param raw: If ``True``, return the raw :class:`~hs_py.grid.Grid`.
        """
        meta: dict[str, Any] = {"watchDis": watch_dis}
        if lease is not None:
            meta["lease"] = lease
        if filter is not None:
            meta["filter"] = filter
        builder = GridBuilder().set_meta(meta).add_col("id")
        for ref in ids:
            builder.add_row({"id": ref})
        grid = await self._call("watchSub", builder.to_grid())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    async def watch_unsub(self, watch_id: str, ids: list[Ref]) -> None:
        """Remove entities from a watch."""
        builder = GridBuilder().set_meta({"watchId": watch_id}).add_col("id")
        for ref in ids:
            builder.add_row({"id": ref})
        await self._call("watchUnsub", builder.to_grid())

    async def watch_close(self, watch_id: str) -> None:
        """Close a watch entirely."""
        builder = GridBuilder().set_meta({"watchId": watch_id, "close": MARKER}).add_col("id")
        await self._call("watchUnsub", builder.to_grid())

    @overload
    async def watch_poll(
        self, watch_id: str, refresh: bool = ..., *, raw: Literal[True]
    ) -> Grid: ...
    @overload
    async def watch_poll(
        self, watch_id: str, refresh: bool = ..., *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def watch_poll(
        self, watch_id: str, refresh: bool = ..., *, raw: bool
    ) -> Grid | list[dict[str, Any]]: ...
    async def watch_poll(
        self, watch_id: str, refresh: bool = False, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Poll a watch for changes."""
        meta: dict[str, Any] = {"watchId": watch_id}
        if refresh:
            meta["refresh"] = MARKER
        grid_req = GridBuilder().set_meta(meta).to_grid()
        grid = await self._call("watchPoll", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    # ---- Action ops --------------------------------------------------------

    @overload
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = ...,
        *,
        raw: Literal[True],
    ) -> Grid: ...
    @overload
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = ...,
        *,
        raw: Literal[False],
    ) -> list[dict[str, Any]]: ...
    @overload
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = ...,
        *,
        raw: bool,
    ) -> Grid | list[dict[str, Any]]: ...
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = None,
        *,
        raw: bool = False,
    ) -> Grid | list[dict[str, Any]]:
        """Invoke an action on an entity."""
        meta: dict[str, Any] = {"id": id, "action": action}
        builder = GridBuilder().set_meta(meta)
        if args:
            for key in args:
                builder.add_col(key)
            builder.add_row(args)
        grid = await self._call("invokeAction", builder.to_grid())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    # ---- Batch ops ---------------------------------------------------------

    async def batch(self, *calls: tuple[str, Grid]) -> list[Grid]:
        """Send multiple operations in a single WebSocket frame.

        :param calls: Tuples of ``(op_name, grid)``.
        :returns: List of response grids in the same order as *calls*.
        :raises NetworkError: If any call times out.
        """
        ws = self._require_ws()
        loop = asyncio.get_running_loop()
        futs: list[tuple[str, asyncio.Future[Grid]]] = []
        envelopes: list[dict[str, Any]] = []

        for op, grid in calls:
            req_id = str(self._next_id)
            self._next_id = (self._next_id + 1) & 0xFFFF
            envelopes.append({"id": req_id, "op": op, "grid": encode_grid_dict(grid)})
            fut: asyncio.Future[Grid] = loop.create_future()
            self._pending[req_id] = fut
            futs.append((req_id, fut))

        msg = orjson.dumps(envelopes).decode()
        await ws.send_text(msg)
        _fire(self._metrics.on_ws_message_sent, "batch", len(msg))

        results: list[Grid] = []
        try:
            for _req_id, fut in futs:
                async with asyncio.timeout(self._timeout):
                    result = await fut
                results.append(result)
        except TimeoutError as exc:
            # Cancel remaining futures
            for req_id, fut in futs:
                self._pending.pop(req_id, None)
                if not fut.done():
                    fut.cancel()
            raise NetworkError("Batch request timed out") from exc
        return results

    # ---- Watch push --------------------------------------------------------

    def on_watch_push(self, callback: Callable[[str, Grid], Any]) -> None:
        """Register a callback for server-initiated watch push messages.

        :param callback: Called with ``(watch_id, grid)`` for each push.
        """
        self._watch_callback = callback

    # ---- Internal ----------------------------------------------------------

    async def _call(self, op: str, grid: Grid) -> Grid:
        """Send a request and await the correlated response."""
        ws = self._require_ws()
        req_id_int = self._next_id
        req_id = str(req_id_int)
        self._next_id = (self._next_id + 1) & 0xFFFF

        # Create future for response
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Grid] = loop.create_future()
        self._pending[req_id] = fut

        try:
            if self._binary:
                frame = encode_binary_request(req_id_int, op, grid)
                await ws.send_bytes(frame)
                _fire(self._metrics.on_ws_message_sent, op, len(frame))
            else:
                envelope = orjson.dumps(
                    {"id": req_id, "op": op, "grid": encode_grid_dict(grid)}
                ).decode()
                await ws.send_text(envelope)
                _fire(self._metrics.on_ws_message_sent, op, len(envelope))
            async with asyncio.timeout(self._timeout):
                result = await fut
            _fire(self._metrics.on_request, op, 0.0)
            return result
        except TimeoutError as exc:
            self._pending.pop(req_id, None)
            _fire(self._metrics.on_error, op, "timeout")
            raise NetworkError(f"Request '{op}' timed out") from exc
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def _recv_loop(self) -> None:
        """Background task: read messages and dispatch responses/pushes."""
        ws = self._require_ws()
        try:
            while True:
                data = await ws.recv()

                # Binary frame handling
                if self._binary:
                    self._handle_binary_frame(data)
                    continue

                try:
                    msg = orjson.loads(data)
                except orjson.JSONDecodeError:
                    _log.warning("Received non-JSON WebSocket message, ignoring")
                    continue

                # Batch response (array of envelopes)
                if isinstance(msg, list):
                    for item in msg:
                        self._handle_json_response(item)
                    continue

                # Server-initiated push
                msg_type = msg.get("type")
                if msg_type == "watch":
                    self._dispatch_watch_push(msg)
                    continue

                self._handle_json_response(msg)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            _log.debug("WebSocket recv loop ended: %s", exc)
            # Fail all pending requests
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(NetworkError(f"Connection lost: {exc}"))
            self._pending.clear()

    def _handle_json_response(self, msg: dict[str, Any]) -> None:
        """Process a single JSON response envelope."""
        if not _resolve_grid_response(self._pending, msg):
            _log.debug("Received unmatched WebSocket message: %s", msg.get("id"))

    def _handle_binary_frame(self, data: bytes) -> None:
        """Process a binary frame response or push."""
        try:
            flags, req_id, op, grid_bytes = decode_binary_frame(data)
        except ValueError:
            _log.warning("Invalid binary frame, ignoring")
            return

        if flags & FLAG_PUSH:
            if self._watch_callback is not None:
                try:
                    grid = decode_grid(grid_bytes)
                    self._watch_callback(op, grid)
                except Exception:
                    _log.exception("Error in binary watch push callback")
            return

        if flags & FLAG_RESPONSE:
            req_key = str(req_id)
            fut = self._pending.pop(req_key, None)
            if fut is not None and not fut.done():
                try:
                    grid = decode_grid(grid_bytes)
                    if flags & FLAG_ERROR:
                        fut.set_exception(CallError(grid.meta.get("dis", "Unknown error"), grid))
                    else:
                        fut.set_result(grid)
                except Exception as exc:
                    fut.set_exception(NetworkError(f"Failed to decode binary response: {exc}"))
            return

        _log.debug("Received unmatched binary frame: req_id=%d op=%s", req_id, op)

    def _dispatch_watch_push(self, msg: dict[str, Any]) -> None:
        """Dispatch a watch push message to the registered callback."""
        if self._watch_callback is None:
            return
        watch_id = msg.get("watchId", "")
        try:
            grid = decode_grid_dict(msg["grid"])
            self._watch_callback(watch_id, grid)
        except Exception:
            _log.exception("Error in watch push callback")

    def _require_ws(self) -> HaystackWebSocket:
        """Return the active WebSocket or raise."""
        if self._ws is None:
            msg = "WebSocketClient is not open. Use 'async with WebSocketClient(...) as c:'"
            raise RuntimeError(msg)
        return self._ws


class ReconnectingWebSocketClient:
    """WebSocket client with automatic reconnection and exponential backoff.

    Wraps :class:`WebSocketClient` and manages the connection lifecycle.
    On disconnect, reconnects with exponential backoff and re-registers
    watch callbacks.

    Usage::

        client = ReconnectingWebSocketClient("ws://host:8080/api/ws")
        await client.start()
        try:
            rows = await client.about()
        finally:
            await client.stop()
    """

    def __init__(
        self,
        url: str,
        *,
        username: str = "",
        password: str = "",
        auth_token: str = "",
        tls: TLSConfig | None = None,
        timeout: float = 30.0,
        heartbeat: float = 30.0,
        min_reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 60.0,
        on_connect: Callable[[], Awaitable[None]] | None = None,
        on_disconnect: Callable[[], Awaitable[None]] | None = None,
        metrics: MetricsHooks | None = None,
        compression: bool = False,
        binary: bool = False,
        pythonic: bool = True,
    ) -> None:
        """Initialise the reconnecting client.

        :param url: WebSocket URI (e.g. ``ws://host:8080/api/ws``).
        :param username: Username for SCRAM-SHA-256 authentication.
        :param password: Password for SCRAM-SHA-256 authentication.
        :param auth_token: Bearer token sent on connect.
        :param tls: Optional :class:`~hs_py.tls.TLSConfig` for ``wss://``.
        :param timeout: Per-request timeout in seconds.
        :param heartbeat: Ping interval in seconds (0 to disable).
        :param min_reconnect_delay: Initial backoff delay in seconds.
        :param max_reconnect_delay: Maximum backoff delay in seconds.
        :param on_connect: Async callback invoked after each successful connect.
        :param on_disconnect: Async callback invoked after each disconnect.
        :param metrics: Optional :class:`~hs_py.metrics.MetricsHooks` callbacks.
        :param compression: Enable per-message deflate compression.
        :param binary: Use binary frame encoding instead of JSON envelopes.
        :param pythonic: When ``True`` (default) Grid-returning methods return
            ``list[dict[str, Any]]``.  Pass ``False`` to always return raw Grid.
        """
        self._url = url
        self._username = username
        self._password = password
        self._auth_token = auth_token
        self._tls = tls
        self._timeout = timeout
        self._heartbeat = heartbeat
        self._min_delay = min_reconnect_delay
        self._max_delay = max_reconnect_delay
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._metrics = metrics
        self._compression = compression
        self._binary = binary
        self._pythonic = pythonic
        self._inner: WebSocketClient | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        self._stopping = False
        self._watch_callback: Callable[[str, Grid], Any] | None = None

    async def start(self) -> None:
        """Start the connection loop in the background."""
        self._stopping = False
        self._loop_task = asyncio.create_task(self._connect_loop(), name="hs-ws-reconnect")
        # Wait for initial connection
        await self._connected.wait()

    async def stop(self) -> None:
        """Stop reconnection and close the connection."""
        self._stopping = True
        self._connected.clear()
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
        if self._inner is not None:
            await self._inner.close()
            self._inner = None

    def on_watch_push(self, callback: Callable[[str, Grid], Any]) -> None:
        """Register a watch push callback, preserved across reconnections."""
        self._watch_callback = callback
        if self._inner is not None:
            self._inner.on_watch_push(callback)

    # ---- Delegated ops -----------------------------------------------------

    @overload
    async def about(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def about(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def about(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def about(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query server information."""
        return await self._require_inner().about(raw=raw)

    @overload
    async def ops(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def ops(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def ops(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def ops(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query available operations."""
        return await self._require_inner().ops(raw=raw)

    @overload
    async def formats(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def formats(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def formats(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def formats(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query supported data formats."""
        return await self._require_inner().formats(raw=raw)

    @overload
    async def read(self, filter: str, limit: int | None = ..., *, raw: Literal[True]) -> Grid: ...
    @overload
    async def read(
        self, filter: str, limit: int | None = ..., *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def read(
        self, filter: str, limit: int | None = ..., *, raw: bool
    ) -> Grid | list[dict[str, Any]]: ...
    async def read(
        self, filter: str, limit: int | None = None, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read entities matching a filter expression."""
        return await self._require_inner().read(filter, limit, raw=raw)

    @overload
    async def read_by_ids(self, ids: list[Ref], *, raw: Literal[True]) -> Grid: ...
    @overload
    async def read_by_ids(
        self, ids: list[Ref], *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def read_by_ids(self, ids: list[Ref], *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def read_by_ids(
        self, ids: list[Ref], *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read entities by their identifiers."""
        return await self._require_inner().read_by_ids(ids, raw=raw)

    @overload
    async def nav(self, nav_id: str | None = ..., *, raw: Literal[True]) -> Grid: ...
    @overload
    async def nav(
        self, nav_id: str | None = ..., *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def nav(self, nav_id: str | None = ..., *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def nav(
        self, nav_id: str | None = None, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Navigate the entity tree."""
        return await self._require_inner().nav(nav_id, raw=raw)

    @overload
    async def his_read(self, id: Ref, range: str, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def his_read(
        self, id: Ref, range: str, *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def his_read(self, id: Ref, range: str, *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def his_read(
        self, id: Ref, range: str, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read time-series data for a single point."""
        return await self._require_inner().his_read(id, range, raw=raw)

    async def his_write(self, id: Ref, items: list[dict[str, Any]]) -> None:
        """Write time-series data to a single point."""
        await self._require_inner().his_write(id, items)

    @overload
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = ...,
        *,
        filter: str | None = ...,
        raw: Literal[True],
    ) -> Grid: ...
    @overload
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = ...,
        *,
        filter: str | None = ...,
        raw: Literal[False],
    ) -> list[dict[str, Any]]: ...
    @overload
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = ...,
        *,
        filter: str | None = ...,
        raw: bool,
    ) -> Grid | list[dict[str, Any]]: ...
    async def watch_sub(
        self,
        ids: list[Ref],
        watch_dis: str,
        lease: Number | None = None,
        *,
        filter: str | None = None,
        raw: bool = False,
    ) -> Grid | list[dict[str, Any]]:
        """Create a new watch or add entities to an existing one."""
        return await self._require_inner().watch_sub(ids, watch_dis, lease, filter=filter, raw=raw)

    async def watch_unsub(self, watch_id: str, ids: list[Ref]) -> None:
        """Remove entities from a watch."""
        await self._require_inner().watch_unsub(watch_id, ids)

    @overload
    async def watch_poll(
        self, watch_id: str, refresh: bool = ..., *, raw: Literal[True]
    ) -> Grid: ...
    @overload
    async def watch_poll(
        self, watch_id: str, refresh: bool = ..., *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def watch_poll(
        self, watch_id: str, refresh: bool = ..., *, raw: bool
    ) -> Grid | list[dict[str, Any]]: ...
    async def watch_poll(
        self, watch_id: str, refresh: bool = False, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Poll a watch for changes."""
        return await self._require_inner().watch_poll(watch_id, refresh, raw=raw)

    @overload
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = ...,
        *,
        raw: Literal[True],
    ) -> Grid: ...
    @overload
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = ...,
        *,
        raw: Literal[False],
    ) -> list[dict[str, Any]]: ...
    @overload
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = ...,
        *,
        raw: bool,
    ) -> Grid | list[dict[str, Any]]: ...
    async def invoke_action(
        self,
        id: Ref,
        action: str,
        args: dict[str, Any] | None = None,
        *,
        raw: bool = False,
    ) -> Grid | list[dict[str, Any]]:
        """Invoke an action on an entity."""
        return await self._require_inner().invoke_action(id, action, args, raw=raw)

    # ---- Internal ----------------------------------------------------------

    async def _connect_loop(self) -> None:
        """Background loop that maintains the connection."""
        delay = self._min_delay
        client: WebSocketClient | None = None
        while not self._stopping:
            try:
                client = WebSocketClient(
                    self._url,
                    username=self._username,
                    password=self._password,
                    auth_token=self._auth_token,
                    tls=self._tls,
                    timeout=self._timeout,
                    heartbeat=self._heartbeat,
                    metrics=self._metrics,
                    compression=self._compression,
                    binary=self._binary,
                    pythonic=self._pythonic,
                )
                await client.__aenter__()
                self._inner = client
                if self._watch_callback is not None:
                    client.on_watch_push(self._watch_callback)
                delay = self._min_delay  # Reset on successful connect
                self._connected.set()
                if self._on_connect is not None:
                    await self._on_connect()
                _log.info("ReconnectingWebSocketClient connected to %s", self._url)

                # Wait for the recv loop to end (connection lost)
                if client._recv_task is not None:
                    await client._recv_task
            except asyncio.CancelledError:
                if client is not None:
                    with contextlib.suppress(Exception):
                        await client.close()
                return
            except Exception:
                _log.debug("Connection attempt to %s failed", self._url, exc_info=True)
            finally:
                if client is not None and self._stopping:
                    with contextlib.suppress(Exception):
                        await client.close()

            # Connection lost or failed
            self._connected.clear()
            self._inner = None
            if self._on_disconnect is not None:
                with contextlib.suppress(Exception):
                    await self._on_disconnect()

            if self._stopping:
                return

            _log.info("Reconnecting to %s in %.1fs", self._url, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._max_delay)

    def _require_inner(self) -> WebSocketClient:
        """Return the connected inner client or raise."""
        if self._inner is None or not self._connected.is_set():
            raise NetworkError("Not connected (reconnecting)")
        return self._inner


class ChannelClient:
    """Virtual client scoped to a single channel within a :class:`WebSocketPool`.

    Each channel has its own request ID space and pending futures.
    The channel name is included in every JSON envelope as the ``ch`` field.
    """

    def __init__(self, pool: WebSocketPool, channel: str, *, pythonic: bool = True) -> None:
        """Initialise a channel client.

        :param pool: Parent :class:`WebSocketPool`.
        :param channel: Channel name included in every message.
        :param pythonic: When ``True`` (default) Grid-returning methods return
            ``list[dict[str, Any]]``.  Pass ``False`` to return raw Grid.
        """
        self._pool = pool
        self._channel = channel
        self._pythonic = pythonic

    @overload
    async def about(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def about(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def about(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def about(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query server information."""
        grid = await self._pool._call(self._channel, "about", Grid.make_empty())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def ops(self, *, raw: Literal[True]) -> Grid: ...
    @overload
    async def ops(self, *, raw: Literal[False] = ...) -> list[dict[str, Any]]: ...
    @overload
    async def ops(self, *, raw: bool = ...) -> Grid | list[dict[str, Any]]: ...
    async def ops(self, *, raw: bool = False) -> Grid | list[dict[str, Any]]:
        """Query available operations."""
        grid = await self._pool._call(self._channel, "ops", Grid.make_empty())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def read(self, filter: str, limit: int | None = ..., *, raw: Literal[True]) -> Grid: ...
    @overload
    async def read(
        self, filter: str, limit: int | None = ..., *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def read(
        self, filter: str, limit: int | None = ..., *, raw: bool
    ) -> Grid | list[dict[str, Any]]: ...
    async def read(
        self, filter: str, limit: int | None = None, *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read entities matching a filter expression."""
        row: dict[str, Any] = {"filter": filter}
        if limit is not None:
            row["limit"] = Number(float(limit))
        grid_req = GridBuilder().add_col("filter").add_col("limit").add_row(row).to_grid()
        grid = await self._pool._call(self._channel, "read", grid_req)
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)

    @overload
    async def read_by_ids(self, ids: list[Ref], *, raw: Literal[True]) -> Grid: ...
    @overload
    async def read_by_ids(
        self, ids: list[Ref], *, raw: Literal[False]
    ) -> list[dict[str, Any]]: ...
    @overload
    async def read_by_ids(self, ids: list[Ref], *, raw: bool) -> Grid | list[dict[str, Any]]: ...
    async def read_by_ids(
        self, ids: list[Ref], *, raw: bool = False
    ) -> Grid | list[dict[str, Any]]:
        """Read entities by their identifiers."""
        builder = GridBuilder().add_col("id")
        for ref in ids:
            builder.add_row({"id": ref})
        grid = await self._pool._call(self._channel, "read", builder.to_grid())
        return grid if raw or not self._pythonic else grid_to_pythonic(grid)


class WebSocketPool:
    """Multiplexes multiple logical channels over a single WebSocket.

    Each channel is identified by a string name included in the JSON
    envelope as the ``ch`` field.

    Usage::

        async with WebSocketPool("ws://host:8080/api/ws") as pool:
            ch1 = pool.channel("tenant-1")
            ch2 = pool.channel("tenant-2")
            about1 = await ch1.about()
            about2 = await ch2.about()
    """

    def __init__(
        self,
        url: str,
        *,
        username: str = "",
        password: str = "",
        auth_token: str = "",
        tls: TLSConfig | None = None,
        timeout: float = 30.0,
        heartbeat: float = 30.0,
        compression: bool = False,
        pythonic: bool = True,
    ) -> None:
        """Initialise the connection pool.

        :param url: WebSocket URI (e.g. ``ws://host:8080/api/ws``).
        :param username: Username for SCRAM-SHA-256 authentication.
        :param password: Password for SCRAM-SHA-256 authentication.
        :param auth_token: Bearer token sent on connect.
        :param tls: Optional :class:`~hs_py.tls.TLSConfig` for ``wss://``.
        :param timeout: Per-request timeout in seconds.
        :param heartbeat: Ping interval in seconds (0 to disable).
        :param compression: Enable per-message deflate compression.
        :param pythonic: When ``True`` (default) channels return
            ``list[dict[str, Any]]``.  Pass ``False`` to return raw Grid.
        """
        self._url = url
        self._username = username
        self._password = password
        self._auth_token = auth_token
        self._tls = tls
        self._timeout = timeout
        self._heartbeat = heartbeat
        self._compression = compression
        self._pythonic = pythonic
        self._ws: HaystackWebSocket | None = None
        self._next_id = 0
        self._pending: dict[str, asyncio.Future[Grid]] = {}
        self._recv_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> WebSocketPool:
        ssl_ctx = build_client_ssl_context(self._tls) if self._tls else None
        self._ws = await HaystackWebSocket.connect(
            self._url, ssl_ctx, compression=self._compression
        )
        # Authenticate before starting recv loop
        if self._username:
            await self._pool_scram_authenticate()
        elif self._auth_token:
            await self._ws.send_text(
                orjson.dumps({"type": "auth", "token": self._auth_token}).decode()
            )
        self._recv_task = asyncio.create_task(self._recv_loop(), name="hs-pool-recv")
        if self._heartbeat > 0:
            self._heartbeat_task = asyncio.create_task(
                heartbeat_loop(self._ws, self._heartbeat), name="hs-pool-heartbeat"
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the pooled WebSocket connection."""
        await cancel_task(self._heartbeat_task)
        self._heartbeat_task = None
        await cancel_task(self._recv_task)
        self._recv_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def _pool_scram_authenticate(self) -> None:
        """Perform SCRAM-SHA-256 handshake (same protocol as WebSocketClient)."""
        from hs_py.auth import (
            _b64url_decode,
            _b64url_encode,
            scram_client_final,
            scram_client_first,
            verify_server_signature,
        )

        assert self._ws is not None
        first = scram_client_first(self._username)
        await self._ws.send_text(
            orjson.dumps(
                {"type": "hello", "username": _b64url_encode(self._username.encode())}
            ).decode()
        )
        data = await self._ws.recv()
        msg = orjson.loads(data)
        if msg.get("type") != "hello":
            raise AuthError(f"Expected hello response, got: {msg.get('type')}")

        ht = msg.get("handshakeToken", "")
        hash_name = msg.get("hash", "SHA-256")

        await self._ws.send_text(
            orjson.dumps(
                {
                    "type": "scram",
                    "handshakeToken": ht,
                    "data": _b64url_encode(first.client_first_msg.encode()),
                }
            ).decode()
        )
        data = await self._ws.recv()
        msg = orjson.loads(data)
        if msg.get("type") != "scram":
            raise AuthError(f"Expected scram response, got: {msg.get('type')}")

        server_first_msg = _b64url_decode(msg.get("data", "")).decode()
        new_ht = msg.get("handshakeToken", "")
        final = scram_client_final(self._password, first, server_first_msg, hash_name)

        await self._ws.send_text(
            orjson.dumps(
                {
                    "type": "scram",
                    "handshakeToken": new_ht,
                    "data": _b64url_encode(final.client_final_msg.encode()),
                }
            ).decode()
        )
        data = await self._ws.recv()
        msg = orjson.loads(data)
        if msg.get("type") != "authOk":
            raise AuthError(f"SCRAM auth failed, got: {msg.get('type')}")

        server_data = msg.get("data", "")
        if server_data:
            verify_server_signature(final, _b64url_decode(server_data).decode())

    def channel(self, name: str) -> ChannelClient:
        """Return a virtual client scoped to the given channel name.

        :param name: Channel identifier (included as ``ch`` in each message).
        :returns: :class:`ChannelClient` bound to this pool and channel.
        """
        return ChannelClient(self, name, pythonic=self._pythonic)

    # ---- Internal ----------------------------------------------------------

    async def _call(self, ch: str, op: str, grid: Grid) -> Grid:
        """Send a channel-scoped request and await the response."""
        ws = self._require_ws()
        req_id = str(self._next_id)
        self._next_id = (self._next_id + 1) & 0xFFFF

        envelope = orjson.dumps(
            {"id": req_id, "op": op, "grid": encode_grid_dict(grid), "ch": ch}
        ).decode()

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Grid] = loop.create_future()
        self._pending[req_id] = fut

        try:
            await ws.send_text(envelope)
            async with asyncio.timeout(self._timeout):
                return await fut
        except TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise NetworkError(f"Channel '{ch}' request '{op}' timed out") from exc
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def _recv_loop(self) -> None:
        """Read messages and dispatch responses."""
        ws = self._require_ws()
        try:
            while True:
                data = await ws.recv()
                try:
                    msg = orjson.loads(data)
                except orjson.JSONDecodeError:
                    continue

                if isinstance(msg, list):
                    for item in msg:
                        self._dispatch_response(item)
                else:
                    self._dispatch_response(msg)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            _log.debug("WebSocketPool recv loop ended: %s", exc)
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(NetworkError(f"Connection lost: {exc}"))
            self._pending.clear()

    def _dispatch_response(self, msg: dict[str, Any]) -> None:
        """Route a response to the correct pending future."""
        _resolve_grid_response(self._pending, msg)

    def _require_ws(self) -> HaystackWebSocket:
        """Return the active WebSocket or raise."""
        if self._ws is None:
            msg = "WebSocketPool is not open. Use 'async with WebSocketPool(...) as p:'"
            raise RuntimeError(msg)
        return self._ws
