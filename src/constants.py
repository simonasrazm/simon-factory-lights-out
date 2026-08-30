"""SFLO pipeline constants — grades, limits, state names, and config-loaded gates."""

import os
import shutil
import sys

try:
    from .config import load_pipeline_config, GRADE_MAP  # noqa: F401 — re-export
except ImportError:  # Support legacy top-level imports from sflo/src on sys.path.
    from config import load_pipeline_config, GRADE_MAP  # noqa: F401

# Root of the sflo repo.
# SFLO_ROOT env var (set by host app to vault path) takes precedence.
# Falls back to __file__-derived path for CLI / dev use.
SFLO_ROOT = os.environ.get("SFLO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

_config = load_pipeline_config()

GATES = _config["gates"]
GRADE_THRESHOLD = _config["grade_threshold"]
SCOUT_CONFIG = _config.get("scout", {})
SFLO_CONFIG = _config.get("sflo", {})

INNER_LOOP_MAX = 10
OUTER_LOOP_MAX = 10


def inner_loop_gate_key():
    """Return gate key for the inner-loop (QA/security) gate.

    Position: third-from-last in sorted gate order.
    Returns None if pipeline has fewer than 3 gates.
    """
    keys = sorted(GATES.keys())
    return keys[-3] if len(keys) >= 3 else None


def outer_loop_gate_key():
    """Return gate key for the outer-loop (PM verify) gate.

    Position: second-from-last in sorted gate order.
    Returns None if pipeline has fewer than 2 gates.
    """
    keys = sorted(GATES.keys())
    return keys[-2] if len(keys) >= 2 else None

# Derived from GATES — auto-syncs with pipeline.yaml, no manual maintenance.
# "extra" and "sflo-dir" are internal tokens used by scaffold assign CLI.
KNOWN_ROLES = {
    e.get("role")
    for g in GATES.values()
    for e in (g if isinstance(g, list) else [g])
    if e.get("role")
} | {"extra", "sflo-dir"}


def reload_pipeline_config(sflo_dir=None):
    """Reload project config after the caller knows its state directory.

    Mutable mappings update in place so modules that imported them earlier
    observe the new project gates. Scalar threshold consumers read through
    ``src.constants`` at validation time.
    """
    global GRADE_THRESHOLD

    cfg = load_pipeline_config(sflo_dir=sflo_dir)
    GATES.clear()
    GATES.update(cfg["gates"])
    SCOUT_CONFIG.clear()
    SCOUT_CONFIG.update(cfg.get("scout", {}))
    SFLO_CONFIG.clear()
    SFLO_CONFIG.update(cfg.get("sflo", {}))
    GRADE_THRESHOLD = cfg["grade_threshold"]

    KNOWN_ROLES.clear()
    KNOWN_ROLES.update(
        e.get("role")
        for gate in GATES.values()
        for e in (gate if isinstance(gate, list) else [gate])
        if e.get("role")
    )
    KNOWN_ROLES.update({"extra", "sflo-dir"})

    from .config import resolve_pipeline_path

    return resolve_pipeline_path(sflo_dir=sflo_dir) or "<built-in defaults>"


def _detect_python():
    """Return the python command available on this system."""
    if shutil.which("python3"):
        return "python3"
    if shutil.which("python"):
        return "python"
    return sys.executable or "python3"


PYTHON_CMD = _detect_python()

S_INIT = "init"
S_SCOUT = "scout"
S_ASSIGN = "assign"
S_ESCALATE = "escalate"
S_DONE = "done"
