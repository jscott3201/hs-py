"""Tokenizer for Haystack filter expressions.

Converts a filter string into a sequence of typed tokens for the parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from hs_py.encoding.scanner import (
    DATE_RE,
    DATETIME_RE,
    IDENT_CHARS,
    REF_CHARS,
    STR_ESCAPES,
    SYMBOL_CHARS,
    TIME_RE,
    UNIT_STOP_BASE,
)

__all__ = [
    "Lexer",
    "Token",
    "TokenType",
]


class TokenType(Enum):
    """Token types produced by the filter lexer."""

    IDENT = auto()
    BOOL = auto()
    STR = auto()
    NUMBER = auto()
    DATE = auto()
    TIME = auto()
    DATETIME = auto()
    URI = auto()
    REF = auto()
    SYMBOL = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    EQ = auto()
    NE = auto()
    LT = auto()
    LE = auto()
    GT = auto()
    GE = auto()
    LPAREN = auto()
    RPAREN = auto()
    ARROW = auto()
    EOF = auto()


@dataclass(frozen=True, slots=True)
class Token:
    """A single token from the filter lexer."""

    type: TokenType
    """Token type discriminator."""

    val: Any
    """Token value (string, number tuple, boolean, or ``None``)."""

    pos: int
    """Character offset in the source text."""


# Characters that terminate a number unit in filter context (operators + parens).
_UNIT_STOP = UNIT_STOP_BASE | frozenset("()!=<>")


class Lexer:
    """Tokenizer for Haystack filter strings."""

    def __init__(self, text: str) -> None:
        """Initialise the lexer.

        :param text: Filter expression source text.
        """
        self._text = text
        self._pos = 0
        self._len = len(text)

    def tokenize(self) -> list[Token]:
        """Tokenize the full input and return a list of :class:`Token` instances.

        :returns: Token list ending with an ``EOF`` token.
        """
        tokens: list[Token] = []
        while self._pos < self._len:
            self._skip_ws()
            if self._pos >= self._len:
                break
            tokens.append(self._next())
        tokens.append(Token(TokenType.EOF, None, self._pos))
        return tokens

    # ---- Internal scanning --------------------------------------------------

    def _skip_ws(self) -> None:
        while self._pos < self._len and self._text[self._pos] in " \t\n\r":
            self._pos += 1

    def _next(self) -> Token:
        ch = self._text[self._pos]

        # Two-char operators / arrow
        if ch == "-":
            return self._on_dash()
        if ch == "=" and self._ahead("=="):
            return self._advance(2, TokenType.EQ, "==")
        if ch == "!" and self._ahead("!="):
            return self._advance(2, TokenType.NE, "!=")
        if ch == "<":
            if self._ahead("<="):
                return self._advance(2, TokenType.LE, "<=")
            return self._advance(1, TokenType.LT, "<")
        if ch == ">":
            if self._ahead(">="):
                return self._advance(2, TokenType.GE, ">=")
            return self._advance(1, TokenType.GT, ">")

        # Delimiters
        if ch == "(":
            return self._advance(1, TokenType.LPAREN, "(")
        if ch == ")":
            return self._advance(1, TokenType.RPAREN, ")")

        # Literals
        if ch == '"':
            return self._scan_str()
        if ch == "`":
            return self._scan_uri()
        if ch == "@":
            return self._scan_ref()
        if ch == "^":
            return self._scan_symbol()
        if ch.isdigit():
            return self._scan_number_or_temporal()
        if ch.isalpha() or ch == "_":
            return self._scan_ident()

        msg = f"Unexpected character {ch!r} at position {self._pos}"
        raise ValueError(msg)

    def _on_dash(self) -> Token:
        """Handle ``-`` which could be arrow, negative number, or -INF."""
        if self._ahead("->"):
            return self._advance(2, TokenType.ARROW, "->")
        rest = self._text[self._pos :]
        if rest.startswith("-INF") and (len(rest) == 4 or not rest[4].isalnum()):
            start = self._pos
            self._pos += 4
            return Token(TokenType.NUMBER, (float("-inf"), None), start)
        if self._pos + 1 < self._len and self._text[self._pos + 1].isdigit():
            return self._scan_negative_number()
        msg = f"Unexpected '-' at position {self._pos}"
        raise ValueError(msg)

    def _ahead(self, s: str) -> bool:
        return self._text[self._pos : self._pos + len(s)] == s

    def _advance(self, n: int, tt: TokenType, val: Any) -> Token:
        start = self._pos
        self._pos += n
        return Token(tt, val, start)

    # ---- String / URI / Ref / Symbol ----------------------------------------

    def _scan_str(self) -> Token:
        start = self._pos
        self._pos += 1  # skip opening "
        chars: list[str] = []
        while self._pos < self._len:
            ch = self._text[self._pos]
            if ch == "\\":
                self._pos += 1
                if self._pos >= self._len:
                    raise ValueError(f"Unterminated string escape at {start}")
                esc = self._text[self._pos]
                if esc == "u" and self._pos + 4 < self._len:
                    code = self._text[self._pos + 1 : self._pos + 5]
                    chars.append(chr(int(code, 16)))
                    self._pos += 5
                else:
                    chars.append(STR_ESCAPES.get(esc, esc))
                    self._pos += 1
            elif ch == '"':
                self._pos += 1
                return Token(TokenType.STR, "".join(chars), start)
            else:
                chars.append(ch)
                self._pos += 1
        raise ValueError(f"Unterminated string at {start}")

    def _scan_uri(self) -> Token:
        start = self._pos
        self._pos += 1  # skip `
        end = self._text.find("`", self._pos)
        if end < 0:
            raise ValueError(f"Unterminated URI at {start}")
        val = self._text[self._pos : end]
        self._pos = end + 1
        return Token(TokenType.URI, val, start)

    def _scan_ref(self) -> Token:
        start = self._pos
        self._pos += 1  # skip @
        ref_start = self._pos
        while self._pos < self._len and self._text[self._pos] in REF_CHARS:
            self._pos += 1
        return Token(TokenType.REF, self._text[ref_start : self._pos], start)

    def _scan_symbol(self) -> Token:
        start = self._pos
        self._pos += 1  # skip ^
        sym_start = self._pos
        while self._pos < self._len and self._text[self._pos] in SYMBOL_CHARS:
            self._pos += 1
        return Token(TokenType.SYMBOL, self._text[sym_start : self._pos], start)

    # ---- Number / Date / Time / DateTime ------------------------------------

    def _scan_number_or_temporal(self) -> Token:
        """Disambiguate number vs date/time/datetime from a leading digit."""
        start = self._pos
        rest = self._text[self._pos :]

        m = DATETIME_RE.match(rest)
        if m:
            self._pos += len(m.group(0))
            return Token(TokenType.DATETIME, m.group(0).strip(), start)

        m = DATE_RE.match(rest)
        if m and (self._pos + 10 >= self._len or not self._text[self._pos + 10].isdigit()):
            self._pos += 10
            return Token(TokenType.DATE, m.group(0), start)

        m = TIME_RE.match(rest)
        if m:
            self._pos += len(m.group(0))
            return Token(TokenType.TIME, m.group(0), start)

        return self._scan_number(start, negative=False)

    def _scan_negative_number(self) -> Token:
        start = self._pos
        self._pos += 1  # skip -
        tok = self._scan_number(start, negative=True)
        return tok

    def _scan_number(self, start: int, *, negative: bool) -> Token:
        """Scan a numeric literal with optional unit."""
        digits = self._collect_digits()
        if self._pos < self._len and self._text[self._pos] == ".":
            digits += "."
            self._pos += 1
            digits += self._collect_digits()
        if self._pos < self._len and self._text[self._pos] in "eE":
            digits += self._text[self._pos]
            self._pos += 1
            if self._pos < self._len and self._text[self._pos] in "+-":
                digits += self._text[self._pos]
                self._pos += 1
            digits += self._collect_digits()

        val = float(digits)
        if negative:
            val = -val

        # Optional unit (non-space, non-operator characters)
        unit: str | None = None
        if self._pos < self._len and self._text[self._pos] not in _UNIT_STOP:
            unit_chars: list[str] = []
            while self._pos < self._len and self._text[self._pos] not in _UNIT_STOP:
                unit_chars.append(self._text[self._pos])
                self._pos += 1
            unit = "".join(unit_chars)

        return Token(TokenType.NUMBER, (val, unit), start)

    def _collect_digits(self) -> str:
        chars: list[str] = []
        while self._pos < self._len and self._text[self._pos].isdigit():
            chars.append(self._text[self._pos])
            self._pos += 1
        return "".join(chars)

    # ---- Identifiers / keywords ---------------------------------------------

    def _scan_ident(self) -> Token:
        start = self._pos
        while self._pos < self._len and self._text[self._pos] in IDENT_CHARS:
            self._pos += 1
        name = self._text[start : self._pos]

        kw = _KEYWORDS.get(name)
        if kw is not None:
            return Token(kw[0], kw[1], start)

        return Token(TokenType.IDENT, name, start)


_KEYWORDS: dict[str, tuple[TokenType, Any]] = {
    "and": (TokenType.AND, "and"),
    "or": (TokenType.OR, "or"),
    "not": (TokenType.NOT, "not"),
    "true": (TokenType.BOOL, True),
    "false": (TokenType.BOOL, False),
    "INF": (TokenType.NUMBER, (float("inf"), None)),
    "NaN": (TokenType.NUMBER, (float("nan"), None)),
}
