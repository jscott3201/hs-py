"""Haystack authentication handshake.

Implements the Project Haystack authentication protocol including
SCRAM-SHA-256 and PLAINTEXT mechanisms.

Uses the ``cryptography`` library for all key derivation, HMAC, and hashing.

See: https://project-haystack.org/doc/docHaystack/Auth
"""

from __future__ import annotations

import base64
import hmac as hmac_mod
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.hmac import HMAC
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from hs_py.errors import AuthError

if TYPE_CHECKING:
    import aiohttp

__all__ = [
    "authenticate",
    "scram_client_final",
    "scram_client_first",
]

_log = logging.getLogger(__name__)

# Maximum PBKDF2 iterations to accept from a server (prevents CPU DoS).
_MAX_SCRAM_ITERATIONS = 100_000

# Minimum salt length in bytes per NIST SP 800-132 (128 bits).
_MIN_SALT_BYTES = 16

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def authenticate(
    session: aiohttp.ClientSession,
    base_url: str,
    username: str,
    password: str,
) -> str:
    """Run the Haystack auth handshake and return a bearer auth token.

    Attempts SCRAM-SHA-256 first, falling back to PLAINTEXT if offered.

    :param session: An open aiohttp client session.
    :param base_url: Haystack server base URL (e.g. ``http://host/api/``).
    :param username: Username for authentication.
    :param password: Password for authentication.
    :returns: Bearer auth token string for subsequent requests.
    :raises AuthError: On authentication failure.
    """
    about_url = base_url.rstrip("/") + "/about"

    # Step 1: HELLO
    _log.debug("HELLO for user '%s' at %s", username, about_url)
    hello_header = f"HELLO username={_b64url_encode(username.encode())}"
    async with session.get(about_url, headers={"Authorization": hello_header}) as resp:
        if resp.status == 200:
            # Server does not require auth — extract token if present
            auth_info = resp.headers.get("Authentication-Info", "")
            token = _parse_param(auth_info, "authToken")
            if token:
                return token
            return ""

        if resp.status != 401:
            raise AuthError(f"Unexpected status {resp.status} during HELLO")

        www_auth = resp.headers.get("WWW-Authenticate", "")

    # Determine mechanism
    params = _parse_header_params(www_auth)
    scheme = www_auth.split()[0].upper() if www_auth else ""
    handshake_token = params.get("handshakeToken", "")

    if scheme == "SCRAM":
        _log.debug("SCRAM-SHA-256 handshake starting for '%s'", username)
        return await _scram_auth(session, about_url, username, password, handshake_token, params)

    if "PLAINTEXT" in www_auth.upper():
        _log.debug("PLAINTEXT auth for '%s'", username)
        return await _plaintext_auth(session, about_url, username, password)

    _log.warning("No supported auth mechanism in: %s", www_auth)
    raise AuthError(f"No supported auth mechanism in: {www_auth}")


# ---------------------------------------------------------------------------
# SCRAM-SHA-256 — transport-independent helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScramClientFirst:
    """Result of the client-first SCRAM step."""

    client_first_msg: str
    """Full client-first message (gs2-header + bare)."""

    client_first_bare: str
    """Bare client-first message (without gs2 header)."""

    c_nonce: str
    """Client nonce."""


@dataclass(frozen=True, slots=True)
class ScramClientFinal:
    """Result of the client-final SCRAM step."""

    client_final_msg: str
    """Full client-final message with proof."""

    auth_message: str
    """Concatenated auth message for server-sig verification."""

    salted_password: bytes
    """Salted password (needed for server signature verification)."""


def scram_client_first(username: str) -> ScramClientFirst:
    """Build the SCRAM client-first message.

    :param username: The username to authenticate as.
    :returns: :class:`ScramClientFirst` with the message and nonce.
    """
    c_nonce = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
    # Escape per RFC 5802 §5.1: '=' → '=3D', ',' → '=2C'
    safe_user = username.replace("=", "=3D").replace(",", "=2C")
    client_first_bare = f"n={safe_user},r={c_nonce}"
    client_first_msg = "n,," + client_first_bare
    return ScramClientFirst(
        client_first_msg=client_first_msg,
        client_first_bare=client_first_bare,
        c_nonce=c_nonce,
    )


