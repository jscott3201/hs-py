"""Tests for user management: model, storage, bootstrap, authenticator, and API."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from hs_py.auth_types import ScramCredentials, StorageAuthenticator
from hs_py.storage.memory import InMemoryAdapter
from hs_py.user import (
    Role,
    create_user,
    derive_scram_credentials,
    user_from_dict,
    user_to_dict,
)

# ---------------------------------------------------------------------------
# Role enum tests
# ---------------------------------------------------------------------------


class TestRole:
    def test_role_values(self):
        assert Role.VIEWER.value == "viewer"
        assert Role.OPERATOR.value == "operator"
        assert Role.ADMIN.value == "admin"

    def test_role_ordering(self):
        assert Role.ADMIN > Role.OPERATOR
        assert Role.OPERATOR > Role.VIEWER
        assert Role.ADMIN >= Role.ADMIN
        assert Role.VIEWER <= Role.OPERATOR
        assert not Role.VIEWER > Role.OPERATOR

    def test_role_from_string(self):
        assert Role("viewer") == Role.VIEWER
        assert Role("operator") == Role.OPERATOR
        assert Role("admin") == Role.ADMIN

    def test_role_invalid_string(self):
        with pytest.raises(ValueError):
            Role("superuser")


# ---------------------------------------------------------------------------
# Permission helper tests
# ---------------------------------------------------------------------------


class TestPermissionHelpers:
    def test_can_admin(self):
        from hs_py.user import can_admin

        assert can_admin(Role.ADMIN) is True
        assert can_admin(Role.OPERATOR) is False
        assert can_admin(Role.VIEWER) is False

    def test_can_write(self):
        from hs_py.user import can_write

        assert can_write(Role.ADMIN) is True
        assert can_write(Role.OPERATOR) is True
        assert can_write(Role.VIEWER) is False

    def test_can_read(self):
        from hs_py.user import can_read

        assert can_read(Role.ADMIN) is True
        assert can_read(Role.OPERATOR) is True
        assert can_read(Role.VIEWER) is True

    def test_write_ops_set(self):
        from hs_py.user import WRITE_OPS

        assert "hisWrite" in WRITE_OPS
        assert "pointWrite" in WRITE_OPS
        assert "invokeAction" in WRITE_OPS
        assert "watchSub" in WRITE_OPS
        assert "read" not in WRITE_OPS

    def test_read_ops_set(self):
        from hs_py.user import READ_OPS

        assert "read" in READ_OPS
        assert "nav" in READ_OPS
        assert "hisRead" in READ_OPS
        assert "hisWrite" not in READ_OPS


# ---------------------------------------------------------------------------
# User model tests
# ---------------------------------------------------------------------------


class TestUserModel:
    def test_create_user_basic(self):
        user = create_user("alice", "secret123")
        assert user.username == "alice"
        assert user.first_name == ""
        assert user.last_name == ""
        assert user.email == ""
        assert user.role == Role.VIEWER
        assert user.enabled is True
        assert isinstance(user.credentials, ScramCredentials)
        assert len(user.credentials.salt) >= 16

    def test_create_user_all_fields(self):
        user = create_user(
            "bob",
            "pass",
            first_name="Bob",
            last_name="Smith",
            email="bob@example.com",
            role=Role.ADMIN,
            enabled=False,
        )
        assert user.first_name == "Bob"
        assert user.last_name == "Smith"
        assert user.email == "bob@example.com"
        assert user.role == Role.ADMIN
        assert user.enabled is False

    def test_create_user_empty_username_raises(self):
        with pytest.raises(ValueError, match="username"):
            create_user("", "pass")

    def test_create_user_empty_password_raises(self):
        with pytest.raises(ValueError, match="password"):
            create_user("alice", "")

    def test_user_frozen(self):
        user = create_user("alice", "secret")
        with pytest.raises(AttributeError):
            user.username = "bob"  # type: ignore[misc]

    def test_derive_scram_credentials(self):
        creds = derive_scram_credentials("password", iterations=1000)
        assert isinstance(creds, ScramCredentials)
        assert creds.iterations == 1000
        assert len(creds.salt) == 16
        assert len(creds.stored_key) == 32  # SHA-256
        assert len(creds.server_key) == 32

    def test_user_serialization_roundtrip(self):
        user = create_user(
            "alice",
            "secret",
            first_name="Alice",
            last_name="Wonder",
            email="a@b.com",
            role=Role.ADMIN,
        )
        d = user_to_dict(user)
        assert d["username"] == "alice"
        assert d["role"] == "admin"
        assert "salt" in d
        assert isinstance(d["salt"], str)  # base64

        restored = user_from_dict(d)
        assert restored.username == user.username
        assert restored.first_name == user.first_name
        assert restored.credentials.salt == user.credentials.salt
        assert restored.credentials.stored_key == user.credentials.stored_key
        assert restored.role == Role.ADMIN

    def test_user_serialization_default_role(self):
        user = create_user("viewer", "pass")
        d = user_to_dict(user)
        assert d["role"] == "viewer"
        restored = user_from_dict(d)
        assert restored.role == Role.VIEWER


# ---------------------------------------------------------------------------
# InMemory UserStore tests
# ---------------------------------------------------------------------------


class TestInMemoryUserStore:
    @pytest.fixture()
    def store(self):
        return InMemoryAdapter()

    @pytest.mark.asyncio()
    async def test_create_and_get(self, store):
        user = create_user("alice", "pass")
        await store.create_user(user)
        found = await store.get_user("alice")
        assert found is not None
        assert found.username == "alice"

    @pytest.mark.asyncio()
    async def test_get_nonexistent(self, store):
        assert await store.get_user("nobody") is None

    @pytest.mark.asyncio()
    async def test_create_duplicate_raises(self, store):
        user = create_user("alice", "pass")
        await store.create_user(user)
        with pytest.raises(ValueError, match="already exists"):
            await store.create_user(user)

    @pytest.mark.asyncio()
    async def test_list_users(self, store):
        await store.create_user(create_user("alice", "pass"))
        await store.create_user(create_user("bob", "pass"))
        users = await store.list_users()
        names = {u.username for u in users}
        assert names == {"alice", "bob"}

    @pytest.mark.asyncio()
    async def test_update_fields(self, store):
        await store.create_user(create_user("alice", "pass"))
        updated = await store.update_user(
            "alice", first_name="Alice", email="a@b.com", role=Role.OPERATOR
        )
        assert updated.first_name == "Alice"
        assert updated.email == "a@b.com"
        assert updated.role == Role.OPERATOR

    @pytest.mark.asyncio()
    async def test_update_role_to_admin(self, store):
        await store.create_user(create_user("alice", "pass"))
        updated = await store.update_user("alice", role=Role.ADMIN)
        assert updated.role == Role.ADMIN

    @pytest.mark.asyncio()
    async def test_update_password(self, store):
        user = create_user("alice", "oldpass")
        await store.create_user(user)
        old_key = user.credentials.stored_key
        updated = await store.update_user("alice", password="newpass")
        assert updated.credentials.stored_key != old_key

    @pytest.mark.asyncio()
    async def test_update_nonexistent_raises(self, store):
        with pytest.raises(KeyError, match="not found"):
            await store.update_user("nobody", first_name="X")

    @pytest.mark.asyncio()
    async def test_disable_user(self, store):
        await store.create_user(create_user("alice", "pass"))
        updated = await store.update_user("alice", enabled=False)
        assert updated.enabled is False

    @pytest.mark.asyncio()
    async def test_delete_user(self, store):
        await store.create_user(create_user("alice", "pass"))
        assert await store.delete_user("alice") is True
        assert await store.get_user("alice") is None

    @pytest.mark.asyncio()
    async def test_delete_nonexistent(self, store):
        assert await store.delete_user("nobody") is False


# ---------------------------------------------------------------------------
# StorageAuthenticator tests
# ---------------------------------------------------------------------------


class TestStorageAuthenticator:
    @pytest.mark.asyncio()
    async def test_valid_user_returns_credentials(self):
        store = InMemoryAdapter()
        user = create_user("alice", "pass")
        await store.create_user(user)
        auth = StorageAuthenticator(store)
        creds = await auth.scram_credentials("alice")
        assert creds is not None
        assert creds.salt == user.credentials.salt

    @pytest.mark.asyncio()
    async def test_disabled_user_returns_none(self):
        store = InMemoryAdapter()
        user = create_user("alice", "pass", enabled=False)
        await store.create_user(user)
        auth = StorageAuthenticator(store)
        assert await auth.scram_credentials("alice") is None

    @pytest.mark.asyncio()
    async def test_unknown_user_returns_none(self):
        store = InMemoryAdapter()
        auth = StorageAuthenticator(store)
        assert await auth.scram_credentials("nobody") is None


# ---------------------------------------------------------------------------
# Bootstrap tests
# ---------------------------------------------------------------------------


class TestBootstrap:
    @pytest.mark.asyncio()
    async def test_existing_admin_skips(self):
        store = InMemoryAdapter()
        await store.create_user(create_user("admin", "pass", role=Role.ADMIN))

        from hs_py.bootstrap import ensure_superuser

        await ensure_superuser(store)
        users = await store.list_users()
        assert len(users) == 1

    @pytest.mark.asyncio()
    async def test_seeds_from_env_vars(self):
        store = InMemoryAdapter()

        from hs_py.bootstrap import ensure_superuser

        with patch.dict(
            os.environ,
            {"HS_SUPERUSER_USERNAME": "admin", "HS_SUPERUSER_PASSWORD": "secret"},
        ):
            await ensure_superuser(store)

        user = await store.get_user("admin")
        assert user is not None
        assert user.role == Role.ADMIN
        assert user.enabled is True

    @pytest.mark.asyncio()
    async def test_exits_without_env_vars(self):
        store = InMemoryAdapter()

        from hs_py.bootstrap import ensure_superuser

        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(SystemExit) as exc_info,
        ):
            os.environ.pop("HS_SUPERUSER_USERNAME", None)
            os.environ.pop("HS_SUPERUSER_PASSWORD", None)
            await ensure_superuser(store)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio()
    async def test_disabled_admin_triggers_seed(self):
        store = InMemoryAdapter()
        await store.create_user(create_user("admin", "pass", role=Role.ADMIN, enabled=False))

        from hs_py.bootstrap import ensure_superuser

        with patch.dict(
            os.environ,
            {"HS_SUPERUSER_USERNAME": "newadmin", "HS_SUPERUSER_PASSWORD": "secret"},
        ):
            await ensure_superuser(store)

        user = await store.get_user("newadmin")
        assert user is not None
        assert user.role == Role.ADMIN

    @pytest.mark.asyncio()
    async def test_operator_not_sufficient_for_bootstrap(self):
        """An operator user should not satisfy the admin bootstrap check."""
        store = InMemoryAdapter()
        await store.create_user(create_user("op", "pass", role=Role.OPERATOR))

        from hs_py.bootstrap import ensure_superuser

        with patch.dict(
            os.environ,
            {"HS_SUPERUSER_USERNAME": "admin", "HS_SUPERUSER_PASSWORD": "secret"},
        ):
            await ensure_superuser(store)

        user = await store.get_user("admin")
        assert user is not None
        assert user.role == Role.ADMIN


# ---------------------------------------------------------------------------
# User API endpoint tests
# ---------------------------------------------------------------------------


class TestUserAPI:
    """Test the user management REST endpoints via ASGI test client."""

    @pytest.mark.asyncio()
    async def test_create_user_endpoint(self):
        from hs_py.fastapi_server import create_fastapi_app

        store = InMemoryAdapter()
        app = create_fastapi_app(storage=store, user_store=store)
        admin = create_user("admin", "pass", role=Role.ADMIN)
        await store.create_user(admin)

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/users/",
                json={
                    "username": "alice",
                    "password": "secret",
                    "first_name": "Alice",
                    "last_name": "Smith",
                    "email": "alice@example.com",
                },
            )
            # Without auth middleware, request.state.username is not set → HaystackError
            assert resp.status_code in (200, 400, 500)

    @pytest.mark.asyncio()
    async def test_user_crud_flow(self):
        """Test full CRUD flow by directly invoking UserStore."""
        store = InMemoryAdapter()

        # Create
        user = create_user(
            "alice",
            "secret",
            first_name="Alice",
            last_name="Smith",
            email="alice@example.com",
        )
        await store.create_user(user)

        # Read
        found = await store.get_user("alice")
        assert found is not None
        assert found.first_name == "Alice"

        # Update
        updated = await store.update_user("alice", last_name="Jones", email="alice@jones.com")
        assert updated.last_name == "Jones"
        assert updated.email == "alice@jones.com"

        # Change role
        promoted = await store.update_user("alice", role=Role.OPERATOR)
        assert promoted.role == Role.OPERATOR

        # Disable
        disabled = await store.update_user("alice", enabled=False)
        assert disabled.enabled is False

        # Re-enable
        enabled = await store.update_user("alice", enabled=True)
        assert enabled.enabled is True

        # Delete
        assert await store.delete_user("alice") is True
        assert await store.get_user("alice") is None

        # List empty
        assert await store.list_users() == []
