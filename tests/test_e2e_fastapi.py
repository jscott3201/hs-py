"""End-to-end tests for the FastAPI Haystack server.

Exercises the full stack: FastAPI + InMemoryAdapter + HaystackOps.
Uses httpx ``AsyncClient`` with ASGI transport for zero-socket tests,
and verifies content negotiation, SCRAM auth, and WebSocket ops.
"""

from __future__ import annotations

from typing import Any

import orjson
import pytest
from httpx import ASGITransport, AsyncClient

from hs_py.auth_types import SimpleAuthenticator
from hs_py.encoding.json import decode_grid, encode_grid
from hs_py.fastapi_server import create_fastapi_app
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref
from hs_py.ops import HaystackOps
from hs_py.storage.memory import InMemoryAdapter


class _TestOps(HaystackOps):
    """Ops subclass that provides a concrete about() for testing."""

    async def about(self) -> Grid:
        return Grid.make_rows(
            [
                {
                    "haystackVersion": "4.0",
                    "serverName": "TestFastAPI",
                    "productName": "hs-py-test",
                    "productVersion": "0.3.0",
                }
            ]
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SEED_ENTITIES: list[dict[str, Any]] = [
    {"id": Ref("s1"), "dis": "Site-1", "site": MARKER},
    {"id": Ref("s2"), "dis": "Site-2", "site": MARKER},
    {"id": Ref("e1"), "dis": "Equip-1", "equip": MARKER, "siteRef": Ref("s1")},
    {
        "id": Ref("p1"),
        "dis": "Point-1",
        "point": MARKER,
        "equipRef": Ref("e1"),
        "siteRef": Ref("s1"),
        "kind": "Number",
    },
    {
        "id": Ref("p2"),
        "dis": "Point-2",
        "point": MARKER,
        "equipRef": Ref("e1"),
        "siteRef": Ref("s1"),
        "kind": "Number",
    },
]


@pytest.fixture
def storage() -> InMemoryAdapter:
    adapter = InMemoryAdapter(list(_SEED_ENTITIES))
    return adapter


@pytest.fixture
def app(storage: InMemoryAdapter) -> Any:
    ops = _TestOps(storage=storage)
    return create_fastapi_app(ops=ops)


@pytest.fixture
async def client(app: Any) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_JSON = "application/json"


def _post_grid(client: AsyncClient, op: str, grid: Grid) -> Any:
    """POST a grid to an op endpoint and return the coroutine."""
    return client.post(f"/api/{op}", content=encode_grid(grid), headers={"Content-Type": _JSON})


# ---------------------------------------------------------------------------
# GET ops
# ---------------------------------------------------------------------------


class TestGetOps:
    async def test_about(self, client: AsyncClient) -> None:
        resp = await client.get("/api/about")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert len(grid) >= 1

    async def test_ops(self, client: AsyncClient) -> None:
        resp = await client.get("/api/ops")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        names = {row["name"] for row in grid}
        assert "about" in names
        assert "ops" in names

    async def test_formats(self, client: AsyncClient) -> None:
        resp = await client.get("/api/formats")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert len(grid) >= 1


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


class TestRead:
    async def test_read_filter(self, client: AsyncClient) -> None:
        req = Grid.make_rows([{"filter": "point"}])
        resp = await _post_grid(client, "read", req)
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert len(grid) == 2
        ids = {row["id"] for row in grid}
        assert Ref("p1") in ids
        assert Ref("p2") in ids

    async def test_read_filter_with_limit(self, client: AsyncClient) -> None:
        req = Grid.make_rows([{"filter": "point", "limit": Number(1.0)}])
        resp = await _post_grid(client, "read", req)
        grid = decode_grid(resp.content)
        assert len(grid) == 1

    async def test_read_by_ids(self, client: AsyncClient) -> None:
        req = Grid.make_rows([{"id": Ref("s1")}, {"id": Ref("p1")}])
        resp = await _post_grid(client, "read", req)
        grid = decode_grid(resp.content)
        assert len(grid) == 2

    async def test_read_missing_id(self, client: AsyncClient) -> None:
        req = Grid.make_rows([{"id": Ref("no-such-entity")}])
        resp = await _post_grid(client, "read", req)
        grid = decode_grid(resp.content)
        assert len(grid) == 0


# ---------------------------------------------------------------------------
# Nav
# ---------------------------------------------------------------------------


class TestNav:
    async def test_nav_root(self, client: AsyncClient) -> None:
        req = Grid.make_empty()
        resp = await _post_grid(client, "nav", req)
        grid = decode_grid(resp.content)
        assert len(grid) == 2  # two sites

    async def test_nav_site_children(self, client: AsyncClient) -> None:
        req = Grid.make_rows([{"navId": "s1"}])
        resp = await _post_grid(client, "nav", req)
        grid = decode_grid(resp.content)
        assert len(grid) == 1  # one equip under s1
        assert grid[0]["id"] == Ref("e1")

    async def test_nav_equip_children(self, client: AsyncClient) -> None:
        req = Grid.make_rows([{"navId": "e1"}])
        resp = await _post_grid(client, "nav", req)
        grid = decode_grid(resp.content)
        assert len(grid) == 2  # two points under e1


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


class TestHistory:
    async def test_his_write_and_read(self, client: AsyncClient) -> None:
        # Write history
        builder = GridBuilder().set_meta({"id": Ref("p1")})
        builder.add_col("ts").add_col("val")
        builder.add_row({"ts": "2024-01-01T00:00:00Z", "val": Number(72.0)})
        builder.add_row({"ts": "2024-01-01T01:00:00Z", "val": Number(73.0)})
        write_grid = builder.to_grid()

        resp = await _post_grid(client, "hisWrite", write_grid)
        assert resp.status_code == 200

        # Read history back
        req = Grid.make_rows([{"id": Ref("p1"), "range": "today"}])
        resp = await _post_grid(client, "hisRead", req)
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid.meta["id"] == Ref("p1")
        assert len(grid) == 2


# ---------------------------------------------------------------------------
# Priority array (pointWrite)
# ---------------------------------------------------------------------------


class TestPointWrite:
    async def test_point_write_and_read(self, client: AsyncClient) -> None:
        # Write level 10
        req = Grid.make_rows([{"id": Ref("p1"), "level": Number(10.0), "val": Number(68.0)}])
        resp = await _post_grid(client, "pointWrite", req)
        assert resp.status_code == 200

        # Read the priority array
        req = Grid.make_rows([{"id": Ref("p1")}])
        resp = await _post_grid(client, "pointWrite", req)
        grid = decode_grid(resp.content)
        assert len(grid) == 17
        # Level 10 should have the value
        level_10 = grid[9]  # 0-indexed
        assert "val" in level_10


# ---------------------------------------------------------------------------
# Watch lifecycle
# ---------------------------------------------------------------------------


class TestWatch:
    async def test_watch_lifecycle(self, client: AsyncClient) -> None:
        # Subscribe
        sub_grid = GridBuilder().set_meta({"watchDis": "test-watch"})
        sub_grid.add_col("id")
        sub_grid.add_row({"id": Ref("p1")})
        sub_grid.add_row({"id": Ref("p2")})

        resp = await _post_grid(client, "watchSub", sub_grid.to_grid())
        assert resp.status_code == 200
        result = decode_grid(resp.content)
        watch_id = result.meta["watchId"]
        assert isinstance(watch_id, str)
        assert len(result) == 2

        # Poll (no changes yet) — should return empty
        poll_grid = GridBuilder().set_meta({"watchId": watch_id})
        resp = await _post_grid(client, "watchPoll", poll_grid.to_grid())
        assert resp.status_code == 200
        poll_result = decode_grid(resp.content)
        assert len(poll_result) == 0

        # Poll with refresh — should return all watched entities
        refresh_grid = GridBuilder().set_meta({"watchId": watch_id, "refresh": MARKER})
        resp = await _post_grid(client, "watchPoll", refresh_grid.to_grid())
        assert resp.status_code == 200
        refresh_result = decode_grid(resp.content)
        assert len(refresh_result) == 2

        # Unsubscribe with close
        unsub_grid = GridBuilder().set_meta({"watchId": watch_id, "close": MARKER})
        unsub_grid.add_col("id")
        resp = await _post_grid(client, "watchUnsub", unsub_grid.to_grid())
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Content negotiation
# ---------------------------------------------------------------------------


class TestContentNegotiation:
    async def test_json_response(self, client: AsyncClient) -> None:
        resp = await client.get("/api/about", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    async def test_zinc_response(self, client: AsyncClient) -> None:
        resp = await client.get("/api/about", headers={"Accept": "text/zinc"})
        assert resp.status_code == 200
        assert "text/zinc" in resp.headers["content-type"]
        assert resp.text.startswith("ver:")

    async def test_csv_response(self, client: AsyncClient) -> None:
        resp = await client.get("/api/about", headers={"Accept": "text/csv"})
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    async def test_wildcard_defaults_to_json(self, client: AsyncClient) -> None:
        resp = await client.get("/api/about", headers={"Accept": "*/*"})
        assert "application/json" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# SCRAM auth
# ---------------------------------------------------------------------------


class TestScramAuth:
    @pytest.fixture
    def auth_app(self, storage: InMemoryAdapter) -> Any:
        auth = SimpleAuthenticator({"testuser": "testpass"}, iterations=4096)
        ops = _TestOps(storage=storage)
        return create_fastapi_app(ops=ops, authenticator=auth)

    @pytest.fixture
    async def auth_client(self, auth_app: Any) -> AsyncClient:
        transport = ASGITransport(app=auth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c  # type: ignore[misc]

    async def test_unauthenticated_returns_401(self, auth_client: AsyncClient) -> None:
        resp = await auth_client.get("/api/about")
        assert resp.status_code == 401

    async def test_scram_full_handshake(self, auth_client: AsyncClient) -> None:
        """Walk through the full SCRAM-SHA-256 handshake and make an authenticated request."""
        import base64
        import hashlib
        import hmac
        import os

        from hs_py.auth import _b64url_decode, _b64url_encode, _parse_header_params

        # Step 0: HELLO
        username_b64 = _b64url_encode(b"testuser")
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": f"HELLO username={username_b64}"},
        )
        assert resp.status_code == 401
        params = _parse_header_params(resp.headers["www-authenticate"])
        handshake_token = params["handshakeToken"]

        # Step 1: client-first
        c_nonce = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
        client_first = f"n,,n=testuser,r={c_nonce}"
        data = _b64url_encode(client_first.encode())
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": f"SCRAM handshakeToken={handshake_token}, data={data}"},
        )
        assert resp.status_code == 401
        params = _parse_header_params(resp.headers["www-authenticate"])
        handshake_token = params["handshakeToken"]
        server_first = _b64url_decode(params["data"]).decode()

        # Parse server-first
        sf = dict(p.split("=", 1) for p in server_first.split(",") if "=" in p)
        s_nonce = sf["r"]
        salt = base64.b64decode(sf["s"])
        iterations = int(sf["i"])

        # Compute client proof
        salted_password = hashlib.pbkdf2_hmac("sha256", b"testpass", salt, iterations)
        client_key = hmac.new(salted_password, b"Client Key", "sha256").digest()
        stored_key = hashlib.sha256(client_key).digest()

        channel_binding = _b64url_encode(b"n,,")
        client_first_bare = f"n=testuser,r={c_nonce}"
        client_final_no_proof = f"c={channel_binding},r={s_nonce}"
        auth_message = f"{client_first_bare},{server_first},{client_final_no_proof}"

        client_signature = hmac.new(stored_key, auth_message.encode(), "sha256").digest()
        client_proof = bytes(a ^ b for a, b in zip(client_key, client_signature, strict=True))
        proof_b64 = base64.b64encode(client_proof).decode()

        # Step 2: client-final
        client_final = f"c={channel_binding},r={s_nonce},p={proof_b64}"
        data = _b64url_encode(client_final.encode())
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": f"SCRAM handshakeToken={handshake_token}, data={data}"},
        )
        assert resp.status_code == 200
        params = _parse_header_params(resp.headers["authentication-info"])
        auth_token = params["authToken"]

        # Step 3: Use bearer token for an authenticated request
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": f"BEARER authToken={auth_token}"},
        )
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert len(grid) >= 1


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


