"""SFLO state machine — compute next action and apply transitions."""

import os
import re

from .constants import (
    GATES, PRE_GATE_PHASES, SFLO_ROOT, INNER_LOOP_MAX, OUTER_LOOP_MAX,
    S_SCOUT, S_ASSIGN, S_ESCALATE, S_DONE,
)
from .state import write_state
from .validate import validate_gate, clean_artifacts_from


def resolve_sflo_base():
    """Find the sflo/ base directory (for gate docs).

    Checks cwd first (for backward compat), then falls back to
    SFLO_ROOT (resolved from scaffold.py's location).
    """
    if os.path.isdir(os.path.join(os.getcwd(), "sflo", "gates")):
        return "sflo"
    if os.path.isdir(os.path.join(os.getcwd(), "gates")):
        return "."
    # Fallback: resolve from scaffold.py's own location
    if os.path.isdir(os.path.join(SFLO_ROOT, "gates")):
        return SFLO_ROOT
    return "sflo"


def agent_reads(gate_num, agent_path, sflo_base, sflo_dir):
    """Build the list of files an agent should read for a gate."""
    info = GATES[gate_num]
    reads = [
        os.path.join(sflo_base, info["gate_doc"]),
        os.path.join(agent_path, "SOUL.md"),
    ]
    for prev_gate in range(1, gate_num):
        prev_artifact = GATES[prev_gate]["artifact"]
        reads.append(os.path.join(sflo_dir, prev_artifact))
    return reads


def _parse_assignments_artifact(artifact_path):
    """Parse a SCOUT-ASSIGNMENTS.md artifact into {role: agent_path}."""
    assignments = {}
    role_map = {"pm": "pm", "developer": "dev", "qa": "qa"}
    with open(artifact_path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"-\s+\*\*(\w+):\*\*\s+(\S+)", line.strip())
            if m:
                label = m.group(1).lower()
                role = role_map.get(label, label)
                assignments[role] = m.group(2)
    return assignments


def auto_transition(state, sflo_dir):
    """Advance state when the expected artifact for the current phase exists.

    Covers both pre-gate phases (from PRE_GATE_PHASES table) and gate
    phases (from GATES table). Each phase produces an artifact; when
    that artifact is present on disk the state advances.

    Returns True if a transition was made.
    """
    current = state["current_state"]

    # --- pre-gate phases (scout, assign, …) ---------------------------------
    phase = PRE_GATE_PHASES.get(current)
    if phase:
        artifact_path = os.path.join(sflo_dir, phase["artifact"])
        if os.path.isfile(artifact_path):
            if not state.get("assignments"):
                state["assignments"] = _parse_assignments_artifact(artifact_path)
            state["current_state"] = phase["next_state"]
            write_state(sflo_dir, state)
            return True
        return False

    # --- gate phases (gate-1 … gate-5) --------------------------------------
    gate_match = re.match(r"gate-(\d+)", current)
    if gate_match:
        n = int(gate_match.group(1))
        artifact = GATES[n]["artifact"]
        artifact_path = os.path.join(sflo_dir, artifact)
        if os.path.isfile(artifact_path):
            state["current_state"] = f"check-{n}"
            write_state(sflo_dir, state)
            return True

    return False


