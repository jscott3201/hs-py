.. _guide-error-handling:

Error Handling
==============

haystack-py uses a structured exception hierarchy to distinguish between different
failure modes.  This guide covers the exception types, error grids, and
patterns for robust error handling.

.. seealso::

   :doc:`../api/core` for the full exception API reference.

.. _guide-errors-hierarchy:

Exception Hierarchy
-------------------

.. code-block:: text

   HaystackError          Base for all haystack-py exceptions
   ├── AuthError          Authentication failures (SCRAM, token, cert)
   ├── CallError          Server returned an error grid
   └── NetworkError       Connection, timeout, transport failures

All exceptions inherit from :class:`~hs_py.errors.HaystackError`, so you
can catch everything with a single handler or be specific:

.. code-block:: python

   from hs_py import Client, HaystackError, CallError, AuthError, NetworkError

   async with Client("http://host/api", "user", "pass") as c:
       try:
           data = await c.read("point")
       except AuthError:
           # Bad credentials, expired token, cert rejected
           ...
       except CallError as e:
           # Server returned an error grid
           print(e.grid)   # The error Grid
           ...
       except NetworkError:
           # Connection refused, timeout, DNS failure
           ...
       except HaystackError:
           # Catch-all for any Haystack error
           ...

.. _guide-errors-call:

CallError and Error Grids
-------------------------

When a Haystack server encounters an error processing a request, it returns
an **error grid** — a grid with error metadata in its ``meta`` dict.
The client detects error grids and raises :class:`~hs_py.errors.CallError`.

.. code-block:: python

   from hs_py.errors import CallError

   try:
       data = await c.read("invalid:::filter")
   except CallError as e:
       print(f"Error: {e.dis}")           # Human-readable description
       print(f"Trace: {e.trace}")          # Optional server stack trace (or None)
       print(f"Error grid: {e.grid}")      # The full error Grid
       # e.grid.meta typically contains:
       #   "err": Marker
       #   "dis": "error message"
       #   "errTrace": "stack trace..."

Creating Error Grids
^^^^^^^^^^^^^^^^^^^^

On the server side, create error grids with the factory method:

.. code-block:: python

   from hs_py import Grid

   err = Grid.make_error("something went wrong")
   assert err.is_error

   # Check if any grid is an error
   if grid.is_error:
       print("Got an error grid:", grid.meta.get("dis"))

Raising Errors in Server Handlers
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In :class:`~hs_py.ops.HaystackOps` methods, raise ``CallError`` to
return an error grid to the client:

.. code-block:: python

   from hs_py.errors import CallError
   from hs_py import Grid, HaystackOps

   class MyOps(HaystackOps):
       async def read(self, grid: Grid) -> Grid:
           if not grid.meta.get("filter"):
               raise CallError("filter is required", Grid.make_error("filter is required"))
           ...

Unhandled exceptions in handlers are caught by the error middleware and
converted to error grids automatically — but prefer explicit errors for
known failure modes.

.. _guide-errors-auth:

AuthError
---------

:class:`~hs_py.errors.AuthError` is raised for authentication failures:

- Wrong username or password during SCRAM handshake
- Expired or invalid bearer token
- Rejected client certificate in mTLS

.. code-block:: python

   from hs_py import Client
   from hs_py.errors import AuthError

   try:
       async with Client("http://host/api", "wrong", "creds") as c:
           await c.about()
   except AuthError as e:
       print(f"Authentication failed: {e}")

.. _guide-errors-network:

NetworkError
------------

:class:`~hs_py.errors.NetworkError` wraps transport-level failures:

- Connection refused or reset
- DNS resolution failure
- Request timeout
- TLS handshake failure

.. code-block:: python

   from hs_py import Client
   from hs_py.errors import NetworkError

   try:
       async with Client("http://unreachable:9999/api") as c:
           await c.about()
   except NetworkError as e:
       print(f"Connection failed: {e}")

.. _guide-errors-patterns:

Common Patterns
---------------

Retry with Backoff
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   import asyncio
   from hs_py import Client, NetworkError

   async def read_with_retry(c: Client, filter_str: str, max_retries: int = 3):
       for attempt in range(max_retries):
           try:
               return await c.read(filter_str)
           except NetworkError:
               if attempt == max_retries - 1:
                   raise
               await asyncio.sleep(2 ** attempt)

Graceful Degradation
^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from hs_py import Client, CallError, NetworkError

   async def safe_read(c: Client, filter_str: str):
       try:
           return await c.read(filter_str)
       except CallError:
           # Server can't fulfill the request — return empty
           return Grid.make_empty()
       except NetworkError:
           # Server unreachable — return cached data or empty
           return Grid.make_empty()

Distinguishing Error Types
^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from hs_py import HaystackError, CallError, AuthError, NetworkError

   try:
       result = await c.read("point")
   except AuthError:
       # Credentials issue — re-authenticate or alert admin
       log.error("Authentication expired, re-authenticating")
   except CallError as e:
       # Application error — log and investigate
       log.warning("Server error: %s", e.grid.meta.get("dis"))
   except NetworkError:
       # Infrastructure issue — retry or failover
       log.error("Network unreachable, switching to backup")
   except HaystackError:
       # Unexpected — log full context
       log.exception("Unexpected Haystack error")
