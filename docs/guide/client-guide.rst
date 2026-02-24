HTTP Client
===========

The :class:`~hs_py.client.Client` implements all standard Project Haystack HTTP
operations as an async context manager.  It handles SCRAM-SHA-256
authentication, transparent 401 retry, JSON encoding/decoding, TLS, and
connection management.

.. seealso::

   :doc:`../api/core` for the full Client API reference.

.. _guide-client-connect:

Connecting
----------

The client requires a base URL pointing to the Haystack API root.
Authentication credentials are optional (some servers allow anonymous access).

.. code-block:: python

   from hs_py import Client

   async with Client("http://host/api", "user", "pass") as c:
       about = await c.about()
       print(about)

The ``async with`` block creates an ``aiohttp.ClientSession``, authenticates
on the first request, and closes the session on exit.

.. _guide-client-options:

Client Options
^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Parameter
     - Type
     - Description
   * - ``base_url``
     - ``str``
     - Haystack API root URL (required)
   * - ``username``
     - ``str``
     - Username for SCRAM authentication
   * - ``password``
     - ``str``
     - Password for SCRAM authentication
   * - ``timeout``
     - ``aiohttp.ClientTimeout``
     - Request timeout configuration
   * - ``connector``
     - ``aiohttp.TCPConnector``
     - Custom TCP connector (connection pooling, limits)
   * - ``tls``
     - :class:`~hs_py.tls.TLSConfig`
     - TLS certificate configuration for mTLS

Custom Timeout
""""""""""""""

.. code-block:: python

   import aiohttp
   from hs_py import Client

   timeout = aiohttp.ClientTimeout(total=30, connect=5)

   async with Client("https://host/api", "user", "pass", timeout=timeout) as c:
       data = await c.read("point")

