#!/usr/bin/env python3
"""Unit tests for SFLO pipeline constants (src/constants.py).

Validates grade maps, state names, loop limits, role sets, and
config-loaded values are consistent and well-formed.
"""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.constants import (
    GRADE_MAP,
    GRADE_THRESHOLD,
    GATES,
    KNOWN_ROLES,
    SFLO_ROOT,
    PYTHON_CMD,
    INNER_LOOP_MAX,
    OUTER_LOOP_MAX,
    S_INIT,
    S_SCOUT,
    S_ASSIGN,
    S_ESCALATE,
    S_DONE,
)


# ---------------------------------------------------------------------------
# GRADE_MAP completeness and ordering
# ---------------------------------------------------------------------------


class TestGradeMap:
    """GRADE_MAP contains all expected grades with correct ordering."""

    EXPECTED_GRADES = ["A", "A-", "B+", "B", "B-", "C", "D", "F"]

    @pytest.mark.parametrize("grade", ["A", "A-", "B+", "B", "B-", "C", "D", "F"])
    def test_expected_grade_present(self, grade):
        assert grade in GRADE_MAP, f"missing grade {grade!r} from GRADE_MAP"

    def test_no_unexpected_grades(self):
        for grade in GRADE_MAP:
            assert grade in self.EXPECTED_GRADES, (
                f"unexpected grade {grade!r} in GRADE_MAP, expected one of {self.EXPECTED_GRADES}"
            )

    @pytest.mark.parametrize(
        "higher,lower",
        [
            ("A", "A-"),
            ("A-", "B+"),
            ("B+", "B"),
            ("B", "B-"),
            ("B-", "C"),
            ("C", "D"),
            ("D", "F"),
        ],
    )
    def test_grade_ordering_descending(self, higher, lower):
        assert GRADE_MAP[higher] > GRADE_MAP[lower], (
            f"{higher} ({GRADE_MAP[higher]}) should be > {lower} ({GRADE_MAP[lower]})"
        )

    def test_a_is_highest(self):
        assert GRADE_MAP["A"] == max(GRADE_MAP.values()), (
            f"expected 'A' ({GRADE_MAP['A']}) to be highest but max is {max(GRADE_MAP.values())}"
        )

    def test_f_is_lowest(self):
        assert GRADE_MAP["F"] == min(GRADE_MAP.values()), (
            f"expected 'F' ({GRADE_MAP['F']}) to be lowest but min is {min(GRADE_MAP.values())}"
        )

    def test_all_values_are_numeric(self):
        for grade, value in GRADE_MAP.items():
            assert isinstance(value, (int, float)), (
                f"Grade {grade} has non-numeric value: {value}"
            )

    def test_all_values_positive(self):
        for grade, value in GRADE_MAP.items():
            assert value > 0, f"Grade {grade} has non-positive value: {value}"


# ---------------------------------------------------------------------------
# GRADE_THRESHOLD
# ---------------------------------------------------------------------------


class TestGradeThreshold:
    """GRADE_THRESHOLD is loaded from config as a resolved numeric value."""

    def test_threshold_is_numeric(self):
        assert isinstance(GRADE_THRESHOLD, (int, float)), (
            f"expected GRADE_THRESHOLD to be numeric, got {type(GRADE_THRESHOLD).__name__}"
        )

    def test_threshold_is_valid_grade_value(self):
        valid_values = set(GRADE_MAP.values())
        assert GRADE_THRESHOLD in valid_values, (
            f"GRADE_THRESHOLD {GRADE_THRESHOLD} not in GRADE_MAP values {valid_values}"
        )

    def test_threshold_is_positive(self):
        assert GRADE_THRESHOLD > 0, (
            f"expected GRADE_THRESHOLD > 0, got {GRADE_THRESHOLD}"
        )


# ---------------------------------------------------------------------------
# KNOWN_ROLES
# ---------------------------------------------------------------------------


class TestKnownRoles:
    """KNOWN_ROLES contains the core pipeline roles."""

    CORE_ROLES = {"pm", "dev", "qa"}

    @pytest.mark.parametrize("role", ["pm", "dev", "qa"])
    def test_contains_core_role(self, role):
        assert role in KNOWN_ROLES, f"missing core role {role!r} from KNOWN_ROLES"

    def test_is_a_set(self):
        assert isinstance(KNOWN_ROLES, set), (
            f"expected KNOWN_ROLES to be a set, got {type(KNOWN_ROLES).__name__}"
        )

    def test_derived_from_gates(self):
        # KNOWN_ROLES is now dynamically derived from GATES, not hardcoded.
        # Every role in GATES must appear in KNOWN_ROLES.
        from src.constants import GATES

        for g in GATES.values():
            entries = g if isinstance(g, list) else [g]
            for e in entries:
                role = e.get("role")
                if role:
                    assert role in KNOWN_ROLES, (
                        f"GATES role {role!r} missing from KNOWN_ROLES"
                    )

    def test_does_not_contain_admin(self):
        assert "admin" not in KNOWN_ROLES, (
            "KNOWN_ROLES should not contain 'admin' (not a core pipeline role)"
        )

    def test_all_roles_are_lowercase_strings(self):
        for role in KNOWN_ROLES:
            assert isinstance(role, str), (
                f"expected role to be str, got {type(role).__name__}: {role!r}"
            )
            assert role == role.lower(), f"Role '{role}' is not lowercase"


# ---------------------------------------------------------------------------
# SFLO_ROOT
# ---------------------------------------------------------------------------


