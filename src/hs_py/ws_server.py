"""Haystack WebSocket server.

Accepts WebSocket connections using the ``websockets`` sans-I/O layer
(via :class:`~hs_py.ws.HaystackWebSocket`) and dispatches operations to a
:class:`~hs_py.server.HaystackOps` implementation.

Uses ``asyncio.start_server`` for full control over TLS context, consistent
with bac-py patterns and independent of aiohttp.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac as hmac_mod
import logging
import weakref
from typing import TYPE_CHECKING, Any

import orjson
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from hs_py._scram_core import (
    HandshakeState,
    TokenEntry,
    handle_scram,
    scram_hello,
)
from hs_py.encoding.json import encode_grid_dict
from hs_py.errors import HaystackError
from hs_py.grid import Grid
from hs_py.metrics import MetricsHooks, _fire
from hs_py.ops import HaystackOps, dispatch_op
from hs_py.tls import TLSConfig, build_server_ssl_context
from hs_py.ws import HaystackWebSocket, cancel_task, heartbeat_loop
from hs_py.ws_codec import (
    FLAG_PUSH,
    decode_binary_frame,
    encode_binary_push,
    encode_binary_response,
)

if TYPE_CHECKING:
    from asyncio import StreamReader, StreamWriter

    from hs_py.auth_types import Authenticator, CertAuthenticator

__all__ = [
    "WebSocketServer",
]

_log = logging.getLogger(__name__)

# Maximum concurrent connections to prevent resource exhaustion.
_MAX_CONNECTIONS = 1000


class WebSocketServer:
    """Haystack WebSocket server using websockets sans-I/O.

    Usage::

        ops = MyHaystackOps()
        server = WebSocketServer(ops, host="0.0.0.0", port=8080)
        await server.start()
        # ... server is running ...
        await server.stop()
    """

    def __init__(
        self,
        ops: HaystackOps,
        *,
        auth_token: str = "",
        authenticator: Authenticator | None = None,
        tls: TLSConfig | None = None,
        host: str = "0.0.0.0",
        port: int = 8080,
        heartbeat: float = 30.0,
        metrics: MetricsHooks | None = None,
        cert_auth: CertAuthenticator | None = None,
        compression: bool = False,
        binary: bool = False,
    ) -> None:
        """Initialise the WebSocket server.

        :param ops: :class:`~hs_py.server.HaystackOps` implementation to dispatch to.
        :param auth_token: Expected bearer token from clients (empty to skip auth).
        :param authenticator: Optional :class:`~hs_py.auth_types.Authenticator` for
            SCRAM-SHA-256 authentication over WebSocket messages.  When provided,
            clients perform a SCRAM handshake after connecting.  Takes precedence
            over *auth_token* when both are given.
        :param tls: Optional :class:`~hs_py.tls.TLSConfig` for TLS 1.3.
        :param host: Bind address.
        :param port: Bind port (0 for OS-assigned).
        :param heartbeat: Ping interval in seconds (0 to disable).
        :param metrics: Optional :class:`~hs_py.metrics.MetricsHooks` callbacks.
        :param cert_auth: Optional :class:`~hs_py.server.CertAuthenticator` for mTLS.
        :param compression: Enable per-message deflate compression.
        :param binary: Use binary frame encoding instead of JSON envelopes.
        """
        self._ops = ops
        self._auth_token = auth_token
        self._authenticator = authenticator
        self._tls = tls
        self._cert_auth = cert_auth
        self._binary = binary
        self._host = host
        self._port = port
        self._heartbeat = heartbeat
        self._metrics = metrics or MetricsHooks()
        self._compression = compression
        self._server: asyncio.Server | None = None
        self._connections: weakref.WeakSet[HaystackWebSocket] = weakref.WeakSet()
        self._connection_count = 0
        # SCRAM state shared across connections
        self._handshakes: dict[str, HandshakeState] = {}
        self._tokens: dict[str, TokenEntry] = {}
        # Wire push handler so ops can trigger watch pushes
        self._ops.set_push_handler(self.push_watch)

    async def start(self) -> None:
        """Start the WebSocket server."""
        ssl_ctx = build_server_ssl_context(self._tls) if self._tls else None
        self._server = await asyncio.start_server(
            self._handle_client,
            self._host,
            self._port,
            ssl=ssl_ctx,
        )
        addr = self._server.sockets[0].getsockname() if self._server.sockets else ("?", "?")
        _log.info("Haystack WebSocket server listening on %s:%s", addr[0], addr[1])

    async def stop(self) -> None:
        """Stop the server and close all connections."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        # Close all tracked connections
        for ws in set(self._connections):
            with contextlib.suppress(Exception):
                await ws.close()
        self._connection_count = 0

    @property
    def port(self) -> int:
        """Return the bound port (useful when bound to port 0)."""
        if self._server and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self._port

    # ---- Watch push --------------------------------------------------------

    async def push_watch(self, watch_id: str, grid: Grid) -> None:
        """Push a watch change notification to all connected clients.

        :param watch_id: The watch identifier.
        :param grid: Grid of changed entities.
        """
        connections = set(self._connections)
        if self._binary:
            frame = encode_binary_push("watchPoll", grid)
            for ws in connections:
                with contextlib.suppress(Exception):
                    await ws.send_bytes(frame)
        else:
            grid_json = encode_grid_dict(grid)
            msg = orjson.dumps({"type": "watch", "watchId": watch_id, "grid": grid_json}).decode()
            for ws in connections:
                with contextlib.suppress(Exception):
                    await ws.send_text(msg)

    # ---- Connection handling -----------------------------------------------

    async def _handle_client(self, reader: StreamReader, writer: StreamWriter) -> None:
        """Per-connection handler: accept WS, authenticate, dispatch ops."""
        if self._connection_count >= _MAX_CONNECTIONS:
            _log.warning("Max connections reached (%d), rejecting", _MAX_CONNECTIONS)
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()
            return

        self._connection_count += 1
        ws: HaystackWebSocket | None = None
        hb_task: asyncio.Task[None] | None = None
        try:
            ws = await HaystackWebSocket.accept(reader, writer, compression=self._compression)
            self._connections.add(ws)
            remote = writer.get_extra_info("peername", ("?",))[0]
            _fire(self._metrics.on_ws_connect, str(remote))

            # Authenticate: cert-based first, then SCRAM, then token-based
            if self._cert_auth is not None:
                peercert = writer.get_extra_info("peercert")
                username = self._cert_auth.authorize(peercert)
                if username is not None:
                    _log.debug("Client authenticated via certificate CN=%s", username)
                elif self._authenticator is not None:
                    authenticated = await self._scram_authenticate(ws)
                    if not authenticated:
                        return
                elif self._auth_token:
                    authenticated = await self._token_authenticate(ws)
                    if not authenticated:
                        return
                else:
                    _log.warning("Client certificate not authorized")
                    return
            elif self._authenticator is not None:
                authenticated = await self._scram_authenticate(ws)
                if not authenticated:
                    return
            elif self._auth_token:
                authenticated = await self._token_authenticate(ws)
                if not authenticated:
                    return

            # Start heartbeat and message dispatch
            if self._heartbeat > 0:
                hb_task = asyncio.create_task(
                    heartbeat_loop(ws, self._heartbeat), name="hs-ws-srv-heartbeat"
                )
            await self._message_loop(ws)
        except (ConnectionClosedOK, ConnectionClosedError):
            pass
        except (TimeoutError, ConnectionError, OSError):
            _log.debug("WebSocket connection error")
        except Exception:
            _log.exception("Unexpected error in WebSocket connection handler")
        finally:
            await cancel_task(hb_task)
            self._connection_count -= 1
            if ws is not None:
                self._connections.discard(ws)
                _fire(self._metrics.on_ws_disconnect, str(remote))
                with contextlib.suppress(Exception):
                    await ws.close()

    async def _token_authenticate(self, ws: HaystackWebSocket) -> bool:
        """Read and validate a bearer token message. Return ``True`` if valid."""
        try:
            async with asyncio.timeout(10.0):
                data = await ws.recv()
            msg = orjson.loads(data)
            token = msg.get("authToken", "")
            if token and hmac_mod.compare_digest(token, self._auth_token):
                return True
            _log.warning("WebSocket token auth failed")
            return False
        except Exception:
            _log.warning("WebSocket token auth error")
            return False

    async def _scram_authenticate(self, ws: HaystackWebSocket) -> bool:
        """Perform SCRAM-SHA-256 handshake over WebSocket messages.

        Message flow::

            Client → {"type":"hello","username":"<b64>"}
            Server → {"type":"hello","handshakeToken":"...","hash":"SHA-256"}
            Client → {"type":"scram","handshakeToken":"...","data":"<b64>"}
            Server → {"type":"scram","handshakeToken":"...","data":"<b64>"}
            Client → {"type":"scram","handshakeToken":"...","data":"<b64>"}
            Server → {"type":"authOk","authToken":"...","data":"<b64>"}

        Returns ``True`` on successful authentication.
        """
        assert self._authenticator is not None
        try:
            async with asyncio.timeout(30.0):
                # Step 1: HELLO
                data = await ws.recv()
                msg = orjson.loads(data)

                # Also accept legacy token auth during SCRAM mode
                if "authToken" in msg:
                    token = msg["authToken"]
                    if self._auth_token and hmac_mod.compare_digest(token, self._auth_token):
                        return True
                    _log.warning("WebSocket token auth failed (SCRAM mode)")
                    await ws.send_text(orjson.dumps({"type": "authErr"}).decode())
                    return False

                if msg.get("type") != "hello":
                    _log.warning("Expected hello message, got: %s", msg.get("type"))
                    await ws.send_text(orjson.dumps({"type": "authErr"}).decode())
                    return False

                auth_header = f"HELLO username={msg.get('username', '')}"
                result = await scram_hello(self._authenticator, self._handshakes, auth_header)
                if result.status != 401 or "handshakeToken" not in result.headers.get(
                    "WWW-Authenticate", ""
                ):
                    await ws.send_text(orjson.dumps({"type": "authErr"}).decode())
                    return False

                from hs_py.auth import _parse_header_params

                hello_resp_params = _parse_header_params(result.headers["WWW-Authenticate"])
                await ws.send_text(
                    orjson.dumps(
                        {
                            "type": "hello",
                            "handshakeToken": hello_resp_params.get("handshakeToken", ""),
                            "hash": hello_resp_params.get("hash", "SHA-256"),
                        }
                    ).decode()
                )

                # Step 2: client-first → server-first
                data = await ws.recv()
                msg = orjson.loads(data)
                if msg.get("type") != "scram":
                    await ws.send_text(orjson.dumps({"type": "authErr"}).decode())
                    return False

                ht = msg.get("handshakeToken", "")
                scram_data = msg.get("data", "")
                auth_header = f"SCRAM handshakeToken={ht}, data={scram_data}"
                result = handle_scram(self._handshakes, self._tokens, auth_header)

                if result.status == 401:
                    www_auth = result.headers.get("WWW-Authenticate", "")
                    resp_params = _parse_header_params(www_auth)
                    await ws.send_text(
                        orjson.dumps(
                            {
                                "type": "scram",
                                "handshakeToken": resp_params.get("handshakeToken", ""),
                                "hash": resp_params.get("hash", "SHA-256"),
                                "data": resp_params.get("data", ""),
                            }
                        ).decode()
                    )
                else:
                    await ws.send_text(orjson.dumps({"type": "authErr"}).decode())
                    return False

                # Step 3: client-final → server-final + authToken
                data = await ws.recv()
                msg = orjson.loads(data)
                if msg.get("type") != "scram":
                    await ws.send_text(orjson.dumps({"type": "authErr"}).decode())
                    return False

                ht = msg.get("handshakeToken", "")
                scram_data = msg.get("data", "")
                auth_header = f"SCRAM handshakeToken={ht}, data={scram_data}"
                result = handle_scram(self._handshakes, self._tokens, auth_header)

                if result.status == 200:
                    auth_info = result.headers.get("Authentication-Info", "")
                    resp_params = _parse_header_params(auth_info)
                    await ws.send_text(
                        orjson.dumps(
                            {
                                "type": "authOk",
                                "authToken": resp_params.get("authToken", ""),
                                "data": resp_params.get("data", ""),
                            }
                        ).decode()
                    )
                    _log.debug("WebSocket SCRAM auth succeeded")
                    return True

                await ws.send_text(orjson.dumps({"type": "authErr"}).decode())
                return False

        except Exception:
            _log.warning("WebSocket SCRAM auth error")
            return False

    async def _message_loop(self, ws: HaystackWebSocket) -> None:
        """Read request messages and dispatch to HaystackOps."""
        while True:
            data = await ws.recv()
            _fire(self._metrics.on_ws_message_recv, "", len(data))

            # Binary frame handling
            if self._binary and isinstance(data, bytes) and len(data) >= 4:
                await self._handle_binary_message(ws, data)
                continue

            # JSON text handling
            try:
                msg = orjson.loads(data)
            except orjson.JSONDecodeError:
                _log.warning("Non-JSON WebSocket message, ignoring")
                continue

            # Batch: JSON array of envelopes
            if isinstance(msg, list):
                await self._handle_batch(ws, msg)
                continue

            await self._handle_json_message(ws, msg)

    async def _handle_json_message(self, ws: HaystackWebSocket, msg: dict[str, Any]) -> None:
        """Dispatch a single JSON request envelope."""
        req_id = msg.get("id")
        op = msg.get("op", "")
        ch = msg.get("ch")
        _fire(self._metrics.on_ws_message_recv, op, 0)

        try:
            result_grid = await dispatch_op(self._ops, op, msg)
        except HaystackError as exc:
            _fire(self._metrics.on_error, op, type(exc).__name__)
            result_grid = Grid.make_error(str(exc))
        except Exception as exc:
            _log.exception("Unhandled error in op '%s'", op)
            _fire(self._metrics.on_error, op, type(exc).__name__)
            result_grid = Grid.make_error(f"Internal error: {type(exc).__name__}")

        grid_json = encode_grid_dict(result_grid)
        response: dict[str, Any] = {"grid": grid_json}
        if req_id is not None:
            response["id"] = req_id
        if ch is not None:
            response["ch"] = ch
        response_bytes = orjson.dumps(response).decode()
        await ws.send_text(response_bytes)
        _fire(self._metrics.on_ws_message_sent, op, len(response_bytes))

    async def _handle_binary_message(self, ws: HaystackWebSocket, data: bytes) -> None:
        """Dispatch a binary frame request."""
        try:
            flags, req_id, op, grid_bytes = decode_binary_frame(data)
        except ValueError:
            _log.warning("Invalid binary frame, ignoring")
            return

        if flags & FLAG_PUSH:
            return  # Server doesn't process inbound pushes

        _fire(self._metrics.on_ws_message_recv, op, len(data))

        try:
            msg: dict[str, Any] = {"op": op}
            if grid_bytes:
                msg["grid"] = orjson.loads(grid_bytes)
            result_grid = await dispatch_op(self._ops, op, msg)
        except HaystackError as exc:
            _fire(self._metrics.on_error, op, type(exc).__name__)
            result_grid = Grid.make_error(str(exc))
        except Exception as exc:
            _log.exception("Unhandled error in binary op '%s'", op)
            _fire(self._metrics.on_error, op, type(exc).__name__)
            result_grid = Grid.make_error(f"Internal error: {type(exc).__name__}")

        response = encode_binary_response(req_id, op, result_grid, is_error=result_grid.is_error)
        await ws.send_bytes(response)
        _fire(self._metrics.on_ws_message_sent, op, len(response))

    async def _handle_batch(self, ws: HaystackWebSocket, batch: list[Any]) -> None:
        """Dispatch a batch of JSON request envelopes and send array response."""
        responses: list[dict[str, Any]] = []
        for item in batch:
            if not isinstance(item, dict):
                continue
            req_id = item.get("id")
            op = item.get("op", "")
            try:
                result_grid = await dispatch_op(self._ops, op, item)
            except HaystackError as exc:
                result_grid = Grid.make_error(str(exc))
            except Exception as exc:
                _log.exception("Unhandled error in batch op '%s'", op)
                result_grid = Grid.make_error(f"Internal error: {type(exc).__name__}")
            grid_json = encode_grid_dict(result_grid)
            resp: dict[str, Any] = {"grid": grid_json}
            if req_id is not None:
                resp["id"] = req_id
            responses.append(resp)
        await ws.send_text(orjson.dumps(responses).decode())
