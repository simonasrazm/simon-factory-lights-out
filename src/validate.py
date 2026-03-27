"""SFLO gate validation — artifact checks for each gate."""

import json
import os
import re
import sys

from .constants import GATES, SFLO_ROOT, GRADE_MAP, GRADE_THRESHOLD


def read_artifact(sflo_dir, filename):
    """Read artifact file content, return (content, error)."""
    p = os.path.join(sflo_dir, filename)
    if not os.path.isfile(p):
        return None, f"File not found: {p}"
    with open(p, "r", encoding="utf-8") as f:
        return f.read(), None


def extract_field(content, pattern):
    """Extract value after a markdown heading pattern like '### Grade:'."""
    m = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    val = m.group(1).strip()
    val = re.sub(r"\*+", "", val).strip()
    return val.split()[0] if val else None


def clean_artifacts_from(start_gate, sflo_dir):
    """Remove artifacts for gates >= start_gate so auto-transition doesn't skip on loop-back."""
    for g in sorted(GATES.keys()):
        if g >= start_gate:
            artifact = GATES[g]["artifact"]
            p = os.path.join(sflo_dir, artifact)
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError as e:
                print(json.dumps({"warning": f"Could not remove {p}: {e}"}), file=sys.stderr)


def validate_agent_path(agent_path):
    """Ensure agent path doesn't escape the project directory or SFLO_ROOT."""
    resolved = os.path.realpath(agent_path)
    cwd = os.path.realpath(os.getcwd())
    sflo_root = os.path.realpath(SFLO_ROOT)
    if resolved.startswith(cwd) or resolved.startswith(sflo_root):
        return True, None
    return False, f"Agent path '{agent_path}' resolves outside project directory"


