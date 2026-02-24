"""Tests for Role comparison edge cases and user.py coverage gaps."""

from __future__ import annotations

from hs_py.user import Role


class TestRoleComparisonNonRole:
    """Role comparison operators return NotImplemented for non-Role operands."""

    def test_ge_non_role(self) -> None:
        assert Role.ADMIN.__ge__("other") is NotImplemented

    def test_gt_non_role(self) -> None:
        assert Role.ADMIN.__gt__(42) is NotImplemented

    def test_le_non_role(self) -> None:
        assert Role.VIEWER.__le__(3.14) is NotImplemented

    def test_lt_non_role(self) -> None:
        assert Role.VIEWER.__lt__(None) is NotImplemented
