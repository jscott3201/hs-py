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
from hs_py.encoding.json import encode_grid as encode_grid_json
from hs_py.errors import HaystackError
from hs_py.grid import Grid
from hs_py.ops import _POST_OP_METHODS, HaystackOps, dispatch_op

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.types import ASGIApp, Receive, Scope, Send

    from hs_py.auth_types import Authenticator
    from hs_py.ontology.namespace import Namespace
    from hs_py.storage.protocol import StorageAdapter, UserStore

__all__ = [
    "ScramAuthMiddleware",
    "create_fastapi_app",
]

_log = logging.getLogger(__name__)

_GET_OPS = ("about", "ops", "formats", "close")
_POST_OPS = tuple(_POST_OP_METHODS.keys())

# Maximum request body size (16 MiB)
_MAX_BODY_SIZE = 16 * 1024 * 1024

# Maximum entries in response/grid caches.
_MAX_CACHE_SIZE = 2048

# Maximum number of items in a WebSocket batch request.
_MAX_BATCH_SIZE = 1000

# Ops that mutate state and should invalidate read caches.
_MUTATION_OPS = frozenset({"hisWrite", "pointWrite", "invokeAction"})

_401_HEADERS = {"WWW-Authenticate": "SCRAM hash=SHA-256"}


# ---------------------------------------------------------------------------
# SCRAM auth middleware (pure ASGI)
# ---------------------------------------------------------------------------


