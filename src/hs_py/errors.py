"""Haystack exception hierarchy.

All exceptions derive from :class:`HaystackError` for convenient catching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hs_py.grid import Grid

__all__ = [
    "AuthError",
    "CallError",
    "HaystackError",
    "NetworkError",
]


class HaystackError(Exception):
    """Base exception for all hs-py errors."""


class AuthError(HaystackError):
    """Authentication handshake failure."""


class CallError(HaystackError):
    """Server returned an error grid.

    The :attr:`grid` attribute contains the full error grid with ``err``
    marker, ``dis`` description, and optional ``errTrace``.
    """

    def __init__(self, dis: str, grid: Grid) -> None:
        """Initialise from a server error grid.

        :param dis: Human-readable error description.
        :param grid: The full error :class:`~hs_py.grid.Grid`.
        """
        super().__init__(dis)
        self.grid = grid

    @property
    def dis(self) -> str:
        """Human-readable error description."""
        return str(self.args[0])

    @property
    def trace(self) -> str | None:
        """Optional server stack trace."""
        return self.grid.meta.get("errTrace")


class NetworkError(HaystackError):
    """Network-level communication failure."""
