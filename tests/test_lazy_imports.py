"""Tests for lazy imports in hs_py.__init__ and hs_py.storage.__init__."""

from __future__ import annotations


class TestLazyImports:
    """Exercise every lazy-loaded attribute in hs_py.__getattr__."""

    def test_redis_ops(self) -> None:
        import hs_py

        cls = hs_py.RedisOps
        assert cls.__name__ == "RedisOps"

    def test_redis_adapter(self) -> None:
        import hs_py

        cls = hs_py.RedisAdapter
        assert cls.__name__ == "RedisAdapter"

    def test_create_redis_client(self) -> None:
        import hs_py

        fn = hs_py.create_redis_client
        assert callable(fn)

    def test_create_fastapi_app(self) -> None:
        import hs_py

        fn = hs_py.create_fastapi_app
        assert callable(fn)

    def test_storage_adapter(self) -> None:
        import hs_py

        cls = hs_py.StorageAdapter
        assert cls.__name__ == "StorageAdapter"

    def test_in_memory_adapter(self) -> None:
        import hs_py

        cls = hs_py.InMemoryAdapter
        assert cls.__name__ == "InMemoryAdapter"

    def test_timescale_adapter(self) -> None:
        import hs_py

        cls = hs_py.TimescaleAdapter
        assert cls.__name__ == "TimescaleAdapter"

    def test_create_timescale_pool(self) -> None:
        import hs_py

        fn = hs_py.create_timescale_pool
        assert callable(fn)

    def test_user_store(self) -> None:
        import hs_py

        cls = hs_py.UserStore
        assert cls.__name__ == "UserStore"

    def test_user(self) -> None:
        import hs_py

        cls = hs_py.User
        assert cls.__name__ == "User"

    def test_create_user(self) -> None:
        import hs_py

        fn = hs_py.create_user
        assert callable(fn)

    def test_ensure_superuser(self) -> None:
        import hs_py

        fn = hs_py.ensure_superuser
        assert callable(fn)

    def test_role(self) -> None:
        import hs_py

        cls = hs_py.Role
        assert cls.__name__ == "Role"

    def test_unknown_attr_raises(self) -> None:
        import pytest

        import hs_py

        with pytest.raises(AttributeError, match="no attribute"):
            _ = hs_py.NoSuchThing


class TestStorageLazyImports:
    """Exercise lazy-loaded attributes in hs_py.storage.__getattr__."""

    def test_redis_adapter(self) -> None:
        from hs_py import storage

        cls = storage.RedisAdapter
        assert cls.__name__ == "RedisAdapter"

    def test_create_redis_client(self) -> None:
        from hs_py import storage

        fn = storage.create_redis_client
        assert callable(fn)

    def test_timescale_adapter(self) -> None:
        from hs_py import storage

        cls = storage.TimescaleAdapter
        assert cls.__name__ == "TimescaleAdapter"

    def test_create_timescale_pool(self) -> None:
        from hs_py import storage

        fn = storage.create_timescale_pool
        assert callable(fn)

    def test_unknown_attr_raises(self) -> None:
        import pytest

        from hs_py import storage

        with pytest.raises(AttributeError, match="no attribute"):
            _ = storage.NoSuchThing
