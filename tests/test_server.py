import base64
import hashlib
import hmac
import os
import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from hs_py._scram_core import (
    HANDSHAKE_TIMEOUT,
    MAX_HANDSHAKES,
    TOKEN_LIFETIME,
    HandshakeState,
    TokenEntry,
)
from hs_py.auth import _b64url_decode, _b64url_encode, _parse_header_params
from hs_py.auth_types import SimpleAuthenticator
from hs_py.encoding.json import decode_grid, encode_grid
from hs_py.errors import HaystackError
from hs_py.fastapi_server import ScramAuthMiddleware, create_fastapi_app
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import MARKER, Number, Ref
from hs_py.ops import HaystackOps
from hs_py.tls import TLSConfig, build_client_ssl_context, generate_test_certificates

# ---------------------------------------------------------------------------
# Test ops implementation
# ---------------------------------------------------------------------------


class _TestOps(HaystackOps):
    """Ops subclass for testing — overrides about and read."""

    async def about(self) -> Grid:
        return Grid.make_rows(
            [
                {
                    "haystackVersion": "4.0",
                    "serverName": "TestServer",
                    "productName": "hs-py-test",
                }
            ]
        )

    async def read(self, grid: Grid) -> Grid:
        if grid.rows and "filter" in grid[0]:
            return Grid.make_rows(
                [
                    {"id": Ref("p1"), "dis": "Point 1", "point": MARKER},
                    {"id": Ref("p2"), "dis": "Point 2", "point": MARKER},
                ]
            )
        if grid.rows and "id" in grid[0]:
            rows = [{"id": row["id"], "dis": f"Entity {row['id'].val}"} for row in grid]
            return Grid.make_rows(rows)
        return Grid.make_empty()

    async def nav(self, grid: Grid) -> Grid:
        return Grid.make_rows(
            [
                {"navId": "site-1", "dis": "Site 1"},
                {"navId": "site-2", "dis": "Site 2"},
            ]
        )

    async def his_read(self, grid: Grid) -> Grid:
        meta: dict[str, Any] = {"id": grid[0]["id"], "hisStart": "start", "hisEnd": "end"}
        return (
            GridBuilder()
            .set_meta(meta)
            .add_col("ts")
            .add_col("val")
            .add_row({"ts": "2024-01-01T00:00:00Z", "val": Number(72.0, "°F")})
            .to_grid()
        )

    async def his_write(self, grid: Grid) -> Grid:
        return Grid.make_empty()

    async def invoke_action(self, grid: Grid) -> Grid:
        action = grid.meta.get("action", "unknown")
        return Grid.make_rows([{"result": f"Invoked {action}"}])


class _ErrorOps(HaystackOps):
    """Ops that raise exceptions for testing error middleware."""

    async def about(self) -> Grid:
        return Grid.make_rows([{"serverName": "ErrorServer"}])

    async def read(self, grid: Grid) -> Grid:
        raise HaystackError("Something went wrong")

    async def nav(self, grid: Grid) -> Grid:
        msg = "unexpected failure"
        raise RuntimeError(msg)


class _MinimalOps(HaystackOps):
    """Only overrides about — for testing ops auto-discovery."""

    async def about(self) -> Grid:
        return Grid.make_rows([{"serverName": "MinimalServer"}])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_JSON = "application/json"


@pytest.fixture
def app():
    """Create a FastAPI app with _TestOps (no auth)."""
    return create_fastapi_app(ops=_TestOps())


