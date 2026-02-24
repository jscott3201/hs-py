"""Tests for JSON/Zinc limit enforcement and FastAPI error paths."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hs_py.encoding import json as json_mod
from hs_py.encoding.json import (
    JsonVersion,
    _decode_val_v3,
    decode_grid_dict,
)


class TestJsonGridLimits:
    """Cover maximum cols/rows limit enforcement."""

    def test_v4_max_cols_exceeded(self) -> None:
        cols = [{"name": f"c{i}"} for i in range(101)]
        d = {"cols": cols, "rows": []}
        with (
            patch.object(json_mod, "_MAX_GRID_COLS", 100),
            pytest.raises(ValueError, match="maximum column count"),
        ):
            decode_grid_dict(d, version=JsonVersion.V4)

    def test_v4_max_rows_exceeded(self) -> None:
        rows = [{"x": 1} for _ in range(101)]
        d = {"cols": [{"name": "x"}], "rows": rows}
        with (
            patch.object(json_mod, "_MAX_GRID_ROWS", 100),
            pytest.raises(ValueError, match="maximum row count"),
        ):
            decode_grid_dict(d, version=JsonVersion.V4)

    def test_v3_max_cols_exceeded(self) -> None:
        cols = [{"name": f"c{i}"} for i in range(101)]
        d = {"meta": {"ver": "3.0"}, "cols": cols, "rows": []}
        with (
            patch.object(json_mod, "_MAX_GRID_COLS", 100),
            pytest.raises(ValueError, match="maximum column count"),
        ):
            decode_grid_dict(d, version=JsonVersion.V3)

    def test_v3_max_rows_exceeded(self) -> None:
        rows = [{"x": "n:1"} for _ in range(101)]
        d = {"meta": {"ver": "3.0"}, "cols": [{"name": "x"}], "rows": rows}
        with (
            patch.object(json_mod, "_MAX_GRID_ROWS", 100),
            pytest.raises(ValueError, match="maximum row count"),
        ):
            decode_grid_dict(d, version=JsonVersion.V3)

    def test_v3_depth_exceeded(self) -> None:
        with pytest.raises(ValueError, match="depth exceeded"):
            _decode_val_v3("n:42", _depth=100)


class TestJsonPythonicGrid:
    """Cover _to_pythonic grid path (line 680)."""

    def test_pythonic_nested_grid(self) -> None:
        """Decode v4 dict with nested grid in pythonic mode."""
        inner = {
            "cols": [{"name": "val"}],
            "rows": [{"val": {"_kind": "number", "val": 42.0, "unit": "°F"}}],
        }
        outer = {
            "cols": [{"name": "nested"}],
            "rows": [{"nested": {"_kind": "grid", **inner}}],
        }
        grid = decode_grid_dict(outer, pythonic=True)
        # The nested value should be a Grid after pythonic transform
        from hs_py.grid import Grid

        nested_val = grid.rows[0]["nested"]
        assert isinstance(nested_val, Grid)
