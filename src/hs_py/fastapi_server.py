"""FastAPI-based Haystack HTTP server.

Provides a FastAPI application factory with content-negotiated Haystack routes,
SCRAM-SHA-256 authentication middleware, and WebSocket support.

See: https://project-haystack.org/doc/docHaystack/HttpApi
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import orjson
from fastapi import APIRouter, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from hs_py._scram_core import (
    TOKEN_LIFETIME,
    HandshakeState,
    TokenEntry,
    handle_scram,
    scram_hello,
    validate_bearer,
)
from hs_py.content_negotiation import decode_request, encode_response, negotiate_format
from hs_py.encoding.json import encode_grid_dict
from hs_py.errors import HaystackError
from hs_py.grid import Grid
from hs_py.ops import _POST_OP_METHODS, HaystackOps, dispatch_op

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from hs_py.auth_types import Authenticator
    from hs_py.ontology.namespace import Namespace
    from hs_py.storage.protocol import StorageAdapter

__all__ = [
    "ScramAuthMiddleware",
    "create_fastapi_app",
]

_log = logging.getLogger(__name__)

_GET_OPS = ("about", "ops", "formats", "close")
_POST_OPS = tuple(_POST_OP_METHODS.keys())

# Maximum request body size (16 MiB)
_MAX_BODY_SIZE = 16 * 1024 * 1024

_401_HEADERS = {"WWW-Authenticate": "SCRAM hash=SHA-256"}


# ---------------------------------------------------------------------------
# SCRAM auth middleware
# ---------------------------------------------------------------------------


class ScramAuthMiddleware(BaseHTTPMiddleware):
    """SCRAM-SHA-256 authentication middleware for FastAPI.

    Implements the Haystack SCRAM handshake:

    1. Client sends ``Authorization: HELLO username=<b64>``
    2. Server returns 401 with ``WWW-Authenticate: SCRAM handshakeToken=...``
    3. Client sends ``Authorization: SCRAM handshakeToken=..., data=<client-first>``
    4. Server returns 401 with server-first message
    5. Client sends ``Authorization: SCRAM handshakeToken=..., data=<client-final>``
    6. Server returns 200 with ``Authentication-Info: authToken=..., data=<server-final>``
    7. Subsequent requests use ``Authorization: BEARER authToken=...``

    :param app: The ASGI application to wrap.
    :param authenticator: Server-side credential store.
    """

    def __init__(self, app: Any, authenticator: Authenticator) -> None:
        """Initialise the middleware with an authenticator.

        :param app: The ASGI application to wrap.
        :param authenticator: Server-side :class:`~hs_py.auth_types.Authenticator`.
        """
        super().__init__(app)
        self._authenticator = authenticator
        self._handshakes: dict[str, HandshakeState] = {}
        self._tokens: dict[str, TokenEntry] = {}
        self._tokens_exposed = False

    async def dispatch(self, request: Request, call_next: Any) -> StarletteResponse:
        """Intercept each request and enforce SCRAM authentication."""
        # Expose tokens on app.state so the WS endpoint can validate auth.
        # Deferred from __init__ because the middleware stack wraps inner apps
        # that do not carry a ``state`` attribute.
        if not self._tokens_exposed:
            request.app.state._auth_tokens = self._tokens
            self._tokens_exposed = True

        auth_header = request.headers.get("Authorization", "")
        scheme = auth_header.split()[0].upper() if auth_header else ""

        if scheme == "HELLO":
            result = await scram_hello(self._authenticator, self._handshakes, auth_header)
            return StarletteResponse(
                status_code=result.status, headers=result.headers, content=result.body
            )

        if scheme == "SCRAM":
            result = handle_scram(self._handshakes, self._tokens, auth_header)
            return StarletteResponse(
                status_code=result.status, headers=result.headers, content=result.body
            )

        if scheme == "BEARER":
            bearer_result = validate_bearer(self._tokens, auth_header)
            if bearer_result is not None:
                return StarletteResponse(
                    status_code=bearer_result.status,
                    headers=bearer_result.headers,
                    content=bearer_result.body,
                )
            response: StarletteResponse = await call_next(request)
            return response

        # No recognized auth scheme → 401
        return StarletteResponse(status_code=401, headers=_401_HEADERS)


# ---------------------------------------------------------------------------
# Request / response helpers
# ---------------------------------------------------------------------------


def _get_ops(request: Request) -> HaystackOps:
    """Extract the HaystackOps instance from app state."""
    return request.app.state.ops  # type: ignore[no-any-return]


async def _parse_grid(request: Request) -> Grid:
    """Decode the request body into a Grid, using content-negotiation."""
    body = await request.body()
    if not body:
        return Grid.make_empty()
    if len(body) > _MAX_BODY_SIZE:
        raise HaystackError("Request body too large")
    ct = request.headers.get("content-type", "application/json")
    return decode_request(body, ct)


def _grid_response(grid: Grid, request: Request) -> Response:
    """Encode a Grid into an HTTP response, honouring the Accept header."""
    accept = request.headers.get("accept", "application/json")
    fmt = negotiate_format(accept)
    body, ct = encode_response(grid, fmt)
    return Response(content=body, media_type=ct)


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------


async def _haystack_error_handler(request: Request, exc: Exception) -> Response:
    """Convert a :class:`~hs_py.errors.HaystackError` into an error Grid response."""
    grid = Grid.make_error(str(exc))
    return _grid_response(grid, request)


async def _generic_error_handler(request: Request, exc: Exception) -> Response:
    """Catch-all for unhandled exceptions — return an error Grid response.

    Mirrors the aiohttp ``_error_middleware`` behaviour so that unexpected
    exceptions never leak a raw 500 to Haystack clients.
    """
    _log.exception("Unhandled exception in request handler")
    grid = Grid.make_error(f"Internal error: {type(exc).__name__}")
    return _grid_response(grid, request)


# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage storage adapter lifecycle — start on startup, close on shutdown."""
    storage = getattr(app.state, "storage", None)
    if storage is not None:
        await storage.start()
    yield
    if storage is not None:
        await storage.close()


