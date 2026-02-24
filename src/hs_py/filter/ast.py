"""Filter expression AST node types.

Defines the abstract syntax tree produced by the filter parser.
Each node is a frozen dataclass for immutability and pattern matching.

See: https://project-haystack.org/doc/docHaystack/Filters
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "And",
    "Cmp",
    "CmpOp",
    "Has",
    "Missing",
    "Node",
    "Or",
    "Path",
]


class CmpOp(Enum):
    """Comparison operators for filter expressions."""

    EQ = "=="
    """Equal (``==``)."""

    NE = "!="
    """Not equal (``!=``)."""

    LT = "<"
    """Less than (``<``)."""

    LE = "<="
    """Less than or equal (``<=``)."""

    GT = ">"
    """Greater than (``>``)."""

    GE = ">="
    """Greater than or equal (``>=``)."""


@dataclass(frozen=True, slots=True)
class Path:
    """Tag path expression (e.g. ``equipRef->siteRef->dis``).

    Single-segment paths contain one name. Multi-segment paths use the
    ``->`` dereference operator to navigate through Ref-valued tags.
    """

    names: tuple[str, ...]
    """Ordered path segments (at least one)."""

    def __post_init__(self) -> None:
        if not self.names:
            raise ValueError("Path must have at least one name segment")

    def __str__(self) -> str:
        return "->".join(self.names)


@dataclass(frozen=True, slots=True)
class Has:
    """Tag existence check (``path``)."""

    path: Path
    """Tag path to test for presence."""


@dataclass(frozen=True, slots=True)
class Missing:
    """Tag absence check (``not path``)."""

    path: Path
    """Tag path to test for absence."""


@dataclass(frozen=True, slots=True)
class Cmp:
    """Comparison of a tag path to a literal value."""

    path: Path
    """Left-hand tag path."""

    op: CmpOp
    """Comparison operator."""

    val: Any
    """Right-hand literal value."""


@dataclass(frozen=True, slots=True)
class And:
    """Logical AND of two filter nodes."""

    left: Node
    """Left operand."""

    right: Node
    """Right operand."""


@dataclass(frozen=True, slots=True)
class Or:
    """Logical OR of two filter nodes."""

    left: Node
    """Left operand."""

    right: Node
    """Right operand."""


#: Union of all filter AST node types.
Node = Has | Missing | Cmp | And | Or
