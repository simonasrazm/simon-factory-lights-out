"""Runner CLI runtime selection behavior."""

import sys
import json
from pathlib import Path

import pytest

from src import runner
from src import state as state_module


def _run_cli_with_captured_pipeline(tmp_path, monkeypatch, argv):
    captured = {}

    async def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        return {"state": "done"}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(runner, "_install_signal_handler", lambda *_a, **_kw: None)
    monkeypatch.setattr(runner.atexit, "register", lambda *_a, **_kw: None)
    monkeypatch.setattr(state_module, "acquire_instance_lock", lambda *_a: object())
    monkeypatch.setattr(
        state_module, "release_instance_lock", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(sys, "argv", ["runner.py", *argv])

    runner.main()
    return captured


def test_runner_cli_requires_runtime_for_pipeline_start(tmp_path, monkeypatch, capsys):
    """Starting a pipeline without --runtime fails before run_pipeline."""
    called = False

    async def fake_run_pipeline(**_kwargs):
        nonlocal called
        called = True
        return {"state": "SHIPPED"}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(runner, "_install_signal_handler", lambda *_args, **_kw: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner.py", "build a click counter", "--sflo-dir", str(tmp_path / ".sflo")],
    )

    with pytest.raises(SystemExit) as excinfo:
        runner.main()

    assert excinfo.value.code == 2
    assert called is False
    assert "--runtime is required" in capsys.readouterr().err
    assert not (tmp_path / ".sflo").exists()


def test_runner_cli_list_does_not_require_runtime(tmp_path, monkeypatch, capsys):
    """Registry-only commands do not need a runtime adapter."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner.py", "--list", "--sflo-dir", str(tmp_path / ".sflo")],
    )

    runner.main()

    out = capsys.readouterr().out
    assert "No factories registered." in out


@pytest.mark.parametrize("factory_args", [[], ["--factory", "smoke-test"]])
def test_runner_cli_uses_invocation_directory_for_deliverables(
    tmp_path, monkeypatch, factory_args
):
    captured = _run_cli_with_captured_pipeline(
        tmp_path,
        monkeypatch,
        ["build hello files", "--runtime", "cursor", *factory_args],
    )

    assert captured["output_dir"] == str(tmp_path.resolve())


def test_runner_cli_accepts_explicit_output_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()

    captured = _run_cli_with_captured_pipeline(
        tmp_path,
        monkeypatch,
        [
            "build hello files",
            "--runtime",
            "cursor",
            "--output-dir",
            str(project),
        ],
    )

    assert captured["output_dir"] == str(project.resolve())


def test_runner_cli_rejects_output_directory_inside_factory_state(
    tmp_path, monkeypatch
):
    state_output = tmp_path / ".sflo" / "user-output"
    state_output.mkdir(parents=True)

    with pytest.raises(SystemExit) as excinfo:
        _run_cli_with_captured_pipeline(
            tmp_path,
            monkeypatch,
            [
                "build hello files",
                "--runtime",
                "cursor",
                "--output-dir",
                str(state_output),
            ],
        )

    assert excinfo.value.code == 2


def test_runner_cli_resume_reuses_factory_output_directory(tmp_path, monkeypatch):
    project = tmp_path / "original-project"
    project.mkdir()
    caller = tmp_path / "different-caller"
    caller.mkdir()
    sflo_parent = tmp_path / "state"
    factory = sflo_parent / "smoke-test"
    factory.mkdir(parents=True)
    (factory / "state.json").write_text(
        json.dumps(
            {
                "current_state": "gate-2",
                "prompt": "build hello files",
                "output_dir": str(project.resolve()),
            }
        ),
        encoding="utf-8",
    )

    captured = _run_cli_with_captured_pipeline(
        caller,
        monkeypatch,
        [
            "continue",
            "--runtime",
            "cursor",
            "--resume",
            "smoke-test",
            "--sflo-dir",
            str(sflo_parent),
        ],
    )

    assert captured["output_dir"] == str(project.resolve())


def test_runner_cli_resume_allows_explicit_output_override(tmp_path, monkeypatch):
    original = tmp_path / "original-project"
    original.mkdir()
    replacement = tmp_path / "replacement-project"
    replacement.mkdir()
    sflo_parent = tmp_path / "state"
    factory = sflo_parent / "smoke-test"
    factory.mkdir(parents=True)
    (factory / "state.json").write_text(
        json.dumps(
            {
                "current_state": "gate-2",
                "prompt": "build hello files",
                "output_dir": str(original.resolve()),
            }
        ),
        encoding="utf-8",
    )

    captured = _run_cli_with_captured_pipeline(
        tmp_path,
        monkeypatch,
        [
            "continue",
            "--runtime",
            "cursor",
            "--resume",
            "smoke-test",
            "--sflo-dir",
            str(sflo_parent),
            "--output-dir",
            str(replacement),
        ],
    )

    assert captured["output_dir"] == str(replacement.resolve())


def test_generic_runner_docs_do_not_default_to_codex():
    """Generic runner instructions require caller-selected runtime."""
    root = Path(__file__).resolve().parents[1]

    for rel in ("README.md", "sflo.md", "src/runner.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "--runtime codex" not in text, rel

    assert "--runtime <runtime>" in (root / "sflo.md").read_text(encoding="utf-8")
    skill = (root / "skill/SKILL.md").read_text(encoding="utf-8")
    assert "--runtime {{SFLO_RUNTIME}}" in skill
