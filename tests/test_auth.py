import base64
import hashlib
import hmac as hmac_mod

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hs_py.auth import (
    _b64url_decode,
    _b64url_encode,
    _derive_key,
    _hash_algo,
    _hash_digest,
    _hmac,
    _parse_header_params,
    _parse_param,
    _parse_scram_msg,
    authenticate,
    scram_client_final,
    scram_client_first,
    verify_server_signature,
)
from hs_py.errors import AuthError

# ---- Helper unit tests -----------------------------------------------------


class TestB64Url:
    def test_encode_decode_roundtrip(self) -> None:
        data = b"hello world"
        encoded = _b64url_encode(data)
        assert "=" not in encoded
        assert _b64url_decode(encoded) == data

    def test_empty(self) -> None:
        assert _b64url_decode(_b64url_encode(b"")) == b""

    def test_various_lengths(self) -> None:
        for n in range(1, 20):
            data = bytes(range(n))
            assert _b64url_decode(_b64url_encode(data)) == data


class TestParseHeaderParams:
    def test_scram_header(self) -> None:
        header = "SCRAM handshakeToken=abc123, hash=SHA-256, data=eHl6"
        params = _parse_header_params(header)
        assert params["handshakeToken"] == "abc123"
        assert params["hash"] == "SHA-256"
        assert params["data"] == "eHl6"

    def test_empty(self) -> None:
        assert _parse_header_params("") == {}

    def test_scheme_only(self) -> None:
        assert _parse_header_params("SCRAM") == {}


class TestParseParam:
    def test_from_auth_info(self) -> None:
        header = "authToken=xyz123, hash=SHA-256"
        assert _parse_param(header, "authToken") == "xyz123"

    def test_missing(self) -> None:
        assert _parse_param("authToken=xyz", "missing") is None


class TestParseScramMsg:
    def test_server_first(self) -> None:
        msg = "r=client-nonce-server-nonce,s=c2FsdA==,i=4096"
        params = _parse_scram_msg(msg)
        assert params["r"] == "client-nonce-server-nonce"
        assert params["s"] == "c2FsdA=="
        assert params["i"] == "4096"


class TestDeriveKey:
    def test_matches_stdlib(self) -> None:
        """Verify _derive_key matches stdlib hashlib.pbkdf2_hmac output."""
        password = b"testpass"
        salt = b"testsalt12345678"
        iterations = 4096
        expected = hashlib.pbkdf2_hmac("sha256", password, salt, iterations)
        result = _derive_key(password, salt, iterations, "sha256")
        assert result == expected

    def test_different_params_different_output(self) -> None:
        k1 = _derive_key(b"pass1", b"salt1234567890ab", 4096, "sha256")
        k2 = _derive_key(b"pass2", b"salt1234567890ab", 4096, "sha256")
        assert k1 != k2

    def test_unsupported_algorithm(self) -> None:
        with pytest.raises(AuthError, match="Unsupported"):
            _derive_key(b"pass", b"salt1234567890ab", 4096, "md5")


class TestHmac:
    def test_matches_stdlib(self) -> None:
        """Verify _hmac matches stdlib hmac output."""
        key = b"secret-key"
        data = b"Client Key"
        expected = hmac_mod.new(key, data, "sha256").digest()
        result = _hmac("sha256", key, data)
        assert result == expected

    def test_different_keys_different_output(self) -> None:
        data = b"test data"
        h1 = _hmac("sha256", b"key1", data)
        h2 = _hmac("sha256", b"key2", data)
        assert h1 != h2


class TestHashDigest:
    def test_matches_stdlib(self) -> None:
        """Verify _hash_digest matches stdlib hashlib output."""
        data = b"hello world"
        expected = hashlib.sha256(data).digest()
        result = _hash_digest("sha256", data)
        assert result == expected

    def test_sha512(self) -> None:
        data = b"test data"
        expected = hashlib.sha512(data).digest()
        result = _hash_digest("sha512", data)
        assert result == expected

    def test_empty_data(self) -> None:
        expected = hashlib.sha256(b"").digest()
        result = _hash_digest("sha256", b"")
        assert result == expected


