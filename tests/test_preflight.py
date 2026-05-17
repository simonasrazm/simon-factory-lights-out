"""Tests for src.preflight — SOUL validation and browser checks."""

from src.preflight import check_agent_soul, preflight_check


class TestCheckAgentSoul:
    def test_dev_with_rebuild_section_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Dev\n## rebuild mode\nFix feedback.\n")
        assert check_agent_soul("dev", str(tmp_path)) == [], (
            "dev with rebuild section should pass with no issues"
        )

    def test_dev_without_rebuild_fails(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Dev\nBuild stuff.\n")
        issues = check_agent_soul("dev", str(tmp_path))
        assert len(issues) == 1, (
            f"expected 1 issue for dev without rebuild, got {len(issues)}: {issues}"
        )
        assert "rebuild" in issues[0].lower() or "feedback" in issues[0].lower(), (
            f"issue should mention rebuild or feedback, got: {issues[0]!r}"
        )

    def test_qa_with_grading_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# QA\n### Grade: A\nGrading scale here.\n")
        assert check_agent_soul("qa", str(tmp_path)) == [], (
            "qa with grading section should pass with no issues"
        )

    def test_qa_without_grading_fails(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# QA\nTest stuff.\n")
        issues = check_agent_soul("qa", str(tmp_path))
        assert len(issues) == 1, (
            f"expected 1 issue for qa without grading, got {len(issues)}: {issues}"
        )

    def test_pm_with_ac_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# PM\nDefine acceptance criteria.\n")
        assert check_agent_soul("pm", str(tmp_path)) == [], (
            "pm with acceptance criteria should pass with no issues"
        )

    def test_unknown_role_passes(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Unknown\nAnything.\n")
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
            (d / "SOUL.md").write_text(content)
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
