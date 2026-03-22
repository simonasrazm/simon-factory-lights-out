"""SFLO state machine — compute next action and apply transitions."""

import os
import re

from .constants import (
    GATES, INNER_LOOP_MAX, OUTER_LOOP_MAX,
    S_SCOUT, S_ASSIGN, S_ESCALATE, S_DONE,
)
from .state import write_state
from .validate import validate_gate, clean_artifacts_from


def resolve_sflo_base():
    """Find the sflo/ base directory (for gate docs)."""
    if os.path.isdir(os.path.join(os.getcwd(), "sflo", "gates")):
        return "sflo"
    if os.path.isdir(os.path.join(os.getcwd(), "gates")):
        return "."
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


def auto_transition(state, sflo_dir):
    """If at gate-N and the artifact already exists, transition to check-N.

    Returns True if a transition was made.
    """
    gate_match = re.match(r"gate-(\d+)", state["current_state"])
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
        if n == 5:
            state["current_state"] = S_DONE
        else:
            state["current_state"] = f"gate-{n + 1}"
            state["gates"][str(n)]["status"] = "done"
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