# ---- Integration tests with mock HTTP server ------------------------------

# SCRAM test credentials
_TEST_USER = "testuser"
_TEST_PASS = "testpass"
_TEST_SALT = b"testsalt12345678"
_TEST_ITER = 4096
_TEST_AUTH_TOKEN = "test-bearer-token-xyz"


def _derive_scram_keys(password: str, salt: bytes, iterations: int) -> tuple[bytes, bytes, bytes]:
    """Derive SCRAM keys for test server using cryptography library."""
    salted = _derive_key(password.encode(), salt, iterations, "sha256")
    client_key = _hmac("sha256", salted, b"Client Key")
    server_key = _hmac("sha256", salted, b"Server Key")
    stored_key = _hash_digest("sha256", client_key)
    return salted, stored_key, server_key


class TestScramAuth(AioHTTPTestCase):
    """Test SCRAM-SHA-256 authentication against a mock server."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app["scram_state"] = {}
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")

        # HELLO
        if auth.startswith("HELLO"):
            params = _parse_header_params(auth)
            username_b64 = params.get("username", "")
            username = _b64url_decode(username_b64).decode()
            if username != _TEST_USER:
                return web.Response(status=403, text="Unknown user")
            handshake_token = "hs-token-1"
            return web.Response(
                status=401,
                headers={
                    "WWW-Authenticate": (f"SCRAM handshakeToken={handshake_token}, hash=SHA-256")
                },
            )

        # SCRAM step 2 (client-first)
        if auth.startswith("SCRAM"):
            params = _parse_header_params(auth)
            data = _b64url_decode(params.get("data", "")).decode()

            # Detect step by message content
            if data.startswith("n,,"):
                # Client first message
                client_first_bare = data[3:]  # strip gs2 header
                scram_params = _parse_scram_msg(client_first_bare)
                c_nonce = scram_params["r"]
                s_nonce = c_nonce + "server-nonce-abc"

                salt_b64 = base64.b64encode(_TEST_SALT).decode()
                server_first = f"r={s_nonce},s={salt_b64},i={_TEST_ITER}"

                state = request.app["scram_state"]
                state["client_first_bare"] = client_first_bare
                state["server_first"] = server_first
                state["s_nonce"] = s_nonce

                handshake_token = "hs-token-2"
                return web.Response(
                    status=401,
                    headers={
                        "WWW-Authenticate": (
                            f"SCRAM handshakeToken={handshake_token}, "
                            f"hash=SHA-256, "
                            f"data={_b64url_encode(server_first.encode())}"
                        )
                    },
                )

            # Client final message
            state = request.app["scram_state"]
            client_final_params = _parse_scram_msg(data)
            proof_b64 = client_final_params.get("p", "")
            client_proof = base64.b64decode(proof_b64)

            # Verify client proof
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

            # Server signature
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

        return web.Response(status=401, text="Unauthorized")

    async def test_scram_success(self) -> None:
        base_url = f"http://localhost:{self.server.port}/api"
        token = await authenticate(self.client.session, base_url, _TEST_USER, _TEST_PASS)
        assert token == _TEST_AUTH_TOKEN

    async def test_scram_wrong_user(self) -> None:
        base_url = f"http://localhost:{self.server.port}/api"
        with pytest.raises(AuthError):
            await authenticate(self.client.session, base_url, "wrong", _TEST_PASS)


class TestPlaintextAuth(AioHTTPTestCase):
    """Test PLAINTEXT authentication against a mock server."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")

        if auth.startswith("HELLO"):
            return web.Response(
                status=401,
                headers={"WWW-Authenticate": "PLAINTEXT"},
            )

        if auth.startswith("PLAINTEXT"):
            params = _parse_header_params(auth)
            username = _b64url_decode(params.get("username", "")).decode()
            password = _b64url_decode(params.get("password", "")).decode()
            if username == _TEST_USER and password == _TEST_PASS:
                return web.Response(
                    status=200,
                    headers={"Authentication-Info": f"authToken={_TEST_AUTH_TOKEN}"},
                    text="OK",
                )
            return web.Response(status=403, text="Forbidden")

        return web.Response(status=401, text="Unauthorized")

    async def test_plaintext_success(self) -> None:
        base_url = f"http://localhost:{self.server.port}/api"
        token = await authenticate(self.client.session, base_url, _TEST_USER, _TEST_PASS)
        assert token == _TEST_AUTH_TOKEN


