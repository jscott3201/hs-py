"""Admin user bootstrap for Haystack servers.

On startup, verifies that at least one enabled admin user exists in the
:class:`~hs_py.storage.protocol.UserStore`.  If none is found, attempts
to seed one from environment variables ``HS_SUPERUSER_USERNAME`` and
``HS_SUPERUSER_PASSWORD``.  Exits with an error if neither source provides
an admin.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

from hs_py.user import Role, create_user

if TYPE_CHECKING:
    from hs_py.storage.protocol import UserStore

__all__ = ["ensure_superuser"]

_log = logging.getLogger(__name__)


async def ensure_superuser(store: UserStore) -> None:
    """Ensure at least one enabled admin user exists.

    Check order:

    1. Query the store for any enabled admin — if found, return.
    2. Read ``HS_SUPERUSER_USERNAME`` and ``HS_SUPERUSER_PASSWORD`` from
       environment variables.
    3. If both are set, create a new admin user and persist it.
    4. If either is missing, exit the process with an error message.

    :param store: User store backend to check and seed.
    """
    users = await store.list_users()
    if any(u.role == Role.ADMIN and u.enabled for u in users):
        _log.debug("Admin user already exists — skipping bootstrap")
        return

    username = os.environ.get("HS_SUPERUSER_USERNAME", "").strip()
    password = os.environ.get("HS_SUPERUSER_PASSWORD", "").strip()

    if not username or not password:
        _log.critical(
            "No admin user found in storage and HS_SUPERUSER_USERNAME / "
            "HS_SUPERUSER_PASSWORD environment variables are not set. "
            "Cannot start server without at least one admin user."
        )
        sys.exit(1)

    user = create_user(
        username=username,
        password=password,
        role=Role.ADMIN,
    )
    await store.create_user(user)
    _log.info("Seeded admin user %r from environment variables", username)
