"""Extended gate validation — config-driven checks for custom gates.

Custom validators can be registered for any gate key (int or float).
Built-in gate validators (1-5) are handled directly in validate.py.
Custom gates without a registered validator get a default file-existence check.

Validator function signature:
    validator_fn(gate_num, content, sflo_dir, checks) -> (passed: bool, checks: list)

    gate_num  - numeric gate key (int or float)
    content   - file content string (already verified to exist)
    sflo_dir  - path to .sflo directory
    checks    - list of check dicts so far (includes file_exists check)

    Return (passed, checks) where passed = all(c["pass"] for c in checks).
"""

import re

# Registry of validation functions: gate_key -> validator function
# Keys are numeric (int or float), matching GATES keys.
# Custom gates get a default validator that just checks file existence.
_VALIDATORS = {}


def register_validator(gate_key, validator_fn):
    """Register a custom validator for a gate.

    Args:
        gate_key: Numeric gate key (int or float).
        validator_fn: Callable matching the validator signature above.
    """
    _VALIDATORS[gate_key] = validator_fn


def get_validator(gate_key):
    """Get the registered validator for a gate, or None if not registered.

    Returns None for built-in gates (1-5) — they are handled in validate.py.
    For unknown/custom gates, returns the registered function or a default.
    """
    from .constants import GATES

    # If explicitly registered, return it
    if gate_key in _VALIDATORS:
        return _VALIDATORS[gate_key]

    # If it's a built-in gate (1-5 in default pipeline), let validate.py handle it
    # We check against the integer keys 1-5
    builtin_keys = {1, 2, 3, 4, 5}
    if gate_key in builtin_keys:
        return None

    # Custom gate with no registered validator — return default file-existence validator
    if gate_key in GATES:
        return _default_validator

    return None


def _default_validator(gate_num, content, sflo_dir, checks):
    """Default validator: file existence check only (already done in checks).

    Used for custom gates without a registered validator.
    """
    # file_exists check is already in checks list
    # Just add a note that no custom checks are registered
    checks.append({
        "name": "custom_gate_no_checks",
        "pass": True,
        "detail": f"Gate {gate_num}: no custom validation checks registered (file exists is sufficient)",
    })
    passed = all(c["pass"] for c in checks)
    return passed, checks


def unregister_validator(gate_key):
    """Remove a registered validator (useful for testing)."""
    _VALIDATORS.pop(gate_key, None)


def list_validators():
    """Return a list of registered gate keys."""
    return list(_VALIDATORS.keys())
