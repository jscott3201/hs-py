"""Additional client tests for coverage of uncovered lines."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hs_py.auth import (
    _b64url_decode,
    _b64url_encode,
    _derive_key,
    _hash_digest,
    _hmac,
    _parse_header_params,
    _parse_scram_msg,
)
from hs_py.client import Client
from hs_py.encoding.json import decode_grid, encode_grid
from hs_py.errors import AuthError, NetworkError
from hs_py.grid import Grid, GridBuilder
from hs_py.kinds import Number, Ref


def _json_response(grid: Grid) -> web.Response:
    return web.Response(body=encode_grid(grid), content_type="application/json")


# ---- SCRAM test credentials (same as test_auth.py) -------------------------

_TEST_USER = "testuser"
_TEST_PASS = "testpass"
_TEST_SALT = b"testsalt12345678"
_TEST_ITER = 4096
_TEST_AUTH_TOKEN = "test-bearer-token-xyz"


def _derive_scram_keys(password: str, salt: bytes, iterations: int) -> tuple[bytes, bytes, bytes]:
    salted = _derive_key(password.encode(), salt, iterations, "sha256")
    client_key = _hmac("sha256", salted, b"Client Key")
    server_key = _hmac("sha256", salted, b"Server Key")
    stored_key = _hash_digest("sha256", client_key)
    return salted, stored_key, server_key


# ---- Repr ------------------------------------------------------------------


class TestClientRepr:
    def test_repr(self) -> None:
        c = Client("http://host/api", "admin", "secret")
        r = repr(c)
        assert "Client(" in r
        assert "http://host/api" in r
        assert "admin" in r


# ---- Close / lifecycle -----------------------------------------------------


class TestClientClose(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        app.router.add_get("/api/close", self._handle_close)
        return app

    async def _handle_about(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_rows([{"serverName": "Test"}]))

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    def _make_client(self) -> Client:
        base_url = f"http://localhost:{self.server.port}/api"
        client = Client(base_url, pythonic=False)
        client._session = self.client.session
        client._auth_token = ""
        return client

    async def test_close_cleans_up(self) -> None:
        base_url = f"http://localhost:{self.server.port}/api"
        c = Client(base_url, pythonic=False)
        async with c:
            c._auth_token = ""
            grid = await c.about()
            assert grid[0]["serverName"] == "Test"
        assert c._session is None
        assert c._auth_token is None

    async def test_close_when_already_closed(self) -> None:
        c = Client("http://localhost/api")
        await c.close()  # no-op when session is None


# ---- his_read_batch / his_write_batch --------------------------------------


class TestClientBatchOps(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/close", self._handle_close)
        app.router.add_post("/api/hisRead", self._handle_his_read)
        app.router.add_post("/api/hisWrite", self._handle_his_write)
        app.router.add_post("/api/read", self._handle_read)
        return app

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def _handle_his_read(self, request: web.Request) -> web.Response:
        body = await request.read()
        req_grid = decode_grid(body)
        meta: dict[str, Any] = {
            "id": req_grid[0]["id"],
            "hisStart": "start",
            "hisEnd": "end",
        }
        grid = (
            GridBuilder()
            .set_meta(meta)
            .add_col("ts")
            .add_col("val")
            .add_row({"ts": "2024-01-01T00:00:00Z", "val": Number(72.0, "°F")})
            .to_grid()
        )
        return _json_response(grid)

    async def _handle_his_write(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def _handle_read(self, _request: web.Request) -> web.Response:
        grid = Grid.make_rows([{"id": Ref("p1"), "dis": "Point 1"}])
        return _json_response(grid)

    def _make_client(self) -> Client:
        base_url = f"http://localhost:{self.server.port}/api"
        client = Client(base_url, pythonic=False)
        client._session = self.client.session
        client._auth_token = ""
        return client

    async def test_his_read_batch(self) -> None:
        c = self._make_client()
        grid = await c.his_read_batch([Ref("p1"), Ref("p2")], "today")
        assert len(grid) >= 1
        assert grid.meta["id"] == Ref("p1")

    async def test_his_write_batch(self) -> None:
        c = self._make_client()
        batch_grid = (
            GridBuilder()
            .add_col("ts")
            .add_col("v0")
            .add_row({"ts": "2024-01-01T00:00:00Z", "v0": Number(72.0)})
            .to_grid()
        )
        await c.his_write_batch(batch_grid)

    async def test_read_with_limit(self) -> None:
        c = self._make_client()
        grid = await c.read("point", limit=10)
        assert len(grid) >= 1


# ---- point_write with duration, watch_sub with lease, watch_poll refresh ---


class TestClientMiscOps(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/close", self._handle_close)
        app.router.add_post("/api/pointWrite", self._handle_point_write)
        app.router.add_post("/api/watchSub", self._handle_watch_sub)
        app.router.add_post("/api/watchPoll", self._handle_watch_poll)
        app.router.add_post("/api/invokeAction", self._handle_invoke_action)
        return app

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def _handle_point_write(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def _handle_watch_sub(self, request: web.Request) -> web.Response:
        body = await request.read()
        req_grid = decode_grid(body)
        rows = [{"id": row["id"], "dis": f"Watched {row['id'].val}"} for row in req_grid]
        meta: dict[str, Any] = {"watchId": "w-001", "lease": Number(60, "s")}
        builder = GridBuilder().set_meta(meta).add_col("id").add_col("dis")
        for row in rows:
            builder.add_row(row)
        return _json_response(builder.to_grid())

    async def _handle_watch_poll(self, _request: web.Request) -> web.Response:
        grid = Grid.make_rows([{"id": Ref("p1"), "val": Number(73.0, "°F")}])
        return _json_response(grid)

    async def _handle_invoke_action(self, request: web.Request) -> web.Response:
        body = await request.read()
        req_grid = decode_grid(body)
        action = req_grid.meta.get("action", "unknown")
        grid = Grid.make_rows([{"result": f"Invoked {action}"}])
        return _json_response(grid)

    def _make_client(self) -> Client:
        base_url = f"http://localhost:{self.server.port}/api"
        client = Client(base_url, pythonic=False)
        client._session = self.client.session
        client._auth_token = ""
        return client

    async def test_point_write_with_duration(self) -> None:
        c = self._make_client()
        await c.point_write(Ref("p1"), 8, Number(72.0), duration=Number(1, "hr"))

    async def test_watch_sub_with_lease(self) -> None:
        c = self._make_client()
        grid = await c.watch_sub([Ref("p1")], "Test", lease=Number(60, "s"))
        assert grid.meta["watchId"] == "w-001"

    async def test_watch_poll_with_refresh(self) -> None:
        c = self._make_client()
        grid = await c.watch_poll("w-001", refresh=True)
        assert len(grid) == 1

    async def test_invoke_action_with_args(self) -> None:
        c = self._make_client()
        grid = await c.invoke_action(
            Ref("p1"), "doSomething", {"speed": Number(50), "mode": "auto"}
        )
        assert grid[0]["result"] == "Invoked doSomething"

    async def test_invoke_action_no_args(self) -> None:
        c = self._make_client()
        grid = await c.invoke_action(Ref("p1"), "doSomething")
        assert grid[0]["result"] == "Invoked doSomething"


# ---- Auth paths ------------------------------------------------------------


class TestClientAuth(AioHTTPTestCase):
    """Test _ensure_auth and _auth_headers paths."""

    _scram_state_key: web.AppKey[dict[str, str]] = web.AppKey("scram_state")

    async def get_application(self) -> web.Application:
        app = web.Application()
        app[self._scram_state_key] = {}
        app.router.add_get("/api/about", self._handle_about)
        app.router.add_get("/api/close", self._handle_close)
        return app

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def _handle_about(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")

        # HELLO
        if auth.startswith("HELLO"):
            params = _parse_header_params(auth)
            username_b64 = params.get("username", "")
            username = _b64url_decode(username_b64).decode()
            if username != _TEST_USER:
                return web.Response(status=403, text="Unknown user")
            return web.Response(
                status=401,
                headers={"WWW-Authenticate": "SCRAM handshakeToken=hs-token-1, hash=SHA-256"},
            )

        # SCRAM
        if auth.startswith("SCRAM"):
            params = _parse_header_params(auth)
            data = _b64url_decode(params.get("data", "")).decode()

            if data.startswith("n,,"):
                client_first_bare = data[3:]
                scram_params = _parse_scram_msg(client_first_bare)
                c_nonce = scram_params["r"]
                s_nonce = c_nonce + "server-nonce-abc"
                salt_b64 = base64.b64encode(_TEST_SALT).decode()
                server_first = f"r={s_nonce},s={salt_b64},i={_TEST_ITER}"
                state = request.app[self._scram_state_key]
                state["client_first_bare"] = client_first_bare
                state["server_first"] = server_first
                return web.Response(
                    status=401,
                    headers={
                        "WWW-Authenticate": (
                            f"SCRAM handshakeToken=hs-token-2, "
                            f"hash=SHA-256, "
                            f"data={_b64url_encode(server_first.encode())}"
                        )
                    },
                )

            # Client final
            state = request.app[self._scram_state_key]
            client_final_params = _parse_scram_msg(data)
            proof_b64 = client_final_params.get("p", "")
            client_proof = base64.b64decode(proof_b64)

            _salted, stored_key, server_key = _derive_scram_keys(
                _TEST_PASS, _TEST_SALT, _TEST_ITER
            )
            client_first_bare = state["client_first_bare"]
            server_first = state["server_first"]
            client_final_no_proof = data.rsplit(",p=", 1)[0]
            auth_message = f"{client_first_bare},{server_first},{client_final_no_proof}"

            client_signature = _hmac("sha256", stored_key, auth_message.encode())
            recovered_client_key = bytes(
                a ^ b for a, b in zip(client_proof, client_signature, strict=True)
            )
            recovered_stored_key = _hash_digest("sha256", recovered_client_key)
            if recovered_stored_key != stored_key:
                return web.Response(status=401, text="Invalid proof")

            server_sig = _hmac("sha256", server_key, auth_message.encode())
            server_sig_b64 = base64.b64encode(server_sig).decode()
            server_final = f"v={server_sig_b64}"

            return web.Response(
                status=200,
                headers={
                    "Authentication-Info": (
                        f"authToken={_TEST_AUTH_TOKEN}, "
                        f"data={_b64url_encode(server_final.encode())}"
                    )
                },
                text="OK",
            )

        # Authenticated request with bearer token
        if auth.startswith("BEARER"):
            return _json_response(Grid.make_rows([{"serverName": "Authed"}]))

        return web.Response(status=401, text="Unauthorized")

    async def test_ensure_auth_no_username(self) -> None:
        """Empty username → no auth, token set to empty string."""
        base_url = f"http://localhost:{self.server.port}/api"
        c = Client(base_url, pythonic=False)
        c._session = self.client.session
        await c._ensure_auth()
        assert c._auth_token == ""

    async def test_ensure_auth_with_scram(self) -> None:
        """Username provided → full SCRAM handshake, token set."""
        base_url = f"http://localhost:{self.server.port}/api"
        c = Client(base_url, _TEST_USER, _TEST_PASS, pythonic=False)
        c._session = self.client.session
        await c._ensure_auth()
        assert c._auth_token == _TEST_AUTH_TOKEN
        # Password should be cleared after auth
        assert c._password == ""

    async def test_auth_headers_with_token(self) -> None:
        c = Client("http://host/api")
        c._auth_token = "my-token"
        headers = c._auth_headers()
        assert headers["Authorization"] == "BEARER authToken=my-token"

    async def test_auth_headers_without_token(self) -> None:
        c = Client("http://host/api")
        c._auth_token = ""
        headers = c._auth_headers()
        assert "Authorization" not in headers

    async def test_full_scram_about(self) -> None:
        """Client with credentials performs SCRAM then uses bearer token."""
        base_url = f"http://localhost:{self.server.port}/api"
        c = Client(base_url, _TEST_USER, _TEST_PASS, pythonic=False)
        c._session = self.client.session
        grid = await c.about()
        assert grid[0]["serverName"] == "Authed"


# ---- Re-auth on 401 -------------------------------------------------------


class TestClientReauth(AioHTTPTestCase):
    """Test automatic re-authentication on 401 response."""

    _call_count: int = 0

    async def get_application(self) -> web.Application:
        self._call_count = 0
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        app.router.add_get("/api/close", self._handle_close)
        return app

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def _handle_about(self, request: web.Request) -> web.Response:
        count = self._call_count
        self._call_count = count + 1
        if count == 0:
            # First call: return 401 to trigger re-auth
            return web.Response(status=401, text="Expired")
        # Second call after re-auth: succeed
        return _json_response(Grid.make_rows([{"serverName": "Reauthed"}]))

    async def test_reauth_on_401(self) -> None:
        base_url = f"http://localhost:{self.server.port}/api"
        c = Client(base_url, pythonic=False)
        c._session = self.client.session
        # Pre-set an expired token so _ensure_auth is a no-op
        c._auth_token = "expired-token"
        grid = await c.about()
        # After 401, client clears token, re-auths (empty username → empty token),
        # then retries and gets the success response
        assert grid[0]["serverName"] == "Reauthed"


# ---- _handle_response edge cases ------------------------------------------


class TestHandleResponse(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/empty", self._handle_empty)
        app.router.add_get("/api/auth401", self._handle_auth_401)
        app.router.add_get("/api/close", self._handle_close)
        return app

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def _handle_empty(self, _request: web.Request) -> web.Response:
        return web.Response(body=b"", content_type="application/json")

    async def _handle_auth_401(self, _request: web.Request) -> web.Response:
        return web.Response(status=401, text="Denied")

    def _make_client(self) -> Client:
        base_url = f"http://localhost:{self.server.port}/api"
        client = Client(base_url, pythonic=False)
        client._session = self.client.session
        client._auth_token = ""
        return client

    async def test_empty_body_returns_empty_grid(self) -> None:
        c = self._make_client()
        grid = await c._call_get("empty")
        assert len(grid) == 0

    async def test_handle_response_401_raises_auth_error(self) -> None:
        """401 on the *retry* raises AuthError via _handle_response."""
        base_url = f"http://localhost:{self.server.port}/api"
        c = Client(base_url, pythonic=False)
        c._session = self.client.session
        c._auth_token = ""
        try:
            await c._call_get("auth401")
            raise AssertionError("should raise AuthError")
        except AuthError:
            pass


# ---- NetworkError wrapping -------------------------------------------------


class TestNetworkError(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        return web.Application()

    async def test_network_error_on_client_error(self) -> None:
        # Point client at a port that doesn't have the route → connection works
        # but we need a real network error. Use a bad host instead.
        c = Client("http://127.0.0.1:1/api", pythonic=False)
        c._auth_token = ""
        c._session = self.client.session
        try:
            await c._call_get("about")
            raise AssertionError("should raise NetworkError")
        except NetworkError:
            pass


# ---- 406/415 response handling ---------------------------------------------


class TestClient406Response(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_406)
        app.router.add_get("/api/close", self._handle_close)
        return app

    async def _handle_406(self, _request: web.Request) -> web.Response:
        return web.Response(status=406, text="Not Acceptable")

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def test_406_raises_call_error(self) -> None:
        """Cover client.py L587: 406 → CallError."""
        from hs_py.errors import CallError

        base_url = f"http://localhost:{self.server.port}/api"
        c = Client(base_url, pythonic=False)
        c._session = self.client.session
        c._auth_token = ""
        with pytest.raises(CallError, match="Accept format"):
            await c._call_get("about")


class TestClient415Response(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_415)
        app.router.add_get("/api/close", self._handle_close)
        return app

    async def _handle_415(self, _request: web.Request) -> web.Response:
        return web.Response(status=415, text="Unsupported Media Type")

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return _json_response(Grid.make_empty())

    async def test_415_raises_call_error(self) -> None:
        """Cover client.py L589: 415 → CallError."""
        from hs_py.errors import CallError

        base_url = f"http://localhost:{self.server.port}/api"
        c = Client(base_url, pythonic=False)
        c._session = self.client.session
        c._auth_token = ""
        with pytest.raises(CallError, match="Content-Type"):
            await c._call_get("about")


# ---- Client accept_format --------------------------------------------------


class TestClientZincFormat(AioHTTPTestCase):
    """Cover client.py: accept_format='zinc' sets headers and encode/decode."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        app.router.add_post("/api/read", self._handle_read)
        app.router.add_get("/api/close", self._handle_close)
        return app

    async def _handle_about(self, request: web.Request) -> web.Response:
        assert "text/zinc" in request.headers.get("Accept", "")
        from hs_py.encoding.zinc import encode_grid as zinc_encode

        grid = Grid.make_rows([{"serverName": "ZincServer"}])
        return web.Response(body=zinc_encode(grid), content_type="text/zinc")

    async def _handle_read(self, request: web.Request) -> web.Response:
        assert "text/zinc" in request.headers.get("Content-Type", "")
        assert "text/zinc" in request.headers.get("Accept", "")
        from hs_py.encoding.zinc import encode_grid as zinc_encode

        grid = Grid.make_rows([{"id": Ref("p1"), "dis": "Point"}])
        return web.Response(body=zinc_encode(grid), content_type="text/zinc")

    async def _handle_close(self, _request: web.Request) -> web.Response:
        return web.Response(body=b"", content_type="text/zinc")

    async def test_zinc_about(self) -> None:
        base_url = f"http://localhost:{self.server.port}/api"
        c = Client(base_url, pythonic=False, accept_format="zinc")
        c._session = self.client.session
        c._auth_token = ""
        grid = await c.about()
        assert grid[0]["serverName"] == "ZincServer"

    async def test_zinc_post(self) -> None:
        base_url = f"http://localhost:{self.server.port}/api"
        c = Client(base_url, pythonic=False, accept_format="zinc")
        c._session = self.client.session
        c._auth_token = ""
        grid = await c.read("point")
        assert len(grid) == 1


# ---- Client with TLS config -----------------------------------------------


class TestClientTLSConnector:
    """Cover client.py L91-92: TLS connector creation."""

    async def test_tls_connector_created(self) -> None:
        from unittest.mock import patch

        from hs_py.tls import TLSConfig

        tls_config = TLSConfig()
        c = Client("https://host/api", tls=tls_config)
        with patch("hs_py.client.build_client_ssl_context") as mock_ssl:
            mock_ssl.return_value = None  # aiohttp accepts None for ssl
            async with c:
                assert c._session is not None
            await c.close()