class TestNoAuthRequired(AioHTTPTestCase):
    """Test server that doesn't require auth."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, _request: web.Request) -> web.Response:
        return web.Response(status=200, text="OK")

    async def test_no_auth(self) -> None:
        base_url = f"http://localhost:{self.server.port}/api"
        token = await authenticate(self.client.session, base_url, _TEST_USER, _TEST_PASS)
        assert token == ""


class TestNoAuthWithToken(AioHTTPTestCase):
    """Test server that returns 200 on HELLO with an authToken."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, _request: web.Request) -> web.Response:
        return web.Response(
            status=200,
            headers={"Authentication-Info": f"authToken={_TEST_AUTH_TOKEN}"},
            text="OK",
        )

    async def test_hello_returns_token(self) -> None:
        """Cover auth.py L70: server returns 200 on HELLO with token present."""
        base_url = f"http://localhost:{self.server.port}/api"
        token = await authenticate(self.client.session, base_url, _TEST_USER, _TEST_PASS)
        assert token == _TEST_AUTH_TOKEN


class TestUnsupportedAuthMechanism(AioHTTPTestCase):
    """Test server that returns 401 with an unknown auth mechanism."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, _request: web.Request) -> web.Response:
        auth = _request.headers.get("Authorization", "")
        if auth.startswith("HELLO"):
            return web.Response(
                status=401,
                headers={"WWW-Authenticate": "KERBEROS realm=test"},
            )
        return web.Response(status=401)

    async def test_unsupported_mechanism_raises(self) -> None:
        """Cover auth.py L91-92: no supported auth mechanism."""
        base_url = f"http://localhost:{self.server.port}/api"
        with pytest.raises(AuthError, match="No supported auth mechanism"):
            await authenticate(self.client.session, base_url, _TEST_USER, _TEST_PASS)


class TestHelloUnexpectedStatus(AioHTTPTestCase):
    """Test server that returns unexpected status on HELLO."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, _request: web.Request) -> web.Response:
        return web.Response(status=500, text="Server Error")

    async def test_unexpected_status_raises(self) -> None:
        """Cover auth.py L73-74: unexpected status during HELLO."""
        base_url = f"http://localhost:{self.server.port}/api"
        with pytest.raises(AuthError, match="Unexpected status 500"):
            await authenticate(self.client.session, base_url, _TEST_USER, _TEST_PASS)


