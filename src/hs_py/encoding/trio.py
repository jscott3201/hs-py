"""Trio text format parser and encoder.

Trio is a line-oriented format for hand-authoring Haystack data records.
Each record contains tag name-value pairs separated by lines of dashes.
Values are encoded in Zinc scalar format with Trio-specific extensions
(unquoted strings, ``true``/``false`` booleans).

See: https://project-haystack.org/doc/docHaystack/Trio
"""

from __future__ import annotations

from typing import Any

from hs_py.encoding.scanner import IDENT_CHARS, scan_val
from hs_py.grid import Grid
from hs_py.kinds import MARKER, Marker

__all__ = [
    "encode_trio",
    "parse_trio",
    "parse_zinc_val",
]


# ---------------------------------------------------------------------------
# Public API — Decode
# ---------------------------------------------------------------------------


def parse_trio(text: str) -> list[dict[str, Any]]:
    """Parse Trio text into a list of tag dicts.

    Each dict represents one record (separated by lines of ``---``).
    Supports multi-line string, Zinc, and Trio values via indented
    continuation lines.

    :param text: Trio-formatted text.
    :returns: List of tag dicts, one per record.
    """
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    ml_tag: str | None = None
    ml_lines: list[str] = []
    ml_mode: str = "string"  # "string", "zinc", or "trio"

    for raw_line in text.split("\n"):
        line = _strip_comment(raw_line)
        stripped = line.strip()

        # Multi-line continuation: blank lines or indented lines.
        # Must be checked BEFORE separator so indented "  ---" inside
        # Trio:/Zinc: multi-line content is collected, not treated as
        # a record boundary.
        if ml_tag is not None:
            if not stripped:
                ml_lines.append("")
                continue
            if line[0] in " \t":
                ml_lines.append(line)
                continue
            # Non-indented, non-blank line ends multi-line mode.
            # Fall through to separator / tag-line handling below.
            _flush_multiline(current, ml_tag, ml_lines, ml_mode)
            ml_tag = None
            ml_lines = []
            ml_mode = "string"

        # Record separator: any line of only dashes
        if _is_separator(line):
            if current:
                records.append(current)
                current = {}
            continue

        if not stripped:
            continue

        # Parse tag line
        name, val_str = _parse_tag_line(stripped)
        if val_str is None:
            current[name] = MARKER
        elif val_str == "":
            # Start multi-line string
            ml_tag = name
            ml_mode = "string"
        elif val_str == "Zinc:":
            # Start multi-line Zinc data
            ml_tag = name
            ml_mode = "zinc"
        elif val_str == "Trio:":
            # Start multi-line Trio data
            ml_tag = name
            ml_mode = "trio"
        else:
            current[name] = _parse_trio_val(val_str)

    # Finalize last record
    _flush_multiline(current, ml_tag, ml_lines, ml_mode)
    if current:
        records.append(current)

    return records


def parse_zinc_val(text: str) -> Any:
    """Parse a Zinc-encoded scalar value string.

    This parses strict Zinc syntax only. For Trio-specific extensions
    (unquoted strings, ``true``/``false``), use :func:`parse_trio`.

    :param text: Zinc value text.
    :returns: Parsed Haystack value.
    """
    text = text.strip()
    if not text:
        return None
    val, _ = scan_val(text, 0)
    return val


# ---------------------------------------------------------------------------
# Public API — Encode
# ---------------------------------------------------------------------------


