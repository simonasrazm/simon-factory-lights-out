"""SFLO state machine — compute next action and apply transitions."""

import os
import re

from .constants import (
    GATES, SFLO_ROOT, INNER_LOOP_MAX, OUTER_LOOP_MAX,
    S_SCOUT, S_ASSIGN, S_ESCALATE, S_DONE,
)
from .state import write_state
from .validate import validate_gate, clean_artifacts_from, save_qa_feedback, save_pm_rejection
from .guardian import guardian_check, record_gate_failure


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


def _sorted_gates():
    """Return sorted gate keys (supports int and float keys)."""
    return sorted(GATES.keys())


def _next_gate_after(n):
    """Return the next gate key after n, or None if n is the last gate."""
    sorted_gates = _sorted_gates()
    for i, key in enumerate(sorted_gates):
        if key == n:
            if i + 1 < len(sorted_gates):
                return sorted_gates[i + 1]
            return None
    return None


def _last_gate():
    """Return the last gate key."""
    keys = _sorted_gates()
    return keys[-1] if keys else None


def agent_reads(gate_num, agent_path, sflo_base, sflo_dir):
    """Build the list of files an agent should read for a gate."""
    info = GATES[gate_num]
    reads = [
        os.path.join(sflo_base, info["gate_doc"]),
        os.path.join(agent_path, "SOUL.md"),
    ]
    for prev_gate in sorted(GATES):
        if prev_gate < gate_num:
            prev_artifact = GATES[prev_gate]["artifact"]
            reads.append(os.path.join(sflo_dir, prev_artifact))

    # Outer loop: PM rejection takes priority (QA report is stale after PM rejects)
    # Inner loop: QA feedback guides dev fixes
    rejection_path = os.path.join(sflo_dir, "PM-REJECTION.md")
    feedback_path = os.path.join(sflo_dir, "QA-FEEDBACK.md")
    if os.path.isfile(rejection_path):
        reads.append(rejection_path)
    elif os.path.isfile(feedback_path):
        reads.append(feedback_path)

    return reads


def auto_transition(state, sflo_dir):
    """If at gate-N and the artifact already exists, transition to check-N.

    Returns True if a transition was made.
    """
    gate_match = re.match(r"gate-(\d+\.?\d*)", state["current_state"])
    if gate_match:
        n_str = gate_match.group(1)
        n = float(n_str)
        n = int(n) if n == int(n) else n
        if n not in GATES:
            return False
        artifact = GATES[n]["artifact"]
        artifact_path = os.path.join(sflo_dir, artifact)
        if os.path.isfile(artifact_path):
            state["current_state"] = f"check-{n_str}"
            write_state(sflo_dir, state)
            return True
    return False


