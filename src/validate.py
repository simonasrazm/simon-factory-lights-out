"""SFLO gate validation — artifact checks for each gate."""

import ntpath
import os
import re

from . import constants

try:
    from .constants import (
        GATES,
        SFLO_ROOT,
        GRADE_MAP,
        inner_loop_gate_key,
        outer_loop_gate_key,
    )
except ImportError:  # Support legacy top-level imports from sflo/src on sys.path.
    from constants import (
        GATES,
        SFLO_ROOT,
        GRADE_MAP,
        inner_loop_gate_key,
        outer_loop_gate_key,
    )


def read_artifact(sflo_dir, filename):
    """Read artifact file content, return (content, error)."""
    p = os.path.join(sflo_dir, filename)
    if not os.path.isfile(p):
        return None, f"File not found: {p}"
    with open(p, "r", encoding="utf-8") as f:
        return f.read(), None


def feedback_name_for_artifact(artifact):
    """Return the feedback filename derived from a pipeline artifact name."""
    base = os.path.basename(artifact or "")
    stem, ext = os.path.splitext(base)
    if not stem:
        return None
    return f"{stem}-FEEDBACK{ext or '.md'}"


def feedback_path_for_artifact(sflo_dir, artifact):
    """Return the feedback path derived from a pipeline artifact name."""
    name = feedback_name_for_artifact(artifact)
    return os.path.join(sflo_dir, name) if name else None


def _gate_entries(gate_info):
    return gate_info if isinstance(gate_info, list) else [gate_info]


def feedback_paths_for_gate(sflo_dir, gate_key, gates=None):
    """Existing feedback files for every artifact produced by a gate."""
    _gates = gates if gates is not None else GATES
    gate_info = _gates.get(gate_key, {})
    paths = []
    for entry in _gate_entries(gate_info):
        artifact = entry.get("artifact") if isinstance(entry, dict) else None
        path = feedback_path_for_artifact(sflo_dir, artifact)
        if path and os.path.isfile(path):
            paths.append(path)
    return paths


def _append_artifact_feedback(sflo_dir, artifact, feedback, label):
    """Append extracted judge feedback to the artifact-specific feedback file."""
    path = feedback_path_for_artifact(sflo_dir, artifact)
    if not path or not feedback:
        return

    existing = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()

    round_count = existing.count("## Feedback Round")
    header = f"## Feedback Round {round_count + 1}"
    if label:
        header += f" — {label}"
    header += "\n\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(existing + header + feedback + "\n\n")


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
    rest = content[m.end() :]
    # Take content up to the next ## heading
    next_heading = re.search(r"\n##", rest)
    body = rest[: next_heading.start()] if next_heading else rest
    return body.strip()


def extract_deliverable_paths(scope_content):
    """Return declared relative deliverable files and contract errors."""
    body = section_body(scope_content, r"Deliverables")
    paths = re.findall(r"(?m)^\s*[-*]\s+`([^`\r\n]+)`", body)
    errors = []
    for path in paths:
        parts = path.split("/")
        if (
            os.path.isabs(path)
            or ntpath.isabs(path)
            or "\\" in path
            or any(part in ("", ".", "..") for part in parts)
            or parts[0].lower() == ".sflo"
            or ":" in path
            or any(char in path for char in "*?[]{}")
        ):
            errors.append(path)
    if len(paths) != len(set(paths)):
        errors.append("duplicate paths")
    return paths, errors


def validate_deliverable_files(scope_content, output_dir, sflo_dir=None):
    """Verify every declared deliverable is a real file inside output_dir."""
    paths, contract_errors = extract_deliverable_paths(scope_content)
    checks = []
    if not paths:
        return [
            {
                "name": "deliverables_declared",
                "pass": False,
                "detail": "SCOPE.md has no declared deliverable files",
            }
        ]

    root = os.path.realpath(os.path.abspath(output_dir))
    state_root = os.path.realpath(os.path.abspath(sflo_dir)) if sflo_dir else None
    root_is_directory = os.path.isdir(root)
    checks.append(
        {
            "name": "output_directory_exists",
            "pass": root_is_directory,
            "detail": root,
        }
    )
    unsafe = set(contract_errors)
    for path in paths:
        target = os.path.realpath(os.path.join(root, *path.split("/")))
        try:
            inside_root = os.path.commonpath((root, target)) == root
        except ValueError:
            inside_root = False
        try:
            inside_state = bool(state_root) and (
                os.path.commonpath((state_root, target)) == state_root
            )
        except ValueError:
            inside_state = False
        safe = path not in unsafe and inside_root and not inside_state
        exists = safe and root_is_directory and os.path.isfile(target)
        checks.append(
            {
                "name": f"deliverable_exists:{path}",
                "pass": exists,
                "detail": (
                    target
                    if exists
                    else f"required project file missing or unsafe: {target}"
                ),
            }
        )
    return checks


