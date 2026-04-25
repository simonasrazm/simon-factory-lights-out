"""SFLO state management — read, write, lock state.json."""

import json
import os
import time
from datetime import datetime, timezone

from .constants import GATES, S_SCOUT


def state_path(sflo_dir):
    return os.path.join(sflo_dir, "state.json")


def _lock_path(sflo_dir):
    return os.path.join(sflo_dir, "state.lock")


def _is_pid_alive(pid):
    """Return True if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(sflo_dir):
    """Acquire a file-based lock. Returns lock file descriptor.

    Stale-lock recovery: if the lock file exists but the PID written inside it
    is no longer alive AND the lock is older than 60 seconds, the lock is
    considered stale and removed before retrying.
    """
    lock = _lock_path(sflo_dir)
    os.makedirs(sflo_dir, exist_ok=True)
    for attempt in range(50):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            # Write current PID so future holders can check liveness
            try:
                os.write(fd, str(os.getpid()).encode())
            except OSError:
                pass
            return fd
        except FileExistsError:
            # Check if lock is stale: PID dead AND mtime > 60s ago
            try:
                stat = os.stat(lock)
                age = time.time() - stat.st_mtime
                if age > 60:
                    try:
                        with open(lock, "r") as _f:
                            pid_str = _f.read().strip()
                        lock_pid = int(pid_str) if pid_str else None
                    except (OSError, ValueError):
                        lock_pid = None
                    if lock_pid is None or not _is_pid_alive(lock_pid):
                        try:
                            os.remove(lock)
                        except OSError:
                            pass
                        continue  # retry immediately
            except OSError:
                pass
            time.sleep(0.1)
    raise RuntimeError(f"Could not acquire lock: {lock}")


def release_lock(sflo_dir, fd):
    """Release the file-based lock."""
    os.close(fd)
    try:
        os.remove(_lock_path(sflo_dir))
    except OSError:
        pass


def read_state(sflo_dir):
    p = state_path(sflo_dir)
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def write_state(sflo_dir, state):
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    p = state_path(sflo_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, p)


def make_initial_state(bindings):
    return {
        "current_state": S_SCOUT,
        "bindings": bindings,
        "assignments": {},
        "inner_loops": 0,
        "outer_loops": 0,
        "gates": {
            str(g): {"status": "waiting", "artifact": info["artifact"]}
            for g, info in GATES.items()
        },
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