Custom Connector
""""""""""""""""

.. code-block:: python

   import aiohttp
   from hs_py import Client

   # Limit to 20 concurrent connections
   connector = aiohttp.TCPConnector(limit=20)

   async with Client("https://host/api", "user", "pass", connector=connector) as c:
       data = await c.read("point")

TLS Client Certificates
""""""""""""""""""""""""

See :ref:`guide-tls` for full TLS configuration.

.. code-block:: python

   from hs_py import Client, TLSConfig

   tls = TLSConfig(
       certificate_path="client.crt",
       private_key_path="client.key",
       ca_certificates_path="ca.crt",
   )

   async with Client("https://host/api", "user", "pass", tls=tls) as c:
       about = await c.about()

.. _guide-client-auth:

Authentication
--------------

The client performs SCRAM-SHA-256 authentication automatically on the first
request.  If the server returns a 401 during normal operation, the client
re-authenticates and retries the request once.

The authentication flow follows the Haystack HTTP Authentication spec:

1. **HELLO** — client sends username, server responds with supported hash
   algorithms and a handshake token.
2. **Client-first** — client generates a nonce and sends a SCRAM client-first
   message.
3. **Server-first** — server responds with its nonce, salt, and iteration
   count.
4. **Client-final** — client computes the SCRAM proof, server verifies and
   returns a bearer token.

If the server only supports PLAINTEXT, the client falls back automatically.

All cryptographic operations use the ``cryptography`` library (not stdlib
``hashlib``/``hmac``).  See :doc:`../api/security` for protocol details.

.. code-block:: python

   # Authentication happens transparently:
   async with Client("http://host/api", "admin", "s3cret") as c:
       # First call triggers SCRAM handshake
       about = await c.about()
       # Subsequent calls reuse the bearer token
       points = await c.read("point")

.. _guide-client-ops:

Standard Operations
-------------------

All operations return a :class:`~hs_py.grid.Grid`.  The client raises
:class:`~hs_py.errors.CallError` if the server returns an error grid.

About
^^^^^

Retrieve server information:

.. code-block:: python

   about = await c.about()
   # Grid with one row: haystackVersion, tz, serverName, etc.

Ops
^^^

Discover which operations the server supports:

.. code-block:: python

   ops = await c.ops()
   for row in ops:
       print(row["name"])

Formats
^^^^^^^

Query supported MIME types:

.. code-block:: python

   formats = await c.formats()

.. _guide-client-read:

Read
^^^^

Read records by filter expression or by id list:

.. code-block:: python

   # Filter read — returns all matching records
   points = await c.read("point and sensor")

   # Read with limit
   first_ten = await c.read("point", limit=10)

   # Read by ids
   recs = await c.read_by_ids([Ref("p:demo:r:1"), Ref("p:demo:r:2")])

.. _guide-client-nav:

Nav
^^^

Navigate the site/equip/point tree:

.. code-block:: python

   # Top-level navigation
   roots = await c.nav()

   # Navigate into a specific node
   children = await c.nav(nav_id="@site-1")

.. _guide-client-hisread:

History Read
^^^^^^^^^^^^

Read time-series data for a single point or a batch of points:

.. code-block:: python

   # Single point, named range
   his = await c.his_read(Ref("p:demo:r:1"), "yesterday")

   # Single point, date range
   his = await c.his_read(Ref("p:demo:r:1"), "2026-01-01,2026-01-31")

   # Batch read — multiple points at once
   batch = await c.his_read_batch(
       [Ref("p:demo:r:1"), Ref("p:demo:r:2")],
       "today",
   )

.. _guide-client-hiswrite:

History Write
^^^^^^^^^^^^^

Write time-series data for one or more points:

.. code-block:: python

   from datetime import datetime, timezone
   from hs_py import GridBuilder, Ref, Number

   b = GridBuilder()
   b.add_col("ts")
   b.add_col("val")
   now = datetime.now(timezone.utc)
   b.add_row({"ts": now, "val": Number(72.5, "°F")})
   items = b.to_grid()

   # Write to a single point
   await c.his_write(Ref("p:demo:r:1"), [
       {"ts": now, "val": Number(72.5, "°F")},
   ])

   # Batch write — pre-built grid with ts and v0/v1 columns
   batch = GridBuilder()
   batch.add_col("ts")
   batch.add_col("v0")  # First point column
   batch.add_col("v1")  # Second point column
   batch.set_meta({"id": Ref("p:demo:r:1"), "id1": Ref("p:demo:r:2")})
   batch.add_row({"ts": now, "v0": Number(72.5, "°F"), "v1": Number(68.0, "°F")})
   await c.his_write_batch(batch.to_grid())

.. _guide-client-pointwrite:

Point Write
^^^^^^^^^^^

Write to the 16-level priority array:

.. code-block:: python

   # Write at priority level 16 (default)
   await c.point_write(Ref("p:demo:r:1"), 16, Number(72, "°F"))

   # Write with optional who and duration
   await c.point_write(Ref("p:demo:r:1"), 16, Number(72, "°F"), who="operator", duration=Number(1, "hr"))

   # Read the priority array
   arr = await c.point_write_array(Ref("p:demo:r:1"))

.. _guide-client-watches:

Watches
^^^^^^^

Subscribe to real-time change notifications.  See :doc:`watch-guide` for
delta encoding and the full watch lifecycle.

.. code-block:: python

   # Subscribe — creates or updates a watch
   watch = await c.watch_sub(
       [Ref("p:demo:r:1"), Ref("p:demo:r:2")],
       watch_dis="My Watch",
       lease=Number(5, "min"),  # Optional lease duration
   )
   watch_id = watch.meta.get("watchId")

   # Poll for changes
   changes = await c.watch_poll(watch_id)

   # Poll with full refresh (re-sends all current values)
   changes = await c.watch_poll(watch_id, refresh=True)

   # Remove points from a watch
   await c.watch_unsub(watch_id, [Ref("p:demo:r:1")])

   # Close the watch entirely
   await c.watch_close(watch_id)

.. _guide-client-actions:

Invoke Action
^^^^^^^^^^^^^

Trigger a server-defined action on a record:

.. code-block:: python

   result = await c.invoke_action(
       Ref("p:demo:r:1"),
       "reset",
       {"duration": Number(30, "s")},
   )

.. _guide-client-errors:

Error Handling
--------------

The client raises specific exceptions for different failure modes.
See :ref:`guide-error-handling` for detailed patterns.

.. code-block:: python

   from hs_py import Client, CallError, AuthError, NetworkError

   async with Client("http://host/api", "user", "pass") as c:
       try:
           data = await c.read("point")
       except CallError as e:
           # Server returned an error grid
           print(f"Server error: {e}")
           print(f"Error grid: {e.grid}")
       except AuthError as e:
           # Authentication failed
           print(f"Auth failed: {e}")
       except NetworkError as e:
           # Connection or transport error
           print(f"Network error: {e}")

.. _guide-client-close:

Closing the Client
------------------

The ``async with`` block automatically sends the Haystack ``close`` op to
the server and closes the underlying HTTP session:

.. code-block:: python

   async with Client("http://host/api", "user", "pass") as c:
       points = await c.read("point")
   # On exit: sends close op, then closes the aiohttp session

You can also call ``close()`` explicitly:

.. code-block:: python

   c = Client("http://host/api", "user", "pass")
   await c.__aenter__()
   try:
       points = await c.read("point")
   finally:
       await c.close()  # Sends close op + closes session
