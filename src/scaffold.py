#!/usr/bin/env python3
"""SFLO Pipeline Scaffold — CLI entry point.

The scaffold IS the pipeline authority. This file is the CLI interface;
logic lives in separate modules (machine, validate, state, etc.).

Usage:
    python3 sflo/src/scaffold.py init [--sflo-dir PATH]
    python3 sflo/src/scaffold.py assign --pm PATH --dev PATH --qa PATH [--extra role=path ...]
    python3 sflo/src/scaffold.py next [--sflo-dir PATH]
    python3 sflo/src/scaffold.py prompt [--sflo-dir PATH]
    python3 sflo/src/scaffold.py status [--sflo-dir PATH]
    python3 sflo/src/scaffold.py clean [--sflo-dir PATH]
"""

import sys
import os
import json

# Allow running as script (python3 sflo/src/scaffold.py) or as module
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.constants import KNOWN_ROLES, GATES as _SCAFFOLD_GATES
    from src.config import derive_roles_from_pipeline
    from src.state import (
        acquire_lock,
        release_lock,
        read_state,
        write_state,
        make_initial_state,
    )
    from src.validate import validate_agent_path, read_artifact, extract_field
    from src.machine import auto_transition, compute_next, apply_transition
    from src.prompt import format_prompt
    from src.archive import archive_to_logs
else:
    from .constants import KNOWN_ROLES, GATES as _SCAFFOLD_GATES
    from .config import derive_roles_from_pipeline
    from .state import (
        acquire_lock,
        release_lock,
        read_state,
        write_state,
        make_initial_state,
    )
    from .validate import validate_agent_path, read_artifact, extract_field
    from .machine import auto_transition, compute_next, apply_transition
    from .prompt import format_prompt
    from .archive import archive_to_logs


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(args, known_flags=None):
    """Parse CLI arguments. Returns (sflo_dir, flags_dict, unknown_args)."""
    if known_flags is None:
        known_flags = set()
    known_flags.add("sflo-dir")

    sflo_dir = ".sflo"
    flags = {}
    unknown = []

    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            flag = args[i][2:]
            if flag in known_flags:
                flags[flag] = args[i + 1]
                if flag == "sflo-dir":
                    sflo_dir = args[i + 1]
                i += 2
            else:
                unknown.append(args[i])
                i += 1
        else:
            unknown.append(args[i])
            i += 1

    return sflo_dir, flags, unknown


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_init(args):
    """Initialize pipeline: derive role config from pipeline.yaml."""
    sflo_dir, flags, _ = parse_args(args)

    roles = derive_roles_from_pipeline()

    os.makedirs(sflo_dir, exist_ok=True)
    state = make_initial_state(roles)
    write_state(sflo_dir, state)

    output(
        {
            "ok": True,
            "source": "pipeline.yaml",
            "roles": roles,
            "sflo_dir": sflo_dir,
            "next": compute_next(state, sflo_dir),
        }
    )