class TestSfloRoot:
    """SFLO_ROOT points to a valid directory."""

    def test_is_string(self):
        assert isinstance(SFLO_ROOT, str), (
            f"expected SFLO_ROOT to be str, got {type(SFLO_ROOT).__name__}"
        )

    def test_is_absolute_path(self):
        assert os.path.isabs(SFLO_ROOT), (
            f"expected SFLO_ROOT to be absolute path, got {SFLO_ROOT!r}"
        )

    def test_directory_exists(self):
        assert os.path.isdir(SFLO_ROOT), f"SFLO_ROOT does not exist: {SFLO_ROOT}"

    def test_contains_src_directory(self):
        src_dir = os.path.join(SFLO_ROOT, "src")
        assert os.path.isdir(src_dir), f"No src/ directory under SFLO_ROOT: {SFLO_ROOT}"


# ---------------------------------------------------------------------------
# PYTHON_CMD
# ---------------------------------------------------------------------------


class TestPythonCmd:
    """PYTHON_CMD is a valid python executable."""

    def test_is_string(self):
        assert isinstance(PYTHON_CMD, str), (
            f"expected PYTHON_CMD to be str, got {type(PYTHON_CMD).__name__}"
        )

    def test_is_non_empty(self):
        assert len(PYTHON_CMD) > 0, "PYTHON_CMD must not be empty"

    def test_is_callable(self):
        assert shutil.which(PYTHON_CMD) is not None, (
            f"PYTHON_CMD '{PYTHON_CMD}' not found on PATH"
        )


# ---------------------------------------------------------------------------
# State constants uniqueness
# ---------------------------------------------------------------------------


class TestStateConstants:
    """State constants S_INIT through S_DONE are unique non-empty strings."""

    ALL_STATES = [S_INIT, S_SCOUT, S_ASSIGN, S_ESCALATE, S_DONE]

    @pytest.mark.parametrize("state", [S_INIT, S_SCOUT, S_ASSIGN, S_ESCALATE, S_DONE])
    def test_state_is_string(self, state):
        assert isinstance(state, str), (
            f"expected state to be str, got {type(state).__name__}: {state!r}"
        )

    @pytest.mark.parametrize("state", [S_INIT, S_SCOUT, S_ASSIGN, S_ESCALATE, S_DONE])
    def test_state_is_non_empty(self, state):
        assert len(state) > 0, f"state constant must not be empty, got {state!r}"

    def test_all_unique(self):
        assert len(set(self.ALL_STATES)) == len(self.ALL_STATES), (
            f"duplicate state constants found: {self.ALL_STATES}"
        )

    @pytest.mark.parametrize(
        "constant,expected",
        [
            (S_INIT, "init"),
            (S_SCOUT, "scout"),
            (S_ASSIGN, "assign"),
            (S_ESCALATE, "escalate"),
            (S_DONE, "done"),
        ],
    )
    def test_known_values(self, constant, expected):
        assert constant == expected, (
            f"expected state constant to be {expected!r}, got {constant!r}"
        )


# ---------------------------------------------------------------------------
# GATES
# ---------------------------------------------------------------------------


class TestGates:
    """GATES is a non-empty dict loaded from pipeline config."""

    def test_is_dict(self):
        assert isinstance(GATES, dict), (
            f"expected GATES to be dict, got {type(GATES).__name__}"
        )

    def test_is_non_empty(self):
        assert len(GATES) > 0, "GATES must contain at least one gate definition"

    def test_keys_are_numeric(self):
        for key in GATES:
            assert isinstance(key, (int, float)), f"Gate key {key!r} is not numeric"

    def test_values_are_dict_or_list(self):
        for key, value in GATES.items():
            assert isinstance(value, (dict, list)), (
                f"Gate {key} value is {type(value).__name__}, expected dict or list"
            )

    def test_dict_gates_have_artifact(self):
        for key, value in GATES.items():
            if isinstance(value, dict):
                assert "artifact" in value, f"Gate {key} dict missing 'artifact' key"

    def test_list_gates_entries_have_artifact(self):
        for key, value in GATES.items():
            if isinstance(value, list):
                for i, entry in enumerate(value):
                    assert "artifact" in entry, (
                        f"Gate {key} list entry {i} missing 'artifact' key"
                    )


# ---------------------------------------------------------------------------
# Loop limits
# ---------------------------------------------------------------------------


class TestLoopLimits:
    """INNER_LOOP_MAX and OUTER_LOOP_MAX are positive integers."""

    def test_inner_loop_max_is_positive_int(self):
        assert isinstance(INNER_LOOP_MAX, int), (
            f"expected INNER_LOOP_MAX to be int, got {type(INNER_LOOP_MAX).__name__}"
        )
        assert INNER_LOOP_MAX > 0, f"expected INNER_LOOP_MAX > 0, got {INNER_LOOP_MAX}"

    def test_outer_loop_max_is_positive_int(self):
        assert isinstance(OUTER_LOOP_MAX, int), (
            f"expected OUTER_LOOP_MAX to be int, got {type(OUTER_LOOP_MAX).__name__}"
        )
        assert OUTER_LOOP_MAX > 0, f"expected OUTER_LOOP_MAX > 0, got {OUTER_LOOP_MAX}"

    def test_inner_loop_max_value(self):
        assert INNER_LOOP_MAX == 10, (
            f"expected INNER_LOOP_MAX == 10, got {INNER_LOOP_MAX}"
        )

    def test_outer_loop_max_value(self):
        assert OUTER_LOOP_MAX == 10, (
            f"expected OUTER_LOOP_MAX == 10, got {OUTER_LOOP_MAX}"
        )
