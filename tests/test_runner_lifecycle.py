"""Runner lifecycle bookkeeping tests."""

import signal

import pytest

from src import runner
from src.state import read_state


def test_signal_handler_reports_terminal_signal_before_exit(tmp_path, monkeypatch):
    """Signal exits expose structured facts before os._exit terminates."""
    previous = signal.getsignal(signal.SIGTERM)
    calls = []

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(runner.os, "_exit", fake_exit)
    try:
        runner._install_signal_handler(
            str(tmp_path),
            on_signal_exit=lambda signum, name: calls.append((signum, name)),
        )
        handler = signal.getsignal(signal.SIGTERM)

        with pytest.raises(SystemExit) as exc:
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert exc.value.code == 128 + signal.SIGTERM
    assert calls == [(signal.SIGTERM, "SIGTERM")]


def test_work_breakdown_gate_skips_precise_and_s_scopes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner,
        "GATES",
        {
            1: {"role": "pm", "artifact": "SCOPE.md"},
            1.5: {"role": "decomposer", "artifact": "WORK-BREAKDOWN.md"},
            2: {"role": "dev", "artifact": "BUILD-STATUS.md"},
        },
    )
    sflo_dir = tmp_path / ".sflo"
    sflo_dir.mkdir()

    precise_state = {
        "current_state": "gate-1.5",
        "assignments": {"scope_tier": "precise"},
    }
    assert runner._work_breakdown_skip_reason(precise_state, str(sflo_dir)) == (
        "scout classified task as precise"
    )

    (sflo_dir / "SCOPE.md").write_text(
        "## Complexity Estimate\n\nS\n", encoding="utf-8"
    )
    small_state = {"current_state": "gate-1.5", "assignments": {}}
    assert runner._work_breakdown_skip_reason(small_state, str(sflo_dir)) == (
        "SCOPE.md complexity estimate is S"
    )

    (sflo_dir / "SCOPE.md").write_text(
        "## Complexity Estimate\n\nM\n", encoding="utf-8"
    )
    assert runner._work_breakdown_skip_reason(small_state, str(sflo_dir)) is None


def test_skip_current_gate_marks_state_and_advances_to_next_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner,
        "GATES",
        {
            1: {"role": "pm", "artifact": "SCOPE.md"},
            1.5: {"role": "decomposer", "artifact": "WORK-BREAKDOWN.md"},
            2: {"role": "dev", "artifact": "BUILD-STATUS.md"},
        },
    )
    sflo_dir = tmp_path / ".sflo"
    sflo_dir.mkdir()
    logs = []
    state = {
        "current_state": "gate-1.5",
        "gates": {"1.5": {"status": "waiting"}},
    }

    assert runner._skip_current_gate(
        state, str(sflo_dir), logs.append, "test skip"
    )

    assert state["gates"]["1.5"]["status"] == "skipped"
    assert state["current_state"] == "gate-2"
    assert read_state(str(sflo_dir))["current_state"] == "gate-2"
    assert logs == ["  Gate 1.5 skipped — test skip"]


def test_epic_iteration_runs_only_at_first_dev_gate_with_work_breakdown(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        runner,
        "GATES",
        {
            1: {"role": "pm", "artifact": "SCOPE.md"},
            1.5: {"role": "decomposer", "artifact": "WORK-BREAKDOWN.md"},
            2: {"role": "dev", "artifact": "BUILD-STATUS.md"},
            3: {"role": "qa", "artifact": "QA-REPORT.md"},
        },
    )
    sflo_dir = tmp_path / ".sflo"
    sflo_dir.mkdir()

    assert not runner._should_run_epic_iteration(
        {"current_state": "gate-1.5"}, str(sflo_dir)
    )
    assert not runner._should_run_epic_iteration(
        {"current_state": "gate-2"}, str(sflo_dir)
    )

    (sflo_dir / "WORK-BREAKDOWN.md").write_text(
        "## Epic E1\n\n### WP-1\n", encoding="utf-8"
    )
    assert runner._should_run_epic_iteration(
        {"current_state": "gate-2"}, str(sflo_dir)
    )
    assert not runner._should_run_epic_iteration(
        {
            "current_state": "gate-2",
            "epic_iteration": {"completed": ["E1"], "total_epics": 1},
        },
        str(sflo_dir),
    )
    assert runner._should_run_epic_iteration(
        {
            "current_state": "gate-2",
            "epic_iteration": {
                "active": True,
                "completed": ["E1"],
                "total_epics": 1,
            },
        },
        str(sflo_dir),
    )
