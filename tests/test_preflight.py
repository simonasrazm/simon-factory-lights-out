"""Tests for src.preflight — SOUL validation, vendor, and browser checks."""

import pytest

import src.preflight as preflight_mod
from src.preflight import (
    check_agent_soul,
    check_vendor,
    preflight_check,
)


class TestCheckAgentSoul:
    def test_dev_with_rebuild_section_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Dev\n## rebuild mode\nFix feedback.\n", encoding="utf-8")
        assert check_agent_soul("dev", str(tmp_path)) == [], (
            "dev with rebuild section should pass with no issues"
        )

    def test_dev_without_rebuild_fails(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Dev\nBuild stuff.\n", encoding="utf-8")
        issues = check_agent_soul("dev", str(tmp_path))
        assert len(issues) == 1, (
            f"expected 1 issue for dev without rebuild, got {len(issues)}: {issues}"
        )
        assert "rebuild" in issues[0].lower() or "feedback" in issues[0].lower(), (
            f"issue should mention rebuild or feedback, got: {issues[0]!r}"
        )

    def test_qa_with_grading_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# QA\n### Grade: A\nGrading scale here.\n", encoding="utf-8")
        assert check_agent_soul("qa", str(tmp_path)) == [], (
            "qa with grading section should pass with no issues"
        )

    def test_qa_without_grading_fails(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# QA\nTest stuff.\n", encoding="utf-8")
        issues = check_agent_soul("qa", str(tmp_path))
        assert len(issues) == 1, (
            f"expected 1 issue for qa without grading, got {len(issues)}: {issues}"
        )

    def test_pm_with_ac_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# PM\nDefine acceptance criteria.\n", encoding="utf-8")
        assert check_agent_soul("pm", str(tmp_path)) == [], (
            "pm with acceptance criteria should pass with no issues"
        )

    def test_unknown_role_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Unknown\nAnything.\n", encoding="utf-8")
        assert check_agent_soul("unknown", str(tmp_path)) == [], (
            "unknown role should pass with no issues"
        )

    def test_missing_soul_fails(self, tmp_path):
        issues = check_agent_soul("dev", str(tmp_path))
        assert len(issues) == 1, (
            f"expected 1 issue for missing soul, got {len(issues)}: {issues}"
        )
        assert "not found" in issues[0], (
            f"issue should mention 'not found', got: {issues[0]!r}"
        )


class TestPreflightCheck:
    def test_all_agents_pass(self, tmp_path):
        for role, content in [
            ("dev", "## rebuild mode\nFix."),
            ("qa", "### Grade: B\n"),
            ("pm", "## Acceptance Criteria\n"),
        ]:
            d = tmp_path / role
            d.mkdir()
            (d / "SOUL.md").write_text(content, encoding="utf-8")
        assignments = {r: str(tmp_path / r) for r in ("dev", "qa", "pm")}
        result = preflight_check(assignments)
        assert result == [], (
            f"all valid agents should pass preflight, got issues: {result}"
        )

    def test_missing_agent_path(self):
        issues = preflight_check({"dev": "/nonexistent/path"})
        assert len(issues) == 1, (
            f"expected 1 issue for missing path, got {len(issues)}: {issues}"
        )
        assert "not found" in issues[0], (
            f"issue should mention 'not found', got: {issues[0]!r}"
        )

    def test_empty_assignments(self):
        assert preflight_check({}) == [], "empty assignments should produce no issues"
        assert preflight_check(None) == [], "None assignments should produce no issues"


class TestCheckBrowser:
    def test_returns_tuple(self):
        from src.preflight import check_browser

        ok, msg = check_browser()
        assert isinstance(ok, bool), (
            f"check_browser should return bool, got {type(ok).__name__}"
        )
        assert isinstance(msg, str), (
            f"check_browser msg should be str, got {type(msg).__name__}"
        )


class TestCheckVendor:
    """Preflight vendor check — the vendor/agent-skills submodule must be
    initialized so SFLO's skill resolution can find SKILL.md files.

    check_vendor() is READ-ONLY: a filesystem check for a populated
    vendor/agent-skills/skills/ directory. SFLO_ROOT is monkeypatched to a
    temp dir so tests are hermetic and never depend on the real checkout's
    submodule state.
    """

    def _populate(self, root):
        """Create a populated vendor/agent-skills/skills/ tree under root."""
        skill_dir = root / "vendor" / "agent-skills" / "skills" / "tdd"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# TDD skill\n", encoding="utf-8")

    def test_populated_vendor_returns_none(self, monkeypatch, tmp_path):
        self._populate(tmp_path)
        monkeypatch.setattr(preflight_mod, "SFLO_ROOT", str(tmp_path))
        assert check_vendor() is None, (
            "a populated vendor/agent-skills/skills/ should pass with no issue"
        )

    def test_missing_vendor_returns_issue(self, monkeypatch, tmp_path):
        # tmp_path has no vendor/ directory at all.
        monkeypatch.setattr(preflight_mod, "SFLO_ROOT", str(tmp_path))
        issue = check_vendor()
        assert issue is not None, "missing vendor should produce an issue"
        assert "agent-skills" in issue, (
            f"issue should name the agent-skills submodule, got: {issue!r}"
        )
        assert "git submodule update" in issue, (
            f"issue should give the init command, got: {issue!r}"
        )

    def test_empty_submodule_dir_returns_issue(self, monkeypatch, tmp_path):
        # The submodule directory exists but is empty — the exact state of a
        # fresh clone before `git submodule update --init`.
        (tmp_path / "vendor" / "agent-skills").mkdir(parents=True)
        monkeypatch.setattr(preflight_mod, "SFLO_ROOT", str(tmp_path))
        issue = check_vendor()
        assert issue is not None, (
            "an empty (uninitialized) submodule dir should produce an issue"
        )
        assert "agent-skills" in issue and "git submodule update" in issue, (
            f"issue should name the submodule and init command, got: {issue!r}"
        )

    def test_skills_dir_present_but_empty_returns_issue(self, monkeypatch, tmp_path):
        # skills/ exists but has no skill subdirectories — still unusable.
        (tmp_path / "vendor" / "agent-skills" / "skills").mkdir(parents=True)
        monkeypatch.setattr(preflight_mod, "SFLO_ROOT", str(tmp_path))
        issue = check_vendor()
        assert issue is not None, (
            "an empty skills/ directory should produce an issue"
        )

    def test_issue_is_actionable_string(self, monkeypatch, tmp_path):
        monkeypatch.setattr(preflight_mod, "SFLO_ROOT", str(tmp_path))
        issue = check_vendor()
        assert isinstance(issue, str), (
            f"check_vendor failure should return a str, got {type(issue).__name__}"
        )
        # Mentions a recovery path the user can act on.
        assert any(
            hint in issue
            for hint in ("setup.sh", "setup.ps1", "git submodule update")
        ), f"issue should suggest a recovery command, got: {issue!r}"
