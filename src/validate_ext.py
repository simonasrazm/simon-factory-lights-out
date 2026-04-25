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
    checks.append(
        {
            "name": "custom_gate_no_checks",
            "pass": True,
            "detail": f"Gate {gate_num}: no custom validation checks registered (file exists is sufficient)",
        }
    )
    passed = all(c["pass"] for c in checks)
    return passed, checks


def unregister_validator(gate_key):
    """Remove a registered validator (useful for testing)."""
    _VALIDATORS.pop(gate_key, None)


def validate_stst_report(gate_num, content, sflo_dir, checks):
    """Validator for Gate 2.5 (STST static filter) — STST-REPORT.md checks.

    Checks:
      (a) file exists (already in checks via caller)
      (b) ## Summary section contains PASS or REJECT token
      (c) ## Tests Evaluated table has >= 1 row
      (d) if verdict is REJECT, ## Rejection Reasons section is non-empty
    """
    # (b) Summary verdict
    summary_body = section_body(content, r"Summary")
    has_verdict = bool(re.search(r"\b(PASS|REJECT)\b", summary_body, re.IGNORECASE))
    checks.append(
        {
            "name": "has_verdict",
            "pass": has_verdict,
            "detail": "Summary contains PASS or REJECT"
            if has_verdict
            else "Missing verdict in ## Summary",
        }
    )

    # (c) Tests Evaluated table has >= 1 data row
    table_body = section_body(content, r"Tests Evaluated")
    # Count pipe-separated rows that are not header/separator lines
    data_rows = [
        ln
        for ln in table_body.splitlines()
        if "|" in ln
        and not re.match(r"^\s*\|[-| ]+\|\s*$", ln)
        and not re.match(r"^\s*\|\s*File\s*\|", ln, re.IGNORECASE)
    ]
    has_table_rows = len(data_rows) >= 1
    checks.append(
        {
            "name": "has_test_table_rows",
            "pass": has_table_rows,
            "detail": f"{len(data_rows)} test row(s) in ## Tests Evaluated"
            if has_table_rows
            else "No test rows in ## Tests Evaluated table",
        }
    )

    # (d) If verdict is REJECT, Rejection Reasons must be non-empty
    verdict_is_reject = bool(re.search(r"\bREJECT\b", summary_body, re.IGNORECASE))
    if verdict_is_reject:
        reasons_body = section_body(content, r"Rejection Reasons")
        has_reasons = bool(reasons_body.strip())
        checks.append(
            {
                "name": "reject_has_reasons",
                "pass": has_reasons,
                "detail": "Rejection Reasons section present"
                if has_reasons
                else "## Rejection Reasons is empty but verdict is REJECT",
            }
        )
        # Gate fails (pipeline must loop back to DEV) whenever verdict is REJECT
        checks.append(
            {
                "name": "verdict_is_pass",
                "pass": False,
                "detail": "STST verdict is REJECT — pipeline loops back to DEV",
            }
        )

    passed = all(c["pass"] for c in checks)
    return passed, checks


def section_body(content, heading_pattern):
    """Extract body text under a markdown heading (mirrors validate.section_body)."""
    m = re.search(rf"##[#]*\s*{heading_pattern}.*?\n", content, re.IGNORECASE)
    if not m:
        return ""
    rest = content[m.end() :]
    next_heading = re.search(r"\n##", rest)
    body = rest[: next_heading.start()] if next_heading else rest
    return body.strip()

# Register STST gate validator (gate key 2.5)
# This runs when pipeline.yaml includes the 2.5 gate block and the
# check-2.5 state is reached (e.g., on resume with existing STST-REPORT.md).
register_validator(2.5, validate_stst_report)


def list_validators():
    """Return a list of registered gate keys."""
    return list(_VALIDATORS.keys())
