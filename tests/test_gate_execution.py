"""Tests for shared custom gate execution."""

import asyncio

import pytest

from src.gate_execution import execute_custom_gate, load_custom_runner


def test_load_custom_runner_rejects_missing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    module, error = load_custom_runner("tools/missing.py")

    assert module is None
    assert "Runner file not found" in error


def test_execute_custom_gate_runs_sync_runner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "custom.py").write_text(
        "from pathlib import Path\n\n"
        "def run_gate(gate, sflo_dir, output_dir):\n"
        "    Path(sflo_dir, gate['artifact']).write_text('sync ok', encoding='utf-8')\n",
        encoding="utf-8",
    )
    sflo_dir = tmp_path / ".sflo"
    sflo_dir.mkdir()

    outcome = asyncio.run(
        execute_custom_gate(
            gate_num=2.5,
            runner_path="tools/custom.py",
            gate_config={"artifact": "CUSTOM.md"},
            sflo_dir=str(sflo_dir),
            output_dir=str(tmp_path),
            log=lambda msg: None,
        )
    )

    assert outcome.ok is True
    assert (sflo_dir / "CUSTOM.md").read_text(encoding="utf-8") == "sync ok"


def test_execute_custom_gate_runs_async_runner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "custom.py").write_text(
        "from pathlib import Path\n\n"
        "async def run_gate(gate, sflo_dir, output_dir):\n"
        "    Path(sflo_dir, gate['artifact']).write_text('async ok', encoding='utf-8')\n",
        encoding="utf-8",
    )
    sflo_dir = tmp_path / ".sflo"
    sflo_dir.mkdir()

    outcome = asyncio.run(
        execute_custom_gate(
            gate_num=2.5,
            runner_path="tools/custom.py",
            gate_config={"artifact": "CUSTOM.md"},
            sflo_dir=str(sflo_dir),
            output_dir=str(tmp_path),
            log=lambda msg: None,
        )
    )

    assert outcome.ok is True
    assert (sflo_dir / "CUSTOM.md").read_text(encoding="utf-8") == "async ok"


@pytest.mark.parametrize(
    "runner_body",
    [
        "",
        "def run_gate(gate, sflo_dir, output_dir):\n"
        "    raise RuntimeError('boom')\n",
    ],
)
def test_execute_custom_gate_writes_degraded_artifact_on_runner_failure(
    tmp_path, monkeypatch, runner_body
):
    monkeypatch.chdir(tmp_path)
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "custom.py").write_text(runner_body, encoding="utf-8")
    sflo_dir = tmp_path / ".sflo"
    sflo_dir.mkdir()

    outcome = asyncio.run(
        execute_custom_gate(
            gate_num=2.5,
            runner_path="tools/custom.py",
            gate_config={"artifact": "CUSTOM.md"},
            sflo_dir=str(sflo_dir),
            output_dir=str(tmp_path),
            log=lambda msg: None,
        )
    )

    report = (sflo_dir / "CUSTOM.md").read_text(encoding="utf-8")
    assert outcome.ok is False
    assert "Verdict: DEGRADED" in report