class ScramAuthMiddleware:
    """SCRAM-SHA-256 authentication middleware for FastAPI.

    Pure ASGI middleware — avoids ``BaseHTTPMiddleware`` overhead and streaming
    issues.  Implements the Haystack SCRAM handshake:

    1. Client sends ``Authorization: HELLO username=<b64>``
    2. Server returns 401 with ``WWW-Authenticate: SCRAM handshakeToken=...``
    3. Client sends ``Authorization: SCRAM handshakeToken=..., data=<client-first>``
    4. Server returns 401 with server-first message
    5. Client sends ``Authorization: SCRAM handshakeToken=..., data=<client-final>``
    6. Server returns 200 with ``Authentication-Info: authToken=..., data=<server-final>``
    7. Subsequent requests use ``Authorization: BEARER authToken=...``

    :param app: The ASGI application to wrap.
    :param authenticator: Server-side credential store.
    :param auth_tokens: Shared token dict (also used by the WS endpoint).
    """

    def __init__(
        self,
        app: ASGIApp,
        authenticator: Authenticator,
        auth_tokens: dict[str, TokenEntry] | None = None,
    ) -> None:
        self.app = app
        self._authenticator = authenticator
        self._handshakes: dict[str, HandshakeState] = {}
        self._tokens: dict[str, TokenEntry] = auth_tokens if auth_tokens is not None else {}

    @property
    def tokens(self) -> dict[str, TokenEntry]:
        """Expose the token store (read-only access for tests)."""
        return self._tokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entry point — intercept HTTP requests for auth."""
        if scope["type"] != "http":
            # Pass WebSocket and lifespan through unchanged
            await self.app(scope, receive, send)
            return

        # Extract Authorization header from raw ASGI scope
        auth_header = ""
        for key, value in scope.get("headers", []):
            if key == b"authorization":
                auth_header = value.decode("latin-1")
                break

        scheme = auth_header.split()[0].upper() if auth_header else ""

        if scheme == "HELLO":
            result = await scram_hello(self._authenticator, self._handshakes, auth_header)
            response = StarletteResponse(
                status_code=result.status, headers=result.headers, content=result.body
            )
            await response(scope, receive, send)
            return

        if scheme == "SCRAM":
            result = handle_scram(self._handshakes, self._tokens, auth_header)
            response = StarletteResponse(
                status_code=result.status, headers=result.headers, content=result.body
            )
            await response(scope, receive, send)
            return

        if scheme == "BEARER":
            bearer_result = validate_bearer(self._tokens, auth_header)
            if bearer_result is not None:
                response = StarletteResponse(
                    status_code=bearer_result.status,
                    headers=bearer_result.headers,
                    content=bearer_result.body,
                )
                await response(scope, receive, send)
                return
            # Attach authenticated username to scope state for downstream
            params = dict(
                p.split("=", 1) for p in auth_header.split(None, 1)[1].split(",") if "=" in p
            )
            token = params.get("authToken", "").strip()
            entry = self._tokens.get(token)
            if entry is not None:
                scope.setdefault("state", {})["username"] = entry.username
            await self.app(scope, receive, send)
            return

        # No recognized auth scheme → 401
        response = StarletteResponse(status_code=401, headers=_401_HEADERS)
        await response(scope, receive, send)


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


def _cached_grid_response(grid: Grid, request: Request, cache_key: str) -> Response:
    """Encode a Grid with per-format caching on app.state._response_cache."""
    accept = request.headers.get("accept", "application/json")
    fmt = negotiate_format(accept)
    key = (cache_key, fmt)
    cache: dict[tuple[str, str], tuple[bytes, str]] = request.app.state._response_cache
    cached = cache.get(key)
    if cached is not None:
        return Response(content=cached[0], media_type=cached[1])
    body, ct = encode_response(grid, fmt)
    if len(cache) < _MAX_CACHE_SIZE:
        cache[key] = (body, ct)
    return Response(content=body, media_type=ct)


# ---------------------------------------------------------------------------
# WebSocket encoding helpers
# ---------------------------------------------------------------------------


def _ws_encode_grid(grid: Grid) -> bytes:
    """Encode a grid to JSON bytes using the fast orjson path."""
    return encode_grid_json(grid)


def _ws_cached_grid_bytes(
    app: Any,
    op: str,
    msg: dict[str, Any],
    grid: Grid,
) -> bytes:
    """Return cached grid bytes for read ops, encode otherwise."""
    cache: dict[str, bytes] = app.state._ws_grid_cache
    # Invalidate cache on mutation ops
    if op in _MUTATION_OPS and cache:
        cache.clear()
    if op == "read":
        grid_data = msg.get("grid")
        if isinstance(grid_data, dict):
            rows = grid_data.get("rows", [])
            if rows and isinstance(rows[0], dict):
                filt = rows[0].get("filter", "")
                limit = rows[0].get("limit", "")
                key = f"ws_read:{filt}:{limit}"
                cached = cache.get(key)
                if cached is not None:
                    return cached
                grid_bytes = _ws_encode_grid(grid)
                if len(cache) < _MAX_CACHE_SIZE:
                    cache[key] = grid_bytes
                return grid_bytes
    return _ws_encode_grid(grid)


def _ws_envelope(grid_bytes: bytes, req_id: Any = None) -> bytes:
    """Build a JSON envelope around pre-encoded grid bytes."""
    if req_id is not None:
        id_bytes = orjson.dumps(req_id)
        return b'{"grid":' + grid_bytes + b',"id":' + id_bytes + b"}"
    return b'{"grid":' + grid_bytes + b"}"


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
    grid = Grid.make_error("Internal server error")
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
    # Bootstrap superuser if a user store is available
    user_store: UserStore | None = getattr(app.state, "user_store", None)
    if user_store is not None:
        from hs_py.bootstrap import ensure_superuser

        await ensure_superuser(user_store)
    yield
    if storage is not None:
        await storage.close()


# ---------------------------------------------------------------------------
# Role-based access control helpers
# ---------------------------------------------------------------------------


async def _check_op_permission(request: Request, op_name: str) -> None:
    """Verify the authenticated user has permission for the given op.

    Raises :class:`~hs_py.errors.HaystackError` if the user lacks the
    required role.  When no ``user_store`` is configured on the app,
    permission checks are skipped (open access).

    :param request: The incoming HTTP request.
    :param op_name: The Haystack op name (e.g. ``"hisWrite"``).
    """
    user_store: UserStore | None = getattr(request.app.state, "user_store", None)
    if user_store is None:
        return  # No user store → no role enforcement

    from hs_py.user import WRITE_OPS, Role

    username: str | None = getattr(request.state, "username", None)
    if username is None:
        raise HaystackError("Authentication required")

    user = await user_store.get_user(username)
    if user is None or not user.enabled:
        raise HaystackError("Authentication required")

    if op_name in WRITE_OPS and user.role < Role.OPERATOR:
        raise HaystackError(f"Insufficient permissions: {op_name} requires operator or admin role")


# ---------------------------------------------------------------------------
# Route handler factories
# ---------------------------------------------------------------------------


def _make_get_handler(op_name: str) -> Any:
    """Create a GET handler for a named Haystack op."""

    async def handler(request: Request) -> Response:
        ops = _get_ops(request)
        if op_name == "about":
            grid = await ops.about()
            return _cached_grid_response(grid, request, "about")
        elif op_name == "ops":
            grid = await ops.ops()
            return _cached_grid_response(grid, request, "ops")
        elif op_name == "formats":
            grid = await ops.formats()
            return _cached_grid_response(grid, request, "formats")
        elif op_name == "close":
            await _check_op_permission(request, "close")
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
        await _check_op_permission(request, op_name)
        ops = _get_ops(request)
        req_grid = await _parse_grid(request)
        method = getattr(ops, method_name)
        result_grid: Grid = await method(req_grid)
        # Invalidate read caches on mutation ops
        if op_name in _MUTATION_OPS:
            cache: dict[Any, Any] | None = getattr(request.app.state, "_response_cache", None)
            if cache:
                cache.clear()
            ws_cache: dict[str, bytes] | None = getattr(request.app.state, "_ws_grid_cache", None)
            if ws_cache:
                ws_cache.clear()
        return _grid_response(result_grid, request)

    handler.__name__ = f"{op_name}_post_handler"
    return handler


def _make_read_handler() -> Any:
    """Create a cached POST handler for the read op.

    Read responses are cached by (filter, limit, format) since entity data
    is typically stable between mutations.  The cache is stored on
    ``app.state._response_cache`` and is cleared on entity mutations.
    """

    async def handler(request: Request) -> Response:
        await _check_op_permission(request, "read")
        ops = _get_ops(request)
        req_grid = await _parse_grid(request)
        result_grid: Grid = await ops.read(req_grid)

        # Build a cache key from the filter/limit in the request grid.
        cache: dict[tuple[str, str], tuple[bytes, str]] | None = getattr(
            request.app.state, "_response_cache", None
        )
        if cache is not None and req_grid.rows:
            first = req_grid[0]
            filter_str = first.get("filter", "")
            limit_val = first.get("limit", "")
            cache_key = f"read:{filter_str}:{limit_val}"
            return _cached_grid_response(result_grid, request, cache_key)

        return _grid_response(result_grid, request)

    handler.__name__ = "read_post_handler"
    return handler


# ---------------------------------------------------------------------------
# WebSocket dispatch helpers
# ---------------------------------------------------------------------------


async def _check_ws_op_permission(app: FastAPI, username: str | None, op_name: str) -> None:
    """Check role permissions for a WebSocket op.

    :raises HaystackError: If the user lacks the required role.
    """
    user_store: UserStore | None = getattr(app.state, "user_store", None)
    if user_store is None:
        return

    from hs_py.user import WRITE_OPS, Role

    if username is None:
        raise HaystackError("Authentication required")

    user = await user_store.get_user(username)
    if user is None or not user.enabled:
        raise HaystackError("Authentication required")

    if op_name in WRITE_OPS and user.role < Role.OPERATOR:
        raise HaystackError(f"Insufficient permissions: {op_name} requires operator or admin role")


async def _handle_ws_single(
    websocket: WebSocket, ops: HaystackOps, msg: dict[str, Any], username: str | None = None
) -> None:
    """Dispatch a single WS message and send the response."""
    req_id = msg.get("id")
    op = msg.get("op", "")
    try:
        await _check_ws_op_permission(websocket.app, username, op)
        result_grid = await dispatch_op(ops, op, msg)
    except HaystackError as exc:
        result_grid = Grid.make_error(str(exc))
    except Exception:
        _log.exception("Unhandled error in WS op '%s'", op)
        result_grid = Grid.make_error("Internal server error")

    grid_bytes = _ws_cached_grid_bytes(websocket.app, op, msg, result_grid)
    payload = _ws_envelope(grid_bytes, req_id)
    await websocket.send_text(payload.decode())


async def _handle_ws_batch(
    websocket: WebSocket, ops: HaystackOps, batch: list[Any], username: str | None = None
) -> None:
    """Dispatch all ops in a batch concurrently, then send array response."""
    items = [item for item in batch if isinstance(item, dict)]
    if not items:
        return
    # Cap batch size to prevent resource exhaustion
    if len(items) > _MAX_BATCH_SIZE:
        items = items[:_MAX_BATCH_SIZE]

    async def _dispatch_item(item: dict[str, Any]) -> bytes:
        r_id = item.get("id")
        r_op = item.get("op", "")
        try:
            await _check_ws_op_permission(websocket.app, username, r_op)
            r_grid = await dispatch_op(ops, r_op, item)
        except HaystackError as exc:
            r_grid = Grid.make_error(str(exc))
        except Exception:
            _log.exception("Unhandled error in batch WS op '%s'", r_op)
            r_grid = Grid.make_error("Internal server error")
        grid_bytes = _ws_cached_grid_bytes(websocket.app, r_op, item, r_grid)
        return _ws_envelope(grid_bytes, r_id)

    item_bytes = await asyncio.gather(*[_dispatch_item(item) for item in items])
    payload = b"[" + b",".join(item_bytes) + b"]"
    await websocket.send_text(payload.decode())


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
        if op_name == "read":
            handler = _make_read_handler()
        else:
            handler = _make_post_handler(op_name, method_name)
        router.add_api_route(
            f"/{op_name}",
            handler,
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
            websocket.app.state, "auth_tokens", None
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
                ws_username: str | None = entry.username
            except Exception:
                await websocket.close(code=4003, reason="Authentication required")
                return
        else:
            ws_username = None

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
                    task = asyncio.create_task(_handle_ws_batch(websocket, ops, msg, ws_username))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                    continue

                # Single message — fire-and-forget via create_task
                if isinstance(msg, dict):
                    task = asyncio.create_task(_handle_ws_single(websocket, ops, msg, ws_username))
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
# User management endpoints
# ---------------------------------------------------------------------------


def _build_user_router(user_store: UserStore) -> APIRouter:
    """Build an APIRouter with user management CRUD endpoints."""
    from pydantic import BaseModel
    from starlette.responses import JSONResponse

    from hs_py.user import Role

    router = APIRouter(prefix="/users", tags=["users"])

    # -- Pydantic request/response models ------------------------------------

    class CreateUserRequest(BaseModel):
        """Request body for creating a new user."""

        username: str
        password: str
        first_name: str = ""
        last_name: str = ""
        email: str = ""
        role: str = "viewer"
        enabled: bool = True

    class UpdateUserRequest(BaseModel):
        """Request body for updating a user (all fields optional)."""

        password: str | None = None
        first_name: str | None = None
        last_name: str | None = None
        email: str | None = None
        role: str | None = None
        enabled: bool | None = None

    class UserResponse(BaseModel):
        """Public user representation (no credentials)."""

        username: str
        first_name: str
        last_name: str
        email: str
        role: str
        enabled: bool
        created_at: float
        updated_at: float

    # -- Dependencies --------------------------------------------------------

    async def _get_current_user(request: Request) -> Any:
        """FastAPI dependency: resolve authenticated user from request state.

        :returns: The :class:`~hs_py.user.User` object.
        :raises HaystackError: If not authenticated or user not found.
        """
        username: str | None = getattr(request.state, "username", None)
        if username is None:
            raise HaystackError("Authentication required")
        user = await user_store.get_user(username)
        if user is None or not user.enabled:
            raise HaystackError("Authentication required")
        return user

    async def _require_admin(request: Request) -> str:
        """Extract authenticated username and verify admin role.

        :returns: The authenticated username.
        :raises HaystackError: If not authenticated or not an admin.
        """
        user = await _get_current_user(request)
        if user.role != Role.ADMIN:
            raise HaystackError("Admin access required")
        return str(user.username)

    def _user_response(user: Any) -> UserResponse:
        """Convert a User to a Pydantic response model (no credentials)."""
        return UserResponse(
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            role=user.role.value,
            enabled=user.enabled,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    @router.get("/")
    async def list_users(request: Request) -> JSONResponse:
        """List all users (admin-only)."""
        await _require_admin(request)
        users = await user_store.list_users()
        return JSONResponse([_user_response(u).model_dump() for u in users])

    @router.get("/{username}")
    async def get_user(request: Request, username: str) -> JSONResponse:
        """Get a single user by username (admin-only)."""
        await _require_admin(request)
        user = await user_store.get_user(username)
        if user is None:
            return JSONResponse({"error": f"User not found: {username!r}"}, status_code=404)
        return JSONResponse(_user_response(user).model_dump())

    @router.post("/")
    async def create_user_endpoint(request: Request) -> JSONResponse:
        """Create a new user (admin-only)."""
        await _require_admin(request)
        raw = await request.json()
        try:
            body = CreateUserRequest(**raw)
        except Exception:
            return JSONResponse({"error": "Invalid request body"}, status_code=400)
        username = body.username.strip()
        password = body.password.strip()
        if not username or not password:
            return JSONResponse({"error": "username and password are required"}, status_code=400)

        from hs_py.user import create_user as _create_user

        try:
            role = Role(body.role)
        except ValueError:
            return JSONResponse(
                {"error": f"Invalid role: {body.role!r}. Must be admin, operator, or viewer"},
                status_code=400,
            )

        try:
            user = _create_user(
                username=username,
                password=password,
                first_name=body.first_name,
                last_name=body.last_name,
                email=body.email,
                role=role,
                enabled=body.enabled,
            )
            await user_store.create_user(user)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(_user_response(user).model_dump(), status_code=201)

    @router.put("/{username}")
    async def update_user_endpoint(request: Request, username: str) -> JSONResponse:
        """Update an existing user (admin-only)."""
        await _require_admin(request)
        raw = await request.json()
        try:
            body = UpdateUserRequest(**raw)
        except Exception:
            return JSONResponse({"error": "Invalid request body"}, status_code=400)
        fields: dict[str, Any] = {}
        for field_name in ("password", "first_name", "last_name", "email", "enabled"):
            val = getattr(body, field_name)
            if val is not None:
                fields[field_name] = val
        if body.role is not None:
            try:
                fields["role"] = Role(body.role)
            except ValueError:
                return JSONResponse(
                    {"error": f"Invalid role: {body.role!r}. Must be admin, operator, or viewer"},
                    status_code=400,
                )
        if not fields:
            return JSONResponse({"error": "No valid fields to update"}, status_code=400)

        try:
            updated = await user_store.update_user(username, **fields)
        except KeyError:
            return JSONResponse({"error": f"User not found: {username!r}"}, status_code=404)
        return JSONResponse(_user_response(updated).model_dump())

    @router.delete("/{username}")
    async def delete_user_endpoint(request: Request, username: str) -> JSONResponse:
        """Delete a user (admin-only)."""
        caller = await _require_admin(request)
        if username == caller:
            return JSONResponse({"error": "Cannot delete your own account"}, status_code=400)
        deleted = await user_store.delete_user(username)
        if not deleted:
            return JSONResponse({"error": f"User not found: {username!r}"}, status_code=404)
        return JSONResponse({"deleted": username})

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
    user_store: UserStore | None = None,
    prefix: str = "/api",
    cors_origins: list[str] | None = None,
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
    :param user_store: Optional :class:`~hs_py.storage.protocol.UserStore` for
        user management.  When provided, user CRUD endpoints are mounted under
        ``{prefix}/users/`` and superuser bootstrapping runs at startup.
    :param prefix: URL path prefix for all Haystack routes (default ``"/api"``).
    :param cors_origins: Optional list of allowed CORS origins.  When provided,
        ``CORSMiddleware`` is added with credentials support.  Example:
        ``["http://localhost:3000", "https://app.example.com"]``.
    :returns: Configured :class:`fastapi.FastAPI` application.

    Example::

        from hs_py.fastapi_server import create_fastapi_app
        from hs_py.storage.memory import InMemoryAdapter
        from hs_py.auth_types import StorageAuthenticator

        storage = InMemoryAdapter()
        auth = StorageAuthenticator(storage)
        app = create_fastapi_app(storage=storage, authenticator=auth, user_store=storage)
        # uvicorn.run(app, host="0.0.0.0", port=8080)
    """
    if ops is None:
        ops = HaystackOps(storage=storage, namespace=namespace)

    app = FastAPI(title="Haystack Server", lifespan=_lifespan)
    app.state.ops = ops
    app.state.storage = storage
    app.state.user_store = user_store
    app.state._response_cache = {}
    app.state._ws_grid_cache = {}

    # Shared token store — set on app.state before middleware so both the
    # SCRAM middleware and the WS endpoint reference the same dict.
    auth_tokens: dict[str, TokenEntry] | None = None
    if authenticator is not None:
        auth_tokens = {}
        app.state.auth_tokens = auth_tokens
        app.add_middleware(
            ScramAuthMiddleware, authenticator=authenticator, auth_tokens=auth_tokens
        )

    if cors_origins:
        from starlette.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

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
        except Exception:
            _log.exception("Unhandled exception in request handler")
            response = _grid_response(Grid.make_error("Internal server error"), request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    app.add_exception_handler(HaystackError, _haystack_error_handler)

    prefix = prefix.rstrip("/")
    app.include_router(_build_router(), prefix=prefix)

    if user_store is not None:
        app.include_router(_build_user_router(user_store), prefix=prefix)

    return app
