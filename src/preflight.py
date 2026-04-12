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

    for role, agent_path in (assignments or {}).items():
        if not agent_path or not os.path.isdir(agent_path):
            all_issues.append(f"{role}: agent path not found: {agent_path}")
            continue
        issues = check_agent_soul(role, agent_path)
        all_issues.extend(issues)

    return all_issues


def check_chrome_devtools_mcp():
    """Check if chrome-devtools MCP is configured in ~/.claude.json.

    Returns (installed: bool, message: str). If not installed, message
    includes the install command as a recommendation.
    """
    config_path = os.path.join(os.path.expanduser("~"), ".claude.json")
    if not os.path.isfile(config_path):
        return (False, "~/.claude.json not found. Install chrome-devtools MCP: "
                       "claude mcp add chrome-devtools --scope user npx chrome-devtools-mcp@latest")
    try:
        import json
        with open(config_path, "r") as f:
            data = json.load(f)
        mcp = data.get("mcpServers", {})
        if "chrome-devtools" in mcp:
            return (True, "chrome-devtools MCP configured")
        else:
            return (False, "chrome-devtools MCP not found. Recommended for browser testing. "
                           "Install: claude mcp add chrome-devtools --scope user npx chrome-devtools-mcp@latest")
    except Exception as e:
        return (False, f"Cannot read ~/.claude.json: {e}")


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
        return (False, f"Chrome not found at {chrome_path} — "
                       f"Chrome extension requires Chrome to be installed")
    else:
        return (False, f"Chrome install check not supported on {system}")
