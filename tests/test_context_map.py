"""Tests for build_context_map and feedback flow in machine.py."""

from src.machine import build_context_map


class TestBuildContextMap:
    def test_fresh_mode_no_feedback(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        (sflo_dir / "SCOPE.md").write_text("scope content")

        mode, text = build_context_map(2, str(sflo_dir))

        assert mode == "fresh"
        assert "Mode: fresh" in text
        assert "SCOPE.md" in text

    def test_rebuild_mode_qa_feedback_only(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        (sflo_dir / "SCOPE.md").write_text("scope")
        (sflo_dir / "QA-FEEDBACK.md").write_text("fix BenchmarkPanel.jsx")

        mode, text = build_context_map(2, str(sflo_dir))

        assert mode == "rebuild"
        assert "Mode: rebuild" in text
        assert "QA-FEEDBACK.md" in text
        assert "read only if you need AC details" in text

    def test_rebuild_mode_pm_feedback_only(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        (sflo_dir / "SCOPE.md").write_text("scope")
        (sflo_dir / "PM-FEEDBACK.md").write_text("Model F missing")

        mode, text = build_context_map(2, str(sflo_dir))

        assert mode == "rebuild"
        assert "PM-FEEDBACK.md" in text

    def test_rebuild_mode_both_feedbacks(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        (sflo_dir / "SCOPE.md").write_text("scope")
        (sflo_dir / "QA-FEEDBACK.md").write_text("qa issues")
        (sflo_dir / "PM-FEEDBACK.md").write_text("pm issues")

        mode, text = build_context_map(2, str(sflo_dir))

        assert mode == "rebuild"
        assert "QA-FEEDBACK.md" in text
        assert "PM-FEEDBACK.md" in text

    def test_prior_artifacts_listed(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()
        (sflo_dir / "SCOPE.md").write_text("scope")

        mode, text = build_context_map(2, str(sflo_dir))

        assert "Prior artifacts on disk:" in text
        assert "SCOPE.md" in text

    def test_no_scope_on_disk(self, tmp_path):
        sflo_dir = tmp_path / ".sflo"
        sflo_dir.mkdir()

        mode, text = build_context_map(2, str(sflo_dir))

        assert mode == "fresh"
        assert "SCOPE.md" in text  # path is always listed, even if file doesn't exist