# ---------------------------------------------------------------------------
# Route handler factories
# ---------------------------------------------------------------------------


def _make_get_handler(op_name: str) -> Any:
    """Create a GET handler for a named Haystack op."""

    async def handler(request: Request) -> Response:
        ops = _get_ops(request)
        if op_name == "about":
            grid = await ops.about()
        elif op_name == "ops":
            grid = await ops.ops()
        elif op_name == "formats":
            grid = await ops.formats()
        elif op_name == "close":
            await ops.on_close()
            grid = Grid.make_empty()
        else:
            grid = Grid.make_error(f"Unknown GET operation: {op_name}")
        return _grid_response(grid, request)

    handler.__name__ = f"{op_name}_get_handler"
    return handler


def _make_post_handler(op_name: str, method_name: str) -> Any:
    """Create a POST handler for a named Haystack op.

    :param op_name: The URL-level op name (e.g. ``"hisRead"``).
    :param method_name: The :class:`~hs_py.ops.HaystackOps` method name (e.g.
        ``"his_read"``).
    """

    async def handler(request: Request) -> Response:
        ops = _get_ops(request)
        req_grid = await _parse_grid(request)
        method = getattr(ops, method_name)
        result_grid: Grid = await method(req_grid)
        return _grid_response(result_grid, request)

    handler.__name__ = f"{op_name}_post_handler"
    return handler


# ---------------------------------------------------------------------------
# WebSocket dispatch helpers
# ---------------------------------------------------------------------------


