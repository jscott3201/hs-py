"""Tests for _scram_core.py — server-side SCRAM-SHA-256 handshake logic."""

from __future__ import annotations

import time

import pytest

from hs_py._scram_core import (
    HandshakeState,
    TokenEntry,
    handle_scram,
    purge_expired_handshakes,
    purge_expired_tokens,
    scram_hello,
    scram_step1,
    scram_step2,
    validate_bearer,
)
from hs_py.auth import (
    _b64url_encode,
    scram_client_final,
    scram_client_first,
)
from hs_py.auth_types import SimpleAuthenticator

_USER = "testuser"
_PASS = "testpass"


@pytest.fixture()
def authenticator() -> SimpleAuthenticator:
    return SimpleAuthenticator({_USER: _PASS}, iterations=4096)


class TestPurgeExpiredTokens:
    def test_removes_expired(self) -> None:
        """Cover _scram_core.py L103: del tokens[k]."""
        tokens: dict[str, TokenEntry] = {
            "fresh": TokenEntry(username="a", created=time.monotonic()),
            "old": TokenEntry(username="b", created=time.monotonic() - 9999),
        }
        purge_expired_tokens(tokens)
        assert "fresh" in tokens
        assert "old" not in tokens


class TestPurgeExpiredHandshakes:
    def test_removes_expired(self) -> None:
        hs: dict[str, HandshakeState] = {
            "ok": HandshakeState(
                username="a",
                server_nonce="",
                salt=b"s",
                iterations=1,
                stored_key=b"k",
                server_key=b"k",
                created=time.monotonic(),
            ),
            "stale": HandshakeState(
                username="b",
                server_nonce="",
                salt=b"s",
                iterations=1,
                stored_key=b"k",
                server_key=b"k",
                created=time.monotonic() - 9999,
            ),
        }
        purge_expired_handshakes(hs)
        assert "ok" in hs
        assert "stale" not in hs


class TestScramStep1NoGs2Header:
    def test_client_first_without_gs2_prefix(self) -> None:
        """Cover _scram_core.py L168: client_first_msg without 'n,,' prefix."""
        state = HandshakeState(
            username="u",
            server_nonce="",
            salt=b"salt1234567890ab",
            iterations=4096,
            stored_key=b"k" * 32,
            server_key=b"s" * 32,
        )
        handshakes: dict[str, HandshakeState] = {"tok1": state}
        # Send bare message without gs2 header
        result = scram_step1(handshakes, "tok1", state, "n=u,r=clientnonce")
        assert result.status == 401
        assert "handshakeToken" in result.headers.get("WWW-Authenticate", "")


class TestScramStep2EdgeCases:
    def test_empty_proof(self) -> None:
        """Cover _scram_core.py L218: empty proof returns 401."""
        state = HandshakeState(
            username="u",
            server_nonce="combined",
            salt=b"salt",
            iterations=4096,
            stored_key=b"k" * 32,
            server_key=b"s" * 32,
            auth_message="test",
            step=2,
        )
        tokens: dict[str, TokenEntry] = {}
        handshakes: dict[str, HandshakeState] = {"tok": state}
        result = scram_step2(handshakes, tokens, "tok", state, "c=biws,r=combined")
        assert result.status == 401

    def test_proof_length_mismatch(self) -> None:
        """Cover _scram_core.py L225: client_proof length != client_signature."""
        state = HandshakeState(
            username="u",
            server_nonce="combined",
            salt=b"salt",
            iterations=4096,
            stored_key=b"k" * 32,
            server_key=b"s" * 32,
            auth_message="test",
            step=2,
        )
        tokens: dict[str, TokenEntry] = {}
        handshakes: dict[str, HandshakeState] = {"tok": state}
        import base64

        # Short proof that won't match 32-byte HMAC
        bad_proof = base64.b64encode(b"short").decode()
        result = scram_step2(handshakes, tokens, "tok", state, f"c=biws,r=combined,p={bad_proof}")
        assert result.status == 403

    def test_proof_invalid_base64_exception(self) -> None:
        """Cover _scram_core.py L228-229: exception during proof decode."""
        state = HandshakeState(
            username="u",
            server_nonce="combined",
            salt=b"salt",
            iterations=4096,
            stored_key=b"k" * 32,
            server_key=b"s" * 32,
            auth_message="test",
            step=2,
        )
        tokens: dict[str, TokenEntry] = {}
        handshakes: dict[str, HandshakeState] = {"tok": state}
        # Invalid base64 that will cause b64decode to raise
        result = scram_step2(handshakes, tokens, "tok", state, "c=biws,r=combined,p=!!!invalid!!!")
        assert result.status == 403

    async def test_max_tokens_evicts_oldest(self) -> None:
        """Cover _scram_core.py L241-242: evict oldest when MAX_TOKENS reached."""
        from hs_py._scram_core import MAX_TOKENS

        auth = SimpleAuthenticator({_USER: _PASS}, iterations=4096)
        creds = await auth.scram_credentials(_USER)
        assert creds is not None

        state = HandshakeState(
            username=_USER,
            server_nonce="",
            salt=creds.salt,
            iterations=creds.iterations,
            stored_key=creds.stored_key,
            server_key=creds.server_key,
        )

        # Do step1 to set up auth_message
        handshakes: dict[str, HandshakeState] = {"tok1": state}
        first = scram_client_first(_USER)
        result1 = scram_step1(handshakes, "tok1", state, first.client_first_msg)
        assert result1.status == 401

        # Extract server-first from result
        www_auth = result1.headers["WWW-Authenticate"]
        # Parse handshakeToken and data
        import re

        ht_match = re.search(r"handshakeToken=(\S+),", www_auth)
        data_match = re.search(r"data=(\S+)", www_auth)
        assert ht_match and data_match
        new_ht = ht_match.group(1)
        from hs_py.auth import _b64url_decode

        server_first_msg = _b64url_decode(data_match.group(1)).decode()

        final = scram_client_final(_PASS, first, server_first_msg)

        # Pre-fill tokens to MAX_TOKENS (all fresh so they won't be purged)
        tokens: dict[str, TokenEntry] = {}
        now = time.monotonic()
        for i in range(MAX_TOKENS):
            tokens[f"tok-{i}"] = TokenEntry(username="u", created=now - i * 0.0001)
        oldest_key = f"tok-{MAX_TOKENS - 1}"
        assert oldest_key in tokens

        result2 = scram_step2(handshakes, tokens, new_ht, state, final.client_final_msg)
        assert result2.status == 200
        # Oldest token should have been evicted
        assert oldest_key not in tokens


