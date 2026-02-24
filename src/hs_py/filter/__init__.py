"""Haystack filter expression parser and evaluator.

Parse filter strings into AST nodes, then evaluate them against entity
dicts or grids.

Usage::

    from hs_py.filter import parse, evaluate, evaluate_grid

    ast = parse("point and sensor")
    assert evaluate(ast, {"point": MARKER, "sensor": MARKER})

    filtered = evaluate_grid(ast, grid)
"""

from hs_py.filter.ast import And, Cmp, CmpOp, Has, Missing, Node, Or, Path
from hs_py.filter.eval import Resolver, evaluate, evaluate_grid
from hs_py.filter.parser import ParseError, parse

__all__ = [
    "And",
    "Cmp",
    "CmpOp",
    "Has",
    "Missing",
    "Node",
    "Or",
    "ParseError",
    "Path",
    "Resolver",
    "evaluate",
    "evaluate_grid",
    "parse",
]
