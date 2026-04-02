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


def section_body(content, heading_pattern):
    """Extract the body text of a markdown section (between heading and next heading).

    Returns the body text stripped, or empty string if section not found.
    """
    # Find the heading line, then capture everything until the next heading or EOF
    m = re.search(rf"##[#]*\s*{heading_pattern}.*?\n", content, re.IGNORECASE)
    if not m:
        return ""
    rest = content[m.end():]
    # Take content up to the next ## heading
    next_heading = re.search(r"\n##", rest)
    body = rest[:next_heading.start()] if next_heading else rest
    return body.strip()


# Patterns that indicate template placeholders rather than real content
PLACEHOLDER_PATTERN = re.compile(
    r"\[URL\]|\[N/?A\]|\[source\]|\[TODO\]|\[TBD\]|\[INSERT[^\]]*\]|\[PLACEHOLDER[^\]]*\]",
    re.IGNORECASE,
)


def extract_qa_feedback(sflo_dir):
    """Extract Issues section and grade from QA-REPORT.md for dev feedback.

    Returns the feedback text, or None if no QA report or no issues found.
    """
    qa_artifact = GATES.get(3, {}).get("artifact", "QA-REPORT.md")
    content, err = read_artifact(sflo_dir, qa_artifact)
    if content is None:
        return None

    parts = []

    # Extract grade
    grade_str = extract_field(content, r"###?\s*Grade[:\s]*(.+)")
    if grade_str:
        parts.append(f"### QA Grade: {grade_str}")

    # Extract Issues section (everything between ### Issues and the next ### heading)
    issues_match = re.search(
        r"(###?\s*Issues.*?)(?=\n###?\s|\Z)", content,
        re.IGNORECASE | re.DOTALL,
    )
    if issues_match:
        parts.append(issues_match.group(1).strip())

    # Extract Test Results section
    test_match = re.search(
        r"(###?\s*Test Results.*?)(?=\n###?\s|\Z)", content,
        re.IGNORECASE | re.DOTALL,
    )
    if test_match:
        parts.append(test_match.group(1).strip())

    if not parts:
        return None

    return "\n\n".join(parts)


def save_qa_feedback(sflo_dir):
    """Save QA findings to QA-FEEDBACK.md so the dev agent can see what to fix.

    Appends to existing feedback to accumulate findings across retries.
    """
    feedback = extract_qa_feedback(sflo_dir)
    if not feedback:
        return

    feedback_path = os.path.join(sflo_dir, "QA-FEEDBACK.md")
    existing = ""
    if os.path.isfile(feedback_path):
        with open(feedback_path, "r", encoding="utf-8") as f:
            existing = f.read()

    # Count existing rounds to label the new one
    round_count = existing.count("## QA Round")
    header = f"## QA Round {round_count + 1}\n\n"

    with open(feedback_path, "w", encoding="utf-8") as f:
        content = existing + header + feedback + "\n\n"
        f.write(content)


