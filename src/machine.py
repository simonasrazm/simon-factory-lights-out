"""SFLO state machine — compute next action and apply transitions."""

import os
import re

from .constants import (
    GATES,
    SFLO_ROOT,
    INNER_LOOP_MAX,
    OUTER_LOOP_MAX,
    S_SCOUT,
    S_ASSIGN,
    S_ESCALATE,
    S_DONE,
)
from .state import write_state
from .validate import (
    validate_gate,
    clean_artifacts_from,
    save_qa_feedback,
    save_pm_feedback,
)


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
    """Minimal reads list — gate doc + SOUL only.

    Agents pull all other context on demand using the context map
    injected by build_agent_prompt. This keeps the prompt small and
    lets agents load only what they need (e.g. skip 71KB SCOPE on
    rebuild when only QA feedback matters).
    """
    info = GATES[gate_num]
    return [
        os.path.join(sflo_base, info["gate_doc"]),
        os.path.join(agent_path, "SOUL.md"),
    ]


def build_context_map(gate_num, sflo_dir):
    """Build a context map for the agent — pointers to relevant files.

    The map tells the agent what files exist and why they matter,
    without injecting their content. Agent reads them on demand.
    Returns (mode, context_lines) where mode is "fresh" or "rebuild".
    """
    feedback_files = []
    qa_feedback = os.path.join(sflo_dir, "QA-FEEDBACK.md")
    pm_feedback = os.path.join(sflo_dir, "PM-FEEDBACK.md")
    if os.path.isfile(pm_feedback):
        feedback_files.append(f"  - {pm_feedback} (PM reviewed and requested changes)")
    if os.path.isfile(qa_feedback):
        feedback_files.append(f"  - {qa_feedback} (QA found issues in your code)")
    stst_feedback = os.path.join(sflo_dir, "STST-FEEDBACK.md")
    if os.path.isfile(stst_feedback):
        feedback_files.append(
            f"  - {stst_feedback} (STST static analysis found issues in your tests — fix before QA)"
        )

    is_rebuild = len(feedback_files) > 0

    # Prior gate artifacts that exist on disk
    prior_artifacts = []
    for prev_gate in sorted(GATES):
        if prev_gate < gate_num:
            artifact = GATES[prev_gate]["artifact"]
            path = os.path.join(sflo_dir, artifact)
            if os.path.isfile(path):
                prior_artifacts.append(f"  - {path}")

    scope_path = os.path.join(sflo_dir, "SCOPE.md")

    lines = ["## Context\n"]
    if is_rebuild:
        lines.append("Mode: rebuild\n")
        lines.append("Feedback to address:")
        lines.extend(feedback_files)
        lines.append(f"\nScope: {scope_path} (read only if you need AC details)")
    else:
        lines.append("Mode: fresh\n")
        lines.append(f"Scope: {scope_path}")

    if prior_artifacts:
        lines.append("\nPrior artifacts on disk:")
        lines.extend(prior_artifacts)

    return "rebuild" if is_rebuild else "fresh", "\n".join(lines)


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
                "path": bindings.get("scout", {}).get(
                    "agent", os.path.join(sflo_base, "agents", "scout")
                ),
                "model": bindings.get("scout", {}).get("model", "sonnet"),
                "tools_mode": bindings.get("scout", {}).get("tools", "readonly"),
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
            return {
                "state": current,
                "action": "unknown",
                "error": f"Unknown gate: {n}",
            }

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

        # STST filter gate — runs CLI, not an LLM agent
        if role == "stst":
            return {
                "state": f"gate-{n_str}",
                "action": "run_stst_gate",
                "gate_num": n,
                "gate_doc": os.path.join(sflo_base, GATES[n]["gate_doc"]),
                "sflo_dir": sflo_dir,
            }

        return {
            "state": f"gate-{n_str}",
            "action": "spawn_agent",
            "agent": {
                "role": role,
                "path": agent_path,
                "model": role_bindings.get("model", "sonnet"),
                # tools_mode flows from bindings.yaml `tools:` field. Unset =
                # full access (None resolved by the adapter); set to "readonly"
                # to clamp scout-style recon agents to Read/Glob/Grep only.
                "tools_mode": role_bindings.get("tools"),
                "reads": agent_reads(n, agent_path, sflo_base, sflo_dir),
                "produces": os.path.join(sflo_dir, GATES[n]["artifact"]),
                "gate_num": n,
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
        # Escalation reason + options are stored in state by whichever branch
        # escalated. Default messaging is for outer-loop PM-rejection exhaustion
        # (the original escalation source); other branches (gate 1/5 validation
        # failure, non-progress guard) set their own.
        reason = state.get("escalate_reason") or (
            f"PM rejected {state['outer_loops']} times. Human decision needed."
        )
        options = state.get("escalate_options") or [
            "continue (reset counters)",
            "ship anyway (override)",
            "kill project",
        ]
        return {
            "state": "escalate",
            "action": "ask_human",
            "reason": reason,
            "options": options,
            "failed_checks": state.get("escalate_failed_checks", []),
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

        # Archive feedback files to logs/ once they've served their purpose
        from .archive import archive_to_logs

        if n == inner_loop_restart:
            pm_fb = os.path.join(sflo_dir, "PM-FEEDBACK.md")
            if os.path.isfile(pm_fb):
                archive_to_logs(sflo_dir, [pm_fb])
        if n == inner_loop_gate:
            qa_fb = os.path.join(sflo_dir, "QA-FEEDBACK.md")
            if os.path.isfile(qa_fb):
                archive_to_logs(sflo_dir, [qa_fb])

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

        # STST filter gate (role="stst") — loop back to DEV without touching
        # inner_loops or outer_loops. Uses gate_retries["2.5"] counter.
        stst_gate = next(
            (g for g in sorted_gates if g in GATES and GATES[g].get("role") == "stst"),
            None,
        )
        if stst_gate is not None and n == stst_gate:
            gate_retries = state.get("gate_retries", {})
            gate_key = str(n)
            gate_retries[gate_key] = gate_retries.get(gate_key, 0) + 1
            state["gate_retries"] = gate_retries

            if gate_retries[gate_key] >= INNER_LOOP_MAX:
                state["current_state"] = S_ESCALATE
                state["escalate_reason"] = (
                    f"STST rejected {gate_retries[gate_key]} DEV rebuilds — "
                    f"likely prompt/SUT mismatch. Human decision needed."
                )
                state["escalate_options"] = [
                    "override (ship-anyway)",
                    "fix DEV test generation prompt",
                    "kill",
                ]
                write_state(sflo_dir, state)
                return compute_next(state, sflo_dir)
            else:
                restart_gate = inner_loop_restart
                from .archive import archive_to_logs

                stst_report = os.path.join(sflo_dir, "STST-REPORT.md")
                build_status = os.path.join(sflo_dir, "BUILD-STATUS.md")
                to_archive = [
                    f for f in [stst_report, build_status] if os.path.isfile(f)
                ]
                if to_archive:
                    archive_to_logs(sflo_dir, to_archive)
                state["current_state"] = f"gate-{restart_gate}"
                write_state(sflo_dir, state)
                return {
                    **result,
                    "state": "loop-stst",
                    "action": "loop_back",
                    "stst_retry_count": gate_retries[gate_key],
                    "max": INNER_LOOP_MAX,
                    "next": compute_next(state, sflo_dir),
                }

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
                # Save PM's verdict as PM-FEEDBACK.md before cleanup
                # deletes PM-VERIFY.md. Same pattern as QA-FEEDBACK.md:
                # gate artifact deleted for auto_transition, feedback
                # copy persists for dev's context map.
                save_pm_feedback(sflo_dir)
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
            # Gate failure on a non-loop gate (e.g. gate 1, 2, or 5).
            #
            # Retry the responsible agent with the validation error as
            # context — same pattern as the inner/outer loops but using
            # gate_retries counter and INNER_LOOP_MAX as cap. The agent
            # gets another chance to fix its artifact, with the failed
            # checks surfaced in state so the runner's crash_context or
            # prompt rebuild includes them.
            #
            # Only escalate after INNER_LOOP_MAX retries — dark factory
            # should self-heal on fixable validation issues (e.g. dev
            # missing a checklist item in BUILD-STATUS.md).
            gate_retries = state.get("gate_retries", {})
            gate_key = str(n)
            gate_retries[gate_key] = gate_retries.get(gate_key, 0) + 1
            state["gate_retries"] = gate_retries

            if gate_retries[gate_key] >= INNER_LOOP_MAX:
                failed_checks = [
                    c for c in result.get("checks", []) if not c.get("pass", True)
                ]
                failed_names = [c.get("name", "?") for c in failed_checks]
                artifact_name = GATES[n]["artifact"] if n in GATES else f"gate-{n}"
                state["current_state"] = S_ESCALATE
                state["escalate_reason"] = (
                    f"Gate {n} ({artifact_name}) failed validation "
                    f"{gate_retries[gate_key]} times: "
                    f"{', '.join(failed_names) or 'unknown'}. "
                    f"Escalating to human."
                )
                state["escalate_options"] = [
                    f"fix {artifact_name} manually and retry",
                    f"delete {sflo_dir}/ and retry",
                    "override validation (not recommended)",
                ]
                state["escalate_failed_checks"] = failed_checks
                write_state(sflo_dir, state)
                return compute_next(state, sflo_dir)
            else:
                # Loop back: re-run the gate's agent with the failed
                # artifact deleted so it rebuilds from scratch.
                failed_checks = [
                    c for c in result.get("checks", []) if not c.get("pass", True)
                ]
                failed_names = [c.get("name", "?") for c in failed_checks]
                artifact_name = GATES[n]["artifact"] if n in GATES else f"gate-{n}"
                artifact_path = os.path.join(sflo_dir, artifact_name)
                if os.path.isfile(artifact_path):
                    from .archive import archive_to_logs

                    archive_to_logs(sflo_dir, [artifact_path])
                state["gates"][gate_key]["status"] = "pending"
                state["current_state"] = f"gate-{n}"
                write_state(sflo_dir, state)
                return {
                    **result,
                    "state": f"gate-retry-{n}",
                    "action": "loop_back",
                    "gate_retry_count": gate_retries[gate_key],
                    "max": INNER_LOOP_MAX,
                    "failed_checks": failed_names,
                    "next": compute_next(state, sflo_dir),
                }

    return result
