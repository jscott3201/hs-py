"""hs-py — Async Project Haystack client library for Python."""

__version__ = "0.1.0"

from hs_py.auth_types import (
    Authenticator,
    CertAuthenticator,
    ScramCredentials,
    SimpleAuthenticator,
)
from hs_py.client import Client
from hs_py.convert import grid_to_pythonic
from hs_py.encoding import JsonVersion
from hs_py.errors import AuthError, CallError, HaystackError, NetworkError
from hs_py.filter import ParseError, evaluate, evaluate_grid, parse
from hs_py.grid import Col, Grid, GridBuilder
from hs_py.kinds import (
    MARKER,
    NA,
    REMOVE,
    Coord,
    Marker,
    Na,
    Number,
    Ref,
    Remove,
    Symbol,
    Uri,
    XStr,
)
from hs_py.metrics import MetricsHooks
from hs_py.ontology.rdf import export_jsonld, export_turtle
from hs_py.ops import HaystackOps
from hs_py.tls import (
    TLSConfig,
    build_client_ssl_context,
    build_server_ssl_context,
    extract_peer_cn,
    extract_peer_sans,
    generate_test_certificates,
)
from hs_py.watch import WatchAccumulator, WatchState
from hs_py.ws import HaystackWebSocket
from hs_py.ws_client import (
    ChannelClient,
    ReconnectingWebSocketClient,
    WebSocketClient,
    WebSocketPool,
)
from hs_py.ws_codec import (
    OP_CODES,
    decode_binary_frame,
    encode_binary_push,
    encode_binary_request,
    encode_binary_response,
)
from hs_py.ws_server import WebSocketServer


def __getattr__(name: str) -> object:
    if name == "RedisOps":
        from hs_py.redis_ops import RedisOps

        return RedisOps
    if name == "RedisAdapter":
        from hs_py.storage.redis import RedisAdapter

        return RedisAdapter
    if name == "create_redis_client":
        from hs_py.redis_ops import create_redis_client

        return create_redis_client
    if name == "create_fastapi_app":
        from hs_py.fastapi_server import create_fastapi_app

        return create_fastapi_app
    if name == "StorageAdapter":
        from hs_py.storage.protocol import StorageAdapter

        return StorageAdapter
    if name == "InMemoryAdapter":
        from hs_py.storage.memory import InMemoryAdapter

        return InMemoryAdapter
    if name == "TimescaleAdapter":
        from hs_py.storage.timescale import TimescaleAdapter

        return TimescaleAdapter
    if name == "create_timescale_pool":
        from hs_py.storage.timescale import create_timescale_pool

        return create_timescale_pool
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


__all__ = [
    "MARKER",
    "NA",
    "OP_CODES",
    "REMOVE",
    "AuthError",
    "Authenticator",
    "CallError",
    "CertAuthenticator",
    "ChannelClient",
    "Client",
    "Col",
    "Coord",
    "Grid",
    "GridBuilder",
    "HaystackError",
    "HaystackOps",
    "HaystackWebSocket",
    "InMemoryAdapter",
    "JsonVersion",
    "Marker",
    "MetricsHooks",
    "Na",
    "NetworkError",
    "Number",
    "ParseError",
    "ReconnectingWebSocketClient",
    "RedisAdapter",
    "RedisOps",
    "Ref",
    "Remove",
    "ScramCredentials",
    "SimpleAuthenticator",
    "StorageAdapter",
    "Symbol",
    "TLSConfig",
    "TimescaleAdapter",
    "Uri",
    "WatchAccumulator",
    "WatchState",
    "WebSocketClient",
    "WebSocketPool",
    "WebSocketServer",
    "XStr",
    "__version__",
    "build_client_ssl_context",
    "build_server_ssl_context",
    "create_fastapi_app",
    "create_redis_client",
    "create_timescale_pool",
    "decode_binary_frame",
    "encode_binary_push",
    "encode_binary_request",
    "encode_binary_response",
    "evaluate",
    "evaluate_grid",
    "export_jsonld",
    "export_turtle",
    "extract_peer_cn",
    "extract_peer_sans",
    "generate_test_certificates",
    "grid_to_pythonic",
    "parse",
]