def compute_next(state, sflo_dir):
    """Given current state, return the next action as a dict.

    Pure query — does NOT mutate state or write to disk.
    """
    # Guardian check — runs before anything else
    guardian_reason = guardian_check(sflo_dir)
    if guardian_reason:
        return {"state": "escalate", "action": "ask_human", "reason": guardian_reason}

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

    gate_match = re.match(r"gate-(\d+\.?\d*)", current)
    if gate_match:
        n_str = gate_match.group(1)
        n = float(n_str)
        n = int(n) if n == int(n) else n

        if n not in GATES:
            return {"state": current, "action": "unknown", "error": f"Unknown gate: {n}"}

        last_gate = _last_gate()
        if n == last_gate:
            # Last gate — SFLO produces the decision artifact
            last_info = GATES[last_gate]
            prior_reads = [
                os.path.join(sflo_dir, GATES[g]["artifact"])
                for g in _sorted_gates()
                if g < last_gate
            ]
            return {
                "state": f"gate-{n_str}",
                "action": "produce_artifact",
                "role": last_info.get("role", "sflo"),
                "artifact": last_info["artifact"],
                "sflo_dir": sflo_dir,
                "reads": prior_reads,
                "gate_doc": os.path.join(sflo_base, last_info["gate_doc"]),
            }

        role = GATES[n]["role"]
        agent_path = assignments.get(role, os.path.join(sflo_base, "agents", role))
        role_bindings = bindings.get(role, {})

        return {
            "state": f"gate-{n_str}",
            "action": "spawn_agent",
            "agent": {
                "role": role,
                "path": agent_path,
                "model": role_bindings.get("model", "sonnet"),
                "reads": agent_reads(n, agent_path, sflo_base, sflo_dir),
                "produces": os.path.join(sflo_dir, GATES[n]["artifact"]),
            },
        }

    check_match = re.match(r"check-(\d+\.?\d*)", current)
    if check_match:
        n_str = check_match.group(1)
        n = float(n_str)
        n = int(n) if n == int(n) else n
        passed, checks = validate_gate(n, sflo_dir)

        if passed:
            return {
                "state": f"check-{n_str}",
                "action": "validated",
                "gate": n,
                "pass": True,
                "checks": checks,
            }
        else:
            return {
                "state": f"check-{n_str}",
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
        last_gate = _last_gate()
        if n == last_gate:
            state["current_state"] = S_DONE
        else:
            next_gate = _next_gate_after(n)
            state["current_state"] = f"gate-{next_gate}"
        write_state(sflo_dir, state)

        # Clean up feedback files once they've served their purpose
        sorted_gates = _sorted_gates()
        inner_loop_restart = sorted_gates[1] if len(sorted_gates) >= 2 else None
        inner_loop_gate = sorted_gates[-3] if len(sorted_gates) >= 3 else None

        # PM rejection served its purpose once dev passes gate 2
        if n == inner_loop_restart:
            rejection_path = os.path.join(sflo_dir, "PM-REJECTION.md")
            if os.path.isfile(rejection_path):
                os.remove(rejection_path)

        # QA feedback served its purpose once QA gate passes
        if n == inner_loop_gate:
            feedback_path = os.path.join(sflo_dir, "QA-FEEDBACK.md")
            if os.path.isfile(feedback_path):
                os.remove(feedback_path)

        next_action = compute_next(state, sflo_dir)
        result["next"] = next_action
        return result

    if action == "check_failed":
        # Get second-to-last and third-to-last gate keys for inner/outer loop logic
        sorted_gates = _sorted_gates()
        last_gate = sorted_gates[-1] if sorted_gates else None
        # Inner loop gate is the one before the last (gate 3 in default pipeline)
        inner_loop_gate = sorted_gates[-3] if len(sorted_gates) >= 3 else None
        # Outer loop gate is the second to last (gate 4 in default pipeline)
        outer_loop_gate = sorted_gates[-2] if len(sorted_gates) >= 2 else None
        # Inner loop restart gate is gate 2 in default pipeline
        inner_loop_restart = sorted_gates[1] if len(sorted_gates) >= 2 else None

        if n == inner_loop_gate:
            state["inner_loops"] += 1
            if state["inner_loops"] >= INNER_LOOP_MAX:
                next_gate = _next_gate_after(n)
                state["current_state"] = f"gate-{next_gate}"
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
                restart_gate = inner_loop_restart
                state["current_state"] = f"gate-{restart_gate}"
                save_qa_feedback(sflo_dir)
                clean_artifacts_from(restart_gate, sflo_dir)
                write_state(sflo_dir, state)
                return {
                    **result,
                    "state": "loop-inner",
                    "action": "loop_back",
                    "inner_count": state["inner_loops"],
                    "max": INNER_LOOP_MAX,
                    "next": compute_next(state, sflo_dir),
                }

        elif n == outer_loop_gate:
            state["outer_loops"] += 1
            state["inner_loops"] = 0

            # Record gate failure for guardian circuit breaker
            trip = record_gate_failure(sflo_dir, n)
            if trip:
                state["current_state"] = S_ESCALATE
                write_state(sflo_dir, state)
                return {"state": "escalate", "action": "ask_human", "reason": trip}

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
                restart_gate = inner_loop_restart
                state["current_state"] = f"gate-{restart_gate}"
                # Save PM's rejection verdict before artifacts are cleaned
                save_pm_rejection(sflo_dir)
                # QA feedback is stale after PM rejects — remove it
                qa_feedback = os.path.join(sflo_dir, "QA-FEEDBACK.md")
                if os.path.isfile(qa_feedback):
                    os.remove(qa_feedback)
                clean_artifacts_from(restart_gate, sflo_dir)
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
            # Record gate failure for other gates too (for guardian)
            record_gate_failure(sflo_dir, n)
            result["action"] = "failed"
            result["message"] = f"Gate {n} validation failed. Fix and retry."
            return result

    return result