def clean_artifacts_from(start_gate, sflo_dir):
    """Remove artifacts for gates >= start_gate so auto-transition doesn't skip on loop-back.

    Preserves QA feedback (QA-FEEDBACK.md) so the dev agent knows what to fix.
    """
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

        # Data Sources must have real content, not template placeholders
        data_body = section_body(content, r"Data Sources")
        has_placeholder = bool(PLACEHOLDER_PATTERN.search(data_body)) if data_body else False
        checks.append({"name": "data_sources_real", "pass": not has_placeholder,
                        "detail": "placeholder detected" if has_placeholder else "OK"})

        ac_lines = re.findall(r"-\s*\[.\]", content)
        checks.append({"name": "has_acceptance_criteria", "pass": len(ac_lines) >= 1,
                        "detail": f"{len(ac_lines)} criteria found"})

        has_challenge_analysis = bool(re.search(r"##\s*Challenge Analysis", content, re.IGNORECASE))
        checks.append({"name": "has_challenge_analysis", "pass": has_challenge_analysis})

        # Challenge Analysis must have substantive content (not just a heading)
        challenge_body = section_body(content, r"Challenge Analysis")
        challenge_words = len(challenge_body.split()) if challenge_body else 0
        checks.append({"name": "challenge_analysis_depth", "pass": challenge_words >= 2,
                        "detail": f"{challenge_words} words (minimum 2)"})

        has_what_building = bool(re.search(r"##\s*What We're Building", content, re.IGNORECASE))
        checks.append({"name": "has_what_building", "pass": has_what_building})

        has_features = bool(re.search(r"##\s*Features", content, re.IGNORECASE))
        checks.append({"name": "has_features", "pass": has_features})

    elif gate_num == 2:
        has_success = bool(re.search(r"build[:\s]*success|zero errors", content, re.IGNORECASE))
        checks.append({"name": "build_success", "pass": has_success})

        unchecked = re.findall(r"-\s*\[\s\]", content)
        checks.append({"name": "all_checks_marked", "pass": len(unchecked) == 0,
                        "detail": f"{len(unchecked)} unchecked items"})

        # Must have at least 1 checked item (not just zero unchecked)
        checked = re.findall(r"-\s*\[[xX]\]", content)
        checks.append({"name": "has_checked_items", "pass": len(checked) >= 1,
                        "detail": f"{len(checked)} checked items"})

        has_core = bool(re.search(r"##\s*(1\.\s*)?Core Functionality", content, re.IGNORECASE))
        checks.append({"name": "has_core_functionality", "pass": has_core})

        # Core Functionality section must have real content
        core_body = section_body(content, r"(1\.\s*)?Core Functionality")
        core_words = len(core_body.split()) if core_body else 0
        checks.append({"name": "core_functionality_depth", "pass": core_words >= 2,
                        "detail": f"{core_words} words (minimum 2)"})

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

        # Test Results must have actual PASS/FAIL entries, not just a heading
        test_entries = re.findall(r"\|\s*(PASS|FAIL)\s*\|", content, re.IGNORECASE)
        checks.append({"name": "test_results_real", "pass": len(test_entries) >= 1,
                        "detail": f"{len(test_entries)} PASS/FAIL entries"})

        has_stranger = bool(re.search(r"###?\s*Stranger Test", content, re.IGNORECASE))
        checks.append({"name": "has_stranger_test", "pass": has_stranger})

        # Stranger Test must have substantive content (not just "Yes" or "No")
        stranger_body = section_body(content, r"Stranger Test")
        stranger_words = len(stranger_body.split()) if stranger_body else 0
        checks.append({"name": "stranger_test_depth", "pass": stranger_words >= 2,
                        "detail": f"{stranger_words} words (minimum 2)"})

    elif gate_num == 4:
        verdict = extract_field(content, r"###?\s*Verdict[:\s]*(.+)")
        is_approved = verdict and "APPROVED" in verdict.upper()
        checks.append({"name": "verdict_present", "pass": verdict is not None,
                        "value": verdict})
        checks.append({"name": "verdict_approved", "pass": is_approved,
                        "value": verdict})

        has_ac = bool(re.search(r"###?\s*Acceptance Criteria Check", content, re.IGNORECASE))
        checks.append({"name": "has_ac_check", "pass": has_ac})

        # AC Check must reference actual criteria (checked items)
        ac_body = section_body(content, r"Acceptance Criteria Check")
        ac_checked = len(re.findall(r"-\s*\[[xX]\]", ac_body)) if ac_body else 0
        checks.append({"name": "ac_check_depth", "pass": ac_checked >= 1,
                        "detail": f"{ac_checked} checked criteria"})

        has_scope = bool(re.search(r"###?\s*Scope Alignment", content, re.IGNORECASE))
        checks.append({"name": "has_scope_alignment", "pass": has_scope})

        # Scope Alignment must have real content
        scope_body = section_body(content, r"Scope Alignment")
        scope_words = len(scope_body.split()) if scope_body else 0
        checks.append({"name": "scope_alignment_depth", "pass": scope_words >= 2,
                        "detail": f"{scope_words} words (minimum 2)"})

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
