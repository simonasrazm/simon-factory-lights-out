"""SFLO gate validation — artifact checks for each gate."""

import json
import os
import re
import sys

from .constants import GATES, GRADE_MAP, GRADE_THRESHOLD


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
    for g in range(start_gate, 6):
        artifact = GATES[g]["artifact"]
        p = os.path.join(sflo_dir, artifact)
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError as e:
            print(json.dumps({"warning": f"Could not remove {p}: {e}"}), file=sys.stderr)


def validate_agent_path(agent_path):
    """Ensure agent path doesn't escape the project directory."""
    resolved = os.path.realpath(agent_path)
    cwd = os.path.realpath(os.getcwd())
    if not resolved.startswith(cwd):
        return False, f"Agent path '{agent_path}' resolves outside project directory"
    return True, None


def validate_gate(gate_num, sflo_dir):
    """Validate a gate's artifact. Returns (passed, checks_list)."""
    info = GATES[gate_num]
    checks = []

    content, err = read_artifact(sflo_dir, info["artifact"])
    checks.append({"name": "file_exists", "pass": content is not None,
                    "detail": err or "OK"})
    if content is None:
        return False, checks

    if gate_num == 1:
        has_data = bool(re.search(r"##\s*Data Sources", content, re.IGNORECASE))
        checks.append({"name": "has_data_sources", "pass": has_data})

        ac_lines = re.findall(r"-\s*\[.\]", content)
        checks.append({"name": "has_acceptance_criteria", "pass": len(ac_lines) >= 1,
                        "detail": f"{len(ac_lines)} criteria found"})

        has_appetite = bool(re.search(r"##\s*(Appetite|Time Budget)", content, re.IGNORECASE))
        checks.append({"name": "has_appetite", "pass": has_appetite})

    elif gate_num == 2:
        has_success = bool(re.search(r"build[:\s]*success|zero errors", content, re.IGNORECASE))
        checks.append({"name": "build_success", "pass": has_success})

        unchecked = re.findall(r"-\s*\[\s\]", content)
        checks.append({"name": "all_checks_marked", "pass": len(unchecked) == 0,
                        "detail": f"{len(unchecked)} unchecked items"})

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
                            "value": grade_str, "minimum": "B+",
                            "detail": f"{grade_str} ({'pass' if grade_val >= GRADE_THRESHOLD else 'below B+'})"})

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

    elif gate_num == 4:
        verdict = extract_field(content, r"###?\s*Verdict[:\s]*(.+)")
        is_approved = verdict and "APPROVED" in verdict.upper()
        checks.append({"name": "verdict_present", "pass": verdict is not None,
                        "value": verdict})
        checks.append({"name": "verdict_approved", "pass": is_approved,
                        "value": verdict})

    elif gate_num == 5:
        decision = extract_field(content, r"###?\s*Decision[:\s]*(.+)")
        valid_decisions = ["SHIP", "HOLD", "KILL"]
        is_valid = decision and decision.upper() in valid_decisions
        checks.append({"name": "decision_present", "pass": decision is not None,
                        "value": decision})
        checks.append({"name": "decision_valid", "pass": is_valid,
                        "value": decision, "valid_options": valid_decisions})

    passed = all(c["pass"] for c in checks)
    return passed, checks
