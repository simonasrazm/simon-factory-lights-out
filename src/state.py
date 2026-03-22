"""SFLO state management — read, write, lock state.json."""

import json
import os
from datetime import datetime, timezone

from .constants import GATES, S_SCOUT


def state_path(sflo_dir):
    return os.path.join(sflo_dir, "state.json")


def _lock_path(sflo_dir):
    return os.path.join(sflo_dir, "state.lock")


def acquire_lock(sflo_dir):
    """Acquire a file-based lock. Returns lock file descriptor."""
    lock = _lock_path(sflo_dir)
    os.makedirs(sflo_dir, exist_ok=True)
    for attempt in range(50):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            return fd
        except FileExistsError:
            import time
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
        "gates": {str(g): {"status": "waiting", "artifact": info["artifact"]}
                  for g, info in GATES.items()},
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
