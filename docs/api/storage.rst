Storage
=======

Pluggable storage backends for the Haystack server.

Protocol
--------

The :class:`~hs_py.storage.protocol.StorageAdapter` runtime-checkable
protocol that all backends implement for entity and history storage, and the
:class:`~hs_py.storage.protocol.UserStore` protocol for user management.

.. automodule:: hs_py.storage.protocol
   :members:

Memory
------

In-memory storage adapter for testing and prototyping.

.. automodule:: hs_py.storage.memory
   :members:

Redis
-----

Redis-backed storage using RediSearch for entity indexing and RedisTimeSeries
for history.

.. automodule:: hs_py.storage.redis
   :members:

Redis Operations
^^^^^^^^^^^^^^^^

Low-level Redis operations for entity and time-series management.

.. automodule:: hs_py.redis_ops
   :members:

TimescaleDB
-----------

PostgreSQL/TimescaleDB storage with JSONB entities, hypertable history, and
filter-to-SQL pushdown.

.. automodule:: hs_py.storage.timescale
   :members:
