"""Server-side authentication types.

Provides credential storage and authenticator protocols for Haystack server
implementations. Framework-independent — used by both aiohttp and FastAPI servers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from hs_py.auth import _derive_key, _hash_digest, _hmac
from hs_py.tls import extract_peer_cn

__all__ = [
    "Authenticator",
    "CertAuthenticator",
    "ScramCredentials",
    "SimpleAuthenticator",
]


@dataclass(frozen=True, slots=True)
class ScramCredentials:
    """Pre-computed SCRAM-SHA-256 credentials for a user."""

    salt: bytes
    """Random salt used during key derivation."""

    iterations: int
    """PBKDF2 iteration count."""

    stored_key: bytes
    """H(ClientKey) — used to verify client proof."""

    server_key: bytes
    """HMAC(SaltedPassword, "Server Key") — used to compute server signature."""


class Authenticator(Protocol):
    """Protocol for server-side authentication."""

    async def scram_credentials(self, username: str) -> ScramCredentials | None:
        """Return SCRAM credentials for a user, or None if unknown."""
        ...  # pragma: no cover


class SimpleAuthenticator:
    """Authenticator that derives SCRAM keys from a username→password dict."""

    def __init__(self, users: dict[str, str], *, iterations: int = 600_000) -> None:
        """Initialise from a username-to-password mapping.

        :param users: Dict mapping usernames to plaintext passwords.
        :param iterations: PBKDF2 iteration count for key derivation.
        """
        self._creds: dict[str, ScramCredentials] = {}
        for username, password in users.items():
            salt = os.urandom(16)
            salted_pw = _derive_key(password.encode(), salt, iterations, "sha256")
            client_key = _hmac("sha256", salted_pw, b"Client Key")
            stored_key = _hash_digest("sha256", client_key)
            server_key = _hmac("sha256", salted_pw, b"Server Key")
            self._creds[username] = ScramCredentials(
                salt=salt,
                iterations=iterations,
                stored_key=stored_key,
                server_key=server_key,
            )

    async def scram_credentials(self, username: str) -> ScramCredentials | None:
        """Return SCRAM credentials for a user, or ``None`` if unknown.

        :param username: The username to look up.
        :returns: :class:`ScramCredentials` or ``None``.
        """
        return self._creds.get(username)


class CertAuthenticator:
    """Authenticator that authorizes clients by TLS client certificate CN.

    When mutual TLS is enabled, the server can extract the Common Name (CN)
    from the client certificate and check it against an allowed set.  This
    bypasses SCRAM entirely for certificate-authenticated connections.

    :param allowed_cns: Set of Common Name strings that are authorized.
    """

    def __init__(self, allowed_cns: set[str]) -> None:
        """Initialise with a set of authorised Common Names.

        :param allowed_cns: Set of CN strings that are permitted access.
        """
        self._allowed_cns = allowed_cns

    def authorize(self, peercert: dict[str, object] | None) -> str | None:
        """Check a peer certificate and return the username if authorized.

        :param peercert: Certificate dict from ``ssl.SSLSocket.getpeercert()``.
        :returns: The CN string if authorized, or *None* if not.
        """
        cn = extract_peer_cn(peercert)
        if cn is not None and cn in self._allowed_cns:
            return cn
        return None
