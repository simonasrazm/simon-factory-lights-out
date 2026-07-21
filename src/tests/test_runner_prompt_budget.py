"""Tests for runner prompt assembly budget guards."""

from __future__ import annotations

import sys
from pathlib import Path


_SFLO_DIR = Path(__file__).parent.parent.parent
if str(_SFLO_DIR) not in sys.path:
    sys.path.insert(0, str(_SFLO_DIR))


from src.runner import (  # noqa: E402
    _apply_runtime_spawn_kwargs,
    _archivable_paths,
    format_prior_artifacts_for_prompt,
)


def test_cursor_spawn_uses_target_project_as_workspace(tmp_path):
    project = tmp_path / "target-project"
    project.mkdir()
    kwargs = {}

    _apply_runtime_spawn_kwargs(kwargs, "cursor", output_dir=str(project))

    assert kwargs["workspace"] == str(project.resolve())


def test_cursor_spawn_falls_back_to_invocation_workspace(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    kwargs = {}

    _apply_runtime_spawn_kwargs(kwargs, "cursor", output_dir=None)

    assert kwargs["workspace"] == str(tmp_path.resolve())


def test_security_report_is_rotated_with_other_stale_artifacts(tmp_path):
    sflo_dir = tmp_path / ".sflo"
    sflo_dir.mkdir()

    paths = _archivable_paths(str(sflo_dir))

    assert str(sflo_dir / "SECURITY-REPORT.md") in paths


def test_prior_artifact_prompt_is_capped_per_file(tmp_path):
    huge = tmp_path / "DEVLOOP-REPORT.md"
    huge.write_text("a" * 2_000, encoding="utf-8")

    prompt = format_prior_artifacts_for_prompt(
        [str(huge)],
        per_file_max_chars=100,
        total_max_chars=500,
    )

    assert "## DEVLOOP-REPORT.md" in prompt
    assert "[TRUNCATED: DEVLOOP-REPORT.md exceeds 100 prompt characters" in prompt
    assert len(prompt) < 300


def test_prior_artifact_prompt_is_capped_overall(tmp_path):
    first = tmp_path / "BUILD-STATUS.md"
    second = tmp_path / "QA-REPORT.md"
    first.write_text("a" * 120, encoding="utf-8")
    second.write_text("b" * 120, encoding="utf-8")

    prompt = format_prior_artifacts_for_prompt(
        [str(first), str(second)],
        per_file_max_chars=50,
        total_max_chars=160,
    )

    assert "## BUILD-STATUS.md" in prompt
    assert "[TRUNCATED: QA-REPORT.md exceeds" in prompt
    assert len(prompt) < 350