def compute_next(state, sflo_dir):
    """Given current state, return the next action as a dict.

    Pure query — does NOT mutate state or write to disk.
    """
    current = state["current_state"]
    sflo_base = resolve_sflo_base()
    assignments = state.get("assignments", {})
    bindings = state.get("bindings", {})

    if current == S_SCOUT:
        return {
            "state": "scout",
            "action": "spawn_agent",
            "agent": {
                "role": "scout",
                "path": bindings.get("scout", {}).get("agent", os.path.join(sflo_base, "agents", "scout")),
                "model": bindings.get("scout", {}).get("model", "sonnet"),
                "reads": [os.path.join(sflo_base, "agents", "scout", "SOUL.md")],
                "instruction": "Read user prompt, scan agents/ for matches, return structured assignments.",
            },
        }

    if current == S_ASSIGN:
        return {
            "state": "assign",
            "action": "waiting",
            "message": "Run 'assign' command with Scout's agent assignments before proceeding.",
        }

    gate_match = re.match(r"gate-(\d+)", current)
    if gate_match:
        n = int(gate_match.group(1))
        if n == 5:
            return {
                "state": f"gate-{n}",
                "action": "produce_artifact",
                "role": "sflo",
                "artifact": GATES[n]["artifact"],
                "sflo_dir": sflo_dir,
                "reads": [os.path.join(sflo_dir, GATES[g]["artifact"]) for g in range(1, 5)],
                "gate_doc": os.path.join(sflo_base, GATES[n]["gate_doc"]),
            }

        role = GATES[n]["role"]
        agent_path = assignments.get(role, os.path.join(sflo_base, "agents", role))
        role_bindings = bindings.get(role, {})

        return {
            "state": f"gate-{n}",
            "action": "spawn_agent",
            "agent": {
                "role": role,
                "path": agent_path,
                "model": role_bindings.get("model", "sonnet"),
                "reads": agent_reads(n, agent_path, sflo_base, sflo_dir),
                "produces": os.path.join(sflo_dir, GATES[n]["artifact"]),
            },
        }

    check_match = re.match(r"check-(\d+)", current)
    if check_match:
        n = int(check_match.group(1))
        passed, checks = validate_gate(n, sflo_dir)

        if passed:
            return {
                "state": f"check-{n}",
                "action": "validated",
                "gate": n,
                "pass": True,
                "checks": checks,
            }
        else:
            return {
                "state": f"check-{n}",
                "action": "check_failed",
                "gate": n,
                "pass": False,
                "checks": checks,
            }

    if current == S_DONE:
        return {"state": "done", "action": "pipeline_complete"}

    if current == S_ESCALATE:
        return {
            "state": "escalate",
            "action": "ask_human",
            "reason": f"PM rejected {state['outer_loops']} times. Human decision needed.",
            "options": ["continue (reset counters)", "ship anyway (override)", "kill project"],
        }

    return {"state": current, "action": "unknown", "error": f"Unknown state: {current}"}


def apply_transition(state, result, sflo_dir):
    """Apply the state transition implied by a compute_next result.

    Mutates state and writes to disk. Returns the enriched result dict.
    """
    action = result.get("action")
    n = result.get("gate")

    if action != "validated" and action != "check_failed":
        return result

    if action == "validated":
        state["gates"][str(n)]["status"] = "done"
        if n == 5:
            state["current_state"] = S_DONE
        else:
            state["current_state"] = f"gate-{n + 1}"
        write_state(sflo_dir, state)

        next_action = compute_next(state, sflo_dir)
        result["next"] = next_action
        return result

    if action == "check_failed":
        if n == 3:
            state["inner_loops"] += 1
            if state["inner_loops"] >= INNER_LOOP_MAX:
                state["current_state"] = "gate-4"
                write_state(sflo_dir, state)
                return {
                    **result,
                    "state": "loop-inner-exhausted",
                    "action": "proceed",
                    "note": f"Inner loop exhausted ({INNER_LOOP_MAX} Dev<>QA cycles). Proceeding to PM verification.",
                    "inner_count": state["inner_loops"],
                    "next": compute_next(state, sflo_dir),
                }
            else:
                state["current_state"] = "gate-2"
                clean_artifacts_from(2, sflo_dir)
                write_state(sflo_dir, state)
                return {
                    **result,
                    "state": "loop-inner",
                    "action": "loop_back",
                    "inner_count": state["inner_loops"],
                    "max": INNER_LOOP_MAX,
                    "next": compute_next(state, sflo_dir),
                }

        elif n == 4:
            state["outer_loops"] += 1
            state["inner_loops"] = 0

            if state["outer_loops"] >= OUTER_LOOP_MAX:
                state["current_state"] = S_ESCALATE
                write_state(sflo_dir, state)
                return {
                    **result,
                    "state": "escalate",
                    "action": "ask_human",
                    "reason": f"PM rejected {OUTER_LOOP_MAX} times. Escalating to human.",
                    "outer_count": state["outer_loops"],
                }
            else:
                state["current_state"] = "gate-2"
                clean_artifacts_from(2, sflo_dir)
                write_state(sflo_dir, state)
                return {
                    **result,
                    "state": "loop-outer",
                    "action": "loop_back",
                    "outer_count": state["outer_loops"],
                    "inner_reset": True,
                    "max": OUTER_LOOP_MAX,
                    "next": compute_next(state, sflo_dir),
                }

        else:
            result["action"] = "failed"
            result["message"] = f"Gate {n} validation failed. Fix and retry."
            return result

    return result
