"""Optional metrics hooks for observability.

Provides a callback interface for tracking connection counts, message rates,
latency, and errors.  Users can plug in Prometheus, StatsD, or any metrics
backend by supplying callbacks on :class:`MetricsHooks`.

All hook invocations are guarded against exceptions so user callback errors
never break the transport.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "MetricsHooks",
]

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MetricsHooks:
    """Optional callback hooks for transport-level metrics.

    All callbacks are optional.  When set, they are invoked at the
    corresponding transport event.  Exceptions raised by callbacks
    are logged and silently suppressed.

    :param on_ws_connect: Called with ``(remote_addr,)`` on new WebSocket connection.
    :param on_ws_disconnect: Called with ``(remote_addr,)`` on WebSocket disconnect.
    :param on_ws_message_sent: Called with ``(op, byte_count)`` after sending a message.
    :param on_ws_message_recv: Called with ``(op, byte_count)`` after receiving a message.
    :param on_request: Called with ``(op, duration_secs)`` after completing a request.
    :param on_error: Called with ``(op, error_type)`` when an operation errors.
    """

    on_ws_connect: Callable[[str], None] | None = None
    """Called with ``(remote_addr,)`` on new WebSocket connection."""

    on_ws_disconnect: Callable[[str], None] | None = None
    """Called with ``(remote_addr,)`` on WebSocket disconnect."""

    on_ws_message_sent: Callable[[str, int], None] | None = None
    """Called with ``(op, byte_count)`` after sending a message."""

    on_ws_message_recv: Callable[[str, int], None] | None = None
    """Called with ``(op, byte_count)`` after receiving a message."""

    on_request: Callable[[str, float], None] | None = None
    """Called with ``(op, duration_secs)`` after completing a request."""

    on_error: Callable[[str, str], None] | None = None
    """Called with ``(op, error_type)`` when an operation errors."""


def _fire(hook: Callable[..., object] | None, *args: object) -> None:
    """Invoke a hook callback, suppressing any exceptions."""
    if hook is not None:
        try:
            hook(*args)
        except Exception:
            _log.debug("Metrics hook %s raised an exception", hook, exc_info=True)