def validate_gate(gate_num, sflo_dir):
    """Validate a gate's artifact. Returns (passed, checks_list).

    For unknown gates (not in built-in gates 1-5), falls back to
    file-existence check only via validate_ext registry.
    """
    from .validate_ext import get_validator

    if gate_num not in GATES:
        return False, [{"name": "gate_not_found", "pass": False,
                         "detail": f"Gate {gate_num} not found in GATES"}]

    info = GATES[gate_num]
    checks = []

    content, err = read_artifact(sflo_dir, info["artifact"])
    checks.append({"name": "file_exists", "pass": content is not None,
                    "detail": err or "OK"})
    if content is None:
        return False, checks

    # Check for custom validator from registry
    custom_validator = get_validator(gate_num)
    if custom_validator is not None:
        return custom_validator(gate_num, content, sflo_dir, checks)

    # Resolve threshold grade name for error messages
    _threshold_grade = next((k for k, v in GRADE_MAP.items() if v == GRADE_THRESHOLD), "?")

    if gate_num == 1:
        has_data = bool(re.search(r"##\s*Data Sources", content, re.IGNORECASE))
        checks.append({"name": "has_data_sources", "pass": has_data})

        ac_lines = re.findall(r"-\s*\[.\]", content)
        checks.append({"name": "has_acceptance_criteria", "pass": len(ac_lines) >= 1,
                        "detail": f"{len(ac_lines)} criteria found"})

        has_challenge_analysis = bool(re.search(r"##\s*Challenge Analysis", content, re.IGNORECASE))
        checks.append({"name": "has_challenge_analysis", "pass": has_challenge_analysis})

        has_what_building = bool(re.search(r"##\s*What We're Building", content, re.IGNORECASE))
        checks.append({"name": "has_what_building", "pass": has_what_building})

        has_features = bool(re.search(r"##\s*Features", content, re.IGNORECASE))
        checks.append({"name": "has_features", "pass": has_features})

        has_state_management = bool(re.search(r"##\s*State Management", content, re.IGNORECASE))
        checks.append({"name": "has_state_management", "pass": has_state_management})

    elif gate_num == 2:
        has_success = bool(re.search(r"build[:\s]*success|zero errors", content, re.IGNORECASE))
        checks.append({"name": "build_success", "pass": has_success})

        unchecked = re.findall(r"-\s*\[\s\]", content)
        checks.append({"name": "all_checks_marked", "pass": len(unchecked) == 0,
                        "detail": f"{len(unchecked)} unchecked items"})

        has_core = bool(re.search(r"##\s*(1\.\s*)?Core Functionality", content, re.IGNORECASE))
        checks.append({"name": "has_core_functionality", "pass": has_core})

        has_a11y = bool(re.search(r"##\s*(2\.\s*)?Accessibility Check", content, re.IGNORECASE))
        checks.append({"name": "has_accessibility_check", "pass": has_a11y})

    elif gate_num == 3:
        grade_str = extract_field(content, r"###?\s*Grade[:\s]*(.+)")
        grade_val = GRADE_MAP.get(grade_str, -1) if grade_str else -1
        checks.append({"name": "grade_present", "pass": grade_str is not None,
                        "value": grade_str})

        if grade_str and grade_val < 0:
            checks.append({"name": "grade_recognized", "pass": False,
                            "detail": f"Unrecognized grade '{grade_str}'. "
                                      f"Valid: {', '.join(sorted(GRADE_MAP.keys()))}"})
        else:
            checks.append({"name": "grade_sufficient", "pass": grade_val >= GRADE_THRESHOLD,
                            "value": grade_str, "minimum": _threshold_grade,
                            "detail": f"{grade_str} ({'pass' if grade_val >= GRADE_THRESHOLD else f'below {_threshold_grade}'})"})

        auto_fail_patterns = [
            (r"mock.data|sample.data", "mock_data"),
            (r"doesn.t start|does not start|won.t run", "doesnt_start"),
            (r"purpose.*(unclear|confusing)", "purpose_unclear"),
        ]
        for pat, name in auto_fail_patterns:
            issues_section = re.split(r"###?\s*Issues", content, flags=re.IGNORECASE)
            if len(issues_section) > 1:
                found = bool(re.search(pat, issues_section[1], re.IGNORECASE))
                if found:
                    checks.append({"name": f"auto_fail_{name}", "pass": False,
                                   "detail": f"Auto-fail trigger: {name}"})

        has_test_results = bool(re.search(r"###?\s*Test Results", content, re.IGNORECASE))
        checks.append({"name": "has_test_results", "pass": has_test_results})

        has_stranger = bool(re.search(r"###?\s*Stranger Test", content, re.IGNORECASE))
        checks.append({"name": "has_stranger_test", "pass": has_stranger})

    elif gate_num == 4:
        verdict = extract_field(content, r"###?\s*Verdict[:\s]*(.+)")
        is_approved = verdict and "APPROVED" in verdict.upper()
        checks.append({"name": "verdict_present", "pass": verdict is not None,
                        "value": verdict})
        checks.append({"name": "verdict_approved", "pass": is_approved,
                        "value": verdict})

        has_ac = bool(re.search(r"###?\s*Acceptance Criteria Check", content, re.IGNORECASE))
        checks.append({"name": "has_ac_check", "pass": has_ac})

        has_scope = bool(re.search(r"###?\s*Scope Alignment", content, re.IGNORECASE))
        checks.append({"name": "has_scope_alignment", "pass": has_scope})

        has_reflection = bool(re.search(r"##\s*Process Reflection", content, re.IGNORECASE))
        checks.append({"name": "has_process_reflection", "pass": has_reflection})

    elif gate_num == 5:
        decision = extract_field(content, r"###?\s*Decision[:\s]*(.+)")
        valid_decisions = ["SHIP", "HOLD", "KILL"]
        is_valid = decision and decision.upper() in valid_decisions
        checks.append({"name": "decision_present", "pass": decision is not None,
                        "value": decision})
        checks.append({"name": "decision_valid", "pass": is_valid,
                        "value": decision, "valid_options": valid_decisions})

        has_evidence = bool(re.search(r"###?\s*Pipeline Evidence", content, re.IGNORECASE))
        checks.append({"name": "has_pipeline_evidence", "pass": has_evidence})

        has_iterations = bool(re.search(r"###?\s*Iterations", content, re.IGNORECASE))
        checks.append({"name": "has_iterations", "pass": has_iterations})

    passed = all(c["pass"] for c in checks)
    return passed, checks
