"""SFLO Pre-flight — validate before pipeline runs.

Two check types:
1. Agent SOUL validation — required sections per role
2. Browser check — Chrome extension connected (for web/UI projects)

All checks run before any tokens are burned.

Usage:
    from src.preflight import preflight_check, check_browser
    issues = preflight_check(assignments, sflo_dir)
    browser_ok, browser_msg = await check_browser()
"""

import os
import re


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

    # Scout may return metadata keys alongside the agent-role assignments
    # (e.g. host projects can extend scout to emit complexity scores or
    # routing hints). Only agent-role keys point to filesystem agent paths —
    # metadata keys hold ints/strings and must not be path-resolved.
    _AGENT_ROLES = {"pm", "dev", "qa", "scout", "interrogator", "troubleshooter"}

    for role, agent_path in (assignments or {}).items():
        if role not in _AGENT_ROLES:
            # Metadata field — skip path validation, leave value in place
            # for downstream consumers (preflight only checks agent paths).
            continue
        if not agent_path:
            all_issues.append(f"{role}: agent path not found: {agent_path}")
            continue
        # Normalize: strip trailing slash, try progressively shorter prefixes.
        # Handles garbled paths like "agents/pm/users/..." → "agents/pm"
        clean_path = agent_path.rstrip("/")
        if not os.path.isdir(clean_path):
            found = False
            # Try prepending / (missing leading slash)
            if not clean_path.startswith("/") and os.path.isdir("/" + clean_path):
                clean_path = "/" + clean_path
                found = True
            else:
                # Try progressively shorter path prefixes
                parts = clean_path.split("/")
                for i in range(2, len(parts)):
                    candidate = "/".join(parts[:i])
                    if os.path.isdir(candidate):
                        clean_path = candidate
                        found = True
                        break
            if not found:
                all_issues.append(f"{role}: agent path not found: {agent_path}")
                continue
        assignments[role] = clean_path
        issues = check_agent_soul(role, clean_path)
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
