"""Tests for CRITICAL 2 — state.json file-lock concurrent write safety.

Verifies that runner._locked_write_state serialises concurrent writes
so that no corruption occurs when runner and stop-hook both write state.json
at the same time.
"""

import os
import re
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.state import (
    read_state,
    acquire_lock,
    release_lock,
    make_initial_state,
)


# ---------------------------------------------------------------------------
# Unit: acquire_lock / release_lock basics
# ---------------------------------------------------------------------------


class TestLockAcquireRelease:
    def test_acquire_creates_lockfile(self, tmp_path):
        sflo_dir = str(tmp_path)
        fd = acquire_lock(sflo_dir)
        lock_path = os.path.join(sflo_dir, "state.lock")
        assert os.path.exists(lock_path)
        release_lock(sflo_dir, fd)

    def test_release_removes_lockfile(self, tmp_path):
        sflo_dir = str(tmp_path)
        fd = acquire_lock(sflo_dir)
        release_lock(sflo_dir, fd)
        lock_path = os.path.join(sflo_dir, "state.lock")
        assert not os.path.exists(lock_path)

    def test_lock_prevents_concurrent_open(self, tmp_path):
        """While lock is held, the lock file exists and cannot be re-created."""
        sflo_dir = str(tmp_path)
        fd = acquire_lock(sflo_dir)
        try:
            lock_path = os.path.join(sflo_dir, "state.lock")
            # Lock file must exist
            assert os.path.exists(lock_path)
            # Attempt to open with O_EXCL should fail
            with pytest.raises(FileExistsError):
                os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        finally:
            release_lock(sflo_dir, fd)


# ---------------------------------------------------------------------------
# Integration: _locked_write_state — no corruption under concurrency
# ---------------------------------------------------------------------------


def _get_locked_write_state():
    """Import and return runner._locked_write_state."""
    import src.runner as runner_mod

    return runner_mod._locked_write_state


class TestLockedWriteState:
    """CRITICAL 2 regression: concurrent writes must not corrupt state.json."""

    def test_single_write_readable(self, tmp_path):
        sflo_dir = str(tmp_path)
        _locked_write_state = _get_locked_write_state()

        state = make_initial_state({"pm": "a", "dev": "b", "qa": "c"})
        _locked_write_state(sflo_dir, state)

        state2 = read_state(sflo_dir)
        assert state2 is not None
        assert state2["current_state"] == "scout"

    def test_concurrent_writes_produce_valid_json(self, tmp_path):
        """Two threads writing state concurrently must not produce truncated JSON."""
        sflo_dir = str(tmp_path)
        _locked_write_state = _get_locked_write_state()

        errors = []

        def writer(value, count):
            for _ in range(count):
                s = make_initial_state({"pm": "a", "dev": "b", "qa": "c"})
                s["current_state"] = value
                try:
                    _locked_write_state(sflo_dir, s)
                except Exception as e:
                    errors.append(str(e))

        t1 = threading.Thread(target=writer, args=("gate-1", 8))
        t2 = threading.Thread(target=writer, args=("gate-2", 8))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Errors during concurrent writes: {errors}"

        # Final state must be parseable JSON
        final = read_state(sflo_dir)
        assert final is not None, "state.json corrupted (not parseable)"
        assert final["current_state"] in ("gate-1", "gate-2")

    def test_runner_uses_locked_write_not_bare(self):
        """Verify runner.py only calls write_state inside _locked_write_state."""
        import inspect
        import src.runner as runner_mod

        source = inspect.getsource(runner_mod)
        lines = source.splitlines()

        inside_locked_fn = False
        violations = []
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if "def _locked_write_state" in stripped:
                inside_locked_fn = True
                continue
            # Exit fn scope when we hit next def at same/lesser indentation
            if (
                inside_locked_fn
                and stripped.startswith("def ")
                and not line.startswith("    ")
            ):
                inside_locked_fn = False
            # Match bare write_state( but not _locked_write_state(
            if (
                re.search(r"(?<![_a-zA-Z])write_state\(", stripped)
                and not inside_locked_fn
            ):
                if not stripped.startswith("#"):
                    violations.append(f"line {lineno}: {line.rstrip()}")

        assert not violations, (
            "runner.py calls bare write_state() outside _locked_write_state:\n"
            + "\n".join(violations)
        )
