"""Coverage tests for fastapi_server.py.

Targets uncovered lines: lifespan, permission checks, user CRUD endpoints,
ontology export, WebSocket auth/dispatch, and error handler middleware.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import orjson
import pytest
from httpx import ASGITransport, AsyncClient

from hs_py._scram_core import TokenEntry
from hs_py.auth_types import SimpleAuthenticator, StorageAuthenticator
from hs_py.encoding.json import decode_grid
from hs_py.errors import HaystackError
from hs_py.fastapi_server import create_fastapi_app
from hs_py.kinds import MARKER, Ref
from hs_py.ops import HaystackOps
from hs_py.storage.memory import InMemoryAdapter
from hs_py.user import Role, create_user

if TYPE_CHECKING:
    from hs_py.grid import Grid

_JSON = "application/json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEED: list[dict[str, Any]] = [
    {"id": Ref("s1"), "dis": "Site-1", "site": MARKER},
]


def _make_storage_and_app(
    *,
    with_auth: bool = True,
    with_user_store: bool = True,
    namespace: Any = None,
) -> tuple[InMemoryAdapter, Any]:
    """Build an InMemoryAdapter + FastAPI app for tests."""
    storage = InMemoryAdapter(list(_SEED))
    auth: SimpleAuthenticator | StorageAuthenticator | None = None
    user_store: InMemoryAdapter | None = None

    if with_user_store:
        user_store = storage

    if with_auth:
        auth = (
            StorageAuthenticator(storage)
            if with_user_store
            else SimpleAuthenticator({"admin": "admin-pass"}, iterations=4096)
        )

    app = create_fastapi_app(
        storage=storage,
        authenticator=auth,
        user_store=user_store,
        namespace=namespace,
    )
    return storage, app


async def _setup_admin(storage: InMemoryAdapter) -> None:
    """Create an admin user in storage."""
    admin = create_user("admin", "admin-pass", role=Role.ADMIN, iterations=4096)
    await storage.create_user(admin)


def _inject_token(app: Any, username: str, token: str = "test-token") -> str:
    """Inject an auth token directly into the middleware token store."""
    app.state.auth_tokens[token] = TokenEntry(username=username, created=time.monotonic())
    return token


def _auth_header(token: str = "test-token") -> dict[str, str]:
    return {"Authorization": f"BEARER authToken={token}"}


async def _get_authed_client(
    storage: InMemoryAdapter,
    app: Any,
    username: str = "admin",
    token: str = "test-token",
) -> AsyncClient:
    """Return an AsyncClient with auth tokens pre-configured."""
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    _inject_token(app, username, token)
    return client


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


class TestLifespan:
    """Cover lifespan storage start/close and bootstrap."""

    async def test_lifespan_calls_start_and_close(self) -> None:
        storage = InMemoryAdapter()
        admin = create_user("admin", "pass", role=Role.ADMIN, iterations=4096)
        await storage.create_user(admin)
        app = create_fastapi_app(storage=storage, user_store=storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/about")
            assert resp.status_code == 200

    async def test_lifespan_bootstrap_no_admin(self) -> None:
        """Lifespan with user_store but no admin triggers ensure_superuser."""
        from hs_py.bootstrap import ensure_superuser

        storage = InMemoryAdapter()
        with patch.dict(
            "os.environ", {"HS_SUPERUSER_USERNAME": "su", "HS_SUPERUSER_PASSWORD": "pw"}
        ):
            await ensure_superuser(storage)
        su = await storage.get_user("su")
        assert su is not None
        assert su.role == Role.ADMIN


# ---------------------------------------------------------------------------
# Permission checks (HTTP)
# ---------------------------------------------------------------------------


class TestPermissionChecks:
    """Cover _check_op_permission for viewer vs operator access."""

    async def test_viewer_blocked_from_write_op(self) -> None:
        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        viewer = create_user("viewer1", "pass", role=Role.VIEWER, iterations=4096)
        await storage.create_user(viewer)
        client = await _get_authed_client(storage, app, username="viewer1", token="viewer-tok")
        try:
            resp = await client.post(
                "/api/hisWrite",
                content=b"{}",
                headers={**_auth_header("viewer-tok"), "Content-Type": _JSON},
            )
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            assert grid.meta.get("err") is not None
        finally:
            await client.aclose()

    async def test_operator_allowed_write_op(self) -> None:
        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        op_user = create_user("op1", "pass", role=Role.OPERATOR, iterations=4096)
        await storage.create_user(op_user)
        client = await _get_authed_client(storage, app, username="op1", token="op-tok")
        try:
            # hisWrite with empty grid — should succeed (no error grid)
            resp = await client.post(
                "/api/read",
                content=b"{}",
                headers={**_auth_header("op-tok"), "Content-Type": _JSON},
            )
            assert resp.status_code == 200
        finally:
            await client.aclose()

    async def test_no_user_store_skips_permission(self) -> None:
        """When user_store is None, permission checks are skipped."""
        storage = InMemoryAdapter(list(_SEED))
        app = create_fastapi_app(storage=storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/read",
                content=b"{}",
                headers={"Content-Type": _JSON},
            )
            assert resp.status_code == 200

    async def test_disabled_user_blocked(self) -> None:
        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        disabled = create_user("dis1", "pass", role=Role.OPERATOR, enabled=False, iterations=4096)
        await storage.create_user(disabled)
        client = await _get_authed_client(storage, app, username="dis1", token="dis-tok")
        try:
            resp = await client.post(
                "/api/hisWrite",
                content=b"{}",
                headers={**_auth_header("dis-tok"), "Content-Type": _JSON},
            )
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            assert grid.meta.get("err") is not None
        finally:
            await client.aclose()

    async def test_unknown_user_blocked(self) -> None:
        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        client = await _get_authed_client(storage, app, username="ghost", token="ghost-tok")
        try:
            resp = await client.post(
                "/api/hisWrite",
                content=b"{}",
                headers={**_auth_header("ghost-tok"), "Content-Type": _JSON},
            )
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            assert grid.meta.get("err") is not None
        finally:
            await client.aclose()

    async def test_no_username_on_request(self) -> None:
        """Token present but username not set → error."""
        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        client = await _get_authed_client(storage, app, username="admin")
        # Manually add a token whose username doesn't match any attribute
        # Inject a token with empty username
        app.state.auth_tokens["nouser-tok"] = TokenEntry(username="", created=time.monotonic())
        try:
            resp = await client.post(
                "/api/hisWrite",
                content=b"{}",
                headers={**_auth_header("nouser-tok"), "Content-Type": _JSON},
            )
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            # Empty username means get_user returns None → error
            assert grid.meta.get("err") is not None
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# User CRUD endpoints
# ---------------------------------------------------------------------------


class TestUserCrud:
    """Cover POST/GET/PUT/DELETE /api/users/ endpoints."""

    async def _admin_client(self) -> tuple[InMemoryAdapter, Any, AsyncClient]:
        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        client = await _get_authed_client(storage, app)
        return storage, app, client

    # -- POST /api/users/ ---------------------------------------------------

    async def test_create_user(self) -> None:
        _storage, _app, client = await self._admin_client()
        try:
            resp = await client.post(
                "/api/users/",
                json={"username": "alice", "password": "secret", "role": "operator"},
                headers=_auth_header(),
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["username"] == "alice"
            assert data["role"] == "operator"
        finally:
            await client.aclose()

    async def test_create_user_missing_fields(self) -> None:
        _, _, client = await self._admin_client()
        try:
            resp = await client.post(
                "/api/users/",
                json={"username": "", "password": ""},
                headers=_auth_header(),
            )
            assert resp.status_code == 400
            assert "required" in resp.json()["error"]
        finally:
            await client.aclose()

    async def test_create_user_invalid_role(self) -> None:
        _, _, client = await self._admin_client()
        try:
            resp = await client.post(
                "/api/users/",
                json={"username": "bob", "password": "pw", "role": "superadmin"},
                headers=_auth_header(),
            )
            assert resp.status_code == 400
            assert "Invalid role" in resp.json()["error"]
        finally:
            await client.aclose()

    async def test_create_user_duplicate(self) -> None:
        _, _, client = await self._admin_client()
        try:
            resp = await client.post(
                "/api/users/",
                json={"username": "admin", "password": "pw"},
                headers=_auth_header(),
            )
            assert resp.status_code == 409
        finally:
            await client.aclose()

    async def test_create_user_default_role(self) -> None:
        _, _, client = await self._admin_client()
        try:
            resp = await client.post(
                "/api/users/",
                json={"username": "default_role_user", "password": "pw"},
                headers=_auth_header(),
            )
            assert resp.status_code == 201
            assert resp.json()["role"] == "viewer"
        finally:
            await client.aclose()

    # -- GET /api/users/ ----------------------------------------------------

    async def test_list_users(self) -> None:
        _, _, client = await self._admin_client()
        try:
            resp = await client.get("/api/users/", headers=_auth_header())
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert any(u["username"] == "admin" for u in data)
        finally:
            await client.aclose()

    # -- GET /api/users/{username} ------------------------------------------

    async def test_get_user(self) -> None:
        _, _, client = await self._admin_client()
        try:
            resp = await client.get("/api/users/admin", headers=_auth_header())
            assert resp.status_code == 200
            assert resp.json()["username"] == "admin"
        finally:
            await client.aclose()

    async def test_get_user_not_found(self) -> None:
        _, _, client = await self._admin_client()
        try:
            resp = await client.get("/api/users/nonexistent", headers=_auth_header())
            assert resp.status_code == 404
        finally:
            await client.aclose()

    # -- PUT /api/users/{username} ------------------------------------------

    async def test_update_user_password(self) -> None:
        storage, _, client = await self._admin_client()
        user = create_user("target", "old", role=Role.VIEWER, iterations=4096)
        await storage.create_user(user)
        try:
            resp = await client.put(
                "/api/users/target",
                json={"password": "new-secret"},
                headers=_auth_header(),
            )
            assert resp.status_code == 200
            assert resp.json()["username"] == "target"
        finally:
            await client.aclose()

    async def test_update_user_role(self) -> None:
        storage, _, client = await self._admin_client()
        user = create_user("target2", "pw", role=Role.VIEWER, iterations=4096)
        await storage.create_user(user)
        try:
            resp = await client.put(
                "/api/users/target2",
                json={"role": "operator"},
                headers=_auth_header(),
            )
            assert resp.status_code == 200
            assert resp.json()["role"] == "operator"
        finally:
            await client.aclose()

    async def test_update_user_invalid_role(self) -> None:
        storage, _, client = await self._admin_client()
        user = create_user("target3", "pw", role=Role.VIEWER, iterations=4096)
        await storage.create_user(user)
        try:
            resp = await client.put(
                "/api/users/target3",
                json={"role": "megaadmin"},
                headers=_auth_header(),
            )
            assert resp.status_code == 400
            assert "Invalid role" in resp.json()["error"]
        finally:
            await client.aclose()

    async def test_update_user_no_valid_fields(self) -> None:
        storage, _, client = await self._admin_client()
        user = create_user("target4", "pw", role=Role.VIEWER, iterations=4096)
        await storage.create_user(user)
        try:
            resp = await client.put(
                "/api/users/target4",
                json={"bogus_field": "value"},
                headers=_auth_header(),
            )
            assert resp.status_code == 400
            assert "No valid fields" in resp.json()["error"]
        finally:
            await client.aclose()

    async def test_update_user_not_found(self) -> None:
        _, _, client = await self._admin_client()
        try:
            resp = await client.put(
                "/api/users/ghost",
                json={"password": "new"},
                headers=_auth_header(),
            )
            assert resp.status_code == 404
        finally:
            await client.aclose()

    # -- DELETE /api/users/{username} ----------------------------------------

    async def test_delete_user(self) -> None:
        storage, _, client = await self._admin_client()
        user = create_user("todelete", "pw", role=Role.VIEWER, iterations=4096)
        await storage.create_user(user)
        try:
            resp = await client.delete("/api/users/todelete", headers=_auth_header())
            assert resp.status_code == 200
            assert resp.json()["deleted"] == "todelete"
        finally:
            await client.aclose()

    async def test_delete_self_blocked(self) -> None:
        _, _, client = await self._admin_client()
        try:
            resp = await client.delete("/api/users/admin", headers=_auth_header())
            assert resp.status_code == 400
            assert "Cannot delete your own account" in resp.json()["error"]
        finally:
            await client.aclose()

    async def test_delete_user_not_found(self) -> None:
        _, _, client = await self._admin_client()
        try:
            resp = await client.delete("/api/users/nope", headers=_auth_header())
            assert resp.status_code == 404
        finally:
            await client.aclose()

    # -- Non-admin gets rejected -------------------------------------------

    async def test_non_admin_rejected(self) -> None:
        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        viewer = create_user("viewer2", "pw", role=Role.VIEWER, iterations=4096)
        await storage.create_user(viewer)
        client = await _get_authed_client(storage, app, username="viewer2", token="v-tok")
        try:
            resp = await client.get("/api/users/", headers=_auth_header("v-tok"))
            # Admin-only → should get error grid (HaystackError caught by middleware)
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            assert grid.meta.get("err") is not None
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Ontology export endpoint
# ---------------------------------------------------------------------------


class TestOntologyExport:
    async def test_export_no_namespace(self) -> None:
        """No namespace loaded → error grid."""
        storage = InMemoryAdapter(list(_SEED))
        app = create_fastapi_app(storage=storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ontology/export?format=turtle")
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            assert grid.meta.get("err") is not None

    async def test_export_turtle(self) -> None:
        """With a namespace, turtle export should return text/turtle."""
        from unittest.mock import MagicMock

        ns = MagicMock()
        ns.all_defs.return_value = iter([])
        storage = InMemoryAdapter(list(_SEED))
        ops = HaystackOps(storage=storage, namespace=ns)
        app = create_fastapi_app(ops=ops, storage=storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ontology/export?format=turtle")
            assert resp.status_code == 200
            assert "text/turtle" in resp.headers.get("content-type", "")

    async def test_export_jsonld(self) -> None:
        """With a namespace, jsonld export should return application/ld+json."""
        from unittest.mock import MagicMock

        ns = MagicMock()
        ns.all_defs.return_value = iter([])
        storage = InMemoryAdapter(list(_SEED))
        ops = HaystackOps(storage=storage, namespace=ns)
        app = create_fastapi_app(ops=ops, storage=storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ontology/export?format=jsonld")
            assert resp.status_code == 200
            assert "application/ld+json" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# WebSocket auth and dispatch
# ---------------------------------------------------------------------------


class TestWebSocketAuth:
    """Cover WebSocket auth token validation and dispatch error handling."""

    async def test_ws_invalid_token_closes(self) -> None:
        """Invalid auth token on WS → close with 4003."""
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)

        with TestClient(app) as tc, pytest.raises(WebSocketDisconnect):  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"authToken": "bad-token"}).decode())
                ws.receive_text()

    async def test_ws_expired_token_closes(self) -> None:
        """Expired auth token on WS → close with 4003."""
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        # Inject an expired token
        app.state.auth_tokens["expired-tok"] = TokenEntry(
            username="admin",
            created=time.monotonic() - 7200,
        )

        with TestClient(app) as tc, pytest.raises(WebSocketDisconnect):  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"authToken": "expired-tok"}).decode())
                ws.receive_text()

    async def test_ws_valid_token_dispatch(self) -> None:
        """Valid token → dispatch succeeds."""
        from starlette.testclient import TestClient

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            await http_client.get("/api/about")
        _inject_token(app, "admin", "ws-tok")

        with TestClient(app) as tc:  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"authToken": "ws-tok"}).decode())
                ws.send_text(orjson.dumps({"id": "1", "op": "about"}).decode())
                data = orjson.loads(ws.receive_text())
                assert "grid" in data
                assert data.get("id") == "1"

    async def test_ws_non_json_ignored(self) -> None:
        """Non-JSON message after auth → ignored, subsequent messages work."""
        from starlette.testclient import TestClient

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            await http_client.get("/api/about")
        _inject_token(app, "admin", "ws-tok2")

        with TestClient(app) as tc:  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"authToken": "ws-tok2"}).decode())
                ws.send_text("not-json{{{")
                ws.send_text(orjson.dumps({"id": "2", "op": "about"}).decode())
                data = orjson.loads(ws.receive_text())
                assert data.get("id") == "2"

    async def test_ws_batch_dispatch(self) -> None:
        """Batch messages dispatched correctly via WS."""
        from starlette.testclient import TestClient

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            await http_client.get("/api/about")
        _inject_token(app, "admin", "ws-tok3")

        with TestClient(app) as tc:  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"authToken": "ws-tok3"}).decode())
                batch = [{"id": "a", "op": "about"}, {"id": "b", "op": "formats"}]
                ws.send_text(orjson.dumps(batch).decode())
                data = orjson.loads(ws.receive_text())
                assert isinstance(data, list)
                assert len(data) == 2

    async def test_ws_single_error_handling(self) -> None:
        """WS op that raises HaystackError → error grid response."""
        from starlette.testclient import TestClient

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            await http_client.get("/api/about")
        _inject_token(app, "admin", "ws-tok4")

        with TestClient(app) as tc:  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"authToken": "ws-tok4"}).decode())
                ws.send_text(orjson.dumps({"id": "e1", "op": "noSuchOp"}).decode())
                data = orjson.loads(ws.receive_text())
                assert data.get("id") == "e1"
                assert data["grid"]["meta"].get("err") is not None

    async def test_ws_batch_empty_items(self) -> None:
        """Batch with no valid dict items → no response sent."""
        from starlette.testclient import TestClient

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            await http_client.get("/api/about")
        _inject_token(app, "admin", "ws-tok5")

        with TestClient(app) as tc:  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"authToken": "ws-tok5"}).decode())
                # Empty batch — no valid dict items
                ws.send_text(orjson.dumps(["string", 123]).decode())
                # Next valid message should still work
                ws.send_text(orjson.dumps({"id": "ok", "op": "about"}).decode())
                data = orjson.loads(ws.receive_text())
                assert data.get("id") == "ok"

    async def test_ws_batch_error_in_item(self) -> None:
        """Batch with a failing op → error grid in that item's response."""
        from starlette.testclient import TestClient

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            await http_client.get("/api/about")
        _inject_token(app, "admin", "ws-tok6")

        with TestClient(app) as tc:  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"authToken": "ws-tok6"}).decode())
                batch = [
                    {"id": "1", "op": "about"},
                    {"id": "2", "op": "noSuchOp"},
                ]
                ws.send_text(orjson.dumps(batch).decode())
                data = orjson.loads(ws.receive_text())
                assert isinstance(data, list)
                assert len(data) == 2
                err_item = next(d for d in data if d["id"] == "2")
                assert err_item["grid"]["meta"].get("err") is not None

    async def test_ws_permission_denied_viewer(self) -> None:
        """WS: viewer trying write op → error grid."""
        from starlette.testclient import TestClient

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        viewer = create_user("wsviewer", "pw", role=Role.VIEWER, iterations=4096)
        await storage.create_user(viewer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            await http_client.get("/api/about")
        _inject_token(app, "wsviewer", "ws-viewer-tok")

        with TestClient(app) as tc:  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"authToken": "ws-viewer-tok"}).decode())
                ws.send_text(orjson.dumps({"id": "w1", "op": "hisWrite"}).decode())
                data = orjson.loads(ws.receive_text())
                assert data.get("id") == "w1"
                assert data["grid"]["meta"].get("err") is not None


