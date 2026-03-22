#!/usr/bin/env python3
"""SFLO Pipeline Scaffold — CLI entry point.

The scaffold IS the pipeline authority. This file is the CLI interface;
logic lives in separate modules (machine, validate, state, etc.).

Usage:
    python sflo/src/scaffold.py init [--bindings PATH] [--sflo-dir PATH]
    python sflo/src/scaffold.py assign --pm PATH --dev PATH --qa PATH [--extra role=path ...]
    python sflo/src/scaffold.py next [--sflo-dir PATH]
    python sflo/src/scaffold.py prompt [--sflo-dir PATH]
    python sflo/src/scaffold.py status [--sflo-dir PATH]
"""

import sys
import os
import json

# Allow running as script (python sflo/src/scaffold.py) or as module
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.constants import KNOWN_ROLES
    from src.bindings import parse_bindings, resolve_bindings_path
    from src.state import acquire_lock, release_lock, read_state, write_state, make_initial_state
    from src.validate import validate_agent_path, read_artifact, extract_field
    from src.machine import auto_transition, compute_next, apply_transition
    from src.prompt import format_prompt
else:
    from .constants import KNOWN_ROLES
    from .bindings import parse_bindings, resolve_bindings_path
    from .state import acquire_lock, release_lock, read_state, write_state, make_initial_state
    from .validate import validate_agent_path, read_artifact, extract_field
    from .machine import auto_transition, compute_next, apply_transition
    from .prompt import format_prompt


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
    """Initialize pipeline: parse bindings, create state."""
    sflo_dir, flags, _ = parse_args(args, {"bindings"})

    path = resolve_bindings_path(flags.get("bindings"))
    if not path:
        output({"ok": False, "error": "bindings.yaml not found"})
        return

    roles, err = parse_bindings(path)
    if err:
        output({"ok": False, "error": err})
        return

    os.makedirs(sflo_dir, exist_ok=True)
    state = make_initial_state(roles)
    write_state(sflo_dir, state)

    output({
        "ok": True,
        "bindings_path": path,
        "roles": roles,
        "sflo_dir": sflo_dir,
        "next": compute_next(state, sflo_dir),
    })


def cmd_assign(args):
    """Register Scout's agent assignments."""
    assignments = {}
    extras = {}
    sflo_dir = ".sflo"
    unknown = []

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
                    extras[k] = v
            elif role in KNOWN_ROLES or role in {"pm", "dev", "qa"}:
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
            output({"ok": False, "error": "Pipeline not initialized. Run 'init' first."})
            return

        state["assignments"] = {**assignments, **extras}
        state["current_state"] = "gate-1"
        state["gates"]["1"]["status"] = "in_progress"
        write_state(sflo_dir, state)
    finally:
        release_lock(sflo_dir, lock)

    output({
        "ok": True,
        "assignments": state["assignments"],
        "next": compute_next(state, sflo_dir),
    })


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
            output({"ok": False, "error": "Pipeline not initialized. Run 'init' first."})
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
        g_num = int(g_str)
        artifact = g_info["artifact"]
        artifact_path = os.path.join(sflo_dir, artifact)
        exists = os.path.isfile(artifact_path)

        entry = {"status": g_info["status"], "artifact": artifact, "exists": exists}

        if exists:
            content, _ = read_artifact(sflo_dir, artifact)
            if content and g_num == 3:
                entry["grade"] = extract_field(content, r"###?\s*Grade[:\s]*(.+)")
            elif content and g_num == 4:
                entry["verdict"] = extract_field(content, r"###?\s*Verdict[:\s]*(.+)")
            elif content and g_num == 5:
                entry["decision"] = extract_field(content, r"###?\s*Decision[:\s]*(.+)")

        gates_info[g_str] = entry

    output({
        "ok": True,
        "current_state": state["current_state"],
        "inner_loops": state["inner_loops"],
        "outer_loops": state["outer_loops"],
        "assignments": state.get("assignments", {}),
        "gates": gates_info,
        "started_at": state.get("started_at"),
    })


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
    print(json.dumps(data, indent=2))


COMMANDS = {
    "init": cmd_init,
    "assign": cmd_assign,
    "next": cmd_next,
    "status": cmd_status,
    "prompt": cmd_prompt,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        output({
            "ok": False,
            "error": "Usage: scaffold.py <command> [args]",
            "commands": list(COMMANDS.keys()),
        })
        sys.exit(1)

    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