@pytest.fixture
async def client(app):
    """Async httpx client wired to the ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_app():
    """Create a FastAPI app with _TestOps + SCRAM auth."""
    auth = SimpleAuthenticator({"admin": "secret"}, iterations=4096)
    return create_fastapi_app(ops=_TestOps(), authenticator=auth)


@pytest.fixture
async def auth_client(auth_app):
    """Async httpx client wired to the auth-enabled ASGI app."""
    transport = ASGITransport(app=auth_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def minimal_app():
    """Create a FastAPI app with _MinimalOps (no auth)."""
    return create_fastapi_app(ops=_MinimalOps())


@pytest.fixture
async def minimal_client(minimal_app):
    transport = ASGITransport(app=minimal_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def error_app():
    """Create a FastAPI app with _ErrorOps (no auth)."""
    return create_fastapi_app(ops=_ErrorOps())


@pytest.fixture
async def error_client(error_app):
    transport = ASGITransport(app=error_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _get_scram_middleware(app: Any) -> ScramAuthMiddleware:
    """Walk the ASGI middleware stack and return the ScramAuthMiddleware instance.

    Forces a middleware-stack build if the stack has not been initialised yet
    (FastAPI builds it lazily on the first request).
    """
    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()
    obj = app.middleware_stack
    while obj is not None:
        if isinstance(obj, ScramAuthMiddleware):
            return obj
        obj = getattr(obj, "app", None)
    msg = "ScramAuthMiddleware not found in middleware stack"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# GET op tests
# ---------------------------------------------------------------------------


class TestGetOps:
    """Test GET operations through create_fastapi_app."""

    async def test_about(self, client: AsyncClient) -> None:
        resp = await client.get("/api/about")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid[0]["serverName"] == "TestServer"

    async def test_ops(self, client: AsyncClient) -> None:
        resp = await client.get("/api/ops")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        names = [row["name"] for row in grid]
        assert "ops" in names
        assert "formats" in names
        # about is overridden in _TestOps
        assert "about" in names
        # read is overridden
        assert "read" in names

    async def test_formats(self, client: AsyncClient) -> None:
        resp = await client.get("/api/formats")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid[0]["mime"] == "application/json"

    async def test_close(self, client: AsyncClient) -> None:
        resp = await client.get("/api/close")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid.is_empty


# ---------------------------------------------------------------------------
# POST op tests
# ---------------------------------------------------------------------------


class TestPostOps:
    """Test POST operations through create_fastapi_app."""

    async def test_read_filter(self, client: AsyncClient) -> None:
        req_grid = GridBuilder().add_col("filter").add_row({"filter": "point"}).to_grid()
        resp = await client.post(
            "/api/read",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert len(grid) == 2
        assert grid[0]["id"] == Ref("p1")

    async def test_read_by_ids(self, client: AsyncClient) -> None:
        builder = GridBuilder().add_col("id")
        builder.add_row({"id": Ref("a")})
        builder.add_row({"id": Ref("b")})
        resp = await client.post(
            "/api/read",
            content=encode_grid(builder.to_grid()),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 2
        assert grid[0]["id"] == Ref("a")

    async def test_nav(self, client: AsyncClient) -> None:
        req_grid = GridBuilder().add_col("navId").add_row({"navId": None}).to_grid()
        resp = await client.post(
            "/api/nav",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 2
        assert grid[0]["navId"] == "site-1"

    async def test_his_read(self, client: AsyncClient) -> None:
        req_grid = (
            GridBuilder()
            .add_col("id")
            .add_col("range")
            .add_row({"id": Ref("p1"), "range": "today"})
            .to_grid()
        )
        resp = await client.post(
            "/api/hisRead",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert grid.meta["id"] == Ref("p1")

    async def test_his_write(self, client: AsyncClient) -> None:
        req_grid = (
            GridBuilder()
            .set_meta({"id": Ref("p1")})
            .add_col("ts")
            .add_col("val")
            .add_row({"ts": "2024-01-01T00:00:00Z", "val": Number(72.0)})
            .to_grid()
        )
        resp = await client.post(
            "/api/hisWrite",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert grid.is_empty

    async def test_invoke_action(self, client: AsyncClient) -> None:
        req_grid = (
            GridBuilder()
            .set_meta({"id": Ref("p1"), "action": "doIt"})
            .add_col("arg1")
            .add_row({"arg1": "val1"})
            .to_grid()
        )
        resp = await client.post(
            "/api/invokeAction",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert grid[0]["result"] == "Invoked doIt"


# ---------------------------------------------------------------------------
# Unsupported ops / error grids
# ---------------------------------------------------------------------------


class TestUnsupportedOps:
    """Test that unimplemented ops return error grids."""

    async def test_unimplemented_read(self, minimal_client: AsyncClient) -> None:
        req_grid = GridBuilder().add_col("filter").add_row({"filter": "point"}).to_grid()
        resp = await minimal_client.post(
            "/api/read",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid.is_error
        assert "not supported" in grid.meta["dis"]

    async def test_unimplemented_watch_sub(self, minimal_client: AsyncClient) -> None:
        req_grid = (
            GridBuilder()
            .set_meta({"watchDis": "test"})
            .add_col("id")
            .add_row({"id": Ref("p1")})
            .to_grid()
        )
        resp = await minimal_client.post(
            "/api/watchSub",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert grid.is_error


class TestErrorMiddleware:
    """Test that exceptions are caught and returned as error grids."""

    async def test_haystack_error(self, error_client: AsyncClient) -> None:
        req_grid = GridBuilder().add_col("filter").add_row({"filter": "point"}).to_grid()
        resp = await error_client.post(
            "/api/read",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid.is_error
        assert grid.meta["dis"] == "Something went wrong"

    async def test_unexpected_error(self, error_client: AsyncClient) -> None:
        req_grid = GridBuilder().add_col("navId").add_row({"navId": None}).to_grid()
        resp = await error_client.post(
            "/api/nav",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid.is_error
        assert "RuntimeError" in grid.meta["dis"]


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------


class TestOpsAutoDiscovery:
    """Test that ops() returns only overridden methods plus defaults."""

    async def test_minimal_ops_discovery(self, minimal_client: AsyncClient) -> None:
        resp = await minimal_client.get("/api/ops")
        grid = decode_grid(resp.content)
        names = {row["name"] for row in grid}
        # Should include about (overridden), ops and formats (defaults)
        assert "about" in names
        assert "ops" in names
        assert "formats" in names
        # Should NOT include unimplemented POST ops
        assert "read" not in names
        assert "hisRead" not in names
        assert "watchSub" not in names

    async def test_full_ops_discovery(self) -> None:
        """Test that _TestOps with overrides shows them."""
        app = create_fastapi_app(ops=_TestOps())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/ops")
            grid = decode_grid(resp.content)
            names = {row["name"] for row in grid}
            assert "about" in names
            assert "read" in names
            assert "nav" in names
            assert "hisRead" in names
            assert "hisWrite" in names
            assert "invokeAction" in names
            # Not overridden
            assert "pointWrite" not in names
            assert "watchSub" not in names


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------


class TestNoAuth:
    """Test that server works without authentication."""

    async def test_about_no_auth(self, client: AsyncClient) -> None:
        resp = await client.get("/api/about")
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid[0]["serverName"] == "TestServer"


class TestAuthFlow:
    """Test full SCRAM auth handshake."""

    async def test_no_auth_header_returns_401(self, auth_client: AsyncClient) -> None:
        resp = await auth_client.get("/api/about")
        assert resp.status_code == 401
        assert "SCRAM" in resp.headers.get("www-authenticate", "")

    async def test_invalid_bearer_returns_401(self, auth_client: AsyncClient) -> None:
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": "BEARER authToken=invalid"},
        )
        assert resp.status_code == 401

    async def test_unknown_user_returns_401(self, auth_client: AsyncClient) -> None:
        username_b64 = base64.urlsafe_b64encode(b"unknown").rstrip(b"=").decode()
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": f"HELLO username={username_b64}"},
        )
        assert resp.status_code == 401

    async def test_full_scram_handshake(self, auth_client: AsyncClient) -> None:
        """Walk through the complete HELLO -> SCRAM -> BEARER flow."""
        username = "admin"
        password = "secret"

        # Step 1: HELLO
        hello_header = f"HELLO username={_b64url_encode(username.encode())}"
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": hello_header},
        )
        assert resp.status_code == 401
        www_auth = resp.headers["www-authenticate"]
        params = _parse_header_params(www_auth)
        handshake_token = params["handshakeToken"]
        assert "SCRAM" in www_auth

        # Step 2: Client-first message
        c_nonce = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
        gs2_header = "n,,"
        client_first_bare = f"n={username},r={c_nonce}"
        client_first_msg = gs2_header + client_first_bare

        auth_header = (
            f"SCRAM handshakeToken={handshake_token}, "
            f"data={_b64url_encode(client_first_msg.encode())}"
        )
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": auth_header},
        )
        assert resp.status_code == 401
        www_auth = resp.headers["www-authenticate"]
        params = _parse_header_params(www_auth)
        handshake_token = params["handshakeToken"]
        server_first_data = params["data"]
        server_first_msg = _b64url_decode(server_first_data).decode()

        # Parse server-first
        sf_params = dict(p.split("=", 1) for p in server_first_msg.split(",") if "=" in p)
        s_nonce = sf_params["r"]
        salt = base64.b64decode(sf_params["s"])
        iterations = int(sf_params["i"])

        assert s_nonce.startswith(c_nonce)

        # Derive keys
        salted_pw = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        client_key = hmac.new(salted_pw, b"Client Key", "sha256").digest()
        stored_key = hashlib.sha256(client_key).digest()

        # Client-final
        channel_binding = _b64url_encode(b"n,,")
        client_final_no_proof = f"c={channel_binding},r={s_nonce}"
        auth_message = f"{client_first_bare},{server_first_msg},{client_final_no_proof}"
        client_signature = hmac.new(stored_key, auth_message.encode(), "sha256").digest()
        client_proof = bytes(a ^ b for a, b in zip(client_key, client_signature, strict=True))
        proof_b64 = base64.b64encode(client_proof).decode()
        client_final_msg = f"{client_final_no_proof},p={proof_b64}"

        # Step 3: Client-final message
        auth_header = (
            f"SCRAM handshakeToken={handshake_token}, "
            f"data={_b64url_encode(client_final_msg.encode())}"
        )
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": auth_header},
        )
        assert resp.status_code == 200
        auth_info = resp.headers["authentication-info"]
        info_params = _parse_header_params(auth_info)
        auth_token = info_params["authToken"]
        assert auth_token

        # Verify server signature
        server_key = hmac.new(salted_pw, b"Server Key", "sha256").digest()
        expected_server_sig = hmac.new(server_key, auth_message.encode(), "sha256").digest()
        server_data = info_params.get("data", "")
        if server_data:
            server_final = _b64url_decode(server_data).decode()
            sf_final = dict(p.split("=", 1) for p in server_final.split(",") if "=" in p)
            server_sig = base64.b64decode(sf_final["v"])
            assert hmac.compare_digest(server_sig, expected_server_sig)

        # Step 4: Use bearer token
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": f"BEARER authToken={auth_token}"},
        )
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid[0]["serverName"] == "TestServer"


# ---------------------------------------------------------------------------
# Client <-> Server integration
# ---------------------------------------------------------------------------


class TestClientServerIntegration:
    """Test Client against a server built with create_fastapi_app.

    Uses httpx AsyncClient with ASGI transport to exercise the same ops
    that the aiohttp Client would call.
    """

    async def test_about(self, client: AsyncClient) -> None:
        resp = await client.get("/api/about")
        grid = decode_grid(resp.content)
        assert grid[0]["serverName"] == "TestServer"

    async def test_read_filter(self, client: AsyncClient) -> None:
        req_grid = GridBuilder().add_col("filter").add_row({"filter": "point"}).to_grid()
        resp = await client.post(
            "/api/read",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 2
        assert grid[0]["id"] == Ref("p1")

    async def test_read_by_ids(self, client: AsyncClient) -> None:
        builder = GridBuilder().add_col("id")
        builder.add_row({"id": Ref("a")})
        builder.add_row({"id": Ref("b")})
        resp = await client.post(
            "/api/read",
            content=encode_grid(builder.to_grid()),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 2

    async def test_nav(self, client: AsyncClient) -> None:
        req_grid = GridBuilder().add_col("navId").add_row({"navId": None}).to_grid()
        resp = await client.post(
            "/api/nav",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert len(grid) == 2

    async def test_his_read(self, client: AsyncClient) -> None:
        req_grid = (
            GridBuilder()
            .add_col("id")
            .add_col("range")
            .add_row({"id": Ref("p1"), "range": "today"})
            .to_grid()
        )
        resp = await client.post(
            "/api/hisRead",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert grid.meta["id"] == Ref("p1")

    async def test_his_write(self, client: AsyncClient) -> None:
        req_grid = (
            GridBuilder()
            .set_meta({"id": Ref("p1")})
            .add_col("ts")
            .add_col("val")
            .add_row({"ts": "2024-01-01T00:00:00Z", "val": Number(72.0)})
            .to_grid()
        )
        resp = await client.post(
            "/api/hisWrite",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert grid.is_empty

    async def test_invoke_action(self, client: AsyncClient) -> None:
        req_grid = (
            GridBuilder()
            .set_meta({"id": Ref("p1"), "action": "doIt"})
            .add_col("arg1")
            .add_row({"arg1": "val1"})
            .to_grid()
        )
        resp = await client.post(
            "/api/invokeAction",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert grid[0]["result"] == "Invoked doIt"

    async def test_unimplemented_op_returns_error_grid(self, client: AsyncClient) -> None:
        req_grid = GridBuilder().add_col("id").add_row({"id": Ref("p1")}).to_grid()
        resp = await client.post(
            "/api/pointWrite",
            content=encode_grid(req_grid),
            headers={"Content-Type": _JSON},
        )
        grid = decode_grid(resp.content)
        assert grid.is_error
        assert "not supported" in grid.meta["dis"]


class TestClientServerAuthIntegration:
    """Test SCRAM auth flow against server SimpleAuthenticator."""

    async def _do_scram(self, client: AsyncClient) -> str:
        """Perform full SCRAM handshake, return bearer auth token."""
        username = "admin"
        password = "secret"

        # HELLO
        hello_header = f"HELLO username={_b64url_encode(username.encode())}"
        resp = await client.get("/api/about", headers={"Authorization": hello_header})
        params = _parse_header_params(resp.headers["www-authenticate"])
        handshake_token = params["handshakeToken"]

        # Client-first
        c_nonce = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
        client_first_bare = f"n={username},r={c_nonce}"
        client_first_msg = "n,," + client_first_bare
        resp = await client.get(
            "/api/about",
            headers={
                "Authorization": (
                    f"SCRAM handshakeToken={handshake_token}, "
                    f"data={_b64url_encode(client_first_msg.encode())}"
                )
            },
        )
        params = _parse_header_params(resp.headers["www-authenticate"])
        handshake_token = params["handshakeToken"]
        server_first_msg = _b64url_decode(params["data"]).decode()

        sf = dict(p.split("=", 1) for p in server_first_msg.split(",") if "=" in p)
        s_nonce = sf["r"]
        salt = base64.b64decode(sf["s"])
        iterations = int(sf["i"])

        salted_pw = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        client_key = hmac.new(salted_pw, b"Client Key", "sha256").digest()
        stored_key = hashlib.sha256(client_key).digest()

        channel_binding = _b64url_encode(b"n,,")
        client_final_no_proof = f"c={channel_binding},r={s_nonce}"
        auth_message = f"{client_first_bare},{server_first_msg},{client_final_no_proof}"
        client_signature = hmac.new(stored_key, auth_message.encode(), "sha256").digest()
        client_proof = bytes(a ^ b for a, b in zip(client_key, client_signature, strict=True))
        proof_b64 = base64.b64encode(client_proof).decode()
        client_final_msg = f"{client_final_no_proof},p={proof_b64}"

        resp = await client.get(
            "/api/about",
            headers={
                "Authorization": (
                    f"SCRAM handshakeToken={handshake_token}, "
                    f"data={_b64url_encode(client_final_msg.encode())}"
                )
            },
        )
        assert resp.status_code == 200
        info_params = _parse_header_params(resp.headers["authentication-info"])
        return info_params["authToken"]

    async def test_client_auth_and_about(self, auth_client: AsyncClient) -> None:
        token = await self._do_scram(auth_client)
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": f"BEARER authToken={token}"},
        )
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert grid[0]["serverName"] == "TestServer"

    async def test_client_auth_and_read(self, auth_client: AsyncClient) -> None:
        token = await self._do_scram(auth_client)
        req_grid = GridBuilder().add_col("filter").add_row({"filter": "point"}).to_grid()
        resp = await auth_client.post(
            "/api/read",
            content=encode_grid(req_grid),
            headers={
                "Content-Type": _JSON,
                "Authorization": f"BEARER authToken={token}",
            },
        )
        assert resp.status_code == 200
        grid = decode_grid(resp.content)
        assert len(grid) == 2


# ---------------------------------------------------------------------------
# Security hardening tests
# ---------------------------------------------------------------------------


class TestMalformedAuthData:
    """Test that malformed auth data returns 401, not 500."""

    async def test_malformed_hello_username(self, auth_client: AsyncClient) -> None:
        """Garbage base64 in HELLO username should return 401."""
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": "HELLO username=!!!invalid-b64!!!"},
        )
        assert resp.status_code == 401

    async def test_malformed_scram_data(self, auth_client: AsyncClient) -> None:
        """Garbage base64 in SCRAM data should return 401, not 500."""
        # First do a valid HELLO to get a handshake token
        hello_header = f"HELLO username={_b64url_encode(b'admin')}"
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": hello_header},
        )
        assert resp.status_code == 401
        params = _parse_header_params(resp.headers["www-authenticate"])
        token = params["handshakeToken"]

        # Send garbage data for SCRAM step
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": f"SCRAM handshakeToken={token}, data=!!!garbage!!!"},
        )
        assert resp.status_code == 401

    async def test_invalid_handshake_token(self, auth_client: AsyncClient) -> None:
        """Nonexistent handshake token should return 401."""
        data = _b64url_encode(b"n,,n=admin,r=abc123")
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": f"SCRAM handshakeToken=nonexistent, data={data}"},
        )
        assert resp.status_code == 401

    async def test_wrong_proof_returns_403(self, auth_client: AsyncClient) -> None:
        """Wrong client proof should return 403."""
        # Walk through HELLO + step 1, then send bad proof in step 2
        hello_header = f"HELLO username={_b64url_encode(b'admin')}"
        resp = await auth_client.get(
            "/api/about",
            headers={"Authorization": hello_header},
        )
        params = _parse_header_params(resp.headers["www-authenticate"])
        token = params["handshakeToken"]

        # Step 1: client-first
        c_nonce = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
        client_first = f"n,,n=admin,r={c_nonce}"
        resp = await auth_client.get(
            "/api/about",
            headers={
                "Authorization": (
                    f"SCRAM handshakeToken={token}, data={_b64url_encode(client_first.encode())}"
                )
            },
        )
        params = _parse_header_params(resp.headers["www-authenticate"])
        token = params["handshakeToken"]
        server_first = _b64url_decode(params["data"]).decode()
        sf = dict(p.split("=", 1) for p in server_first.split(",") if "=" in p)
        s_nonce = sf["r"]

        # Step 2: client-final with wrong proof (all zeros)
        channel_binding = _b64url_encode(b"n,,")
        wrong_proof = base64.b64encode(b"\x00" * 32).decode()
        client_final = f"c={channel_binding},r={s_nonce},p={wrong_proof}"
        resp = await auth_client.get(
            "/api/about",
            headers={
                "Authorization": (
                    f"SCRAM handshakeToken={token}, data={_b64url_encode(client_final.encode())}"
                )
            },
        )
        assert resp.status_code == 403


class TestHandshakeLimits:
    """Test that handshake count is bounded."""

    async def test_handshake_limit_enforced(self, auth_app: Any) -> None:
        """Once MAX_HANDSHAKES is reached, new HELLOs should be rejected."""
        # Warm up the middleware stack first.
        transport = ASGITransport(app=auth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/api/about")

        mw = _get_scram_middleware(auth_app)
        handshakes = mw._handshakes

        # Artificially fill handshakes to the limit
        for i in range(MAX_HANDSHAKES):
            handshakes[f"fake-{i}"] = HandshakeState(
                username="x",
                server_nonce="",
                salt=b"x",
                iterations=1,
                stored_key=b"x",
                server_key=b"x",
            )

        async with AsyncClient(transport=transport, base_url="http://test") as c:
            hello_header = f"HELLO username={_b64url_encode(b'admin')}"
            resp = await c.get(
                "/api/about",
                headers={"Authorization": hello_header},
            )
            assert resp.status_code == 401

    async def test_expired_handshakes_are_purged(self, auth_app: Any) -> None:
        """Stale handshakes should be cleaned up on HELLO."""
        # Warm up the middleware stack with a dummy request so the ASGI app
        # materialises its middleware chain before we grab a reference.
        transport = ASGITransport(app=auth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/api/about")

        mw = _get_scram_middleware(auth_app)
        handshakes = mw._handshakes

        # Add an expired handshake (created far in the past)
        handshakes["stale"] = HandshakeState(
            username="x",
            server_nonce="",
            salt=b"x",
            iterations=1,
            stored_key=b"x",
            server_key=b"x",
            created=time.monotonic() - HANDSHAKE_TIMEOUT - 10,  # guaranteed expired
        )
        assert "stale" in handshakes

        # A valid HELLO should purge it
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            hello_header = f"HELLO username={_b64url_encode(b'admin')}"
            resp = await c.get(
                "/api/about",
                headers={"Authorization": hello_header},
            )
            assert resp.status_code == 401
            assert "stale" not in handshakes


class TestTokenExpiry:
    """Test that bearer tokens expire."""

    async def test_expired_token_returns_401(self, auth_app: Any) -> None:
        """An expired token should be rejected."""
        # Warm up the middleware stack first.
        transport = ASGITransport(app=auth_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/api/about")

        mw = _get_scram_middleware(auth_app)
        tokens = mw._tokens

        # Add an expired token (created far in the past)
        tokens["expired-token"] = TokenEntry(
            username="admin", created=time.monotonic() - TOKEN_LIFETIME - 10
        )

        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/about",
                headers={"Authorization": "BEARER authToken=expired-token"},
            )
            assert resp.status_code == 401
            # Token should have been purged
            assert "expired-token" not in tokens


class TestErrorMiddlewareCoversAuth:
    """Verify the error-catching middleware wraps auth middleware (outermost)."""

    async def test_haystack_error_handler_registered(self, auth_app: Any) -> None:
        """The app should have a HaystackError exception handler registered."""
        handlers = auth_app.exception_handlers
        assert HaystackError in handlers

    async def test_error_middleware_catches_runtime_errors(self) -> None:
        """Unexpected errors should return error grids, not 500 status."""
        app = create_fastapi_app(ops=_ErrorOps())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            req_grid = GridBuilder().add_col("navId").add_row({"navId": None}).to_grid()
            resp = await c.post(
                "/api/nav",
                content=encode_grid(req_grid),
                headers={"Content-Type": _JSON},
            )
            assert resp.status_code == 200
            grid = decode_grid(resp.content)
            assert grid.is_error
            assert "RuntimeError" in grid.meta["dis"]


# ---------------------------------------------------------------------------
# TLS integration tests
# ---------------------------------------------------------------------------


class TestServerTLS:
    async def test_tls_server_accepts_tls_client(self) -> None:
        """A TLS 1.3 client should connect to a TLS-enabled server."""
        import asyncio
        import ssl
        import tempfile
        from pathlib import Path

        import httpx
        import uvicorn

        with tempfile.TemporaryDirectory() as d:
            server_config = generate_test_certificates(d)
            client_config = TLSConfig(
                certificate_path=str(Path(d) / "client.pem"),
                private_key_path=str(Path(d) / "client.key"),
                ca_certificates_path=str(Path(d) / "ca.pem"),
            )

            app = create_fastapi_app(ops=_TestOps())

            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=0,
                ssl_certfile=server_config.certificate_path,
                ssl_keyfile=server_config.private_key_path,
                ssl_ca_certs=server_config.ca_certificates_path,
                log_level="error",
            )
            server = uvicorn.Server(config)

            task = asyncio.get_event_loop().create_task(server.serve())

            # Wait for the server to start
            while not server.started:
                await asyncio.sleep(0.05)

            # Get the actual bound port
            port = server.servers[0].sockets[0].getsockname()[1]

            try:
                client_ssl = build_client_ssl_context(client_config)
                assert client_ssl.minimum_version == ssl.TLSVersion.TLSv1_3
                async with httpx.AsyncClient(verify=client_ssl) as hc:
                    resp = await hc.get(f"https://localhost:{port}/api/about")
                    assert resp.status_code == 200
                    grid = decode_grid(resp.content)
                    assert grid[0]["serverName"] == "TestServer"
            finally:
                server.should_exit = True
                await task

    async def test_non_tls_client_rejected(self) -> None:
        """A plain HTTP client should not connect to a TLS server."""
        import asyncio
        import ssl
        import tempfile

        import httpx
        import uvicorn

        with tempfile.TemporaryDirectory() as d:
            server_config = generate_test_certificates(d)
            app = create_fastapi_app(ops=_TestOps())

            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=0,
                ssl_certfile=server_config.certificate_path,
                ssl_keyfile=server_config.private_key_path,
                ssl_ca_certs=server_config.ca_certificates_path,
                log_level="error",
            )
            server = uvicorn.Server(config)

            task = asyncio.get_event_loop().create_task(server.serve())

            while not server.started:
                await asyncio.sleep(0.05)

            port = server.servers[0].sockets[0].getsockname()[1]

            try:
                async with httpx.AsyncClient() as hc:
                    with pytest.raises(
                        (httpx.ConnectError, httpx.RemoteProtocolError, ssl.SSLError, OSError)
                    ):
                        await hc.get(f"http://localhost:{port}/api/about")
            finally:
                server.should_exit = True
                await task