async def _handle_ws_single(websocket: WebSocket, ops: HaystackOps, msg: dict[str, Any]) -> None:
    """Dispatch a single WS message and send the response."""
    req_id = msg.get("id")
    op = msg.get("op", "")
    try:
        result_grid = await dispatch_op(ops, op, msg)
    except HaystackError as exc:
        result_grid = Grid.make_error(str(exc))
    except Exception as exc:
        _log.exception("Unhandled error in WS op '%s'", op)
        result_grid = Grid.make_error(f"Internal error: {type(exc).__name__}")

    response: dict[str, Any] = {"grid": encode_grid_dict(result_grid)}
    if req_id is not None:
        response["id"] = req_id
    await websocket.send_text(orjson.dumps(response).decode())


async def _handle_ws_batch(websocket: WebSocket, ops: HaystackOps, batch: list[Any]) -> None:
    """Dispatch all ops in a batch concurrently, then send array response."""
    items = [item for item in batch if isinstance(item, dict)]
    if not items:
        return

    async def _dispatch_item(item: dict[str, Any]) -> dict[str, Any]:
        r_id = item.get("id")
        r_op = item.get("op", "")
        try:
            r_grid = await dispatch_op(ops, r_op, item)
        except HaystackError as exc:
            r_grid = Grid.make_error(str(exc))
        except Exception as exc:
            _log.exception("Unhandled error in batch WS op '%s'", r_op)
            r_grid = Grid.make_error(f"Internal error: {type(exc).__name__}")
        resp: dict[str, Any] = {"grid": encode_grid_dict(r_grid)}
        if r_id is not None:
            resp["id"] = r_id
        return resp

    responses = await asyncio.gather(*[_dispatch_item(item) for item in items])
    await websocket.send_text(orjson.dumps(list(responses)).decode())


# ---------------------------------------------------------------------------
# Router construction
# ---------------------------------------------------------------------------