class TestScramStep1Failure(AioHTTPTestCase):
    """Test SCRAM failure at step 1 (server returns non-401)."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app["step"] = 0
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("HELLO"):
            return web.Response(
                status=401,
                headers={"WWW-Authenticate": "SCRAM handshakeToken=tok1, hash=SHA-256"},
            )
        if auth.startswith("SCRAM"):
            return web.Response(status=500, text="Internal Error")
        return web.Response(status=401)

    async def test_step1_non_401(self) -> None:
        """Cover auth.py L232-233: SCRAM step 1 non-401."""
        base_url = f"http://localhost:{self.server.port}/api"
        with pytest.raises(AuthError, match="Expected 401"):
            await authenticate(self.client.session, base_url, _TEST_USER, _TEST_PASS)


class TestScramStep2Failure(AioHTTPTestCase):
    """Test SCRAM failure at step 2 (server returns non-200)."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app["scram_state"] = {}
        app["call"] = 0
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("HELLO"):
            return web.Response(
                status=401,
                headers={"WWW-Authenticate": "SCRAM handshakeToken=tok1, hash=SHA-256"},
            )
        if auth.startswith("SCRAM"):
            request.app["call"] += 1
            if request.app["call"] == 1:
                # Step 1: return valid server-first
                params = _parse_header_params(auth)
                data = _b64url_decode(params.get("data", "")).decode()
                client_first_bare = data[3:]
                scram_params = _parse_scram_msg(client_first_bare)
                c_nonce = scram_params["r"]
                s_nonce = c_nonce + "server-nonce-abc"
                salt_b64 = base64.b64encode(_TEST_SALT).decode()
                server_first = f"r={s_nonce},s={salt_b64},i={_TEST_ITER}"
                return web.Response(
                    status=401,
                    headers={
                        "WWW-Authenticate": (
                            f"SCRAM handshakeToken=tok2, hash=SHA-256, "
                            f"data={_b64url_encode(server_first.encode())}"
                        )
                    },
                )
            # Step 2: return 403
            return web.Response(status=403, text="Forbidden")
        return web.Response(status=401)

    async def test_step2_non_200(self) -> None:
        """Cover auth.py L251-252: SCRAM auth failed."""
        base_url = f"http://localhost:{self.server.port}/api"
        with pytest.raises(AuthError, match="SCRAM auth failed"):
            await authenticate(self.client.session, base_url, _TEST_USER, _TEST_PASS)


class TestScramNoTokenInFinal(AioHTTPTestCase):
    """Test SCRAM success but no authToken in final response."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app["scram_state"] = {}
        app["call"] = 0
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("HELLO"):
            return web.Response(
                status=401,
                headers={"WWW-Authenticate": "SCRAM handshakeToken=tok1, hash=SHA-256"},
            )
        if auth.startswith("SCRAM"):
            request.app["call"] += 1
            if request.app["call"] == 1:
                params = _parse_header_params(auth)
                data = _b64url_decode(params.get("data", "")).decode()
                client_first_bare = data[3:]
                scram_params = _parse_scram_msg(client_first_bare)
                c_nonce = scram_params["r"]
                s_nonce = c_nonce + "server-nonce-abc"
                salt_b64 = base64.b64encode(_TEST_SALT).decode()
                server_first = f"r={s_nonce},s={salt_b64},i={_TEST_ITER}"
                return web.Response(
                    status=401,
                    headers={
                        "WWW-Authenticate": (
                            f"SCRAM handshakeToken=tok2, hash=SHA-256, "
                            f"data={_b64url_encode(server_first.encode())}"
                        )
                    },
                )
            # Step 2: return 200 but no authToken
            return web.Response(
                status=200,
                headers={"Authentication-Info": ""},
                text="OK",
            )
        return web.Response(status=401)

    async def test_no_token_in_final(self) -> None:
        """Cover auth.py L257-258: no authToken in final SCRAM response."""
        base_url = f"http://localhost:{self.server.port}/api"
        with pytest.raises(AuthError, match="No authToken in final SCRAM"):
            await authenticate(self.client.session, base_url, _TEST_USER, _TEST_PASS)


class TestPlaintextAuthFailure(AioHTTPTestCase):
    """Test PLAINTEXT auth with bad credentials."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("HELLO"):
            return web.Response(
                status=401,
                headers={"WWW-Authenticate": "PLAINTEXT"},
            )
        if auth.startswith("PLAINTEXT"):
            return web.Response(status=403, text="Forbidden")
        return web.Response(status=401)

    async def test_plaintext_failure(self) -> None:
        """Cover auth.py L286: PLAINTEXT auth non-200."""
        base_url = f"http://localhost:{self.server.port}/api"
        with pytest.raises(AuthError, match="PLAINTEXT auth failed"):
            await authenticate(self.client.session, base_url, "wrong", "wrong")


