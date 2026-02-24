"""User model and helpers for Haystack server user management.

Provides a frozen :class:`User` dataclass, a :class:`Role` enum for
role-based access control, and a :func:`create_user` factory that derives
SCRAM-SHA-256 credentials from a plaintext password.  Passwords are
**never** stored — only the derived ``ScramCredentials``.
"""

from __future__ import annotations

import base64
import enum
import os
import time
from dataclasses import dataclass, field
from typing import Any

from hs_py.auth import _derive_key, _hash_digest, _hmac
from hs_py.auth_types import ScramCredentials

__all__ = [
    "Role",
    "User",
    "create_user",
    "user_from_dict",
    "user_to_dict",
]

_DEFAULT_ITERATIONS = 600_000


class Role(enum.Enum):
    """User role for permission enforcement.

    Roles form a strict hierarchy: ADMIN > OPERATOR > VIEWER.

    * **VIEWER** — read-only Haystack ops (read, nav, hisRead, defs, …).
    * **OPERATOR** — read + write ops (hisWrite, pointWrite, invokeAction, watches).
    * **ADMIN** — full access including user management.
    """

    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"

    # Numeric level for ordering comparisons
    @property
    def level(self) -> int:
        """Return numeric privilege level (higher = more access)."""
        return _ROLE_LEVEL[self]

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Role):
            return NotImplemented
        return self.level >= other.level

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Role):
            return NotImplemented
        return self.level > other.level

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Role):
            return NotImplemented
        return self.level <= other.level

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Role):
            return NotImplemented
        return self.level < other.level


_ROLE_LEVEL: dict[Role, int] = {
    Role.VIEWER: 0,
    Role.OPERATOR: 1,
    Role.ADMIN: 2,
}

# ---- Op classification sets ------------------------------------------------

#: Haystack ops that mutate data — require OPERATOR or ADMIN.
WRITE_OPS: frozenset[str] = frozenset(
    {
        "hisWrite",
        "pointWrite",
        "invokeAction",
        "watchSub",
        "watchUnsub",
        "watchPoll",
        "close",
    }
)

#: Haystack ops that only read data — require VIEWER or above.
READ_OPS: frozenset[str] = frozenset(
    {
        "read",
        "nav",
        "hisRead",
        "defs",
        "libs",
        "filetypes",
    }
)


def can_admin(role: Role) -> bool:
    """Return ``True`` if the role has admin privileges."""
    return role >= Role.ADMIN


def can_write(role: Role) -> bool:
    """Return ``True`` if the role can perform write operations."""
    return role >= Role.OPERATOR


def can_read(role: Role) -> bool:
    """Return ``True`` if the role can perform read operations."""
    return role >= Role.VIEWER


@dataclass(frozen=True, slots=True)
class User:
    """Immutable user record.

    Passwords are stored as pre-computed :class:`~hs_py.auth_types.ScramCredentials`
    — the plaintext is never retained.

    :param username: Unique login identifier (required).
    :param credentials: SCRAM-SHA-256 credentials derived from the password.
    :param first_name: User's first name.
    :param last_name: User's last name.
    :param email: User's email address.
    :param role: Permission role (ADMIN, OPERATOR, or VIEWER).
    :param enabled: Whether the user can log in.
    :param created_at: Monotonic creation timestamp.
    :param updated_at: Monotonic last-update timestamp.
    """

    username: str
    credentials: ScramCredentials
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    role: Role = Role.VIEWER
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def derive_scram_credentials(
    password: str,
    *,
    iterations: int = _DEFAULT_ITERATIONS,
) -> ScramCredentials:
    """Derive SCRAM-SHA-256 credentials from a plaintext password.

    :param password: Plaintext password.
    :param iterations: PBKDF2 iteration count.
    :returns: Pre-computed :class:`ScramCredentials`.
    """
    salt = os.urandom(16)
    salted_pw = _derive_key(password.encode(), salt, iterations, "sha256")
    client_key = _hmac("sha256", salted_pw, b"Client Key")
    stored_key = _hash_digest("sha256", client_key)
    server_key = _hmac("sha256", salted_pw, b"Server Key")
    return ScramCredentials(
        salt=salt,
        iterations=iterations,
        stored_key=stored_key,
        server_key=server_key,
    )


def create_user(
    username: str,
    password: str,
    *,
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    role: Role = Role.VIEWER,
    enabled: bool = True,
    iterations: int = _DEFAULT_ITERATIONS,
) -> User:
    """Create a :class:`User` with derived SCRAM credentials.

    :param username: Unique login identifier.
    :param password: Plaintext password (used to derive credentials, then discarded).
    :param first_name: User's first name.
    :param last_name: User's last name.
    :param email: User's email address.
    :param role: Permission role (default :attr:`Role.VIEWER`).
    :param enabled: Whether the user can log in.
    :param iterations: PBKDF2 iteration count.
    :returns: Frozen :class:`User` instance.
    """
    if not username:
        raise ValueError("username must not be empty")
    if not password:
        raise ValueError("password must not be empty")
    credentials = derive_scram_credentials(password, iterations=iterations)
    now = time.time()
    return User(
        username=username,
        credentials=credentials,
        first_name=first_name,
        last_name=last_name,
        email=email,
        role=role,
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Serialization helpers (for storage backends)
# ---------------------------------------------------------------------------


def user_to_dict(user: User) -> dict[str, Any]:
    """Serialize a :class:`User` to a plain dict for storage.

    Credential bytes are base64-encoded for JSON compatibility.
    """
    creds = user.credentials
    return {
        "username": user.username,
        "salt": base64.b64encode(creds.salt).decode(),
        "iterations": creds.iterations,
        "stored_key": base64.b64encode(creds.stored_key).decode(),
        "server_key": base64.b64encode(creds.server_key).decode(),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "role": user.role.value,
        "enabled": user.enabled,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def user_from_dict(d: dict[str, Any]) -> User:
    """Deserialize a :class:`User` from a plain dict.

    Inverse of :func:`user_to_dict`.
    """
    credentials = ScramCredentials(
        salt=base64.b64decode(d["salt"]),
        iterations=d["iterations"],
        stored_key=base64.b64decode(d["stored_key"]),
        server_key=base64.b64decode(d["server_key"]),
    )
    return User(
        username=d["username"],
        credentials=credentials,
        first_name=d.get("first_name", ""),
        last_name=d.get("last_name", ""),
        email=d.get("email", ""),
        role=Role(d["role"]) if "role" in d else Role.VIEWER,
        enabled=d.get("enabled", True),
        created_at=d.get("created_at", 0.0),
        updated_at=d.get("updated_at", 0.0),
    )
