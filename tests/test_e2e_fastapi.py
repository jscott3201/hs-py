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
