"""Haystack storage adapters."""

from hs_py.storage.memory import InMemoryAdapter
from hs_py.storage.protocol import StorageAdapter

__all__ = [
    "InMemoryAdapter",
    "RedisAdapter",
    "StorageAdapter",
    "TimescaleAdapter",
    "create_redis_client",
    "create_timescale_pool",
]


def __getattr__(name: str) -> object:
    if name == "RedisAdapter":
        from hs_py.storage.redis import RedisAdapter

        return RedisAdapter
    if name == "create_redis_client":
        from hs_py.storage.redis import create_redis_client

        return create_redis_client
    if name == "TimescaleAdapter":
        from hs_py.storage.timescale import TimescaleAdapter

        return TimescaleAdapter
    if name == "create_timescale_pool":
        from hs_py.storage.timescale import create_timescale_pool

        return create_timescale_pool
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