def encode_trio(records: list[dict[str, Any]]) -> str:
    """Encode a list of tag dicts as Trio text.

    Multi-line strings, nested :class:`~hs_py.grid.Grid` values (via Zinc),
    and nested record lists (via Trio) are encoded using indented
    continuation lines.

    :param records: List of tag dicts, one per record.
    :returns: Trio-formatted text with trailing newline.
    """
    from hs_py.encoding.zinc import encode_grid as _zinc_encode_grid
    from hs_py.encoding.zinc import encode_val as _zinc_encode_val

    parts: list[str] = []
    for rec in records:
        lines: list[str] = ["---"]
        for name, val in rec.items():
            if isinstance(val, Marker):
                lines.append(name)
            elif isinstance(val, str) and "\n" in val:
                # Multi-line string
                lines.append(f"{name}:")
                for ml in val.split("\n"):
                    lines.append(f"  {ml}" if ml else "")
            elif isinstance(val, Grid):
                # Nested grid via Zinc: multi-line
                zinc_text = _zinc_encode_grid(val)
                lines.append(f"{name}: Zinc:")
                for ml in zinc_text.split("\n"):
                    lines.append(f"  {ml}")
            elif isinstance(val, list) and val and isinstance(val[0], dict):
                # Nested records via Trio: multi-line
                trio_text = encode_trio(val)
                lines.append(f"{name}: Trio:")
                for ml in trio_text.split("\n"):
                    if ml:
                        lines.append(f"  {ml}")
            else:
                lines.append(f"{name}: {_zinc_encode_val(val)}")
        parts.append("\n".join(lines))
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Trio-specific value parsing
# ---------------------------------------------------------------------------


def _parse_trio_val(text: str) -> Any:
    """Parse a Trio value with Zinc syntax and unquoted string fallback.

    Extends Zinc parsing with:
    - ``true``/``false`` boolean keywords
    - Unquoted string fallback when Zinc parsing doesn't consume the full value
    """
    text = text.strip()
    if not text:
        return None

    # Trio-specific boolean keywords
    if text == "true":
        return True
    if text == "false":
        return False

    # Try Zinc parsing
    try:
        val, end = scan_val(text, 0)
        # If fully consumed, use the parsed value
        if not text[end:].strip():
            return val
    except ValueError:
        pass

    # Fall back to unquoted string
    return text


# ---------------------------------------------------------------------------
# Line-level helpers
# ---------------------------------------------------------------------------


def _is_separator(line: str) -> bool:
    """Check if a line is a record separator (one or more dashes)."""
    stripped = line.strip()
    return bool(stripped) and all(c == "-" for c in stripped)


def _strip_comment(line: str) -> str:
    """Strip ``//`` comment from a line, respecting quoted strings and URIs."""
    in_str = False
    in_uri = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif in_uri:
            if ch == "`":
                in_uri = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "`":
                in_uri = True
            elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                return line[:i]
        i += 1
    return line


def _parse_tag_line(line: str) -> tuple[str, str | None]:
    """Parse a tag line into ``(name, value_str)`` or ``(name, None)``."""
    i = 0
    while i < len(line) and line[i] in IDENT_CHARS:
        i += 1
    name = line[:i]
    if not name:
        msg = f"Expected tag name: {line!r}"
        raise ValueError(msg)

    # Look for colon (skip optional whitespace between name and colon)
    j = i
    while j < len(line) and line[j] == " ":
        j += 1
    if j < len(line) and line[j] == ":":
        val_str = line[j + 1 :].strip()
        return name, val_str
    return name, None


def _flush_multiline(
    current: dict[str, Any],
    tag: str | None,
    lines: list[str],
    mode: str,
) -> None:
    """Finalize a multi-line value and add it to the current record."""
    if tag is None:
        return
    text = _join_multiline(lines)
    if mode == "zinc":
        from hs_py.encoding.zinc import decode_grid

        current[tag] = decode_grid(text)
    elif mode == "trio":
        current[tag] = parse_trio(text)
    else:
        current[tag] = text


def _join_multiline(lines: list[str]) -> str:
    """Join multi-line string continuation lines, stripping common indent."""
    if not lines:
        return ""
    indents = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    min_indent = min(indents) if indents else 0
    stripped = [ln[min_indent:] if ln.strip() else "" for ln in lines]
    while stripped and not stripped[-1]:
        stripped.pop()
    return "\n".join(stripped)
