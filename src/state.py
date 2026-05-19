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
    """Return True if a process with the given PID is running.

    POSIX uses signal 0 — a pure existence probe. On Windows os.kill(pid, 0)
    would TERMINATE the process (it maps to TerminateProcess), so query a
    process handle there instead — never send a signal.
    """
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return bool(ok) and exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_lock_pid(path):
    """Read the PID recorded in a lock file.

    Returns the PID as an int, or None when the file is missing, empty, or
    does not contain a valid integer.
    """
    try:
        with open(path, "r") as f:
            pid_str = f.read().strip()
        return int(pid_str) if pid_str else None
    except (OSError, ValueError):
        return None


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
                    lock_pid = _read_lock_pid(lock)
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


def _instance_lock_path(sflo_dir):
    return os.path.join(sflo_dir, "runner.pid")


def acquire_instance_lock(sflo_dir):
    """Acquire a process-lifetime lock — at most one runner per state dir.

    acquire_lock() above is a brief lock around a single state.json write.
    This lock is different: it is held for the WHOLE run. Two runners sharing
    one .sflo/ interleave read-modify-write cycles on state.json and corrupt
    it, so if a live runner already holds this lock we raise immediately
    rather than waiting. A lock left by a dead process (crash) is reclaimed.

    Returns a file descriptor — pass it to release_instance_lock().
    """
    path = _instance_lock_path(sflo_dir)
    os.makedirs(sflo_dir, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = _read_lock_pid(path)
        if holder and holder != os.getpid() and _is_pid_alive(holder):
            raise RuntimeError(
                f"Another SFLO runner (PID {holder}) is already running in "
                f"{sflo_dir}. Wait for it to finish, or delete {path} if you "
                f"are certain that process is gone."
            )
        # Stale lock — dead PID, unreadable content, or our own. Reclaim it.
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(
                f"Another SFLO runner just started in {sflo_dir}. "
                f"Only one runner may run per state directory."
            ) from None
    try:
        os.write(fd, str(os.getpid()).encode())
    except OSError:
        pass
    return fd


def release_instance_lock(sflo_dir, fd):
    """Release the process-lifetime instance lock (see acquire_instance_lock)."""
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.remove(_instance_lock_path(sflo_dir))
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


def make_initial_state(roles):
    return {
        "current_state": S_SCOUT,
        "roles": roles,
        "assignments": {},
        "inner_loops": 0,
        "outer_loops": 0,
        "gates": {
            str(g): {
                "status": "waiting",
                "artifact": (
                    info[0].get("artifact", f"gate-{g}") if info else f"gate-{g}"
                )
                if isinstance(info, list)
                else info.get("artifact", f"gate-{g}"),
                "parallel_artifacts": (
                    [e.get("artifact", f"gate-{g}-{i}") for i, e in enumerate(info)]
                    if info
                    else []
                )
                if isinstance(info, list)
                else None,
            }
            for g, info in GATES.items()
        },
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
