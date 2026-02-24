"""Shared SCRAM-SHA-256 authentication logic.

Framework-independent SCRAM handshake helpers used by both the FastAPI and
aiohttp server implementations.  Each server wraps the returned
:class:`ScramResult` into its native HTTP response type.
"""

from __future__ import annotations

import base64
import hmac as hmac_mod
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from hs_py.auth import (
    _b64url_decode,
    _b64url_encode,
    _hash_digest,
    _hmac,
    _parse_header_params,
)

__all__ = [
    "ScramResult",
    "scram_hello",
    "scram_step1",
    "scram_step2",
    "validate_bearer",
]

_log = logging.getLogger(__name__)

# Handshake timeout in seconds
HANDSHAKE_TIMEOUT = 60.0
# Max concurrent handshakes to prevent memory exhaustion
MAX_HANDSHAKES = 1000
# Default token lifetime (1 hour)
TOKEN_LIFETIME = 3600.0
# Max stored tokens
MAX_TOKENS = 10000

_401_HEADERS = {"WWW-Authenticate": "SCRAM hash=SHA-256"}


# ---------------------------------------------------------------------------
# Internal auth state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HandshakeState:
    """In-progress SCRAM handshake state."""

    username: str
    server_nonce: str
    salt: bytes
    iterations: int
    stored_key: bytes
    server_key: bytes
    auth_message: str = ""
    step: int = 1
    created: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, slots=True)
class TokenEntry:
    """A valid auth token."""

    username: str
    created: float


@dataclass(frozen=True, slots=True)
class ScramResult:
    """Framework-independent SCRAM response."""

    status: int
    headers: dict[str, str]
    body: str = ""


# ---------------------------------------------------------------------------
# Purge helpers
# ---------------------------------------------------------------------------


def purge_expired_handshakes(handshakes: dict[str, HandshakeState]) -> None:
    """Remove handshakes that have exceeded the timeout."""
    now = time.monotonic()
    expired = [k for k, v in handshakes.items() if (now - v.created) > HANDSHAKE_TIMEOUT]
    for k in expired:
        del handshakes[k]


def purge_expired_tokens(tokens: dict[str, TokenEntry]) -> None:
    """Remove tokens that have exceeded the lifetime."""
    now = time.monotonic()
    expired = [k for k, v in tokens.items() if (now - v.created) > TOKEN_LIFETIME]
    for k in expired:
        del tokens[k]


# ---------------------------------------------------------------------------
# SCRAM step functions
# ---------------------------------------------------------------------------


async def scram_hello(
    authenticator: Any,
    handshakes: dict[str, HandshakeState],
    auth_header: str,
) -> ScramResult:
    """Process HELLO step — return 401 with SCRAM challenge.

    :param authenticator: Server-side credential store with ``scram_credentials()``.
    :param handshakes: Mutable dict of in-progress handshakes.
    :param auth_header: Raw ``Authorization`` header value.
    :returns: :class:`ScramResult` to send to the client.
    """
    params = _parse_header_params(auth_header)
    username_b64 = params.get("username", "")
    try:
        username = _b64url_decode(username_b64).decode()
    except Exception:
        return ScramResult(status=401, headers=dict(_401_HEADERS))

    creds = await authenticator.scram_credentials(username)
    if creds is None:
        return ScramResult(status=401, headers=dict(_401_HEADERS))

    purge_expired_handshakes(handshakes)
    if len(handshakes) >= MAX_HANDSHAKES:
        _log.warning("Max concurrent handshakes reached (%d), rejecting HELLO", MAX_HANDSHAKES)
        return ScramResult(status=401, headers=dict(_401_HEADERS))

    handshake_token = secrets.token_urlsafe(24)
    handshakes[handshake_token] = HandshakeState(
        username=username,
        server_nonce="",
        salt=creds.salt,
        iterations=creds.iterations,
        stored_key=creds.stored_key,
        server_key=creds.server_key,
    )

    return ScramResult(
        status=401,
        headers={
            "WWW-Authenticate": f"SCRAM hash=SHA-256, handshakeToken={handshake_token}",
        },
    )