def scram_client_final(
    password: str,
    first: ScramClientFirst,
    server_first_msg: str,
    hash_name: str = "SHA-256",
) -> ScramClientFinal:
    """Build the SCRAM client-final message from the server-first response.

    :param password: The plaintext password.
    :param first: The :class:`ScramClientFirst` from :func:`scram_client_first`.
    :param server_first_msg: Raw server-first message (``r=...,s=...,i=...``).
    :param hash_name: Hash algorithm name (default ``SHA-256``).
    :returns: :class:`ScramClientFinal` with the proof message.
    :raises AuthError: On nonce mismatch or unsupported hash.
    """
    sf_params = _parse_scram_msg(server_first_msg)
    s_nonce = sf_params.get("r", "")
    salt_b64 = sf_params.get("s", "")
    iterations = int(sf_params.get("i", "4096"))

    if not s_nonce.startswith(first.c_nonce):
        raise AuthError("Server nonce does not start with client nonce")

    if iterations > _MAX_SCRAM_ITERATIONS:
        raise AuthError(f"Server requested excessive PBKDF2 iterations: {iterations}")

    salt = base64.b64decode(salt_b64)
    if len(salt) < _MIN_SALT_BYTES:
        raise AuthError(f"Server provided insufficient salt length: {len(salt)} bytes")
    algo = _hash_algo(hash_name)
    salted_password = _derive_key(password.encode(), salt, iterations, algo)
    client_key = _hmac(algo, salted_password, b"Client Key")
    stored_key = _hash_digest(algo, client_key)

    channel_binding = _b64url_encode(b"n,,")
    client_final_no_proof = f"c={channel_binding},r={s_nonce}"
    auth_message = f"{first.client_first_bare},{server_first_msg},{client_final_no_proof}"
    client_signature = _hmac(algo, stored_key, auth_message.encode())
    client_proof = bytes(a ^ b for a, b in zip(client_key, client_signature, strict=True))
    proof_b64 = base64.b64encode(client_proof).decode()
    client_final_msg = f"{client_final_no_proof},p={proof_b64}"

    return ScramClientFinal(
        client_final_msg=client_final_msg,
        auth_message=auth_message,
        salted_password=salted_password,
    )


def verify_server_signature(
    final: ScramClientFinal,
    server_final_msg: str,
) -> None:
    """Verify the server's signature from the server-final message.

    :param final: The :class:`ScramClientFinal` from :func:`scram_client_final`.
    :param server_final_msg: Raw server-final message (``v=...``).
    :raises AuthError: If the server signature does not match.
    """
    sf_params = _parse_scram_msg(server_final_msg)
    server_sig_b64 = sf_params.get("v", "")
    if not server_sig_b64:
        raise AuthError("Server signature missing — server authentication failed")
    server_sig = base64.b64decode(server_sig_b64)
    server_key = _hmac("sha256", final.salted_password, b"Server Key")
    expected = _hmac("sha256", server_key, final.auth_message.encode())
    if not hmac_mod.compare_digest(server_sig, expected):
        raise AuthError("Server signature verification failed")


# ---------------------------------------------------------------------------
# SCRAM-SHA-256 — HTTP transport
# ---------------------------------------------------------------------------


async def _scram_auth(
    session: aiohttp.ClientSession,
    url: str,
    username: str,
    password: str,
    handshake_token: str,
    hello_params: dict[str, str],
) -> str:
    """Perform SCRAM-SHA-256 authentication over HTTP."""
    first = scram_client_first(username)

    auth_header = (
        f"SCRAM handshakeToken={handshake_token}, "
        f"data={_b64url_encode(first.client_first_msg.encode())}"
    )
    _log.debug("SCRAM step 1: sending client-first message")
    async with session.get(url, headers={"Authorization": auth_header}) as resp:
        if resp.status != 401:
            _log.warning("SCRAM step 1 failed: expected 401, got %d", resp.status)
            raise AuthError(f"Expected 401 during SCRAM step 2, got {resp.status}")
        www_auth = resp.headers.get("WWW-Authenticate", "")

    step2_params = _parse_header_params(www_auth)
    handshake_token = step2_params.get("handshakeToken", handshake_token)
    hash_name = step2_params.get("hash", "SHA-256")
    server_first_data = step2_params.get("data", "")
    server_first_msg = _b64url_decode(server_first_data).decode()

    final = scram_client_final(password, first, server_first_msg, hash_name)

    auth_header = (
        f"SCRAM handshakeToken={handshake_token}, "
        f"data={_b64url_encode(final.client_final_msg.encode())}"
    )
    _log.debug("SCRAM step 2: sending client-final message")
    async with session.get(url, headers={"Authorization": auth_header}) as resp:
        if resp.status != 200:
            _log.warning("SCRAM auth failed with status %d", resp.status)
            raise AuthError(f"SCRAM auth failed with status {resp.status}")
        auth_info = resp.headers.get("Authentication-Info", "")

    token = _parse_param(auth_info, "authToken")
    if not token:
        _log.warning("No authToken in final SCRAM response")
        raise AuthError("No authToken in final SCRAM response")

    # Verify server signature
    server_data = _parse_param(auth_info, "data")
    if server_data:
        verify_server_signature(final, _b64url_decode(server_data).decode())

    return token


