"""Runner CLI runtime selection behavior."""

import sys

import pytest

from src import runner


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