def scram_step1(
    handshakes: dict[str, HandshakeState],
    handshake_token: str,
    state: HandshakeState,
    client_first_msg: str,
) -> ScramResult:
    """SCRAM step 1: receive client-first, return server-first."""
    # Strip gs2-header to get client-first-bare
    if client_first_msg.startswith("n,,"):
        client_first_bare = client_first_msg[3:]
    else:
        client_first_bare = client_first_msg

    # Extract client nonce
    parts = dict(p.split("=", 1) for p in client_first_bare.split(",") if "=" in p)
    c_nonce = parts.get("r", "")

    # Generate combined nonce
    s_nonce = c_nonce + secrets.token_urlsafe(24)
    state.server_nonce = s_nonce

    salt_b64 = base64.b64encode(state.salt).decode()
    server_first_msg = f"r={s_nonce},s={salt_b64},i={state.iterations}"

    # Pre-compute auth_message for step 2
    channel_binding = _b64url_encode(b"n,,")
    client_final_no_proof = f"c={channel_binding},r={s_nonce}"
    state.auth_message = f"{client_first_bare},{server_first_msg},{client_final_no_proof}"
    state.step = 2

    # Rotate handshake token
    new_token = secrets.token_urlsafe(24)
    handshakes[new_token] = state
    handshakes.pop(handshake_token, None)

    server_first_b64 = _b64url_encode(server_first_msg.encode())
    return ScramResult(
        status=401,
        headers={
            "WWW-Authenticate": (
                f"SCRAM handshakeToken={new_token}, hash=SHA-256, data={server_first_b64}"
            ),
        },
    )


def scram_step2(
    handshakes: dict[str, HandshakeState],
    tokens: dict[str, TokenEntry],
    handshake_token: str,
    state: HandshakeState,
    client_final_msg: str,
) -> ScramResult:
    """SCRAM step 2: verify client proof, return server signature + auth token."""
    handshakes.pop(handshake_token, None)

    # Parse client-final: c=<binding>,r=<nonce>,p=<proof>
    parts = dict(p.split("=", 1) for p in client_final_msg.split(",") if "=" in p)
    proof_b64 = parts.get("p", "")

    if not proof_b64:
        return ScramResult(status=401, headers=dict(_401_HEADERS))

    # Verify client proof — guard against length mismatches
    try:
        client_signature = _hmac("sha256", state.stored_key, state.auth_message.encode())
        client_proof = base64.b64decode(proof_b64)
        if len(client_proof) != len(client_signature):
            return ScramResult(status=403, headers={}, body="Authentication failed")
        client_key = bytes(a ^ b for a, b in zip(client_proof, client_signature, strict=True))
        computed_stored_key = _hash_digest("sha256", client_key)
    except Exception:
        return ScramResult(status=403, headers={}, body="Authentication failed")

    if not hmac_mod.compare_digest(computed_stored_key, state.stored_key):
        return ScramResult(status=403, headers={}, body="Authentication failed")

    # Compute server signature
    server_signature = _hmac("sha256", state.server_key, state.auth_message.encode())
    server_final_msg = f"v={base64.b64encode(server_signature).decode()}"

    # Issue auth token — purge expired and enforce limit
    purge_expired_tokens(tokens)
    if len(tokens) >= MAX_TOKENS:
        oldest_key = min(tokens, key=lambda k: tokens[k].created)
        del tokens[oldest_key]

    auth_token = secrets.token_urlsafe(32)
    tokens[auth_token] = TokenEntry(username=state.username, created=time.monotonic())

    server_final_b64 = _b64url_encode(server_final_msg.encode())
    return ScramResult(
        status=200,
        headers={
            "Authentication-Info": f"authToken={auth_token}, data={server_final_b64}",
        },
    )


def validate_bearer(
    tokens: dict[str, TokenEntry],
    auth_header: str,
) -> ScramResult | None:
    """Validate a bearer auth token.

    :returns: ``None`` when the token is valid (proceed to handler), or a
        :class:`ScramResult` when it is invalid/expired.
    """
    params = _parse_header_params(auth_header)
    token = params.get("authToken", "")
    entry = tokens.get(token)

    if entry is None or (time.monotonic() - entry.created) > TOKEN_LIFETIME:
        tokens.pop(token, None)
        return ScramResult(status=401, headers=dict(_401_HEADERS))

    return None


def handle_scram(
    handshakes: dict[str, HandshakeState],
    tokens: dict[str, TokenEntry],
    auth_header: str,
) -> ScramResult:
    """Process SCRAM steps (client-first and client-final).

    :param handshakes: Mutable dict of in-progress handshakes.
    :param tokens: Mutable dict of valid auth tokens.
    :param auth_header: Raw ``Authorization`` header value.
    :returns: :class:`ScramResult` to send to the client.
    """
    params = _parse_header_params(auth_header)
    handshake_token = params.get("handshakeToken", "")
    data_b64 = params.get("data", "")

    state = handshakes.get(handshake_token)
    if state is None or (time.monotonic() - state.created) > HANDSHAKE_TIMEOUT:
        handshakes.pop(handshake_token, None)
        return ScramResult(status=401, headers=dict(_401_HEADERS))

    try:
        data = _b64url_decode(data_b64).decode()
    except Exception:
        handshakes.pop(handshake_token, None)
        return ScramResult(status=401, headers=dict(_401_HEADERS))

    if state.step == 1:
        return scram_step1(handshakes, handshake_token, state, data)
    if state.step == 2:
        return scram_step2(handshakes, tokens, handshake_token, state, data)

    handshakes.pop(handshake_token, None)
    return ScramResult(status=401, headers=dict(_401_HEADERS))