# ---------------------------------------------------------------------------
# PLAINTEXT
# ---------------------------------------------------------------------------


async def _plaintext_auth(
    session: aiohttp.ClientSession,
    url: str,
    username: str,
    password: str,
) -> str:
    """Perform PLAINTEXT authentication (TLS-only)."""
    auth_header = (
        f"PLAINTEXT username={_b64url_encode(username.encode())}, "
        f"password={_b64url_encode(password.encode())}"
    )
    async with session.get(url, headers={"Authorization": auth_header}) as resp:
        if resp.status != 200:
            raise AuthError(f"PLAINTEXT auth failed with status {resp.status}")
        auth_info = resp.headers.get("Authentication-Info", "")

    token = _parse_param(auth_info, "authToken")
    if not token:
        raise AuthError("No authToken in PLAINTEXT response")
    return token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64url_encode(data: bytes) -> str:
    """Base64url encode without padding (per Haystack spec)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    """Base64url decode, adding padding as needed."""
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _parse_header_params(header: str) -> dict[str, str]:
    """Parse ``key=value`` pairs from an HTTP auth header.

    Handles both scheme-prefixed headers (``SCRAM key=val, ...``) and
    bare param headers (``Authentication-Info: key=val, ...``).

    :param header: Raw header value string.
    :returns: Dict mapping parameter names to values.
    """
    result: dict[str, str] = {}
    if not header:
        return result

    # Determine if first token is a scheme name (no '=' sign)
    parts = header.split(None, 1)
    param_str = header
    if len(parts) >= 2 and "=" not in parts[0]:
        param_str = parts[1]

    for pair in param_str.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            result[k.strip()] = v.strip()
    return result


def _parse_param(header: str, key: str) -> str | None:
    """Extract a single parameter value from a header string."""
    return _parse_header_params(header).get(key)


def _parse_scram_msg(msg: str) -> dict[str, str]:
    """Parse a SCRAM message into its ``key=value`` parts."""
    result: dict[str, str] = {}
    for part in msg.split(","):
        if "=" in part:
            k, _, v = part.partition("=")
            result[k] = v
    return result


_HASH_ALGORITHMS: dict[str, hashes.HashAlgorithm] = {
    "sha256": hashes.SHA256(),
    "sha512": hashes.SHA512(),
}


def _get_hash_algorithm(algo: str) -> hashes.HashAlgorithm:
    """Map algorithm name to a cryptography hash instance."""
    result = _HASH_ALGORITHMS.get(algo)
    if result is None:
        raise AuthError(f"Unsupported hash algorithm: {algo}")
    return result


def _derive_key(password: bytes, salt: bytes, iterations: int, algo: str) -> bytes:
    """Derive a key using PBKDF2-HMAC via the cryptography library."""
    hash_algo = _get_hash_algorithm(algo)
    kdf = PBKDF2HMAC(
        algorithm=hash_algo,
        length=hash_algo.digest_size,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password)


def _hmac(algo: str, key: bytes, data: bytes) -> bytes:
    """Compute HMAC with the given hash algorithm via the cryptography library."""
    h = HMAC(key, _get_hash_algorithm(algo))
    h.update(data)
    return h.finalize()


def _hash_digest(algo: str, data: bytes) -> bytes:
    """Compute a hash digest via the cryptography library."""
    h = hashes.Hash(_get_hash_algorithm(algo))
    h.update(data)
    return h.finalize()


_HASH_ALGO_NAMES: dict[str, str] = {
    "SHA-256": "sha256",
    "SHA-512": "sha512",
}


def _hash_algo(name: str) -> str:
    """Map Haystack hash name to internal algorithm name."""
    result = _HASH_ALGO_NAMES.get(name)
    if result is None:
        raise AuthError(f"Unsupported hash algorithm: {name}")
    return result
