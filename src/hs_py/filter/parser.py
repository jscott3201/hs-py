"""Recursive descent parser for Haystack filter expressions.

Parses a filter string into an AST of :mod:`hs_py.filter.ast` nodes
with properly typed Haystack values.

Grammar::

    filter   = condOr
    condOr   = condAnd ("or" condAnd)*
    condAnd  = term ("and" term)*
    term     = "(" condOr ")" | "not" path | path [cmpOp val]
    path     = name ("->" name)*
    cmpOp    = "==" | "!=" | "<" | "<=" | ">" | ">="
    val      = bool | str | number | date | time | dateTime | uri | ref | symbol

See: https://project-haystack.org/doc/docHaystack/Filters
"""

from __future__ import annotations

import datetime
import functools
from typing import Any

from hs_py.encoding.scanner import parse_datetime
from hs_py.filter.ast import And, Cmp, CmpOp, Has, Missing, Node, Or, Path
from hs_py.filter.lexer import Lexer, Token, TokenType
from hs_py.kinds import Number, Ref, Symbol, Uri

__all__ = [
    "ParseError",
    "parse",
]

_CMP_OPS: dict[TokenType, CmpOp] = {
    TokenType.EQ: CmpOp.EQ,
    TokenType.NE: CmpOp.NE,
    TokenType.LT: CmpOp.LT,
    TokenType.LE: CmpOp.LE,
    TokenType.GT: CmpOp.GT,
    TokenType.GE: CmpOp.GE,
}


class ParseError(ValueError):
    """Raised when a filter string cannot be parsed."""


@functools.lru_cache(maxsize=256)
def parse(text: str) -> Node:
    """Parse a Haystack filter string into an AST.

    Results are cached for repeated filter expressions.

    :param text: Filter expression string.
    :returns: Root AST node.
    :raises ParseError: If the filter string is invalid.
    """
    return _Parser(text).parse()


class _Parser:
    """Recursive descent filter parser."""

    def __init__(self, text: str) -> None:
        try:
            self._tokens = Lexer(text).tokenize()
        except ValueError as exc:
            raise ParseError(str(exc)) from exc
        self._pos = 0

    def parse(self) -> Node:
        node = self._cond_or()
        if self._peek().type != TokenType.EOF:
            tok = self._peek()
            raise ParseError(f"Unexpected token {tok.type.name} at position {tok.pos}")
        return node

    # ---- Grammar rules ------------------------------------------------------

    def _cond_or(self) -> Node:
        left = self._cond_and()
        while self._peek().type == TokenType.OR:
            self._advance()
            right = self._cond_and()
            left = Or(left, right)
        return left

    def _cond_and(self) -> Node:
        left = self._term()
        while self._peek().type == TokenType.AND:
            self._advance()
            right = self._term()
            left = And(left, right)
        return left

    def _term(self) -> Node:
        tok = self._peek()

        # Parenthesized expression
        if tok.type == TokenType.LPAREN:
            self._advance()
            node = self._cond_or()
            self._expect(TokenType.RPAREN)
            return node

        # Missing (not path)
        if tok.type == TokenType.NOT:
            self._advance()
            path = self._path()
            return Missing(path)

        # Has or Cmp
        path = self._path()
        if self._peek().type in _CMP_OPS:
            op = _CMP_OPS[self._advance().type]
            val = self._val()
            return Cmp(path, op, val)
        return Has(path)

    def _path(self) -> Path:
        names: list[str] = [self._expect(TokenType.IDENT).val]
        while self._peek().type == TokenType.ARROW:
            self._advance()
            names.append(self._expect(TokenType.IDENT).val)
        return Path(tuple(names))

    def _val(self) -> Any:
        tok = self._advance()
        tt = tok.type

        if tt == TokenType.BOOL:
            return tok.val
        if tt == TokenType.STR:
            return tok.val
        if tt == TokenType.NUMBER:
            val, unit = tok.val
            return Number(val, unit)
        if tt == TokenType.REF:
            return Ref(tok.val)
        if tt == TokenType.URI:
            return Uri(tok.val)
        if tt == TokenType.SYMBOL:
            return Symbol(tok.val)
        if tt == TokenType.DATE:
            return datetime.date.fromisoformat(tok.val)
        if tt == TokenType.TIME:
            return datetime.time.fromisoformat(tok.val)
        if tt == TokenType.DATETIME:
            return parse_datetime(tok.val)

        raise ParseError(f"Expected value, got {tt.name} at position {tok.pos}")

    # ---- Token helpers ------------------------------------------------------

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _expect(self, tt: TokenType) -> Token:
        tok = self._advance()
        if tok.type != tt:
            raise ParseError(f"Expected {tt.name}, got {tok.type.name} at position {tok.pos}")
        return tok