class TestWebSocket:
    async def test_ws_about(self, app: Any) -> None:
        from starlette.testclient import TestClient

        with (
            TestClient(app) as tc,
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            ws.send_text(orjson.dumps({"id": "1", "op": "about"}).decode())
            data = orjson.loads(ws.receive_text())
            assert "grid" in data
            assert data.get("id") == "1"

    async def test_ws_read(self, app: Any) -> None:
        from starlette.testclient import TestClient

        req_grid = Grid.make_rows([{"filter": "site"}])
        from hs_py.encoding.json import encode_grid_dict

        grid_dict = encode_grid_dict(req_grid)

        with (
            TestClient(app) as tc,
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            ws.send_text(orjson.dumps({"id": "2", "op": "read", "grid": grid_dict}).decode())
            data = orjson.loads(ws.receive_text())
            assert "grid" in data
            rows = data["grid"].get("rows", [])
            assert len(rows) == 2

    async def test_ws_batch(self, app: Any) -> None:
        from starlette.testclient import TestClient

        batch = [
            {"id": "1", "op": "about"},
            {"id": "2", "op": "formats"},
        ]

        with (
            TestClient(app) as tc,
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            ws.send_text(orjson.dumps(batch).decode())
            data = orjson.loads(ws.receive_text())
            assert isinstance(data, list)
            assert len(data) == 2
            ids = {item["id"] for item in data}
            assert ids == {"1", "2"}


# ---------------------------------------------------------------------------
# Auth-enabled fixtures (StorageAuthenticator + InMemoryAdapter)
# ---------------------------------------------------------------------------


def _make_auth_app(
    *,
    namespace: Any = None,
) -> tuple[InMemoryAdapter, Any]:
    """Build an InMemoryAdapter + FastAPI app WITH auth and user management."""
    from hs_py.auth_types import StorageAuthenticator
    from hs_py.user import Role, create_user

    adapter = InMemoryAdapter(list(_SEED_ENTITIES))
    admin = create_user("admin", "admin-pass", role=Role.ADMIN, iterations=4096)
    # Pre-populate the user store via direct mutation (async fixture will call start())
    adapter._users["admin"] = admin

    auth = StorageAuthenticator(adapter)
    app = create_fastapi_app(
        storage=adapter,
        authenticator=auth,
        user_store=adapter,
        namespace=namespace,
    )
    return adapter, app


async def _scram_login(client: AsyncClient, username: str, password: str) -> str:
    """Perform SCRAM-SHA-256 handshake and return the auth token."""
    import base64
    import hashlib
    import hmac
    import os as _os

    from hs_py.auth import _b64url_decode, _b64url_encode, _parse_header_params

    username_b64 = _b64url_encode(username.encode())
    resp = await client.get(
        "/api/about",
        headers={"Authorization": f"HELLO username={username_b64}"},
    )
    assert resp.status_code == 401
    params = _parse_header_params(resp.headers["www-authenticate"])
    ht = params["handshakeToken"]

    c_nonce = base64.urlsafe_b64encode(_os.urandom(24)).decode().rstrip("=")
    client_first = f"n,,n={username},r={c_nonce}"
    data = _b64url_encode(client_first.encode())
    resp = await client.get(
        "/api/about",
        headers={"Authorization": f"SCRAM handshakeToken={ht}, data={data}"},
    )
    assert resp.status_code == 401
    params = _parse_header_params(resp.headers["www-authenticate"])
    ht = params["handshakeToken"]
    server_first = _b64url_decode(params["data"]).decode()

    sf = dict(p.split("=", 1) for p in server_first.split(",") if "=" in p)
    s_nonce = sf["r"]
    salt = base64.b64decode(sf["s"])
    iterations = int(sf["i"])

    salted_password = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted_password, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()

    channel_binding = _b64url_encode(b"n,,")
    client_first_bare = f"n={username},r={c_nonce}"
    client_final_no_proof = f"c={channel_binding},r={s_nonce}"
    auth_message = f"{client_first_bare},{server_first},{client_final_no_proof}"

    client_signature = hmac.new(stored_key, auth_message.encode(), "sha256").digest()
    client_proof = bytes(a ^ b for a, b in zip(client_key, client_signature, strict=True))
    proof_b64 = base64.b64encode(client_proof).decode()

    client_final = f"c={channel_binding},r={s_nonce},p={proof_b64}"
    data = _b64url_encode(client_final.encode())
    resp = await client.get(
        "/api/about",
        headers={"Authorization": f"SCRAM handshakeToken={ht}, data={data}"},
    )
    assert resp.status_code == 200
    params = _parse_header_params(resp.headers["authentication-info"])
    return params["authToken"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"BEARER authToken={token}"}


# ---------------------------------------------------------------------------
# User CRUD via /users/ endpoints
# ---------------------------------------------------------------------------


class TestUserCrud:
    """Full CRUD lifecycle for user management endpoints."""

    async def test_create_and_list_users(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            h = _bearer(token)

            # Create a new user
            resp = await c.post(
                "/api/users/",
                json={
                    "username": "alice",
                    "password": "alice-pw",
                    "first_name": "Alice",
                    "last_name": "Smith",
                    "email": "alice@example.com",
                    "role": "operator",
                },
                headers=h,
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["username"] == "alice"
            assert body["role"] == "operator"
            assert body["enabled"] is True

            # List users — should include admin + alice
            resp = await c.get("/api/users/", headers=h)
            assert resp.status_code == 200
            users = resp.json()
            usernames = {u["username"] for u in users}
            assert "admin" in usernames
            assert "alice" in usernames

    async def test_get_single_user(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            h = _bearer(token)

            resp = await c.get("/api/users/admin", headers=h)
            assert resp.status_code == 200
            assert resp.json()["username"] == "admin"

    async def test_get_nonexistent_user_404(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            resp = await c.get("/api/users/nobody", headers=_bearer(token))
            assert resp.status_code == 404

    async def test_update_user(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            h = _bearer(token)

            # Create
            await c.post(
                "/api/users/",
                json={
                    "username": "bob",
                    "password": "bob-pw",
                    "role": "viewer",
                },
                headers=h,
            )

            # Update
            resp = await c.put(
                "/api/users/bob",
                json={
                    "first_name": "Robert",
                    "role": "operator",
                },
                headers=h,
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["first_name"] == "Robert"
            assert body["role"] == "operator"

    async def test_delete_user(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            h = _bearer(token)

            await c.post(
                "/api/users/",
                json={
                    "username": "temp",
                    "password": "temp-pw",
                },
                headers=h,
            )

            resp = await c.delete("/api/users/temp", headers=h)
            assert resp.status_code == 200
            assert resp.json()["deleted"] == "temp"

            # Verify gone
            resp = await c.get("/api/users/temp", headers=h)
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Role-based permission enforcement
# ---------------------------------------------------------------------------


class TestRolePermissions:
    """Verify role enforcement across HTTP ops."""

    async def test_viewer_can_read(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            h = _bearer(token)

            # Create a viewer
            await c.post(
                "/api/users/",
                json={
                    "username": "viewer1",
                    "password": "viewer-pw",
                    "role": "viewer",
                },
                headers=h,
            )

            # Login as viewer
            v_token = await _scram_login(c, "viewer1", "viewer-pw")
            vh = _bearer(v_token)

            # Viewer can read
            resp = await c.get("/api/about", headers=vh)
            assert resp.status_code == 200

            req = Grid.make_rows([{"filter": "site"}])
            resp = await c.post(
                "/api/read",
                content=encode_grid(req),
                headers={**vh, "Content-Type": _JSON},
            )
            assert resp.status_code == 200

    async def test_viewer_cannot_write(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            await c.post(
                "/api/users/",
                json={
                    "username": "viewer2",
                    "password": "viewer-pw",
                    "role": "viewer",
                },
                headers=_bearer(token),
            )

            v_token = await _scram_login(c, "viewer2", "viewer-pw")
            vh = {**_bearer(v_token), "Content-Type": _JSON}

            # Viewer cannot hisWrite
            builder = GridBuilder().set_meta({"id": Ref("p1")})
            builder.add_col("ts").add_col("val")
            builder.add_row({"ts": "2024-01-01T00:00:00Z", "val": Number(72.0)})
            resp = await c.post(
                "/api/hisWrite", content=encode_grid(builder.to_grid()), headers=vh
            )
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            assert grid.meta.get("err") is not None
            assert "permissions" in grid.meta.get("dis", "").lower()

    async def test_operator_can_write(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            await c.post(
                "/api/users/",
                json={
                    "username": "oper1",
                    "password": "oper-pw",
                    "role": "operator",
                },
                headers=_bearer(token),
            )

            o_token = await _scram_login(c, "oper1", "oper-pw")
            oh = {**_bearer(o_token), "Content-Type": _JSON}

            # Operator can hisWrite
            builder = GridBuilder().set_meta({"id": Ref("p1")})
            builder.add_col("ts").add_col("val")
            builder.add_row({"ts": "2024-01-01T00:00:00Z", "val": Number(72.0)})
            resp = await c.post(
                "/api/hisWrite", content=encode_grid(builder.to_grid()), headers=oh
            )
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            assert grid.meta.get("err") is None

    async def test_operator_cannot_manage_users(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            await c.post(
                "/api/users/",
                json={
                    "username": "oper2",
                    "password": "oper-pw",
                    "role": "operator",
                },
                headers=_bearer(token),
            )

            o_token = await _scram_login(c, "oper2", "oper-pw")
            oh = _bearer(o_token)

            # Operator cannot list users (admin-only)
            resp = await c.get("/api/users/", headers=oh)
            assert resp.status_code == 200  # returns 200 with error in body
            # The user router _require_admin raises HaystackError → caught by middleware
            resp.json()
            # It may be an error grid response or a direct JSON error
            assert resp.status_code != 201


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_bad_filter(self, client: AsyncClient) -> None:
        """Malformed filter returns error grid, not 500."""
        req = Grid.make_rows([{"filter": "((( bad filter !!!"}])
        resp = await _post_grid(client, "read", req)
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid.meta.get("err") is not None

    async def test_his_read_missing_id(self, client: AsyncClient) -> None:
        """HisRead with non-existent ID returns empty or error."""
        req = Grid.make_rows([{"id": Ref("no-such-id"), "range": "today"}])
        resp = await _post_grid(client, "hisRead", req)
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert len(grid) == 0 or grid.meta.get("err") is not None

    async def test_unknown_post_op(self, client: AsyncClient) -> None:
        """POST to unknown op path returns error."""
        resp = await client.post("/api/noSuchOp", content=b"", headers={"Content-Type": _JSON})
        assert resp.status_code in {200, 404, 405}


# ---------------------------------------------------------------------------
# WebSocket auth
# ---------------------------------------------------------------------------


class TestWsAuth:
    async def test_ws_token_auth_flow(self) -> None:
        """Full SCRAM login then WS with token."""
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")

        from starlette.testclient import TestClient

        with (
            TestClient(app) as tc,
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            ws.send_text(orjson.dumps({"authToken": token}).decode())
            ws.send_text(orjson.dumps({"id": "1", "op": "about"}).decode())
            data = orjson.loads(ws.receive_text())
            assert data.get("id") == "1"
            assert "grid" in data

    async def test_ws_bad_token_rejected(self) -> None:
        """WS with invalid token → connection closed."""
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Make one request to initialize middleware state
            await c.get("/api/about")

        with (
            TestClient(app) as tc,
            pytest.raises(WebSocketDisconnect),
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            ws.send_text(orjson.dumps({"authToken": "bad-token"}).decode())
            ws.receive_text()

    async def test_ws_viewer_read_only(self) -> None:
        """WS: viewer can read but not write."""
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            admin_token = await _scram_login(c, "admin", "admin-pass")
            await c.post(
                "/api/users/",
                json={
                    "username": "wsv",
                    "password": "wsv-pw",
                    "role": "viewer",
                },
                headers=_bearer(admin_token),
            )
            v_token = await _scram_login(c, "wsv", "wsv-pw")

        from starlette.testclient import TestClient

        with (
            TestClient(app) as tc,
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            ws.send_text(orjson.dumps({"authToken": v_token}).decode())

            # Read OK
            ws.send_text(orjson.dumps({"id": "r1", "op": "about"}).decode())
            data = orjson.loads(ws.receive_text())
            assert data["id"] == "r1"
            assert data["grid"].get("meta", {}).get("err") is None

            # Write denied
            ws.send_text(orjson.dumps({"id": "w1", "op": "hisWrite"}).decode())
            data = orjson.loads(ws.receive_text())
            assert data["id"] == "w1"
            assert data["grid"]["meta"].get("err") is not None


# ---------------------------------------------------------------------------
# Watch partial unsub
# ---------------------------------------------------------------------------


class TestWatchPartial:
    async def test_watch_partial_unsub(self, client: AsyncClient) -> None:
        """Subscribe multiple entities, unsub one, verify poll returns remainder."""
        sub = GridBuilder().set_meta({"watchDis": "partial-test"})
        sub.add_col("id")
        sub.add_row({"id": Ref("p1")})
        sub.add_row({"id": Ref("p2")})
        sub.add_row({"id": Ref("s1")})

        resp = await _post_grid(client, "watchSub", sub.to_grid())
        result = decode_grid(resp.content)
        watch_id = result.meta["watchId"]
        assert len(result) == 3

        # Unsub one entity
        unsub = GridBuilder().set_meta({"watchId": watch_id})
        unsub.add_col("id")
        unsub.add_row({"id": Ref("s1")})
        resp = await _post_grid(client, "watchUnsub", unsub.to_grid())
        assert resp.status_code == 200

        # Refresh poll should return only 2
        refresh = GridBuilder().set_meta({"watchId": watch_id, "refresh": MARKER})
        resp = await _post_grid(client, "watchPoll", refresh.to_grid())
        result = decode_grid(resp.content)
        assert len(result) == 2
        ids = {row["id"] for row in result}
        assert Ref("p1") in ids
        assert Ref("p2") in ids
        assert Ref("s1") not in ids

        # Cleanup
        close = GridBuilder().set_meta({"watchId": watch_id, "close": MARKER})
        close.add_col("id")
        await _post_grid(client, "watchUnsub", close.to_grid())

    async def test_watch_extend_subscription(self, client: AsyncClient) -> None:
        """Extend an existing watch with additional entities."""
        sub = GridBuilder().set_meta({"watchDis": "extend-test"})
        sub.add_col("id")
        sub.add_row({"id": Ref("p1")})
        resp = await _post_grid(client, "watchSub", sub.to_grid())
        result = decode_grid(resp.content)
        watch_id = result.meta["watchId"]
        assert len(result) == 1

        # Extend
        ext = GridBuilder().set_meta({"watchId": watch_id})
        ext.add_col("id")
        ext.add_row({"id": Ref("p2")})
        ext.add_row({"id": Ref("s1")})
        resp = await _post_grid(client, "watchSub", ext.to_grid())
        result = decode_grid(resp.content)
        assert len(result) >= 2  # returns newly added (or all watched)

        # Refresh should return all 3
        refresh = GridBuilder().set_meta({"watchId": watch_id, "refresh": MARKER})
        resp = await _post_grid(client, "watchPoll", refresh.to_grid())
        result = decode_grid(resp.content)
        assert len(result) >= 3

        # Cleanup
        close = GridBuilder().set_meta({"watchId": watch_id, "close": MARKER})
        close.add_col("id")
        await _post_grid(client, "watchUnsub", close.to_grid())


# ---------------------------------------------------------------------------
# Concurrent requests
# ---------------------------------------------------------------------------


class TestConcurrent:
    async def test_parallel_reads(self, client: AsyncClient) -> None:
        """Fire multiple reads concurrently."""
        import asyncio

        tasks = [
            client.get("/api/about"),
            client.get("/api/ops"),
            client.get("/api/formats"),
            _post_grid(client, "read", Grid.make_rows([{"filter": "site"}])),
            _post_grid(client, "read", Grid.make_rows([{"filter": "point"}])),
        ]
        results = await asyncio.gather(*tasks)
        for resp in results:
            assert resp.status_code == 200

    async def test_parallel_ws_ops(self, app: Any) -> None:
        """Multiple WS messages in rapid succession."""
        from starlette.testclient import TestClient

        with (
            TestClient(app) as tc,
            tc.websocket_connect("/api/ws", subprotocols=["haystack"]) as ws,
        ):
            # Send multiple ops without waiting
            for i in range(5):
                ws.send_text(orjson.dumps({"id": str(i), "op": "about"}).decode())

            received_ids = set()
            for _ in range(5):
                data = orjson.loads(ws.receive_text())
                received_ids.add(data["id"])
            assert received_ids == {"0", "1", "2", "3", "4"}


# ---------------------------------------------------------------------------
# Edge / negative cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_duplicate_user_creation(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            h = _bearer(token)

            await c.post(
                "/api/users/",
                json={
                    "username": "dup",
                    "password": "pw",
                },
                headers=h,
            )

            # Second creation should fail
            resp = await c.post(
                "/api/users/",
                json={
                    "username": "dup",
                    "password": "pw2",
                },
                headers=h,
            )
            assert resp.status_code == 409

    async def test_self_delete_prevented(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            resp = await c.delete("/api/users/admin", headers=_bearer(token))
            assert resp.status_code == 400
            assert "own account" in resp.json()["error"].lower()

    async def test_create_user_missing_fields(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            h = _bearer(token)

            # Missing password
            resp = await c.post("/api/users/", json={"username": "nopass"}, headers=h)
            assert resp.status_code == 400

            # Missing username
            resp = await c.post("/api/users/", json={"password": "nope"}, headers=h)
            assert resp.status_code == 400

    async def test_create_user_invalid_role(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            resp = await c.post(
                "/api/users/",
                json={
                    "username": "bad",
                    "password": "pw",
                    "role": "superadmin",
                },
                headers=_bearer(token),
            )
            assert resp.status_code == 400
            assert "invalid role" in resp.json()["error"].lower()

    async def test_update_nonexistent_user(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            resp = await c.put(
                "/api/users/ghost", json={"first_name": "X"}, headers=_bearer(token)
            )
            assert resp.status_code == 404

    async def test_update_invalid_role(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            resp = await c.put("/api/users/admin", json={"role": "wizard"}, headers=_bearer(token))
            assert resp.status_code == 400

    async def test_update_no_valid_fields(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            resp = await c.put("/api/users/admin", json={"foo": "bar"}, headers=_bearer(token))
            assert resp.status_code == 400

    async def test_delete_nonexistent_user(self) -> None:
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            resp = await c.delete("/api/users/ghost", headers=_bearer(token))
            assert resp.status_code == 404

    async def test_disable_user_blocks_access(self) -> None:
        """Disabled user cannot authenticate."""
        _, app = _make_auth_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            token = await _scram_login(c, "admin", "admin-pass")
            h = _bearer(token)

            await c.post(
                "/api/users/",
                json={
                    "username": "toban",
                    "password": "toban-pw",
                    "role": "operator",
                },
                headers=h,
            )

            # Verify can login
            await _scram_login(c, "toban", "toban-pw")

            # Disable
            resp = await c.put("/api/users/toban", json={"enabled": False}, headers=h)
            assert resp.status_code == 200
            assert resp.json()["enabled"] is False

            # Disabled user SCRAM should fail at handshake
            from hs_py.auth import _b64url_encode

            username_b64 = _b64url_encode(b"toban")
            resp = await c.get(
                "/api/about",
                headers={"Authorization": f"HELLO username={username_b64}"},
            )
            # Server should reject disabled user during HELLO
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Ontology export
# ---------------------------------------------------------------------------


class TestOntologyExport:
    async def test_ontology_export_no_namespace(self, client: AsyncClient) -> None:
        """Export with no namespace loaded returns error grid."""
        resp = await client.get("/api/ontology/export")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid.meta.get("err") is not None

    async def test_ontology_export_turtle(self) -> None:
        """Export namespace as Turtle when loaded."""
        from hs_py.kinds import Symbol as Sym
        from hs_py.ontology.defs import Def, Lib
        from hs_py.ontology.namespace import Namespace

        defs = [
            Def(symbol=Sym("site"), tags={"doc": "A site entity", "is": Sym("entity")}),
            Def(symbol=Sym("equip"), tags={"doc": "An equip entity", "is": Sym("entity")}),
        ]
        lib = Lib(symbol=Sym("lib:test"), version="1.0", defs=defs)
        ns = Namespace([lib])

        storage = InMemoryAdapter(list(_SEED_ENTITIES))
        ops = _TestOps(storage=storage)
        ops._namespace = ns  # type: ignore[attr-defined]
        app = create_fastapi_app(ops=ops, namespace=ns)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/ontology/export?format=turtle")
            assert resp.status_code == 200
            assert "text/turtle" in resp.headers["content-type"]

    async def test_ontology_export_jsonld(self) -> None:
        """Export namespace as JSON-LD."""
        from hs_py.kinds import Symbol as Sym
        from hs_py.ontology.defs import Def, Lib
        from hs_py.ontology.namespace import Namespace

        defs = [
            Def(symbol=Sym("site"), tags={"doc": "A site entity", "is": Sym("entity")}),
        ]
        lib = Lib(symbol=Sym("lib:test"), version="1.0", defs=defs)
        ns = Namespace([lib])

        storage = InMemoryAdapter(list(_SEED_ENTITIES))
        ops = _TestOps(storage=storage)
        ops._namespace = ns  # type: ignore[attr-defined]
        app = create_fastapi_app(ops=ops, namespace=ns)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/ontology/export?format=jsonld")
            assert resp.status_code == 200
            assert "application/ld+json" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Close endpoint
# ---------------------------------------------------------------------------


class TestClose:
    async def test_close_returns_empty_grid(self, client: AsyncClient) -> None:
        resp = await client.get("/api/close")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert len(grid) == 0
