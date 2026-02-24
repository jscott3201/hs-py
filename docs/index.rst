haystack-py
===========

Asynchronous `Project Haystack <https://project-haystack.org/>`_ client and
server library for Python 3.13+.

haystack-py implements the Project Haystack protocol for exchanging tagged
building/IoT data with an async-first architecture built on Python's native
``asyncio`` framework. It provides HTTP and WebSocket transports, four wire
formats (JSON, Zinc, Trio, CSV), SCRAM-SHA-256 and mTLS authentication,
pluggable storage backends, and full ontology support.

.. code-block:: python

   import asyncio
   from hs_py import Client

   async def main():
       async with Client("http://server/api", "admin", "secret") as c:
           about = await c.about()
           points = await c.read("point and temp and sensor")

   asyncio.run(main())

Head to :doc:`getting-started` for installation and first steps, or browse
the :doc:`guide/client-guide` to see what haystack-py can do. For server setup
with storage backends, see :doc:`guide/server-guide` and
:doc:`guide/storage-guide`.

.. toctree::
   :caption: Getting Started
   :maxdepth: 2

   getting-started
   features

.. toctree::
   :caption: User Guide
   :maxdepth: 2

   guide/data-types
   guide/client-guide
   guide/server-guide
   guide/auth-users-permissions
   guide/storage-guide
   guide/encoding-guide
   guide/filter-guide
   guide/websocket-guide
   guide/ontology-guide
   guide/tls-guide
   guide/watch-guide
   guide/error-handling
   guide/observability

.. toctree::
   :caption: API Reference
   :maxdepth: 2

   api/core
   api/types
   api/encoding
   api/filter
   api/ontology
   api/storage
   api/websocket
   api/security

.. toctree::
   :caption: Project
   :maxdepth: 1

   changelog


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
