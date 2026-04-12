"""Tests for src.preflight — SOUL validation and browser checks."""

import os
import tempfile
import pytest

from src.preflight import check_agent_soul, preflight_check, check_chrome_devtools_mcp


class TestCheckAgentSoul:
    def test_dev_with_rebuild_section_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Dev\n## rebuild mode\nFix feedback.\n")
        assert check_agent_soul("dev", str(tmp_path)) == []

    def test_dev_without_rebuild_fails(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Dev\nBuild stuff.\n")
        issues = check_agent_soul("dev", str(tmp_path))
        assert len(issues) == 1
        assert "rebuild" in issues[0].lower() or "feedback" in issues[0].lower()

    def test_qa_with_grading_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# QA\n### Grade: A\nGrading scale here.\n")
        assert check_agent_soul("qa", str(tmp_path)) == []

    def test_qa_without_grading_fails(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# QA\nTest stuff.\n")
        issues = check_agent_soul("qa", str(tmp_path))
        assert len(issues) == 1

    def test_pm_with_ac_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# PM\nDefine acceptance criteria.\n")
        assert check_agent_soul("pm", str(tmp_path)) == []

    def test_unknown_role_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Unknown\nAnything.\n")
        assert check_agent_soul("unknown", str(tmp_path)) == []

    def test_missing_soul_fails(self, tmp_path):
        issues = check_agent_soul("dev", str(tmp_path))
        assert len(issues) == 1
        assert "not found" in issues[0]


class TestPreflightCheck:
    def test_all_agents_pass(self, tmp_path):
        for role, content in [
            ("dev", "## rebuild mode\nFix."),
            ("qa", "### Grade: B\n"),
            ("pm", "## Acceptance Criteria\n"),
        ]:
            d = tmp_path / role
            d.mkdir()
            (d / "SOUL.md").write_text(content)
        assignments = {r: str(tmp_path / r) for r in ("dev", "qa", "pm")}
        assert preflight_check(assignments) == []

    def test_missing_agent_path(self):
        issues = preflight_check({"dev": "/nonexistent/path"})
        assert len(issues) == 1
        assert "not found" in issues[0]

    def test_empty_assignments(self):
        assert preflight_check({}) == []
        assert preflight_check(None) == []


class TestCheckChromeDevtools:
    def test_returns_tuple(self):
        ok, msg = check_chrome_devtools_mcp()
        assert isinstance(ok, bool)
        assert isinstance(msg, str)

    def test_detects_missing_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        ok, msg = check_chrome_devtools_mcp()
        assert not ok
        assert "Install" in msg or "not found" in msg


class TestCheckBrowser:
    def test_returns_tuple(self):
        from src.preflight import check_browser
        ok, msg = check_browser()
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
