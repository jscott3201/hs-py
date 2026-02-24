.. _guide-websocket:

WebSocket Transport
===================

.. warning::

   The WebSocket transport API is **experimental** and subject to breaking
   changes in future releases.

haystack-py includes a full WebSocket transport layer for persistent, bidirectional
communication.  The WebSocket client mirrors the HTTP
:class:`~hs_py.client.Client` API, with added support for server-push
notifications, binary frames, channel multiplexing, and automatic reconnection.

.. seealso::

   :doc:`../api/websocket` for the WebSocket API reference,
   :doc:`../api/websocket` for binary frame codec details.

Architecture
------------

The WebSocket stack is split into four layers:

.. list-table::
   :header-rows: 1
   :widths: 25 25 50

   * - Module
     - Layer
     - Responsibility
   * - :mod:`hs_py.ws`
     - Sans-I/O
     - Protocol logic, frame I/O, heartbeat, TLS
   * - :mod:`hs_py.ws_client`
     - Client
     - Request/response, watches, reconnection, channel multiplexing
   * - :mod:`hs_py.ws_server`
     - Server
     - Dispatch, auth, push distribution, connection limits
   * - :mod:`hs_py.ws_codec`
     - Codec
     - Binary frame encoding/decoding

.. _guide-ws-client:

WebSocket Client
----------------

Basic Usage
^^^^^^^^^^^

:class:`~hs_py.ws_client.WebSocketClient` connects to a Haystack WebSocket
endpoint and provides the same operations as the HTTP client:

.. code-block:: python

   from hs_py.ws_client import WebSocketClient
   from hs_py.kinds import Ref

   async with WebSocketClient("ws://host/api/ws", auth_token="token") as ws:
       about = await ws.about()
       points = await ws.read("point and sensor")
       his = await ws.his_read(Ref("p1"), "yesterday")

All standard operations are available: ``about``, ``ops``, ``formats``,
``read``, ``read_by_ids``, ``nav``, ``his_read``, ``his_write``,
``point_write``, ``point_write_array``, ``watch_sub``, ``watch_unsub``,
``watch_poll``, ``invoke_action``, and ``close``.

