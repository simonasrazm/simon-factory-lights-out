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

    # Check for custom validator from extension registry (if available)
    custom_validator = get_validator(gate_num)
    if custom_validator is not None:
        return custom_validator(gate_num, content, sflo_dir, checks)

    # Resolve threshold grade name for error messages
    _threshold_grade = next((k for k, v in GRADE_MAP.items() if v == GRADE_THRESHOLD), "?")

    if gate_num == 1:
        # SCOPE.md must have acceptance criteria — the contract for downstream agents
        ac_lines = re.findall(r"-\s*\[.\]", content)
        checks.append({"name": "has_acceptance_criteria", "pass": len(ac_lines) >= 1,
                        "detail": f"{len(ac_lines)} criteria found"})

        # Must have substantive content (not a near-empty file)
        word_count = len(content.split())
        checks.append({"name": "has_substance", "pass": word_count >= 50,
                        "detail": f"{word_count} words (minimum 50)"})

        # No template placeholders left
        has_placeholder = bool(PLACEHOLDER_PATTERN.search(content))
        checks.append({"name": "no_placeholders", "pass": not has_placeholder,
                        "detail": "placeholder detected" if has_placeholder else "OK"})

    elif gate_num == 2:
        # Build success marker
        has_success = bool(re.search(r"build[:\s]*success|zero errors", content, re.IGNORECASE))
        checks.append({"name": "build_success", "pass": has_success})

        # Self-checks all marked
        unchecked = re.findall(r"-\s*\[\s\]", content)
        checks.append({"name": "all_checks_marked", "pass": len(unchecked) == 0,
                        "detail": f"{len(unchecked)} unchecked items"})

        checked = re.findall(r"-\s*\[[xX]\]", content)
        checks.append({"name": "has_checked_items", "pass": len(checked) >= 1,
                        "detail": f"{len(checked)} checked items"})

        # AC-tracing: read SCOPE.md ACs, check BUILD-STATUS.md addresses each one
        scope_content, _ = read_artifact(sflo_dir, "SCOPE.md")
        if scope_content:
            scope_acs = re.findall(r"-\s*\[.\]\s*(?:AC\d+[:\s]*)?(.+)", scope_content)
            if scope_acs:
                addressed = 0
                for ac in scope_acs:
                    # Check if the AC text (or key words from it) appears in BUILD-STATUS
                    ac_words = [w for w in ac.split()[:5] if len(w) > 3]
                    if any(w.lower() in content.lower() for w in ac_words):
                        addressed += 1
                checks.append({"name": "acs_addressed", "pass": addressed >= len(scope_acs) * 0.5,
                                "detail": f"{addressed}/{len(scope_acs)} ACs referenced"})

    elif gate_num == 3:
        # Grade present and meets threshold
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

        # Auto-fail triggers — universal red flags regardless of project type
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
        # Verdict present and approved
        verdict = extract_field(content, r"###?\s*Verdict[:\s]*(.+)")
        is_approved = verdict and "APPROVED" in verdict.upper()
        checks.append({"name": "verdict_present", "pass": verdict is not None,
                        "value": verdict})
        checks.append({"name": "verdict_approved", "pass": is_approved,
                        "value": verdict})

    elif gate_num == 5:
        # Decision present and valid
        decision = extract_field(content, r"###?\s*Decision[:\s]*(.+)")
        valid_decisions = ["SHIP", "HOLD", "KILL"]
        is_valid = decision and decision.upper() in valid_decisions
        checks.append({"name": "decision_present", "pass": decision is not None,
                        "value": decision})
        checks.append({"name": "decision_valid", "pass": is_valid,
                        "value": decision, "valid_options": valid_decisions})

    passed = all(c["pass"] for c in checks)
    return passed, checks