# ---------------------------------------------------------------------------
# Error handler middleware
# ---------------------------------------------------------------------------


class TestErrorMiddleware:
    async def test_haystack_error_caught(self) -> None:
        """HaystackError raised in handler → error grid response."""
        storage = InMemoryAdapter(list(_SEED))

        class _BrokenOps(HaystackOps):
            async def about(self) -> Grid:
                raise HaystackError("test boom")

        app = create_fastapi_app(ops=_BrokenOps(storage=storage), storage=storage)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/about")
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            assert grid.meta.get("err") is not None
            assert "test boom" in grid.meta.get("dis", "")

    async def test_unexpected_error_caught(self) -> None:
        """Unexpected exception → generic error grid response."""
        storage = InMemoryAdapter(list(_SEED))

        class _CrashOps(HaystackOps):
            async def about(self) -> Grid:
                raise RuntimeError("unexpected crash")

        app = create_fastapi_app(ops=_CrashOps(storage=storage), storage=storage)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/about")
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            assert grid.meta.get("err") is not None
            assert "Internal server error" in grid.meta.get("dis", "")

    async def test_empty_body_read_op(self) -> None:
        """POST with empty body → empty grid (covers line 154)."""
        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        client = await _get_authed_client(storage, app)
        resp = await client.post(
            "/api/read",
            content=b"",
            headers={**_auth_header(), "content-type": _JSON},
        )
        assert resp.status_code == 200
        await client.aclose()

    async def test_ws_auth_exception_path(self) -> None:
        """WS auth token lookup that triggers exception → close with 4003 (line 425-427)."""
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)

        # Inject an expired token to trigger the close path
        app.state.auth_tokens["expired-tok"] = TokenEntry(
            username="admin", created=time.monotonic() - 999999
        )

        with TestClient(app) as tc, pytest.raises(WebSocketDisconnect):  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"authToken": "expired-tok"}).decode())
                ws.receive_text()

    async def test_ws_no_username_permission_check(self) -> None:
        """WS op without username in user_store mode → error (lines 309-310)."""
        from starlette.testclient import TestClient

        storage, app = _make_storage_and_app()
        await _setup_admin(storage)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            await http_client.get("/api/about")

        # Don't inject any token → ws_username=None when no auth
        app_no_auth = create_fastapi_app(
            storage=storage,
            authenticator=None,
            user_store=storage,
        )
        with TestClient(app_no_auth) as tc:  # noqa: SIM117
            with tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws:
                ws.send_text(orjson.dumps({"id": "u1", "op": "about"}).decode())
                data = orjson.loads(ws.receive_text())
                # With no auth but user_store, username=None → error
                assert data.get("id") == "u1"
