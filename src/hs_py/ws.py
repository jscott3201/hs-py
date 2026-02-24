"""Sans-I/O WebSocket wrapper for Haystack.

Uses the ``websockets`` library's sans-I/O protocol objects together with
``asyncio`` TCP/TLS streams.  Each :class:`HaystackWebSocket` instance owns
one WebSocket connection backed by a ``(StreamReader, StreamWriter)`` pair.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections import deque
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from websockets.client import ClientProtocol
from websockets.exceptions import (
    ConnectionClosedError,
    ConnectionClosedOK,
    InvalidState,
    ProtocolError,
)
from websockets.extensions.permessage_deflate import (
    ClientPerMessageDeflateFactory,
    ServerPerMessageDeflateFactory,
)
from websockets.frames import Close, Frame, Opcode
from websockets.http11 import Request
from websockets.protocol import State as _WSState
from websockets.server import ServerProtocol
from websockets.typing import Subprotocol
from websockets.uri import parse_uri

if TYPE_CHECKING:
    import ssl
    from asyncio import StreamReader, StreamWriter
    from collections.abc import Sequence

__all__ = [
    "HaystackWebSocket",
    "cancel_task",
    "heartbeat_loop",
]

_log = logging.getLogger(__name__)

# Read buffer size for asyncio streams.
_READ_SIZE = 65536

# Write buffer tuning — keep low for prompt frame delivery.
_WRITE_HIGH_WATER = 32768
_WRITE_LOW_WATER = 8192

# Default subprotocol for Haystack WebSocket connections.
HAYSTACK_SUBPROTOCOL = "haystack"


async def cancel_task(task: asyncio.Task[object] | None) -> None:
    """Cancel an asyncio task and suppress :class:`~asyncio.CancelledError`.

    :param task: Task to cancel, or ``None`` (no-op).
    """
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def heartbeat_loop(ws: HaystackWebSocket, interval: float) -> None:
    """Periodically send WebSocket pings to keep a connection alive.

    :param ws: WebSocket connection to ping.
    :param interval: Seconds between pings.
    """
    try:
        while True:
            await asyncio.sleep(interval)
            await ws.ping()
    except asyncio.CancelledError:
        return
    except Exception:
        _log.debug("Heartbeat loop ended")


def _set_nodelay(writer: StreamWriter) -> None:
    """Enable TCP_NODELAY and tune write buffer limits."""
    sock = writer.get_extra_info("socket")
    if sock is not None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    transport = writer.transport
    if transport is not None:
        transport.set_write_buffer_limits(high=_WRITE_HIGH_WATER, low=_WRITE_LOW_WATER)


def _write_pending(protocol: ClientProtocol | ServerProtocol, writer: StreamWriter) -> bool:
    """Write all pending protocol data to the writer. Return True if any written."""
    wrote = False
    for chunk in protocol.data_to_send():
        if chunk:
            writer.write(chunk)
            wrote = True
    return wrote


def _drain_to_send(protocol: ClientProtocol | ServerProtocol) -> bytes:
    """Collect all pending outgoing data from the protocol."""
    return b"".join(protocol.data_to_send())


class HaystackWebSocket:
    """Async WebSocket connection using the websockets sans-I/O protocol.

    Use :meth:`connect` to initiate a client connection or :meth:`accept`
    to accept a server-side connection.  Both return a ready-to-use instance.
    """

    def __init__(
        self,
        reader: StreamReader,
        writer: StreamWriter,
        protocol: ClientProtocol | ServerProtocol,
    ) -> None:
        """Initialise from an established TCP connection.

        Prefer :meth:`connect` (client) or :meth:`accept` (server)
        rather than calling this directly.

        :param reader: asyncio stream reader.
        :param writer: asyncio stream writer.
        :param protocol: Negotiated websockets protocol object.
        """
        self._reader = reader
        self._writer = writer
        self._protocol = protocol
        self._pending_frames: deque[Frame] = deque()

    # -- Client factory --

    @classmethod
    async def connect(
        cls,
        uri: str,
        ssl_ctx: ssl.SSLContext | None = None,
        *,
        subprotocol: str = HAYSTACK_SUBPROTOCOL,
        handshake_timeout: float = 10.0,
        max_size: int | None = None,
        compression: bool = False,
    ) -> HaystackWebSocket:
        """Initiate a WebSocket client connection.

        :param uri: WebSocket URI (``wss://host:port/path``).
        :param ssl_ctx: TLS context, or *None* for plaintext ``ws://``.
        :param subprotocol: WebSocket subprotocol to negotiate.
        :param handshake_timeout: Maximum seconds for the handshake.
        :param max_size: Maximum WebSocket message size.
        :param compression: Enable per-message deflate compression.
        :returns: Connected :class:`HaystackWebSocket` instance.
        """
        parsed = urlparse(uri)
        host = parsed.hostname or "localhost"
        default_port = 443 if parsed.scheme == "wss" else 80
        port = parsed.port or default_port
        use_ssl = ssl_ctx if parsed.scheme == "wss" else None

        reader, writer = await asyncio.open_connection(host, port, ssl=use_ssl)
        _set_nodelay(writer)

        ws_uri = parse_uri(uri)
        extensions_factories = [ClientPerMessageDeflateFactory()] if compression else None
        protocol = ClientProtocol(
            ws_uri,
            subprotocols=[Subprotocol(subprotocol)],
            max_size=max_size,
            extensions=extensions_factories,
        )

        request = protocol.connect()
        protocol.send_request(request)
        outgoing = _drain_to_send(protocol)
        if outgoing:
            writer.write(outgoing)
            await writer.drain()

        try:
            async with asyncio.timeout(handshake_timeout):
                while True:
                    data = await reader.read(_READ_SIZE)
                    if not data:
                        msg = "Connection closed during WebSocket handshake"
                        raise ConnectionError(msg)
                    protocol.receive_data(data)

                    if protocol.handshake_exc is not None:
                        raise protocol.handshake_exc

                    events = protocol.events_received()
                    if events:
                        break

                    outgoing = _drain_to_send(protocol)
                    if outgoing:
                        writer.write(outgoing)
                        await writer.drain()
        except BaseException:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()
            raise

        _log.debug("Haystack WebSocket client connected to %s:%d", host, port)
        return cls(reader, writer, protocol)

    # -- Server factory --

    @classmethod
    async def accept(
        cls,
        reader: StreamReader,
        writer: StreamWriter,
        *,
        subprotocol: str = HAYSTACK_SUBPROTOCOL,
        handshake_timeout: float = 10.0,
        max_size: int | None = None,
        compression: bool = False,
    ) -> HaystackWebSocket:
        """Accept an inbound WebSocket connection on existing streams.

        :param reader: asyncio StreamReader from the accepted TCP connection.
        :param writer: asyncio StreamWriter from the accepted TCP connection.
        :param subprotocol: WebSocket subprotocol to accept.
        :param handshake_timeout: Maximum seconds for the handshake.
        :param max_size: Maximum WebSocket message size.
        :param compression: Enable per-message deflate compression.
        :returns: Accepted :class:`HaystackWebSocket` instance.
        """
        extensions_factories = [ServerPerMessageDeflateFactory()] if compression else []
        protocol = ServerProtocol(
            subprotocols=[Subprotocol(subprotocol)],
            max_size=max_size,
            extensions=extensions_factories,
        )
        _set_nodelay(writer)

        async with asyncio.timeout(handshake_timeout):
            while True:
                data = await reader.read(_READ_SIZE)
                if not data:
                    msg = "Connection closed before WebSocket handshake"
                    raise ConnectionError(msg)
                protocol.receive_data(data)

                events: Sequence[object] = protocol.events_received()
                if events:
                    request = events[0]
                    if not isinstance(request, Request):
                        msg = f"Expected HTTP request, got {type(request)}"
                        raise ProtocolError(msg)
                    break

        response = protocol.accept(request)
        protocol.send_response(response)
        outgoing = _drain_to_send(protocol)
        if outgoing:
            writer.write(outgoing)
            await writer.drain()

        if protocol.handshake_exc is not None:
            raise protocol.handshake_exc

        _log.debug("Haystack WebSocket server accepted connection")
        return cls(reader, writer, protocol)

    # -- I/O operations --

    async def send_text(self, text: str) -> None:
        """Send a text WebSocket frame.

        :param text: Text payload to send.
        """
        self._protocol.send_text(text.encode())
        if _write_pending(self._protocol, self._writer):
            await self._writer.drain()

    async def send_text_preencoded(self, data: bytes) -> None:
        """Send pre-encoded UTF-8 bytes as a text WebSocket frame.

        Avoids the ``str → encode → bytes`` roundtrip when the caller
        already holds a UTF-8 byte string (e.g. from :func:`orjson.dumps`).

        :param data: UTF-8 encoded payload bytes.
        """
        self._protocol.send_text(data)
        if _write_pending(self._protocol, self._writer):
            await self._writer.drain()

    async def send_bytes(self, data: bytes) -> None:
        """Send a binary WebSocket frame.

        :param data: Binary payload to send.
        """
        self._protocol.send_binary(data)
        if _write_pending(self._protocol, self._writer):
            await self._writer.drain()

    async def ping(self, data: bytes = b"") -> None:
        """Send a WebSocket ping frame."""
        self._protocol.send_ping(data)
        if _write_pending(self._protocol, self._writer):
            await self._writer.drain()

    async def recv(self) -> str | bytes:
        """Receive the next WebSocket message payload.

        :returns: ``str`` for text frames, ``bytes`` for binary frames.
        :raises ConnectionClosedOK: On graceful close.
        :raises ConnectionClosedError: On abnormal close.
        """
        while True:
            # Drain buffered frames first
            while self._pending_frames:
                frame = self._pending_frames.popleft()
                result = self._handle_frame(frame)
                if result is not None:
                    await self._flush_outgoing()
                    return result

            # Check for already-received events
            events = self._protocol.events_received()
            for i, raw_event in enumerate(events):
                if isinstance(raw_event, Frame):
                    result = self._handle_frame(raw_event)
                    if result is not None:
                        # Stash remaining frames
                        for remaining in events[i + 1 :]:
                            if isinstance(remaining, Frame):
                                self._pending_frames.append(remaining)
                        await self._flush_outgoing()
                        return result

            # Need more data from the network
            data = await self._reader.read(_READ_SIZE)
            if not data:
                raise ConnectionClosedError(None, None, rcvd_then_sent=None)
            self._protocol.receive_data(data)

            if self._protocol.handshake_exc is not None:
                raise self._protocol.handshake_exc

            await self._flush_outgoing()

    def _handle_frame(self, frame: Frame) -> str | bytes | None:
        """Process a single frame. Return payload for data frames, None for control."""
        if frame.opcode == Opcode.BINARY:
            return bytes(frame.data)
        if frame.opcode == Opcode.TEXT:
            return bytes(frame.data).decode()
        if frame.opcode == Opcode.CLOSE:
            rcvd = Close.parse(frame.data) if frame.data else None
            raise ConnectionClosedOK(rcvd, None, rcvd_then_sent=None)
        # PING/PONG — protocol auto-replies, just flush
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Initiate a graceful WebSocket close.

        :param code: WebSocket close code (default 1000 = normal).
        :param reason: Optional close reason string.
        """
        try:
            self._protocol.send_close(code, reason)
            outgoing = _drain_to_send(self._protocol)
            if outgoing:
                self._writer.write(outgoing)
                await self._writer.drain()
            try:
                async with asyncio.timeout(5):
                    data = await self._reader.read(_READ_SIZE)
                    if data:
                        self._protocol.receive_data(data)
            except (TimeoutError, OSError, ConnectionError):
                pass
        except (OSError, ConnectionError, InvalidState):
            pass
        finally:
            await self._close_transport()

    async def _flush_outgoing(self) -> None:
        """Write any pending protocol output (e.g. PONG replies)."""
        if _write_pending(self._protocol, self._writer):
            with contextlib.suppress(OSError, ConnectionError):
                await self._writer.drain()

    async def _close_transport(self) -> None:
        """Close the underlying TCP connection and wait for it to complete."""
        try:
            if not self._writer.is_closing():
                self._writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await self._writer.wait_closed()
        except (OSError, RuntimeError):
            pass

    @property
    def is_open(self) -> bool:
        """``True`` if the WebSocket connection appears open."""
        return self._protocol.state is _WSState.OPEN

    @property
    def subprotocol(self) -> str | None:
        """Return the negotiated WebSocket subprotocol."""
        return self._protocol.subprotocol