class TestPlaintextNoToken(AioHTTPTestCase):
    """Test PLAINTEXT auth succeeds but no authToken."""

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/about", self._handle_about)
        return app

    async def _handle_about(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("HELLO"):
            return web.Response(
                status=401,
                headers={"WWW-Authenticate": "PLAINTEXT"},
            )
        if auth.startswith("PLAINTEXT"):
            return web.Response(
                status=200,
                headers={"Authentication-Info": ""},
                text="OK",
            )
        return web.Response(status=401)

    async def test_no_token_in_plaintext(self) -> None:
        """Cover auth.py L291: no authToken in PLAINTEXT response."""
        base_url = f"http://localhost:{self.server.port}/api"
        with pytest.raises(AuthError, match="No authToken in PLAINTEXT"):
            await authenticate(self.client.session, base_url, _TEST_USER, _TEST_PASS)


# ---- Transport-independent SCRAM helper tests -----------------------------


class TestScramClientFirst:
    def test_returns_valid_message(self) -> None:
        result = scram_client_first("admin")
        assert result.client_first_msg.startswith("n,,n=admin,r=")
        assert result.client_first_bare.startswith("n=admin,r=")
        assert len(result.c_nonce) > 10

    def test_nonces_are_unique(self) -> None:
        r1 = scram_client_first("user")
        r2 = scram_client_first("user")
        assert r1.c_nonce != r2.c_nonce


class TestScramClientFinal:
    def test_nonce_mismatch_raises(self) -> None:
        """Cover auth.py L165: nonce mismatch."""
        first = scram_client_first("user")
        server_first = "r=totally-different-nonce,s=c2FsdA==,i=4096"
        with pytest.raises(AuthError, match="nonce"):
            scram_client_final("pass", first, server_first)


class TestVerifyServerSignature:
    def _make_final(self) -> tuple:
        """Create a valid SCRAM exchange for testing."""
        password = "testpass"
        salt = b"testsalt12345678"
        iterations = 4096
        first = scram_client_first("user")
        salted_pw = _derive_key(password.encode(), salt, iterations, "sha256")
        c_nonce = first.c_nonce
        s_nonce = c_nonce + "servernonce"
        salt_b64 = base64.b64encode(salt).decode()
        server_first = f"r={s_nonce},s={salt_b64},i={iterations}"
        final = scram_client_final(password, first, server_first)
        # Compute correct server sig
        server_key = _hmac("sha256", salted_pw, b"Server Key")
        server_sig = _hmac("sha256", server_key, final.auth_message.encode())
        return final, server_sig

    def test_missing_v_raises_error(self) -> None:
        """Missing v= in server-final message should raise AuthError."""
        final, _ = self._make_final()
        with pytest.raises(AuthError, match="Server signature missing"):
            verify_server_signature(final, "something=else")

    def test_bad_server_signature_raises(self) -> None:
        """Cover auth.py L206: server signature verification failed."""
        final, _ = self._make_final()
        bad_sig = base64.b64encode(b"bad-signature-00000000000000000").decode()
        with pytest.raises(AuthError, match="Server signature verification failed"):
            verify_server_signature(final, f"v={bad_sig}")

    def test_valid_server_signature(self) -> None:
        final, server_sig = self._make_final()
        server_sig_b64 = base64.b64encode(server_sig).decode()
        # Should not raise
        verify_server_signature(final, f"v={server_sig_b64}")


class TestHashAlgo:
    def test_unsupported_hash(self) -> None:
        """Cover auth.py L405: unsupported hash in _hash_algo."""
        with pytest.raises(AuthError, match="Unsupported"):
            _hash_algo("MD5")
