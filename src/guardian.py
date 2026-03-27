"""SFLO Guardian — independent safety layer (opt-in via pipeline.yaml).

Three mechanisms:
1. Circuit Breaker - detects stalled progress (same gate failing repeatedly)
2. Wall-Clock Budget - absolute time limit
3. Resource Budget - maximum agent spawns
"""

import json
import os
import time
from typing import Optional

from .constants import GUARDIAN_CONFIG


def _is_enabled() -> bool:
    return GUARDIAN_CONFIG.get("enabled", False)


def guardian_path(sflo_dir: str) -> str:
    """Return path to the guardian state file."""
    return os.path.join(sflo_dir, "guardian.json")


def read_guardian(sflo_dir: str) -> dict:
    """Read guardian state from disk. Returns empty dict if not found."""
    p = guardian_path(sflo_dir)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def write_guardian(sflo_dir: str, state: dict) -> None:
    """Write guardian state to disk."""
    p = guardian_path(sflo_dir)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def init_guardian(sflo_dir: str) -> dict:
    """Initialize guardian state. No-op if disabled.

    Returns the initial state dict (or empty dict if disabled).
    """
    if not _is_enabled():
        return {}

    state = {
        "enabled": True,
        "started_at": time.time(),
        "spawn_count": 0,
        "gate_failures": {},  # gate_num (str) -> consecutive failure count
    }
    write_guardian(sflo_dir, state)
    return state


def record_spawn(sflo_dir: str) -> Optional[str]:
    """Record an agent spawn. Returns a trip reason string if budget exceeded, else None.

    No-op (returns None) when guardian is disabled.
    """
    if not _is_enabled():
        return None

    max_spawns = GUARDIAN_CONFIG.get("max_spawns", 50)
    state = read_guardian(sflo_dir)
    if not state:
        return None

    state["spawn_count"] = state.get("spawn_count", 0) + 1
    write_guardian(sflo_dir, state)

    if state["spawn_count"] > max_spawns:
        return (
            f"Guardian: spawn budget exceeded "
            f"({state['spawn_count']} spawns > max {max_spawns}). "
            f"Human decision needed."
        )
    return None


def record_gate_failure(sflo_dir: str, gate_num) -> Optional[str]:
    """Record a gate failure for circuit-breaker purposes.

    Returns a trip reason string if circuit breaker fires, else None.
    No-op (returns None) when guardian is disabled.
    """
    if not _is_enabled():
        return None

    window = GUARDIAN_CONFIG.get("circuit_breaker_window", 5)
    state = read_guardian(sflo_dir)
    if not state:
        return None

    key = str(gate_num)
    failures = state.get("gate_failures", {})
    failures[key] = failures.get(key, 0) + 1
    state["gate_failures"] = failures
    write_guardian(sflo_dir, state)

    if failures[key] >= window:
        return (
            f"Guardian: circuit breaker tripped at gate {gate_num} "
            f"({failures[key]} consecutive failures >= window {window}). "
            f"Human decision needed."
        )
    return None


def check_time_budget(sflo_dir: str) -> Optional[str]:
    """Check if wall-clock budget has been exceeded.

    Returns a trip reason string if exceeded, else None.
    No-op (returns None) when guardian is disabled.
    """
    if not _is_enabled():
        return None

    wall_clock_s = GUARDIAN_CONFIG.get("wall_clock_s", 7200)
    state = read_guardian(sflo_dir)
    if not state:
        return None

    started_at = state.get("started_at")
    if started_at is None:
        return None

    elapsed = time.time() - started_at
    if elapsed > wall_clock_s:
        return (
            f"Guardian: wall-clock budget exceeded "
            f"({int(elapsed)}s > {wall_clock_s}s). "
            f"Human decision needed."
        )
    return None


def guardian_check(sflo_dir: str) -> Optional[str]:
    """Run all guardian checks. Returns trip reason if any check fires, else None.

    No-op (returns None) when guardian is disabled.
    """
    if not _is_enabled():
        return None

    # Check time budget
    trip = check_time_budget(sflo_dir)
    if trip:
        return trip

    # Check spawn budget
    state = read_guardian(sflo_dir)
    if state:
        max_spawns = GUARDIAN_CONFIG.get("max_spawns", 50)
        spawn_count = state.get("spawn_count", 0)
        if spawn_count > max_spawns:
            return (
                f"Guardian: spawn budget exceeded "
                f"({spawn_count} spawns > max {max_spawns}). "
                f"Human decision needed."
            )

    return None


def guardian_status(sflo_dir: str) -> dict:
    """Return a status summary dict for the guardian.

    Always returns a dict (with enabled=False when disabled).
    """
    if not _is_enabled():
        return {"enabled": False}

    state = read_guardian(sflo_dir)
    if not state:
        return {"enabled": True, "initialized": False}

    wall_clock_s = GUARDIAN_CONFIG.get("wall_clock_s", 7200)
    max_spawns = GUARDIAN_CONFIG.get("max_spawns", 50)
    started_at = state.get("started_at")
    elapsed = time.time() - started_at if started_at else 0

    return {
        "enabled": True,
        "initialized": True,
        "spawn_count": state.get("spawn_count", 0),
        "max_spawns": max_spawns,
        "elapsed_s": int(elapsed),
        "wall_clock_s": wall_clock_s,
        "gate_failures": state.get("gate_failures", {}),
        "circuit_breaker_window": GUARDIAN_CONFIG.get("circuit_breaker_window", 5),
    }