class TestHandleScramInvalidStep:
    def test_step_beyond_2(self) -> None:
        """Cover _scram_core.py L308-309: invalid step number."""
        state = HandshakeState(
            username="u",
            server_nonce="",
            salt=b"salt",
            iterations=4096,
            stored_key=b"k" * 32,
            server_key=b"s" * 32,
            step=3,
        )
        handshakes: dict[str, HandshakeState] = {"tok": state}
        tokens: dict[str, TokenEntry] = {}
        auth_header = f"SCRAM handshakeToken=tok, data={_b64url_encode(b'test')}"
        result = handle_scram(handshakes, tokens, auth_header)
        assert result.status == 401
        assert "tok" not in handshakes


class TestHandleScramExpiredHandshake:
    def test_expired_handshake(self) -> None:
        state = HandshakeState(
            username="u",
            server_nonce="",
            salt=b"salt",
            iterations=4096,
            stored_key=b"k" * 32,
            server_key=b"s" * 32,
            created=time.monotonic() - 9999,
        )
        handshakes: dict[str, HandshakeState] = {"tok": state}
        tokens: dict[str, TokenEntry] = {}
        auth_header = f"SCRAM handshakeToken=tok, data={_b64url_encode(b'test')}"
        result = handle_scram(handshakes, tokens, auth_header)
        assert result.status == 401


class TestHandleScramBadData:
    def test_invalid_base64_data(self) -> None:
        """Cover _scram_core.py L300-301: bad base64 in data."""
        state = HandshakeState(
            username="u",
            server_nonce="",
            salt=b"salt",
            iterations=4096,
            stored_key=b"k" * 32,
            server_key=b"s" * 32,
        )
        handshakes: dict[str, HandshakeState] = {"tok": state}
        tokens: dict[str, TokenEntry] = {}
        # Use invalid UTF-8 after decoding by encoding raw bytes
        import base64

        bad_b64 = base64.urlsafe_b64encode(b"\xff\xfe").rstrip(b"=").decode()
        auth_header = f"SCRAM handshakeToken=tok, data={bad_b64}"
        result = handle_scram(handshakes, tokens, auth_header)
        assert result.status == 401


class TestValidateBearer:
    def test_expired_token(self) -> None:
        tokens: dict[str, TokenEntry] = {
            "old-tok": TokenEntry(username="u", created=time.monotonic() - 9999),
        }
        result = validate_bearer(tokens, "Bearer authToken=old-tok")
        assert result is not None
        assert result.status == 401
        assert "old-tok" not in tokens

    def test_missing_token(self) -> None:
        tokens: dict[str, TokenEntry] = {}
        result = validate_bearer(tokens, "Bearer authToken=nonexistent")
        assert result is not None
        assert result.status == 401

    def test_valid_token(self) -> None:
        tokens: dict[str, TokenEntry] = {
            "good": TokenEntry(username="u", created=time.monotonic()),
        }
        result = validate_bearer(tokens, "Bearer authToken=good")
        assert result is None


class TestScramHello:
    async def test_bad_username_base64(self) -> None:
        """Cover scram_hello with invalid base64 username."""
        auth = SimpleAuthenticator({_USER: _PASS}, iterations=4096)
        handshakes: dict[str, HandshakeState] = {}
        result = await scram_hello(auth, handshakes, "HELLO username=%%%invalid")
        assert result.status == 401

    async def test_unknown_user(self) -> None:
        auth = SimpleAuthenticator({_USER: _PASS}, iterations=4096)
        handshakes: dict[str, HandshakeState] = {}
        result = await scram_hello(auth, handshakes, f"HELLO username={_b64url_encode(b'nobody')}")
        assert result.status == 401

    async def test_max_handshakes(self) -> None:
        """Cover scram_hello max handshakes limit."""
        from hs_py._scram_core import MAX_HANDSHAKES

        auth = SimpleAuthenticator({_USER: _PASS}, iterations=4096)
        handshakes: dict[str, HandshakeState] = {}
        for i in range(MAX_HANDSHAKES):
            handshakes[f"hs-{i}"] = HandshakeState(
                username="u",
                server_nonce="",
                salt=b"s",
                iterations=1,
                stored_key=b"k",
                server_key=b"k",
            )
        result = await scram_hello(
            auth, handshakes, f"HELLO username={_b64url_encode(_USER.encode())}"
        )
        assert result.status == 401
