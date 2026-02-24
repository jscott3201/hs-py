"""Shared SCRAM-SHA-256 authentication for benchmark clients.

Implements the Haystack 3-step auth handshake:
  1. HELLO username=<b64url> → 401 WWW-Authenticate: SCRAM hash=SHA-256
  2. SCRAM handshakeToken=..., data=<client-first> → 401 with server-first
  3. SCRAM handshakeToken=..., data=<client-final> → 200 with authToken
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

import aiohttp


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
    """Parse key=value pairs from an HTTP auth header."""
    result: dict[str, str] = {}
    if not header:
        return result
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


async def scram_auth(
    session: aiohttp.ClientSession,
    base_url: str,
    username: str,
    password: str,
) -> str:
    """Perform full SCRAM-SHA-256 handshake, return auth token."""
    about_url = base_url.rstrip("/") + "/about"

    # Step 1: HELLO
    hello_header = f"HELLO username={_b64url_encode(username.encode())}"
    async with session.get(about_url, headers={"Authorization": hello_header}) as resp:
        assert resp.status == 401, f"HELLO: expected 401, got {resp.status}"
        www_auth = resp.headers.get("WWW-Authenticate", "")

    params = _parse_header_params(www_auth)
    handshake_token = params.get("handshakeToken", "")

    # Step 2: SCRAM client-first
    c_nonce = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
    safe_user = username.replace("=", "=3D").replace(",", "=2C")
    c_first_bare = f"n={safe_user},r={c_nonce}"
    c_first_msg = f"n,,{c_first_bare}"

    auth_header = (
        f"SCRAM handshakeToken={handshake_token}, "
        f"data={_b64url_encode(c_first_msg.encode())}"
    )
    async with session.get(about_url, headers={"Authorization": auth_header}) as resp:
        assert resp.status == 401, f"SCRAM step 1: expected 401, got {resp.status}"
        www_auth = resp.headers.get("WWW-Authenticate", "")

    step2_params = _parse_header_params(www_auth)
    handshake_token = step2_params.get("handshakeToken", handshake_token)
    server_first_data = step2_params.get("data", "")
    server_first_msg = _b64url_decode(server_first_data).decode()

    # Parse server-first message
    sf_fields: dict[str, str] = {}
    for item in server_first_msg.split(","):
        if "=" in item:
            k, _, v = item.partition("=")
            sf_fields[k] = v

    s_nonce = sf_fields["r"]
    salt = base64.b64decode(sf_fields["s"])
    iterations = int(sf_fields["i"])

    # Derive keys
    salted_pw = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, dklen=32)
    client_key = hmac.new(salted_pw, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()

    c_final_no_proof = f"c={_b64url_encode(b'n,,')},r={s_nonce}"
    auth_message = f"{c_first_bare},{server_first_msg},{c_final_no_proof}"
    client_sig = hmac.new(stored_key, auth_message.encode(), hashlib.sha256).digest()
    proof = bytes(a ^ b for a, b in zip(client_key, client_sig))
    c_final = f"{c_final_no_proof},p={base64.b64encode(proof).decode()}"

    # Step 3: SCRAM client-final
    auth_header = (
        f"SCRAM handshakeToken={handshake_token}, "
        f"data={_b64url_encode(c_final.encode())}"
    )
    async with session.get(about_url, headers={"Authorization": auth_header}) as resp:
        assert resp.status == 200, f"SCRAM step 2: expected 200, got {resp.status}"
        auth_info = resp.headers.get("Authentication-Info", "")

    token_params = _parse_header_params(auth_info)
    token = token_params.get("authToken", "")
    if not token:
        raise RuntimeError("No authToken in final SCRAM response")
    return token