def validate_scope_deliverables(sflo_dir, output_dir):
    """Load SCOPE.md and validate its deliverables against the project root."""
    scope_content, _ = read_artifact(sflo_dir, "SCOPE.md")
    if not scope_content:
        return [
            {
                "name": "deliverables_declared",
                "pass": False,
                "detail": "SCOPE.md missing or has no deliverable manifest",
            }
        ]
    return validate_deliverable_files(scope_content, output_dir, sflo_dir=sflo_dir)


# ---------------------------------------------------------------------------
# Reusable role validators — shared by both parallel-gate and single-gate paths
# ---------------------------------------------------------------------------

# Auto-fail patterns for QA reports — universal red flags regardless of project type.
_QA_AUTO_FAIL_PATTERNS = [
    (r"mock.data|sample.data", "mock_data"),
    (r"doesn.t start|does not start|won.t run", "doesnt_start"),
    (r"purpose.*(unclear|confusing)", "purpose_unclear"),
]


def _extract_grade(content):
    """Extract grade letter from QA/PM report content.

    Priority chain (first match wins):
      1. Dedicated heading: ### Grade: A  (canonical format)
      2. Fallback: **Final grade: A** or - Final grade: A (legacy/improvised)

    Returns grade string or None.
    """
    # Primary: dedicated heading line — tighter regex avoids matching section headers
    # like "## Grade Calculation" by requiring the grade value to be a valid letter
    m = re.search(
        r"###?\s*Grade[:\s]+([A-DF][+-]?)\s*$", content, re.IGNORECASE | re.MULTILINE
    )
    if m:
        return m.group(1).strip().upper()

    # Fallback: "Final grade: X" anywhere (bold, bullet, or plain)
    m = re.search(r"[Ff]inal\s+[Gg]rade[:\s*]+([A-DF][+-]?)", content)
    if m:
        return m.group(1).strip().upper()

    return None


def _check_grade(content, threshold, require_grade=False):
    """Shared grade validation: extract, recognize, check threshold.

    Args:
        content: artifact text
        threshold: numeric grade threshold from config
        require_grade: if True, missing grade fails validation (security).
                       if False, missing grade just means threshold not met (QA).

    Returns (passed: bool, checks: list[dict]).
    """
    checks = []
    passed = True

    grade_str = _extract_grade(content)
    grade_val = GRADE_MAP.get(grade_str, -1) if grade_str else -1
    checks.append(
        {"name": "grade_present", "pass": grade_str is not None, "value": grade_str}
    )

    if not grade_str:
        if require_grade:
            passed = False
    elif grade_val < 0:
        checks.append(
            {
                "name": "grade_recognized",
                "pass": False,
                "detail": f"Unrecognized grade '{grade_str}'. "
                f"Valid: {', '.join(sorted(GRADE_MAP.keys()))}",
            }
        )
        passed = False
    else:
        _threshold_grade = next(
            (k for k, v in GRADE_MAP.items() if v == threshold), "?"
        )
        grade_pass = grade_val >= threshold
        checks.append(
            {
                "name": "grade_sufficient",
                "pass": grade_pass,
                "value": grade_str,
                "minimum": _threshold_grade,
                "detail": f"{grade_str} ({'pass' if grade_pass else f'below {_threshold_grade}'})",
            }
        )
        if not grade_pass:
            passed = False

    return passed, checks