Client Options
^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Parameter
     - Type
     - Description
   * - ``url``
     - ``str``
     - WebSocket endpoint URL (``ws://`` or ``wss://``)
   * - ``auth_token``
     - ``str``
     - Bearer token sent on connect
   * - ``tls``
     - :class:`~hs_py.tls.TLSConfig`
     - TLS configuration for ``wss://`` connections
   * - ``timeout``
     - ``float``
     - Per-request timeout in seconds (default: 30)
   * - ``heartbeat``
     - ``float``
     - Ping interval in seconds (default: 30, 0 to disable)
   * - ``metrics``
     - :class:`~hs_py.metrics.MetricsHooks`
     - Observability callbacks
   * - ``binary``
     - ``bool``
     - Enable binary frame encoding (default: ``False``)
   * - ``compression``
     - ``bool``
     - Enable permessage-deflate compression (default: ``False``)

.. _guide-ws-watches:

Watch Subscriptions
^^^^^^^^^^^^^^^^^^^

The WebSocket client supports watch subscriptions for real-time updates.
See :doc:`watch-guide` for delta encoding and the
:class:`~hs_py.watch.WatchAccumulator`.

.. code-block:: python

   from hs_py.ws_client import WebSocketClient
   from hs_py.kinds import Ref

   async with WebSocketClient("ws://host/api/ws", auth_token="token") as ws:
       watch = await ws.watch_sub(
           [Ref("p1"), Ref("p2")], watch_dis="My Watch",
       )
       watch_id = watch.meta["watchId"]

       # Subscribe with a server-side filter
       watch = await ws.watch_sub(
           [Ref("p1"), Ref("p2")], watch_dis="Filtered",
           filter="curVal > 70",
       )

       # Poll for changes
       delta = await ws.watch_poll(watch_id)
       for row in delta:
           print(f"  {row['id']}: curVal={row.get('curVal')}")

Watch Push Callbacks
^^^^^^^^^^^^^^^^^^^^

Register a callback to receive server-initiated watch push messages.  The
callback receives the watch ID and the delta grid:

.. code-block:: python

   from hs_py.ws_client import WebSocketClient
   from hs_py import Grid
   from hs_py.watch import WatchAccumulator
   from hs_py.kinds import Ref

   acc = WatchAccumulator()

   def handle_push(watch_id: str, grid: Grid) -> None:
       acc.apply_delta(grid)
       print(f"Watch {watch_id}: {len(grid)} rows changed")

   async with WebSocketClient("ws://host/api/ws", auth_token="token") as ws:
       ws.on_watch_push(handle_push)
       await ws.watch_sub([Ref("p1"), Ref("p2")], watch_dis="My Watch")
       # Push updates arrive via the callback while the connection is open
       await asyncio.sleep(60)

The callback is also preserved across reconnections when using
:class:`~hs_py.ws_client.ReconnectingWebSocketClient`:

.. code-block:: python

   from hs_py.ws_client import ReconnectingWebSocketClient

   client = ReconnectingWebSocketClient("ws://host/api/ws", auth_token="token")
   client.on_watch_push(handle_push)
   await client.start()

.. _guide-ws-batch:

Batch Operations
^^^^^^^^^^^^^^^^

Send multiple operations in a single WebSocket message for reduced round-trips:

.. code-block:: python

   from hs_py import GridBuilder

   read_grid = GridBuilder().add_col("filter").add_row(
       {"filter": "point and sensor"}
   ).to_grid()
   about_grid = GridBuilder().to_grid()

   results = await ws.batch(("read", read_grid), ("about", about_grid))
   # results is a list of Grids, one per operation

.. _guide-ws-binary:

Binary Frames
^^^^^^^^^^^^^

Binary frame mode replaces JSON envelopes with a compact 4-byte header,
reducing overhead for high-frequency operations.  See :doc:`../api/websocket`
for the codec API.

.. code-block:: python

   async with WebSocketClient(
       "ws://host/api/ws", auth_token="token",
       binary=True,
   ) as ws:
       # Same API — binary encoding is transparent
       about = await ws.about()
       points = await ws.read("point")

Binary frame header format:

.. code-block:: text

   Byte 0: Flags (FLAG_RESPONSE=0x01, FLAG_ERROR=0x02, FLAG_PUSH=0x04)
   Bytes 1-2: Request ID (uint16, big-endian)
   Byte 3: Operation code (uint8)

Operation codes: ``about=1``, ``ops=2``, ``formats=3``, ``close=4``,
``read=10``, ``nav=11``, ``hisRead=12``, ``hisWrite=13``, ``pointWrite=14``,
``watchSub=15``, ``watchUnsub=16``, ``watchPoll=17``, ``invokeAction=18``.

.. _guide-ws-compress:

Compression
^^^^^^^^^^^

Enable permessage-deflate for bandwidth reduction on text-heavy payloads:

.. code-block:: python

   async with WebSocketClient(
       "ws://host/api/ws", auth_token="token",
       compression=True,
   ) as ws:
       points = await ws.read("point")

.. _guide-ws-reconnect:

Reconnecting Client
-------------------

:class:`~hs_py.ws_client.ReconnectingWebSocketClient` automatically
reconnects with exponential backoff when the connection drops:

.. code-block:: python

   from hs_py.ws_client import ReconnectingWebSocketClient

   client = ReconnectingWebSocketClient(
       "ws://host/api/ws", auth_token="token",
       min_reconnect_delay=1.0,
       max_reconnect_delay=60.0,
   )
   await client.start()
   try:
       about = await client.about()
   finally:
       await client.stop()

Parameters:

- ``min_reconnect_delay`` — Initial delay in seconds (default: 1.0).
- ``max_reconnect_delay`` — Maximum delay cap in seconds (default: 60.0).
- ``on_connect`` — Async callback invoked after each (re)connection.
- ``on_disconnect`` — Async callback invoked when the connection drops.

The delay doubles after each failed attempt, capped at ``max_reconnect_delay``.

.. _guide-ws-pool:

Channel Multiplexing
--------------------

:class:`~hs_py.ws_client.WebSocketPool` multiplexes multiple logical channels
over a single WebSocket connection.  Each channel is identified by a string
name included in the JSON envelope as the ``ch`` field.

.. code-block:: python

   from hs_py.ws_client import WebSocketPool

   async with WebSocketPool("ws://host/api/ws", auth_token="token") as pool:
       ch1 = pool.channel("tenant-1")
       ch2 = pool.channel("tenant-2")

       # Each channel's requests are scoped independently
       about1 = await ch1.about()
       about2 = await ch2.about()

.. _guide-ws-channel:

Channel Client
--------------

:class:`~hs_py.ws_client.ChannelClient` scopes requests to a named
channel within a pool, useful for multi-tenant or multi-context scenarios:

.. code-block:: python

   from hs_py.ws_client import WebSocketPool, ChannelClient

   async with WebSocketPool("ws://host/api/ws", auth_token="token") as pool:
       ch1 = ChannelClient(pool, channel="building-a")
       ch2 = ChannelClient(pool, channel="building-b")

       # Each channel's requests are scoped independently
       a_points = await ch1.read("point")
       b_points = await ch2.read("point")

.. _guide-ws-server:

WebSocket Server
----------------

:class:`~hs_py.ws_server.WebSocketServer` is a standalone WebSocket server
that dispatches messages to your :class:`~hs_py.ops.HaystackOps`
implementation:

.. code-block:: python

   from hs_py.ws_server import WebSocketServer

   ops = MyOps()
   server = WebSocketServer(ops, host="0.0.0.0", port=8080)
   await server.start()
   # ... server is running ...
   await server.stop()

Features:

- **JSON envelope dispatch** — routes ``op`` field to the matching handler
- **Binary frame support** — decodes binary frames and responds in kind
- **Batch requests** — processes ``batch`` messages as parallel operations
- **Watch push** — distributes watch updates to connected clients
- **Certificate auth** — extracts client CN from TLS for mTLS authentication
- **Token auth** — validates bearer tokens via ``auth_token`` parameter

Pushing Watch Updates
^^^^^^^^^^^^^^^^^^^^^

The server can push watch updates to all connected WebSocket clients:

.. code-block:: python

   from hs_py import Grid, Col, Ref, Number

   # Build the update grid
   update = Grid(
       cols=(Col("id", {}), Col("curVal", {})),
       rows=({"id": Ref("p1"), "curVal": Number(73.5, "°F")},),
   )

   # Push to all connected clients for a specific watch
   await server.push_watch("w1", update)