def cmd_assign(args):
    """Register Scout's agent assignments.

    Only roles defined in GATES or the canonical set {pm, dev, qa} are
    accepted as valid assignment targets.  Unknown role names are rejected
    to prevent silent misconfiguration.
    """
    assignments = {}
    extras = {}
    sflo_dir = ".sflo"
    unknown = []

    # Derive the set of assignable role names from GATES constants plus the
    # canonical trio.  GATES keys are integers; role names come from gate
    # artifact/role fields in pipeline.yaml.  We accept pm, dev, qa, and
    # any additional role in KNOWN_ROLES minus internal tokens.
    _INTERNAL_TOKENS = {"extra", "sflo-dir"}
    _ASSIGNABLE_ROLES = (KNOWN_ROLES - _INTERNAL_TOKENS) | {"pm", "dev", "qa"}

    i = 0
    while i < len(args):
        if args[i] == "--sflo-dir" and i + 1 < len(args):
            sflo_dir = args[i + 1]
            i += 2
        elif args[i].startswith("--") and i + 1 < len(args):
            role = args[i][2:]
            path = args[i + 1]
            if role == "extra":
                if "=" in path:
                    k, v = path.split("=", 1)
                    ok, err = validate_agent_path(v)
                    if not ok:
                        output({"ok": False, "error": f"Extra '{k}': {err}"})
                        return
                    extras[k] = v
            elif role in _ASSIGNABLE_ROLES:
                ok, err = validate_agent_path(path)
                if not ok:
                    output({"ok": False, "error": err})
                    return
                assignments[role] = path
            else:
                unknown.append(args[i])
            i += 2
        else:
            unknown.append(args[i])
            i += 1

    if unknown:
        output({"ok": False, "error": f"Unknown arguments: {' '.join(unknown)}"})
        return

    lock = acquire_lock(sflo_dir)
    try:
        state = read_state(sflo_dir)
        if not state:
            output(
                {"ok": False, "error": "Pipeline not initialized. Run 'init' first."}
            )
            return

        state["assignments"] = {**assignments, **extras}
        first_gate = min(_SCAFFOLD_GATES.keys()) if _SCAFFOLD_GATES else 1
        first_gate_key = str(first_gate)
        state["current_state"] = f"gate-{first_gate_key}"
        if first_gate_key in state.get("gates", {}):
            state["gates"][first_gate_key]["status"] = "in_progress"
        write_state(sflo_dir, state)
    finally:
        release_lock(sflo_dir, lock)

    output(
        {
            "ok": True,
            "assignments": state["assignments"],
            "next": compute_next(state, sflo_dir),
        }
    )


def cmd_next(args):
    """Get next pipeline step. Performs validation and state transitions."""
    sflo_dir, _, unknown = parse_args(args)

    if unknown:
        output({"ok": False, "error": f"Unknown arguments: {' '.join(unknown)}"})
        return

    lock = acquire_lock(sflo_dir)
    try:
        state = read_state(sflo_dir)
        if not state:
            output(
                {"ok": False, "error": "Pipeline not initialized. Run 'init' first."}
            )
            return

        auto_transition(state, sflo_dir)
        result = compute_next(state, sflo_dir)
        result = apply_transition(state, result, sflo_dir)
    finally:
        release_lock(sflo_dir, lock)

    output({"ok": True, **result})


def cmd_status(args):
    """Show pipeline status."""
    sflo_dir, _, _ = parse_args(args)

    state = read_state(sflo_dir)
    if not state:
        output({"ok": False, "error": "Pipeline not initialized."})
        return

    gates_info = {}
    for g_str, g_info in state["gates"].items():
        g_num = float(g_str)
        g_num = int(g_num) if g_num == int(g_num) else g_num
        artifact = g_info["artifact"]
        artifact_path = os.path.join(sflo_dir, artifact)
        exists = os.path.isfile(artifact_path)

        entry = {"status": g_info["status"], "artifact": artifact, "exists": exists}

        if exists:
            content, _ = read_artifact(sflo_dir, artifact)
            if content and g_num == 3:
                entry["grade"] = extract_field(content, r"###?\s*Grade[: ]*(.+)")
            elif content and g_num == 4:
                entry["verdict"] = extract_field(content, r"###?\s*Verdict[: ]*(.+)")
            elif content and g_num == 5:
                entry["decision"] = extract_field(content, r"###?\s*Decision[: ]*(.+)")

        gates_info[g_str] = entry

    output(
        {
            "ok": True,
            "current_state": state["current_state"],
            "inner_loops": state["inner_loops"],
            "outer_loops": state["outer_loops"],
            "assignments": state.get("assignments", {}),
            "gates": gates_info,
            "started_at": state.get("started_at"),
        }
    )