def _validate_qa_content(content, threshold):
    """Validate QA artifact: grade present, meets threshold, no auto-fails.

    Returns (passed: bool, checks: list[dict]).
    """
    checks = []
    passed = True

    grade_passed, grade_checks = _check_grade(content, threshold)
    checks.extend(grade_checks)
    if not grade_passed:
        passed = False

    for pat, name in _QA_AUTO_FAIL_PATTERNS:
        issues_section = re.split(r"###?\s*Issues", content, flags=re.IGNORECASE)
        if len(issues_section) > 1:
            found = bool(re.search(pat, issues_section[1], re.IGNORECASE))
            if found:
                checks.append(
                    {
                        "name": f"auto_fail_{name}",
                        "pass": False,
                        "detail": f"Auto-fail trigger: {name}",
                    }
                )
                passed = False

    return passed, checks


def _validate_security_content(content, threshold):
    """Validate security artifact: grade present, meets threshold, no Criticals.

    Returns (passed: bool, checks: list[dict]).
    """
    checks = []
    passed = True

    # Auto-fail: any Critical findings
    critical_match = re.search(r"[Cc]ritical[:\s]+(\d+)", content)
    critical_count = int(critical_match.group(1)) if critical_match else 0
    if critical_count > 0:
        checks.append(
            {
                "name": "no_critical_findings",
                "pass": False,
                "detail": f"{critical_count} Critical finding(s) — auto-fail",
            }
        )
        passed = False
    else:
        checks.append({"name": "no_critical_findings", "pass": True, "detail": "OK"})

    # Grade check — require_grade=True because security reports MUST include a grade
    grade_passed, grade_checks = _check_grade(content, threshold, require_grade=True)
    checks.extend(grade_checks)
    if not grade_passed:
        passed = False

    return passed, checks


def _validate_pm_content(content):
    """Validate PM artifact: verdict present and APPROVED.

    Returns (passed: bool, checks: list[dict]).
    """
    checks = []
    verdict = extract_field(content, r"###?\s*Verdict[: ]*(.+)")
    is_approved = verdict and "APPROVED" in verdict.upper()
    checks.append(
        {"name": "verdict_present", "pass": verdict is not None, "value": verdict}
    )
    checks.append({"name": "verdict_approved", "pass": is_approved, "value": verdict})
    return is_approved, checks


# Patterns that indicate template placeholders rather than real content.
#
# Detection is context-aware. A bracket-wrapped token like [URL] or [TBD] is
# only treated as a placeholder when it appears in a template position:
#
#   (a) Alone on a line (ignoring leading/trailing whitespace), e.g.
#           [TBD]
#       or
#           <whitespace>[URL]<whitespace>
#
#   (b) Immediately after a field label, e.g.
#           Grade: [TODO]
#           Owner:[N/A]
#
# Bracketed tokens appearing inline in prose are NOT flagged. Example:
#     "a small [source] link next to each data point"   -> legit prose
#     "the [URL] field is validated"                    -> legit prose
#     "add [TODO] comments where needed"                -> legit prose
#
# Two forms are ALWAYS flagged regardless of context — they are never
# legitimate prose:
#     [INSERT anything]       -> explicit insertion marker
#     [PLACEHOLDER anything]  -> explicit placeholder marker
#
# This removes the false positive on literal UI markup like "[source]" that
# appeared in real SCOPE.md prose describing source-link affordances.
_SIMPLE_TOKENS = r"URL|TODO|TBD|N/?A|FILL[\s_-]?IN|SOURCE"
# PLACEHOLDER_PATTERN trade-off: the field-label-colon form uses a greedy
# line-anchored regex to avoid false positives on Swift/TypeScript type
# annotations like `maintains partialPaths: [URL] covering every ...`.
# Consequence: a placeholder that appears mid-sentence (e.g. "see [TODO]")
# is NOT flagged unless it occupies a whole label line.  This is intentional —
# flagging every bracket token in prose would produce too many false positives
# on documents that legitimately reference placeholder syntax in explanations.
PLACEHOLDER_PATTERN = re.compile(
    # alone-on-line form
    rf"(?:^|\n)[ \t]*\[(?:{_SIMPLE_TOKENS})\][ \t]*(?=$|\n)"
    r"|"
    # field-label-colon form (e.g. "Grade: [TBD]") — must occupy the whole line
    # (label-colon-bracket-end-of-line) to avoid matching mid-prose Swift type
    # annotations like `... maintains partialPaths: [URL] covering every ...`
    rf"(?:^|\n)[ \t]*\w[\w -]*:[ \t]*\[(?:{_SIMPLE_TOKENS})\][ \t]*(?=$|\n)"
    r"|"
    # explicit insert/placeholder forms (always flagged)
    r"\[INSERT[^\]]*\]|\[PLACEHOLDER[^\]]*\]",
    re.IGNORECASE,
)