def _build_router() -> APIRouter:
    """Build and return an APIRouter with all Haystack op endpoints."""
    router = APIRouter()

    # GET ops
    for op_name in _GET_OPS:
        router.add_api_route(
            f"/{op_name}",
            _make_get_handler(op_name),
            methods=["GET"],
        )

    # POST ops
    for op_name, method_name in _POST_OP_METHODS.items():
        router.add_api_route(
            f"/{op_name}",
            _make_post_handler(op_name, method_name),
            methods=["POST"],
        )

    # WebSocket endpoint
    @router.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        """Handle a WebSocket upgrade and dispatch Haystack operations.

        Uses the same JSON envelope protocol as the aiohttp WebSocket handler.
        Messages are dispatched concurrently via ``asyncio.create_task`` —
        response ordering is maintained by correlation IDs.

        When SCRAM auth is enabled, the first message must contain an
        ``authToken`` field matching a valid bearer token.  Connections
        without a valid token are closed with code 4003.
        """
        await websocket.accept(subprotocol="haystack")
        ops: HaystackOps = websocket.app.state.ops
        tasks: set[asyncio.Task[None]] = set()

        # Check if SCRAM auth is enabled — require token on WS too
        auth_tokens: dict[str, TokenEntry] | None = getattr(
            websocket.app.state, "_auth_tokens", None
        )
        if auth_tokens is not None:
            try:
                raw = await websocket.receive_text()
                msg = orjson.loads(raw)
                token = msg.get("authToken", "")
                entry = auth_tokens.get(token)
                if entry is None or (time.monotonic() - entry.created) > TOKEN_LIFETIME:
                    await websocket.close(code=4003, reason="Authentication required")
                    return
            except Exception:
                await websocket.close(code=4003, reason="Authentication required")
                return

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = orjson.loads(raw)
                except orjson.JSONDecodeError:
                    _log.warning("Non-JSON WebSocket message, ignoring")
                    continue

                # Batch: JSON array of envelopes — dispatch all ops concurrently
                if isinstance(msg, list):
                    task = asyncio.create_task(_handle_ws_batch(websocket, ops, msg))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                    continue

                # Single message — fire-and-forget via create_task
                if isinstance(msg, dict):
                    task = asyncio.create_task(_handle_ws_single(websocket, ops, msg))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)

        except WebSocketDisconnect:
            pass
        finally:
            # Wait for in-flight tasks before returning
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    @router.get("/ontology/export")
    async def export_ontology(request: Request, format: str = "turtle") -> Response:
        """Export the loaded ontology namespace as RDF (Turtle or JSON-LD)."""
        ops = _get_ops(request)
        ns = getattr(ops, "_namespace", None)
        if ns is None:
            return _grid_response(Grid.make_error("No namespace loaded"), request)
        from hs_py.ontology.rdf import export_jsonld, export_turtle

        if format == "jsonld":
            return Response(content=export_jsonld(ns), media_type="application/ld+json")
        return Response(content=export_turtle(ns), media_type="text/turtle")

    return router


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_fastapi_app(
    ops: HaystackOps | None = None,
    *,
    storage: StorageAdapter | None = None,
    authenticator: Authenticator | None = None,
    namespace: Namespace | None = None,
    prefix: str = "/api",
) -> FastAPI:
    """Create a FastAPI application with Haystack HTTP routes.

    The returned app supports content-negotiated responses (JSON, Zinc, CSV),
    SCRAM-SHA-256 authentication (when an *authenticator* is provided), and
    WebSocket connections at ``{prefix}/ws``.

    :param ops: :class:`~hs_py.ops.HaystackOps` implementation to dispatch to.
        When *None* and *storage* is provided, a default :class:`~hs_py.ops.HaystackOps`
        is constructed automatically.
    :param storage: Optional :class:`~hs_py.storage.protocol.StorageAdapter`
        backend.  Its ``start()`` / ``close()`` lifecycle methods are called
        automatically via the FastAPI lifespan.
    :param authenticator: Optional :class:`~hs_py.auth_types.Authenticator` for
        SCRAM-SHA-256 auth.  When *None*, all requests are accepted without auth.
    :param namespace: Optional :class:`~hs_py.ontology.namespace.Namespace` for
        ``defs`` and ``libs`` operations.
    :param prefix: URL path prefix for all Haystack routes (default ``"/api"``).
    :returns: Configured :class:`fastapi.FastAPI` application.

    Example::

        from hs_py.fastapi_server import create_fastapi_app
        from hs_py.storage.memory import InMemoryAdapter
        from hs_py.auth_types import SimpleAuthenticator

        storage = InMemoryAdapter()
        auth = SimpleAuthenticator({"admin": "secret"})
        app = create_fastapi_app(storage=storage, authenticator=auth)
        # uvicorn.run(app, host="0.0.0.0", port=8080)
    """
    if ops is None:
        ops = HaystackOps(storage=storage, namespace=namespace)

    app = FastAPI(title="Haystack Server", lifespan=_lifespan)
    app.state.ops = ops
    app.state.storage = storage

    if authenticator is not None:
        app.add_middleware(ScramAuthMiddleware, authenticator=authenticator)

    @app.middleware("http")
    async def error_and_security_headers(request: Request, call_next: Any) -> StarletteResponse:
        """Catch unhandled exceptions and add security headers.

        This outermost middleware mirrors the aiohttp ``_error_middleware``:
        Haystack-specific errors become error-grid responses, unexpected
        exceptions produce a generic error grid, and every response gets
        standard security headers.
        """
        try:
            response: StarletteResponse = await call_next(request)
        except HaystackError as exc:
            response = _grid_response(Grid.make_error(str(exc)), request)
        except Exception as exc:
            _log.exception("Unhandled exception in request handler")
            response = _grid_response(
                Grid.make_error(f"Internal error: {type(exc).__name__}"), request
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    app.add_exception_handler(HaystackError, _haystack_error_handler)

    prefix = prefix.rstrip("/")
    app.include_router(_build_router(), prefix=prefix)

    return app
