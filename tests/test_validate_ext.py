#!/usr/bin/env python3
"""Comprehensive tests for SFLO extended gate validation registry (src/validate_ext.py).

Covers the full register/get/unregister lifecycle, edge cases,
overwrite behaviour, and the default validator logic.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.validate_ext import (
    register_validator,
    get_validator,
    unregister_validator,
    list_validators,
    _default_validator,
    _VALIDATORS,
)


# ---------------------------------------------------------------------------
# Fixtures — ensure registry is clean between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_registry():
    """Snapshot and restore the global registry around every test."""
    snapshot = dict(_VALIDATORS)
    yield
    _VALIDATORS.clear()
    _VALIDATORS.update(snapshot)


# ---------------------------------------------------------------------------
# register / get / unregister lifecycle
# ---------------------------------------------------------------------------


class TestRegistryLifecycle:
    """Full lifecycle: register -> get -> unregister -> get returns None."""

    def test_register_then_get(self):
        def my_validator(gn, c, sd, ch):
            return True, ch

        register_validator(100, my_validator)
        assert get_validator(100) is my_validator, (
            f"expected registered validator back for gate 100, got {get_validator(100)}"
        )

    def test_unregister_removes_validator(self):
        def my_validator(gn, c, sd, ch):
            return True, ch

        register_validator(101, my_validator)
        assert get_validator(101) is my_validator, (
            f"expected registered validator back for gate 101, got {get_validator(101)}"
        )
        unregister_validator(101)
        assert 101 not in list_validators(), (
            f"gate 101 should not appear in validators after unregister, but list contains {list_validators()}"
        )

    def test_get_after_unregister_returns_none(self):
        def my_validator(gn, c, sd, ch):
            return True, ch

        register_validator(102, my_validator)
        unregister_validator(102)
        # Gate 102 is not in GATES, and not registered -> None
        assert get_validator(102) is None, (
            f"expected None for unregistered gate 102, got {get_validator(102)}"
        )

    def test_list_validators_reflects_state(self):
        before = set(list_validators())
        register_validator(200, lambda *a: (True, []))
        register_validator(201, lambda *a: (True, []))
        after = set(list_validators())
        assert {200, 201}.issubset(after - before), (
            f"gates 200, 201 should appear in newly registered set, diff was {after - before}"
        )

        unregister_validator(200)
        assert 200 not in list_validators(), (
            f"gate 200 should be gone after unregister, but found in {list_validators()}"
        )
        assert 201 in list_validators(), (
            f"gate 201 should still be registered, but not found in {list_validators()}"
        )


# ---------------------------------------------------------------------------
# Overwrite behaviour
# ---------------------------------------------------------------------------


class TestOverwriteBehaviour:
    """Registering same gate key twice overwrites the first validator."""

    def test_second_register_overwrites_first(self):
        def v1(gn, c, sd, ch):
            return True, ch

        def v2(gn, c, sd, ch):
            return False, ch

        register_validator(300, v1)
        assert get_validator(300) is v1, (
            f"expected v1 for gate 300 after first register, got {get_validator(300)}"
        )

        register_validator(300, v2)
        assert get_validator(300) is v2, (
            f"expected v2 for gate 300 after overwrite, got {get_validator(300)}"
        )

    def test_overwrite_does_not_duplicate_keys(self):
        register_validator(301, lambda *a: (True, []))
        register_validator(301, lambda *a: (True, []))
        count = list_validators().count(301)
        assert count == 1, (
            f"expected gate 301 to appear exactly once after overwrite, but found {count} times"
        )


# ---------------------------------------------------------------------------
# Getting unregistered / non-existent gates
# ---------------------------------------------------------------------------


class TestUnregisteredGates:
    """get_validator returns None for unknown gates and built-ins."""

    def test_builtin_gates_return_none(self):
        for gate_key in [1, 2, 3, 4, 5]:
            assert get_validator(gate_key) is None, (
                f"expected None for built-in gate {gate_key}, got {get_validator(gate_key)}"
            )

    def test_unknown_gate_not_in_gates_returns_none(self):
        # Gate 9999 is not in GATES config and not registered
        assert get_validator(9999) is None, (
            f"expected None for unknown gate 9999, got {get_validator(9999)}"
        )

    def test_totally_unknown_float_gate_returns_none(self):
        # A float key that is neither registered nor in GATES config
        assert get_validator(99.5) is None, (
            f"expected None for unknown float gate 99.5, got {get_validator(99.5)}"
        )


# ---------------------------------------------------------------------------
# Unregister non-existent gate (should not raise)
# ---------------------------------------------------------------------------


class TestUnregisterNonExistent:
    """Unregistering a gate that was never registered raises no error."""

    def test_unregister_unknown_gate_no_error(self):
        unregister_validator(7777)  # should not raise
        assert get_validator(7777) is None, (
            f"expected None for never-registered gate 7777, got {get_validator(7777)}"
        )

    def test_unregister_twice_no_error(self):
        register_validator(400, lambda *a: (True, []))
        unregister_validator(400)
        unregister_validator(400)  # second call should not raise
        assert get_validator(400) is None, (
            f"expected None for double-unregistered gate 400, got {get_validator(400)}"
        )


# ---------------------------------------------------------------------------
# Validator callable enforcement
# ---------------------------------------------------------------------------


class TestValidatorCallable:
    """Registry stores whatever is passed — callability is caller's job.
    Verify that non-callables can be stored and retrieved."""

    def test_non_callable_can_be_registered(self):
        # The registry itself does not enforce callability
        register_validator(500, "not_a_function")
        assert get_validator(500) == "not_a_function", (
            f"expected non-callable 'not_a_function' stored and retrieved for gate 500, got {get_validator(500)!r}"
        )

    def test_none_value_can_be_registered(self):
        register_validator(501, None)
        # get_validator checks _VALIDATORS first, so it returns None
        # but it's the stored None, not the "not found" None
        assert 501 in _VALIDATORS, (
            f"expected gate 501 present in _VALIDATORS even with None value, keys: {list(_VALIDATORS.keys())}"
        )

    def test_lambda_validator(self):
        fn = lambda gn, c, sd, ch: (True, ch)  # noqa: E731
        register_validator(502, fn)
        assert get_validator(502) is fn, (
            f"expected lambda validator back for gate 502, got {get_validator(502)}"
        )


# ---------------------------------------------------------------------------
# Key types: int, float, string
# ---------------------------------------------------------------------------


class TestKeyTypes:
    """Registry supports int and float keys (matching GATES conventions)."""

    def test_int_key(self):
        register_validator(10, lambda *a: (True, []))
        assert 10 in list_validators(), (
            f"expected int key 10 in validators, got {list_validators()}"
        )

    def test_float_key(self):
        register_validator(2.5, lambda *a: (True, []))
        assert 2.5 in list_validators(), (
            f"expected float key 2.5 in validators, got {list_validators()}"
        )

    def test_int_and_float_are_distinct_when_different(self):
        fn_int = lambda *a: (True, [])  # noqa: E731
        fn_float = lambda *a: (False, [])  # noqa: E731
        register_validator(6, fn_int)
        register_validator(6.5, fn_float)
        assert get_validator(6) is fn_int, (
            f"expected int-keyed validator for gate 6, got {get_validator(6)}"
        )
        assert get_validator(6.5) is fn_float, (
            f"expected float-keyed validator for gate 6.5, got {get_validator(6.5)}"
        )


# ---------------------------------------------------------------------------
# Default validator
# ---------------------------------------------------------------------------


class TestDefaultValidator:
    """The _default_validator adds a pass-through check."""

    def test_default_validator_appends_check(self):
        checks = [{"name": "file_exists", "pass": True, "detail": "file found"}]
        passed, result_checks = _default_validator(7, "content", "/tmp/sflo", checks)
        assert passed is True, (
            f"expected default validator to pass when prior checks pass, got passed={passed}"
        )
        assert len(result_checks) == 2, (
            f"expected 2 checks (original + appended), got {len(result_checks)}"
        )
        custom_check = result_checks[1]
        assert custom_check["name"] == "custom_gate_no_checks", (
            f"expected appended check named 'custom_gate_no_checks', got {custom_check['name']!r}"
        )
        assert custom_check["pass"] is True, (
            f"expected appended check to pass, got {custom_check['pass']}"
        )

    def test_default_validator_fails_if_prior_check_failed(self):
        checks = [{"name": "file_exists", "pass": False, "detail": "missing"}]
        passed, result_checks = _default_validator(8, "content", "/tmp/sflo", checks)
        assert passed is False, (
            f"expected default validator to fail when prior check failed, got passed={passed}"
        )
        assert len(result_checks) == 2, (
            f"expected 2 checks after default validator, got {len(result_checks)}"
        )

    def test_default_validator_includes_gate_num_in_detail(self):
        checks = [{"name": "file_exists", "pass": True, "detail": "ok"}]
        _, result_checks = _default_validator(42, "content", "/tmp/sflo", checks)
        custom_check = result_checks[1]
        assert "42" in custom_check["detail"], (
            f"expected gate number '42' in detail string, got {custom_check['detail']!r}"
        )

    def test_default_validator_empty_checks_list(self):
        passed, result_checks = _default_validator(9, "content", "/tmp/sflo", [])
        assert passed is True, (
            f"expected default validator to pass with empty checks, got passed={passed}"
        )
        assert len(result_checks) == 1, (
            f"expected 1 check appended to empty list, got {len(result_checks)}"
        )


# ---------------------------------------------------------------------------
# get_validator with custom gates in GATES config
# ---------------------------------------------------------------------------


class TestGetValidatorWithGatesConfig:
    """When a gate exists in GATES but has no registered validator,
    get_validator returns the default validator."""

    def test_custom_gate_in_gates_returns_default(self, monkeypatch):
        from src import constants

        test_gates = {
            1: {"artifact": "SCOPE.md", "role": "pm"},
            6: {"artifact": "CUSTOM.md", "role": "extra"},
        }
        monkeypatch.setattr(constants, "GATES", test_gates)

        validator = get_validator(6)
        assert validator is _default_validator, (
            f"expected _default_validator for gate 6 in GATES config with no registered validator, got {validator}"
        )

    def test_registered_validator_takes_precedence(self, monkeypatch):
        from src import constants

        test_gates = {
            1: {"artifact": "SCOPE.md", "role": "pm"},
            6: {"artifact": "CUSTOM.md", "role": "extra"},
        }
        monkeypatch.setattr(constants, "GATES", test_gates)

        def custom_fn(gn, c, sd, ch):
            return True, ch

        register_validator(6, custom_fn)
        assert get_validator(6) is custom_fn, (
            f"expected registered custom_fn to take precedence over default for gate 6, got {get_validator(6)}"
        )