def extract_agent_feedback(sflo_dir, artifact, role):
    """Extract grade and findings from any gate agent's report artifact.

    Generic extractor — works for QA, Security, or any future parallel agent.
    Searches for common report sections: Grade, Findings, Issues, Test Results.

    Args:
        sflo_dir: path to .sflo directory
        artifact: filename of the report (e.g. "QA-REPORT.md")
        role: agent role name (used as label in extracted feedback)

    Returns: feedback text string, or None if artifact missing/empty.
    """
    content, err = read_artifact(sflo_dir, artifact)
    if content is None:
        return None

    parts = []

    # Extract grade
    grade_str = extract_field(content, r"###?\s*Grade[: ]*(.+)")
    if grade_str:
        label = role.upper() if len(role) <= 4 else role.title()
        parts.append(f"### {label} Grade: {grade_str}")

    # Extract Findings section (Security reports, generic)
    findings_match = re.search(
        r"(###?\s*Findings.*?)(?=\n###?\s|\Z)",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    if findings_match:
        parts.append(findings_match.group(1).strip())

    # Extract Issues section (QA reports)
    issues_match = re.search(
        r"(###?\s*Issues.*?)(?=\n###?\s|\Z)",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    if issues_match:
        parts.append(issues_match.group(1).strip())

    # Extract Test Results section (QA reports)
    test_match = re.search(
        r"(###?\s*Test Results.*?)(?=\n###?\s|\Z)",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    if test_match:
        parts.append(test_match.group(1).strip())

    if not parts:
        return None

    return "\n\n".join(parts)


def save_gate_feedback(sflo_dir, gate_key, gates=None):
    """Save feedback from every artifact configured at ``gate_key``."""
    _gates = gates if gates is not None else GATES
    gate_info = _gates.get(gate_key, {}) if gate_key is not None else {}
    agents = gate_info if isinstance(gate_info, list) else [gate_info]

    for agent in agents:
        artifact = agent.get("artifact")
        role = agent.get("role", "unknown")
        if not artifact:
            continue
        feedback = extract_agent_feedback(sflo_dir, artifact, role)
        if feedback:
            _append_artifact_feedback(sflo_dir, artifact, feedback, role)


def _first_gate_for_role(role, gates=None):
    """Return the first configured gate containing ``role``."""
    _gates = gates if gates is not None else GATES
    for gate_key in sorted(_gates):
        for entry in _gate_entries(_gates[gate_key]):
            if isinstance(entry, dict) and entry.get("role") == role:
                return gate_key
    return None


def save_qa_feedback(sflo_dir):
    """Backward-compatible helper that saves the configured QA feedback."""
    save_gate_feedback(sflo_dir, _first_gate_for_role("qa"))


def extract_qa_feedback(sflo_dir):
    """Backward-compat wrapper — extracts QA agent feedback only.

    Prefer extract_agent_feedback() for new code. This exists for tests
    and any external callers that expect the old interface.
    """
    gate_key = _first_gate_for_role("qa")
    gate_info = GATES.get(gate_key, {}) if gate_key is not None else {}
    if isinstance(gate_info, list):
        qa_artifact = next(
            (e.get("artifact") for e in gate_info if e.get("role") == "qa"),
            "QA-REPORT.md",
        )
    else:
        qa_artifact = gate_info.get("artifact", "QA-REPORT.md")
    return extract_agent_feedback(sflo_dir, qa_artifact, "qa")


def save_pm_feedback(sflo_dir):
    """Copy outer-loop verification artifacts to artifact feedback files.

    Same pattern as save_qa_feedback: gate artifact is deleted for
    auto_transition, but a feedback copy persists for dev's context map.
    """
    gate_key = outer_loop_gate_key()
    gate_info = GATES.get(gate_key, {}) if gate_key is not None else {}
    for entry in _gate_entries(gate_info):
        artifact = entry.get("artifact") if isinstance(entry, dict) else None
        role = entry.get("role", "unknown") if isinstance(entry, dict) else "unknown"
        if not artifact:
            continue
        content, _ = read_artifact(sflo_dir, artifact)
        if content is not None:
            _append_artifact_feedback(sflo_dir, artifact, content, role)


def clean_artifacts_from(start_gate, sflo_dir, preserve=None):
    """Archive gate OUTPUT artifacts >= start_gate to logs/ so auto-transition rebuilds them.

    Feedback files (`*-FEEDBACK.md`) are preserved in place — they're context
    for the next agent, not gate outputs to regenerate.
    Gate artifacts (including PM-VERIFY.md) are moved to logs/ for debugging.
    """
    try:
        from .archive import archive_to_logs
    except ImportError:
        from archive import archive_to_logs

    preserve_names = set()
    if preserve:
        preserve_names.update(preserve)

    to_archive = []
    for g in sorted(GATES.keys()):
        if g >= start_gate:
            g_info = GATES[g]
            if isinstance(g_info, list):
                for entry in g_info:
                    artifact = entry.get("artifact")
                    if artifact and artifact not in preserve_names:
                        p = os.path.join(sflo_dir, artifact)
                        if os.path.isfile(p):
                            to_archive.append(p)
            else:
                artifact = g_info.get("artifact")
                if not artifact or artifact in preserve_names:
                    continue
                p = os.path.join(sflo_dir, artifact)
                if os.path.isfile(p):
                    to_archive.append(p)

    if to_archive:
        archive_to_logs(sflo_dir, to_archive)


def validate_agent_path(agent_path):
    """Ensure agent path doesn't escape the project directory or SFLO_ROOT."""
    resolved = os.path.realpath(agent_path)
    cwd = os.path.realpath(os.getcwd())
    sflo_root = os.path.realpath(SFLO_ROOT)
    if (
        resolved == cwd
        or resolved.startswith(cwd + os.sep)
        or resolved == sflo_root
        or resolved.startswith(sflo_root + os.sep)
    ):
        return True, None
    return False, f"Agent path '{agent_path}' resolves outside project directory"


def _load_validator_module(validator_path):
    """Load a validator module from a relative file path via importlib.

    Returns (module, error_string). Rejects absolute paths and '..' traversal.
    Path resolved relative to cwd, contained within cwd or SFLO_ROOT.
    """
    import importlib.util

    if not validator_path:
        return None, "validator path is empty"
    if os.path.isabs(validator_path):
        return None, f"Validator path must be relative: {validator_path}"
    if ".." in validator_path.replace("\\", "/").split("/"):
        return None, f"Validator path must not contain '..': {validator_path}"

    abs_path = os.path.realpath(os.path.join(os.getcwd(), validator_path))
    ok, err = validate_agent_path(abs_path)
    if not ok:
        return None, err

    if not os.path.isfile(abs_path):
        return None, f"Validator file not found: {abs_path}"

    spec = importlib.util.spec_from_file_location("_sflo_validator", abs_path)
    if spec is None:
        return None, f"Cannot load module from {abs_path}"
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        return None, f"Failed to load validator {abs_path}: {e}"
    return module, None


def validate_gate(gate_num, sflo_dir, gates=None, output_dir=None):
    """Validate a gate's artifact. Returns (passed, checks_list).

    For unknown gates (not in built-in gates 1-5), falls back to
    file-existence check only via validate_ext registry.
    """
    _gates = gates if gates is not None else GATES
    try:
        from .validate_ext import get_validator
    except ImportError:
        from validate_ext import get_validator

    if gate_num not in _gates:
        return False, [
            {
                "name": "gate_not_found",
                "pass": False,
                "detail": f"Gate {gate_num} not found in GATES",
            }
        ]

    info = _gates[gate_num]

    # List-based parallel gates: validate each entry's artifact
    if isinstance(info, list):
        all_checks = []
        all_passed = True
        for entry in info:
            artifact = entry.get("artifact")
            if not artifact:
                continue
            content, err = read_artifact(sflo_dir, artifact)
            all_checks.append(
                {
                    "name": f"file_exists:{artifact}",
                    "pass": content is not None,
                    "detail": err or "OK",
                }
            )
            if content is None:
                all_passed = False
                continue

            entry_validator = entry.get("validator")
            if entry_validator:
                mod, load_err = _load_validator_module(entry_validator)
                if load_err:
                    all_checks.append(
                        {
                            "name": f"validator_load_error:{artifact}",
                            "pass": False,
                            "detail": load_err,
                        }
                    )
                    all_passed = False
                elif hasattr(mod, "validate"):
                    try:
                        entry_passed, entry_checks = mod.validate(
                            gate_num, content, sflo_dir, []
                        )
                        all_checks.extend(entry_checks)
                        if not entry_passed:
                            all_passed = False
                    except Exception as e:
                        all_checks.append(
                            {
                                "name": f"validator_error:{artifact}",
                                "pass": False,
                                "detail": f"Validator failed: {e}",
                            }
                        )
                        all_passed = False
                else:
                    all_checks.append(
                        {
                            "name": f"validator_missing_fn:{artifact}",
                            "pass": False,
                            "detail": f"{entry_validator} has no validate() function",
                        }
                    )
                    all_passed = False
                continue

            entry_role = entry.get("role")
            # Per-gate threshold: entry.threshold -> global GRADE_THRESHOLD
            entry_threshold_str = entry.get("threshold")
            entry_threshold = (
                GRADE_MAP.get(entry_threshold_str, constants.GRADE_THRESHOLD)
                if entry_threshold_str
                else constants.GRADE_THRESHOLD
            )
            if entry_role == "qa":
                qa_passed, qa_checks = _validate_qa_content(content, entry_threshold)
                all_checks.extend(qa_checks)
                if not qa_passed:
                    all_passed = False
            elif entry_role == "security":
                sec_passed, sec_checks = _validate_security_content(
                    content, entry_threshold
                )
                all_checks.extend(sec_checks)
                if not sec_passed:
                    all_passed = False
            elif entry_role == "pm":
                pm_passed, pm_checks = _validate_pm_content(content)
                all_checks.extend(pm_checks)
                if not pm_passed:
                    all_passed = False

        review_roles = {
            entry.get("role") for entry in info if isinstance(entry, dict)
        }
        if output_dir is not None and review_roles.intersection({"qa", "security"}):
            deliverable_checks = validate_scope_deliverables(sflo_dir, output_dir)
            all_checks.extend(deliverable_checks)
            if not all(check["pass"] for check in deliverable_checks):
                all_passed = False

        return all_passed, all_checks

    checks = []

    artifact_name = info.get("artifact", f"gate-{gate_num}")
    content, err = read_artifact(sflo_dir, artifact_name)
    checks.append(
        {"name": "file_exists", "pass": content is not None, "detail": err or "OK"}
    )
    if content is None:
        return False, checks

    # Config-driven validator: gate has `validator` field pointing to a script
    validator_path = info.get("validator")
    if validator_path:
        mod, load_err = _load_validator_module(validator_path)
        if load_err:
            checks.append(
                {"name": "validator_load_error", "pass": False, "detail": load_err}
            )
            return False, checks
        if hasattr(mod, "validate"):
            return mod.validate(gate_num, content, sflo_dir, checks)
        checks.append(
            {
                "name": "validator_missing_fn",
                "pass": False,
                "detail": f"{validator_path} has no validate() function",
            }
        )
        return False, checks

    # Resolve per-gate threshold: gate entry threshold -> global GRADE_THRESHOLD
    gate_threshold_str = info.get("threshold") if isinstance(info, dict) else None
    effective_threshold = (
        GRADE_MAP.get(gate_threshold_str, constants.GRADE_THRESHOLD)
        if gate_threshold_str
        else constants.GRADE_THRESHOLD
    )

    # Single-entry review gates validate by role, not by numeric position.
    # This keeps QA and Security contracts intact when they are sequenced at
    # integer or float gate keys instead of grouped in a parallel list.
    entry_role = info.get("role") if isinstance(info, dict) else None
    if entry_role == "qa":
        role_passed, role_checks = _validate_qa_content(content, effective_threshold)
        checks.extend(role_checks)
        if output_dir is not None:
            checks.extend(validate_scope_deliverables(sflo_dir, output_dir))
        return role_passed and all(c["pass"] for c in checks), checks
    if entry_role == "security":
        role_passed, role_checks = _validate_security_content(
            content, effective_threshold
        )
        checks.extend(role_checks)
        if output_dir is not None:
            checks.extend(validate_scope_deliverables(sflo_dir, output_dir))
        return role_passed and all(c["pass"] for c in checks), checks

    # Check for custom validator from extension registry (if available).
    # Role-aware review gates above must not fall through to the custom-gate
    # file-existence default merely because they use a float key.
    custom_validator = get_validator(gate_num)
    if custom_validator is not None:
        return custom_validator(gate_num, content, sflo_dir, checks)

    if gate_num == 1:
        # SCOPE.md must have acceptance criteria — the contract for downstream agents.
        # Accept multiple formats: - [ ] AC1, * [ ] AC1, - AC1:, numbered 1. AC1:
        ac_checkbox = re.findall(r"[-*]\s*\[.\]", content)
        ac_labeled = re.findall(r"(?:^|\n)\s*[-*]\s*AC\d+\s*:", content)
        ac_numbered = re.findall(r"(?:^|\n)\s*\d+\.\s*(?:\[.\]\s*)?AC\d+\s*:", content)
        # Also match "Acceptance Criteria" section with any list items
        ac_section = re.search(r"(?i)acceptance\s+criteria", content)
        ac_items_after_header = []
        if ac_section:
            after = content[ac_section.end() :]
            ac_items_after_header = re.findall(r"(?:^|\n)\s*[-*\d.]+\s*\S", after[:500])
        ac_total = len(ac_checkbox) + len(ac_labeled) + len(ac_numbered)
        if ac_total == 0 and ac_items_after_header:
            ac_total = len(ac_items_after_header)
        checks.append(
            {
                "name": "has_acceptance_criteria",
                "pass": ac_total >= 1,
                "detail": f"{ac_total} criteria found",
            }
        )

        # External sources are an ingestion gate, not a prose claim. A scope
        # must either declare that none are needed or retain concrete probe
        # evidence (method plus an observed result) for the sources it uses.
        data_sources = section_body(content, r"Data Sources")
        no_external_sources = bool(
            re.search(
                r"\bno external (?:data sources?|data|sources?) (?:are )?required\b",
                data_sources,
                re.IGNORECASE,
            )
        )
        has_probe_method = bool(
            re.search(
                r"\b(?:curl|fetch|http request|queried|opened|read)\b",
                data_sources,
                re.IGNORECASE,
            )
        )
        has_probe_result = bool(
            re.search(
                r"(?:\bHTTP\s*\d{3}\b|\bstatus(?:\s+code)?[:\s]+\d{3}\b|"
                r"\breturned\s+\d+\b|\b\d+\s+records?\b|"
                r"\bresponse\s+time[:\s]+\d+)",
                data_sources,
                re.IGNORECASE,
            )
        )
        sources_verified = no_external_sources or (
            has_probe_method and has_probe_result
        )
        checks.append(
            {
                "name": "data_sources_verified",
                "pass": sources_verified,
                "detail": (
                    "no external source required"
                    if no_external_sources
                    else "retained probe method and result"
                    if sources_verified
                    else "missing no-source declaration or concrete probe evidence"
                ),
            }
        )

        # Must have substantive content (not a near-empty file)
        word_count = len(content.split())
        checks.append(
            {
                "name": "has_substance",
                "pass": word_count >= 50,
                "detail": f"{word_count} words (minimum 50)",
            }
        )

        # No template placeholders left
        has_placeholder = bool(PLACEHOLDER_PATTERN.search(content))
        checks.append(
            {
                "name": "no_placeholders",
                "pass": not has_placeholder,
                "detail": "placeholder detected" if has_placeholder else "OK",
            }
        )

        if output_dir is not None:
            deliverable_paths, deliverable_errors = extract_deliverable_paths(content)
            checks.append(
                {
                    "name": "deliverables_declared",
                    "pass": bool(deliverable_paths),
                    "paths": deliverable_paths,
                    "detail": (
                        f"{len(deliverable_paths)} required file(s) declared"
                        if deliverable_paths
                        else "missing ## Deliverables entries formatted as - `relative/path`"
                    ),
                }
            )
            checks.append(
                {
                    "name": "deliverables_safe",
                    "pass": bool(deliverable_paths) and not deliverable_errors,
                    "detail": (
                        "OK"
                        if deliverable_paths and not deliverable_errors
                        else f"unsafe deliverable declarations: {', '.join(deliverable_errors)}"
                        if deliverable_errors
                        else "no deliverables to validate"
                    ),
                }
            )

        pm_precise_escalated = bool(
            re.search(r"(?im)^\s*(?:#{1,6}\s*)?VERDICT\s*:\s*ESCALATE\b", content)
        )
        checks.append(
            {
                "name": "pm_precise_not_escalated",
                "pass": not pm_precise_escalated,
                "detail": "pm-precise requested full PM reroute"
                if pm_precise_escalated
                else "OK",
            }
        )

    elif gate_num == 1.5:
        try:
            from .validate_wb import validate_work_breakdown
        except ImportError:
            from validate_wb import validate_work_breakdown

        scope_content, _ = read_artifact(sflo_dir, "SCOPE.md")
        wb_checks = validate_work_breakdown(content, scope_content or "")
        checks.extend(
            {"name": c.name, "pass": c.passed, "detail": c.detail}
            for c in wb_checks
        )

    elif gate_num == 2:
        # Build success marker
        has_success = bool(
            re.search(r"build[:\s]*success|zero errors", content, re.IGNORECASE)
        )
        checks.append({"name": "build_success", "pass": has_success})

        # Self-checks all marked (accept - [x], * [x], [x], [X], ✅, ☑)
        unchecked = re.findall(r"[-*]\s*\[\s\]", content)
        checks.append(
            {
                "name": "all_checks_marked",
                "pass": len(unchecked) == 0,
                "detail": f"{len(unchecked)} unchecked items",
            }
        )

        checked = re.findall(r"(?:[-*]\s*)?\[[xX✓✅☑]\]", content)
        checks.append(
            {
                "name": "has_checked_items",
                "pass": len(checked) >= 1,
                "detail": f"{len(checked)} checked items",
            }
        )

        # AC-tracing: read SCOPE.md ACs, check BUILD-STATUS.md addresses each one
        scope_content, _ = read_artifact(sflo_dir, "SCOPE.md")
        if scope_content:
            scope_acs = re.findall(r"-\s*\[.\]\s*(?:AC\d+[:\s]*)?(.+)", scope_content)
            if scope_acs:
                addressed = 0
                content_lower = content.lower()
                for i, ac in enumerate(scope_acs, 1):
                    # Match by keyword from AC text
                    ac_words = [w for w in ac.split()[:5] if len(w) > 3]
                    keyword_match = any(w.lower() in content_lower for w in ac_words)
                    # Also match by AC number reference (AC1, AC2, etc.)
                    ac_num_match = (
                        f"ac{i}" in content_lower or f"ac {i}" in content_lower
                    )
                    if keyword_match or ac_num_match:
                        addressed += 1
                checks.append(
                    {
                        "name": "acs_addressed",
                        "pass": addressed >= len(scope_acs) * 0.5,
                        "detail": f"{addressed}/{len(scope_acs)} ACs referenced",
                    }
                )
            if output_dir is not None:
                checks.extend(validate_scope_deliverables(sflo_dir, output_dir))

    elif gate_num == 3:
        qa_passed, qa_checks = _validate_qa_content(content, effective_threshold)
        checks.extend(qa_checks)

    elif gate_num == 4:
        pm_passed, pm_checks = _validate_pm_content(content)
        checks.extend(pm_checks)

    elif gate_num == 5:
        # Decision present and valid
        decision = extract_field(content, r"###?\s*Decision[: ]*(.+)")
        valid_decisions = ["SHIP", "HOLD", "KILL"]
        is_valid = decision and decision.upper() in valid_decisions
        checks.append(
            {
                "name": "decision_present",
                "pass": decision is not None,
                "value": decision,
            }
        )
        if output_dir is not None and decision and decision.upper() == "SHIP":
            checks.extend(validate_scope_deliverables(sflo_dir, output_dir))
        checks.append(
            {
                "name": "decision_valid",
                "pass": is_valid,
                "value": decision,
                "valid_options": valid_decisions,
            }
        )

    passed = all(c["pass"] for c in checks)
    return passed, checks
