"""Tests for build_context_map and feedback flow in machine.py."""

from src.machine import build_context_map


class TestBuildContextMap:
    def test_fresh_mode_no_feedback(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        (sflo_dir / "SCOPE.md").write_text("scope content", encoding="utf-8")

        mode, text = build_context_map(2, str(sflo_dir))

        assert mode == "fresh", f"expected mode 'fresh' with no feedback, got {mode!r}"
        assert "Mode: fresh" in text, "context map text should contain 'Mode: fresh'"
        assert "SCOPE.md" in text, "context map text should reference SCOPE.md"

    def test_rebuild_mode_qa_feedback_only(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        (sflo_dir / "SCOPE.md").write_text("scope", encoding="utf-8")
        (sflo_dir / "QA-FEEDBACK.md").write_text(
            "fix BenchmarkPanel.jsx", encoding="utf-8"
        )

        mode, text = build_context_map(2, str(sflo_dir))

        assert mode == "rebuild", (
            f"expected mode 'rebuild' with QA feedback, got {mode!r}"
        )
        assert "Mode: rebuild" in text, (
            "context map text should contain 'Mode: rebuild'"
        )
        assert "QA-FEEDBACK.md" in text, "context map should reference QA-FEEDBACK.md"
        assert "read only if you need AC details" in text, (
            "rebuild mode should deprioritize SCOPE.md"
        )

    def test_rebuild_mode_pm_feedback_only(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        (sflo_dir / "SCOPE.md").write_text("scope", encoding="utf-8")
        (sflo_dir / "PM-FEEDBACK.md").write_text("Model F missing", encoding="utf-8")

        mode, text = build_context_map(2, str(sflo_dir))

        assert mode == "rebuild", (
            f"expected mode 'rebuild' with PM feedback, got {mode!r}"
        )
        assert "PM-FEEDBACK.md" in text, "context map should reference PM-FEEDBACK.md"

    def test_rebuild_mode_both_feedbacks(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        (sflo_dir / "SCOPE.md").write_text("scope", encoding="utf-8")
        (sflo_dir / "QA-FEEDBACK.md").write_text("qa issues", encoding="utf-8")
        (sflo_dir / "PM-FEEDBACK.md").write_text("pm issues", encoding="utf-8")

        mode, text = build_context_map(2, str(sflo_dir))

        assert mode == "rebuild", (
            f"expected mode 'rebuild' with both feedbacks, got {mode!r}"
        )
        assert "QA-FEEDBACK.md" in text, "context map should reference QA-FEEDBACK.md"
        assert "PM-FEEDBACK.md" in text, "context map should reference PM-FEEDBACK.md"

    def test_prior_artifacts_listed(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        (sflo_dir / "SCOPE.md").write_text("scope", encoding="utf-8")

        mode, text = build_context_map(2, str(sflo_dir))

        assert "Prior artifacts on disk:" in text, (
            "context map should list prior artifacts on disk"
        )
        assert "SCOPE.md" in text, (
            "context map should include SCOPE.md in prior artifacts"
        )

    def test_no_scope_on_disk(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()

        mode, text = build_context_map(2, str(sflo_dir))

        assert mode == "fresh", f"expected mode 'fresh' with no scope, got {mode!r}"
        assert "SCOPE.md" in text, (
            "context map should reference SCOPE.md path even if file doesn't exist"
        )
