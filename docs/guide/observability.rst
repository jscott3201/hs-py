.. _guide-observability:

Observability
=============

hs-py provides two levels of observability: metrics hooks for structured
telemetry and standard Python logging for operational insight.

.. seealso::

   :doc:`../api/core` for the full MetricsHooks API reference.

.. _guide-metrics:

Metrics Hooks
-------------

:class:`~hs_py.metrics.MetricsHooks` is a frozen dataclass with optional
callbacks for key events.  Pass it to the WebSocket client or server to
receive structured telemetry.

.. code-block:: python

   from hs_py import MetricsHooks

   hooks = MetricsHooks(
       on_ws_connect=lambda addr: print(f"Connected: {addr}"),
       on_ws_disconnect=lambda addr: print(f"Disconnected: {addr}"),
       on_ws_message_sent=lambda op, size: print(f"Sent {op}: {size} bytes"),
       on_ws_message_recv=lambda op, size: print(f"Recv {op}: {size} bytes"),
       on_request=lambda op, duration: print(f"{op} took {duration:.3f}s"),
       on_error=lambda op, err_type: print(f"{op} failed: {err_type}"),
   )

Available Hooks
^^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 25 35 40

   * - Hook
     - Signature
     - Triggered When
   * - ``on_ws_connect``
     - ``(remote_addr: str) -> None``
     - WebSocket connection established
   * - ``on_ws_disconnect``
     - ``(remote_addr: str) -> None``
     - WebSocket connection closed
   * - ``on_ws_message_sent``
     - ``(op: str, byte_count: int) -> None``
     - Message sent (op name, payload bytes)
   * - ``on_ws_message_recv``
     - ``(op: str, byte_count: int) -> None``
     - Message received (op name, payload bytes)
   * - ``on_request``
     - ``(op: str, duration_secs: float) -> None``
     - Request completed (op name, seconds)
   * - ``on_error``
     - ``(op: str, error_type: str) -> None``
     - Request failed (op name, error type string)

All hooks are optional — provide only the ones you need.  Hooks that raise
exceptions are logged at DEBUG level and silently suppressed to avoid
disrupting the transport.

Using with WebSocket Client
^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from hs_py.ws_client import WebSocketClient
   from hs_py import MetricsHooks

   hooks = MetricsHooks(
       on_ws_connect=lambda addr: stats.incr("ws.connect"),
       on_ws_disconnect=lambda addr: stats.incr("ws.disconnect"),
       on_ws_message_sent=lambda op, size: stats.histogram("ws.send_bytes", size),
       on_ws_message_recv=lambda op, size: stats.histogram("ws.recv_bytes", size),
   )

   async with WebSocketClient("ws://host/api/ws", metrics=hooks) as ws:
       await ws.read("point")

Prometheus Example
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from prometheus_client import Counter, Histogram
   from hs_py import MetricsHooks

   REQUEST_DURATION = Histogram(
       "haystack_request_seconds", "Haystack op duration", ["op"]
   )
   REQUEST_ERRORS = Counter(
       "haystack_request_errors_total", "Haystack op errors", ["op"]
   )
   WS_CONNECTIONS = Counter(
       "haystack_ws_connections_total", "WebSocket connections"
   )

   hooks = MetricsHooks(
       on_ws_connect=lambda addr: WS_CONNECTIONS.inc(),
       on_request=lambda op, dur: REQUEST_DURATION.labels(op=op).observe(dur),
       on_error=lambda op, err: REQUEST_ERRORS.labels(op=op).inc(),
   )

.. _guide-logging:

Logging
-------

hs-py uses the standard ``logging`` module.  All loggers are under the
``hs_py`` namespace.

Quick Setup
^^^^^^^^^^^

.. code-block:: python

   import logging

   logging.basicConfig(
       level=logging.INFO,
       format="%(asctime)s %(name)s %(levelname)s %(message)s",
   )

Logger Hierarchy
^^^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Logger
     - Events
   * - ``hs_py.client``
     - HTTP requests, responses, auth, retry
   * - ``hs_py.auth``
     - SCRAM handshake steps, token exchange
   * - ``hs_py.fastapi_server``
     - Incoming requests, auth, errors, middleware
   * - ``hs_py.ws``
     - WebSocket frames, heartbeat, connection lifecycle
   * - ``hs_py.ws_client``
     - WS client requests, reconnection, pool events
   * - ``hs_py.ws_server``
     - WS server dispatch, push, connection limits

Targeted Logging
^^^^^^^^^^^^^^^^

Enable verbose logging for specific modules without flooding the console:

.. code-block:: python

   import logging

   # Only show debug output for the client
   logging.getLogger("hs_py.client").setLevel(logging.DEBUG)

   # Quiet the WebSocket heartbeat chatter
   logging.getLogger("hs_py.ws").setLevel(logging.WARNING)

Log Levels
^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Level
     - What You See
   * - ``DEBUG``
     - Request/response bodies, SCRAM steps, frame contents, reconnection
       attempts — useful for troubleshooting but verbose in production
   * - ``INFO``
     - Connection lifecycle (opened, closed), authentication success,
       server startup
   * - ``WARNING``
     - Unexpected conditions that are handled (auth retry, reconnection,
       connection limit reached)
   * - ``ERROR``
     - Unrecoverable failures (connection lost, auth rejected, handler
       exceptions)