def cmd_clean(args):
    """Wipe pipeline state and gate artifacts for a fresh run.

    Removes: state.json, all configured gate artifacts, `*-FEEDBACK.md`
    feedback files, and pipeline.log.

    Preserves: anything else in sflo_dir (e.g. .venv, user-provided files).

    Bounded allow-list, regenerable artifacts — no flags, no ceremony.
    Run `ls .sflo/` first if you want to preview.
    """
    sflo_dir, _, unknown = parse_args(args)

    if unknown:
        output({"ok": False, "error": f"Unknown arguments: {' '.join(unknown)}"})
        return

    if not os.path.isdir(sflo_dir):
        output({"ok": False, "error": f"{sflo_dir} does not exist — nothing to clean"})
        return

    # Known SFLO-owned entries at the top level of sflo_dir
    known_files = {
        "state.json",
        "pipeline.log",
        "state.lock",
        ".last_hook_state",
    }
    for _g, _ginfo in _SCAFFOLD_GATES.items():
        if isinstance(_ginfo, list):
            for _entry in _ginfo:
                if _entry.get("artifact"):
                    known_files.add(_entry["artifact"])
        elif isinstance(_ginfo, dict) and _ginfo.get("artifact"):
            known_files.add(_ginfo["artifact"])
    known_dirs = set()

    to_remove = []
    try:
        for entry in sorted(os.listdir(sflo_dir)):
            full = os.path.join(sflo_dir, entry)
            if (
                entry in known_files or entry.endswith("-FEEDBACK.md")
            ) and os.path.isfile(full):
                to_remove.append(full)
            elif entry in known_dirs and os.path.isdir(full):
                to_remove.append(full)
    except OSError as e:
        output({"ok": False, "error": f"Cannot list {sflo_dir}: {e}"})
        return

    if not to_remove:
        output(
            {
                "ok": True,
                "sflo_dir": sflo_dir,
                "removed": [],
                "note": "Nothing to clean — directory contains no SFLO artifacts.",
            }
        )
        return

    archived = []
    errors = []
    try:
        archived = archive_to_logs(sflo_dir, to_remove)
    except OSError as e:
        errors.append({"path": "<archive>", "error": str(e)})

    output(
        {
            "ok": len(errors) == 0,
            "sflo_dir": sflo_dir,
            "archived_to": os.path.join(sflo_dir, "logs"),
            "archived": archived,
            "errors": errors,
            "note": (
                "Fresh run ready. Removed artifacts moved to logs/ for inspection. "
                "Invoke pipeline with a new user prompt."
            )
            if not errors
            else None,
        }
    )


def cmd_prompt(args):
    """Generate reinjectable prompt for stop hook."""
    sflo_dir, _, _ = parse_args(args)

    lock = acquire_lock(sflo_dir)
    try:
        state = read_state(sflo_dir)
        if not state:
            output({"ok": False, "error": "Pipeline not initialized."})
            return

        auto_transition(state, sflo_dir)
        result = compute_next(state, sflo_dir)
        result = apply_transition(state, result, sflo_dir)
    finally:
        release_lock(sflo_dir, lock)

    prompt_text = format_prompt(result)

    if prompt_text:
        output({"ok": True, "prompt": prompt_text})
    else:
        output({"ok": False, "error": "Pipeline at terminal state."})


# ---------------------------------------------------------------------------
# Output + Dispatch
# ---------------------------------------------------------------------------


def output(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


COMMANDS = {
    "init": cmd_init,
    "assign": cmd_assign,
    "next": cmd_next,
    "status": cmd_status,
    "prompt": cmd_prompt,
    "clean": cmd_clean,
}


def main():
    # Force UTF-8 stdout so non-ASCII JSON output (e.g. checkmarks) does not
    # raise UnicodeEncodeError on a Windows console.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        output(
            {
                "ok": False,
                "error": "Usage: scaffold.py <command> [args]",
                "commands": list(COMMANDS.keys()),
            }
        )
        sys.exit(1)

    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
