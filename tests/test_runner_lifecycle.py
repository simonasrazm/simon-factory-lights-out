"""Runner lifecycle bookkeeping tests."""

import signal

import pytest

from src import runner


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
