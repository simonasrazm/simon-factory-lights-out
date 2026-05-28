"""SFLO Pre-flight — validate before pipeline runs.

Check types:
1. Agent SOUL validation — required sections per role
2. Browser check — Chrome extension connected (for web/UI projects)
3. Vendor check — the vendor/agent-skills git submodule must be
   initialized so pipeline skill resolution can find SKILL.md files.

All checks run before any model calls are made.

Usage:
    from src.preflight import preflight_check, check_browser
    issues = preflight_check(assignments, sflo_dir)
    browser_ok, browser_msg = await check_browser()
"""

import os
import re

from .constants import SFLO_ROOT


# Required patterns per role. Each entry is (description, regex pattern).
# Pattern is searched case-insensitively against the full SOUL.md content.
# An agent passes if ALL patterns for its role match.
ROLE_REQUIREMENTS = {
    "dev": [
        (
            "rebuild/feedback handling section (QA or PM)",
            r"(?:rebuild|loop.?back|qa.?feedback|pm.?reject|fix.?mode|when.*feedback.*exists|when.*reject)",
        ),
    ],
    "qa": [
        (
            "grading scale or grade assignment",
            r"(?:grad(?:e|ing)|score|rating)",
        ),
    ],
    "pm": [
        (
            "acceptance criteria format",
            r"(?:acceptance.criter|AC\b)",
        ),
    ],
}


def check_agent_soul(role, agent_path):
    """Check a single agent's SOUL.md against its role requirements.

    Returns list of issue strings (empty = pass).
    """
    requirements = ROLE_REQUIREMENTS.get(role)
    if not requirements:
        return []  # no requirements defined for this role

    soul_path = os.path.join(agent_path, "SOUL.md")
    if not os.path.isfile(soul_path):
        return [f"{role}: SOUL.md not found at {soul_path}"]

    try:
        with open(soul_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return [f"{role}: cannot read SOUL.md: {e}"]

    issues = []
    for description, pattern in requirements:
        if not re.search(pattern, content, re.IGNORECASE):
            issues.append(f"{role}: missing {description} in {soul_path}")

    return issues


def check_pipeline_yaml():
    """Verify pipeline.yaml is discoverable.

    Returns issue string if missing, None if found.
    """
    from .config import resolve_pipeline_path

    if not resolve_pipeline_path():
        return (
            "pipeline.yaml not found. SFLO requires pipeline.yaml at "
            "cwd/, cwd/sflo/, or SFLO_ROOT/. "
            "Copy sflo/pipeline.yaml to your project root to get started."
        )
    return None


def check_vendor():
    """Verify the vendor/agent-skills git submodule is initialized.

    SFLO resolves pipeline skills from vendor/agent-skills/skills/<name>/
    SKILL.md. A fresh clone leaves that directory empty until
    `git submodule update --init --recursive` runs, and skill resolution
    then fails mid-pipeline. This catches that cheaply at preflight time.

    Read-only filesystem check: it does not mutate anything or touch the
    network — applying the fix is the user's to do.

    Returns an issue string if the submodule is not populated, else None.
    """
    skills_dir = os.path.join(SFLO_ROOT, "vendor", "agent-skills", "skills")
    try:
        # Populated == at least one entry. A missing or empty skills/ dir is
        # equally unusable: skill resolution has nothing to resolve against.
        with os.scandir(skills_dir) as entries:
            populated = any(entries)
    except OSError:
        populated = False

    if populated:
        return None

    return (
        "vendor/agent-skills submodule is not initialized. SFLO resolves "
        "pipeline skills from vendor/agent-skills/skills/ and cannot run "
        "without it. Initialize it with `git submodule update --init "
        "--recursive` (or run `bash setup.sh` on macOS/Linux, `.\\setup.ps1` "
        "on Windows)."
    )


def _looks_like_path(value):
    """Heuristic: is this assignments-dict value a filesystem path?

    True for non-empty strings containing a path separator. This lets host
    projects extend scout with metadata fields (ints, classifier hints, etc.)
    without forcing preflight to maintain a hardcoded role allowlist.
    """
    return isinstance(value, str) and value and ("/" in value or "\\" in value)


def _resolve_agent_path(agent_path):
    """Best-effort resolution of an agent path to an existing directory.

    Tries, in order:
      1. The path as-given (absolute, or relative to cwd).
      2. The path resolved against SFLO_ROOT — handles cases where the
         caller (Scout LLM, pipeline.yaml `agent:` field) emitted a path
         relative to the SFLO checkout rather than the runner's cwd.
      3. Progressively shorter path prefixes — handles garbled output
         like "agents/pm/users/..." → "agents/pm".

    Returns the resolved absolute path if found, else None.
    """
    if not agent_path:
        return None
    clean_path = agent_path.rstrip("/").rstrip("\\")
    if os.path.isdir(clean_path):
        return os.path.abspath(clean_path)
    sflo_candidate = os.path.join(SFLO_ROOT, clean_path)
    if os.path.isdir(sflo_candidate):
        return os.path.abspath(sflo_candidate)
    parts = re.split(r"[\\/]+", clean_path)
    for i in range(len(parts) - 1, 1, -1):
        candidate = os.path.join(*parts[:i])
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
        sflo_sub = os.path.join(SFLO_ROOT, candidate)
        if os.path.isdir(sflo_sub):
            return os.path.abspath(sflo_sub)
    return None


def preflight_check(assignments, sflo_dir=None):
    """Run pre-flight validation on all assigned agents.

    Args:
        assignments: dict with role -> agent_path mappings
            (e.g. {"pm": "/path/to/agents/pm", "dev": "/path/to/agents/developer"})
        sflo_dir: pipeline state directory (unused, reserved for future checks)

    Returns:
        list of issue strings. Empty list = all agents pass.
    """
    all_issues = []

    # Check pipeline.yaml exists
    yaml_issue = check_pipeline_yaml()
    if yaml_issue:
        all_issues.append(yaml_issue)

    # Check the vendor/agent-skills submodule is initialized (read-only).
    vendor_issue = check_vendor()
    if vendor_issue:
        all_issues.append(vendor_issue)

    # Validate every assignment value that looks like a filesystem path.
    # Host projects can extend Scout to emit metadata fields (ints, hints)
    # alongside role->path entries; those non-string or path-separator-less
    # values are skipped automatically.
    for role, agent_path in (assignments or {}).items():
        if not _looks_like_path(agent_path):
            continue
        resolved = _resolve_agent_path(agent_path)
        if resolved is None:
            all_issues.append(f"{role}: agent path not found: {agent_path}")
            continue
        assignments[role] = resolved
        issues = check_agent_soul(role, resolved)
        all_issues.extend(issues)

    return all_issues


def check_browser():
    """Check if Chrome is installed (prerequisite for Chrome extension).

    Pure file check — no subprocess, no SDK, instant.
    Checks standard macOS/Linux Chrome install paths.

    Returns:
        (installed: bool, message: str)
    """
    import platform

    system = platform.system()
    if system == "Darwin":
        chrome_path = "/Applications/Google Chrome.app"
    elif system == "Linux":
        chrome_path = "/usr/bin/google-chrome"
    else:
        chrome_path = None

    if chrome_path and os.path.exists(chrome_path):
        return (True, "Chrome installed")
    elif chrome_path:
        return (
            False,
            f"Chrome not found at {chrome_path} — "
            f"Chrome extension requires Chrome to be installed",
        )
    else:
        return (False, f"Chrome install check not supported on {system}")
