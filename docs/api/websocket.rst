WebSocket
=========

.. warning::

   The WebSocket transport API is **experimental** and subject to breaking
   changes in future releases.

WebSocket-based Haystack transport with sans-I/O protocol, async client,
and server implementations.

Sans-I/O Protocol
-----------------

Core WebSocket protocol logic, independent of any async framework.

.. automodule:: hs_py.ws
   :members:

Client
------

Async WebSocket client, reconnecting client, connection pool, and channel
multiplexer.

.. automodule:: hs_py.ws_client
   :members:

Server
------

Async WebSocket server with SCRAM handshake and batch dispatch.

.. automodule:: hs_py.ws_server
   :members:

Binary Frame Codec
------------------

Binary WebSocket frame encoding and decoding for compact Haystack message
transport.

.. automodule:: hs_py.ws_codec
   :members:
