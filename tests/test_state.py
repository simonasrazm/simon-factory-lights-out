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
    acquire_instance_lock,
    release_instance_lock,
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
        assert os.path.exists(lock_path), (
            f"lock file should exist at {lock_path} after acquire"
        )
        release_lock(sflo_dir, fd)

    def test_release_removes_lockfile(self, tmp_path):
        sflo_dir = str(tmp_path)
        fd = acquire_lock(sflo_dir)
        release_lock(sflo_dir, fd)
        lock_path = os.path.join(sflo_dir, "state.lock")
        assert not os.path.exists(lock_path), (
            f"lock file should be removed after release at {lock_path}"
        )

    def test_lock_prevents_concurrent_open(self, tmp_path):
        """While lock is held, the lock file exists and cannot be re-created."""
        sflo_dir = str(tmp_path)
        fd = acquire_lock(sflo_dir)
        try:
            lock_path = os.path.join(sflo_dir, "state.lock")
            # Lock file must exist
            assert os.path.exists(lock_path), (
                f"lock file should exist while lock is held at {lock_path}"
            )
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
        assert state2 is not None, (
            "read_state should return valid state after _locked_write_state"
        )
        assert state2["current_state"] == "scout", (
            f"expected current_state 'scout', got {state2['current_state']!r}"
        )

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
        assert final["current_state"] in ("gate-1", "gate-2"), (
            f"expected current_state in ('gate-1', 'gate-2'), got {final['current_state']!r}"
        )

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
            f"runner.py calls bare write_state() outside _locked_write_state "
            f"({len(violations)} violation(s)):\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Unit: acquire_instance_lock / release_instance_lock — one runner per dir
# ---------------------------------------------------------------------------


class TestInstanceLock:
    """Process-lifetime instance lock — at most one runner per state dir."""

    def test_acquire_creates_pidfile(self, tmp_path):
        sflo_dir = str(tmp_path)
        fd = acquire_instance_lock(sflo_dir)
        try:
            pidfile = os.path.join(sflo_dir, "runner.pid")
            assert os.path.exists(pidfile), "instance lock pidfile should exist"
            with open(pidfile) as f:
                assert f.read().strip() == str(os.getpid())
        finally:
            release_instance_lock(sflo_dir, fd)

    def test_release_removes_pidfile(self, tmp_path):
        sflo_dir = str(tmp_path)
        fd = acquire_instance_lock(sflo_dir)
        release_instance_lock(sflo_dir, fd)
        assert not os.path.exists(os.path.join(sflo_dir, "runner.pid")), (
            "instance lock pidfile should be gone after release"
        )

    def test_live_holder_blocks_second_runner(self, tmp_path):
        """A second runner must fail fast while a live runner holds the lock."""
        import subprocess

        sflo_dir = str(tmp_path)
        os.makedirs(sflo_dir, exist_ok=True)
        # Simulate another live runner with a real, alive, non-self PID — a
        # sleeper subprocess we own for the test (deterministic, unlike the
        # pytest parent PID, which is not guaranteed to outlive the test).
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        try:
            with open(os.path.join(sflo_dir, "runner.pid"), "w") as f:
                f.write(str(holder.pid))
            with pytest.raises(RuntimeError, match="already running"):
                acquire_instance_lock(sflo_dir)
        finally:
            holder.terminate()
            holder.wait()

    def test_stale_lock_reclaimed(self, tmp_path):
        """A pidfile left by a dead runner is reclaimed, not treated as conflict."""
        import subprocess

        sflo_dir = str(tmp_path)
        os.makedirs(sflo_dir, exist_ok=True)
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()  # PID is now dead
        with open(os.path.join(sflo_dir, "runner.pid"), "w") as f:
            f.write(str(dead.pid))
        fd = acquire_instance_lock(sflo_dir)  # must reclaim, not raise
        try:
            with open(os.path.join(sflo_dir, "runner.pid")) as f:
                assert f.read().strip() == str(os.getpid())
        finally:
            release_instance_lock(sflo_dir, fd)
