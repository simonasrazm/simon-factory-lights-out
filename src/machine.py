"""SFLO state machine — compute next action and apply transitions."""

import os
import re

from .constants import (
    GATES,
    SFLO_ROOT,
    SCOUT_CONFIG,
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
    feedback_name_for_artifact,
    feedback_paths_for_gate,
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


def _sorted_gates(gates=None):
    """Return sorted gate keys (supports int and float keys)."""
    _gates = gates if gates is not None else GATES
    return sorted(_gates.keys())


def _next_gate_after(n, gates=None):
    """Return the next gate key after n, or None if n is the last gate."""
    sorted_gates = _sorted_gates(gates=gates)
    for i, key in enumerate(sorted_gates):
        if key == n:
            if i + 1 < len(sorted_gates):
                return sorted_gates[i + 1]
            return None
    return None


def _last_gate(gates=None):
    """Return the last gate key."""
    keys = _sorted_gates(gates=gates)
    return keys[-1] if keys else None


def _discover_vendor_dirs(sflo_base):
    """Discover all vendor directories (SFLO_ROOT/vendor/* and sflo_base/vendor/*).

    Each subdirectory under vendor/ is a vendor. Returns list of absolute
    vendor root paths, deduplicated by realpath, SFLO_ROOT entries first.
    """
    seen = set()
    dirs = []
    for base in (SFLO_ROOT, sflo_base):
        vendor_root = os.path.join(base, "vendor")
        if not os.path.isdir(vendor_root):
            continue
        for entry in sorted(os.listdir(vendor_root)):
            full = os.path.join(vendor_root, entry)
            real = os.path.realpath(full)
            if os.path.isdir(full) and real not in seen:
                seen.add(real)
                dirs.append(full)
    return dirs


class SkillResolutionError(Exception):
    """Raised when a skill declared in pipeline.yaml cannot be resolved."""

    pass


def resolve_skill_paths(skill_names, sflo_base):
    """Resolve skill names to absolute SKILL.md paths.

    Supports two formats:
      - Unqualified: "tdd" → resolves only when the leaf name is unique
      - Qualified: "mattpocock-skills/engineering/tdd" → exact identity

    Skills may be nested to arbitrary depth below each vendor's skills directory.
    Returns list of existing file paths.

    Raises SkillResolutionError if any declared skill cannot be resolved —
    a missing skill means the agent would run without its intended methodology,
    silently degrading output quality.

    Security: rejects names containing traversal sequences.
    """
    if not skill_names:
        return []

    vendor_dirs = _discover_vendor_dirs(sflo_base)
    index = {}
    canonical = {}
    for vdir in vendor_dirs:
        skills_root = os.path.realpath(os.path.join(vdir, "skills"))
        if not os.path.isdir(skills_root):
            continue
        vendor_name = os.path.basename(vdir)
        for root, dirs, files in os.walk(skills_root, followlinks=False):
            dirs.sort()
            if "SKILL.md" not in files:
                continue
            path = os.path.realpath(os.path.join(root, "SKILL.md"))
            if os.path.commonpath((skills_root, path)) != skills_root or not os.path.isfile(path):
                continue
            rel = os.path.relpath(root, skills_root).replace(os.sep, "/")
            identity = f"{vendor_name}/{rel}"
            canonical.setdefault(identity, []).append(path)
            index.setdefault(rel.rsplit("/", 1)[-1], []).append((identity, path))
    paths = []
    unresolved = []

    for name in skill_names:
        if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
            unresolved.append(f"{name} (malformed skill name)")
            continue
        parts = name.split("/")
        if any(part in ("", ".", "..") for part in parts) or os.path.isabs(name):
            unresolved.append(f"{name} (malformed or unsafe skill name)")
            continue
        if len(parts) > 1:
            matches = canonical.get(name, [])
        else:
            matches = [path for _, path in index.get(name, [])]
        if len(matches) == 1:
            paths.append(matches[0])
        elif len(matches) > 1:
            choices = sorted(identity for identity, _ in index.get(name, []))
            unresolved.append(f"{name} (ambiguous; qualify as one of: {', '.join(choices)})")
        else:
            unresolved.append(f"{name} (not found)")

    if unresolved:
        raise SkillResolutionError(
            f"Pipeline skills failed to resolve: {', '.join(unresolved)}. "
            f"Searched vendors: {[os.path.basename(v) for v in vendor_dirs]}. "
            f"Fix pipeline.yaml skill names or install missing vendor skills."
        )

    return paths


def resolve_agent_paths(agent_refs, sflo_base):
    """Resolve per-gate agents: list entries to absolute .md paths.

    Each entry can be:
    - A directory path (e.g. agents/qa-w-agent-skills) → load <dir>/SOUL.md
    - A file path with .md extension → load directly
    - A path without .md → try as dir/SOUL.md, then as <path>.md

    Resolves relative to sflo_base. Returns list of existing file paths.

    Security: rejects traversal sequences and verifies resolved paths stay
    within sflo_base or cwd boundaries.
    """
    if not agent_refs:
        return []

    # Containment boundaries: sflo_base and cwd
    cwd_real = os.path.realpath(os.getcwd())
    sflo_real = os.path.realpath(sflo_base)

    paths = []
    for ref in agent_refs:
        # Reject traversal sequences in ref
        if ".." in ref:
            continue

        # Make absolute if relative
        if not os.path.isabs(ref):
            abs_ref = os.path.join(sflo_base, ref)
        else:
            abs_ref = ref

        # Containment check: resolved path must be under sflo_base or cwd
        real_ref = os.path.realpath(abs_ref)
        if not (
            real_ref.startswith(sflo_real + os.sep)
            or real_ref.startswith(cwd_real + os.sep)
            or real_ref == sflo_real
            or real_ref == cwd_real
        ):
            continue

        # Try as directory with SOUL.md
        soul_path = os.path.join(abs_ref, "SOUL.md")
        if os.path.isfile(soul_path):
            paths.append(soul_path)
            continue

        # Try as .md file directly
        if abs_ref.endswith(".md") and os.path.isfile(abs_ref):
            paths.append(abs_ref)
            continue

        # Try adding .md extension
        md_path = abs_ref + ".md"
        if os.path.isfile(md_path):
            paths.append(md_path)
            continue
    return paths


def agent_reads(gate_num, agent_path, sflo_base, sflo_dir, gates=None):
    """Minimal reads list — gate doc + SOUL only.

    Agents pull all other context on demand using the context map
    injected by build_agent_prompt. This keeps the prompt small and
    lets agents load only what they need (e.g. skip 71KB SCOPE on
    rebuild when only QA feedback matters).
    """
    _gates = gates if gates is not None else GATES
    info = _gates[gate_num]
    reads = []
    if info.get("gate_doc"):
        reads.append(os.path.join(sflo_base, info["gate_doc"]))
    reads.append(os.path.join(agent_path, "SOUL.md"))
    return reads


def build_context_map(gate_num, sflo_dir, gates=None):
    """Build a context map for the agent — pointers to relevant files.

    The map tells the agent what files exist and why they matter,
    without injecting their content. Agent reads them on demand.
    Returns (mode, context_lines) where mode is "fresh" or "rebuild".
    """
    _gates = gates if gates is not None else GATES
    feedback_files = []
    seen_feedback = set()
    for gate_key in sorted(_gates):
        gate_info = _gates[gate_key]
        entries = gate_info if isinstance(gate_info, list) else [gate_info]
        for entry in entries:
            artifact = entry.get("artifact", "")
            fb_name = feedback_name_for_artifact(artifact) if artifact else None
            if fb_name:
                fb_path = os.path.join(sflo_dir, fb_name)
                if os.path.isfile(fb_path):
                    seen_feedback.add(os.path.abspath(fb_path))
                    feedback_files.append(
                        f"  - {fb_path} (gate {gate_key} found issues — fix before proceeding)"
                    )
    try:
        for filename in sorted(os.listdir(sflo_dir)):
            if not filename.endswith("-FEEDBACK.md"):
                continue
            fb_path = os.path.join(sflo_dir, filename)
            if (
                os.path.isfile(fb_path)
                and os.path.abspath(fb_path) not in seen_feedback
            ):
                feedback_files.append(
                    f"  - {fb_path} (feedback file found — fix before proceeding)"
                )
    except OSError:
        pass

    is_rebuild = len(feedback_files) > 0

    # Prior gate artifacts that exist on disk
    prior_artifacts = []
    for prev_gate in sorted(_gates):
        if prev_gate < gate_num:
            g_info = _gates[prev_gate]
            if isinstance(g_info, list):
                for entry in g_info:
                    artifact = entry.get("artifact")
                    if artifact:
                        path = os.path.join(sflo_dir, artifact)
                        if os.path.isfile(path):
                            prior_artifacts.append(f"  - {path}")
            else:
                artifact = g_info.get("artifact")
                if artifact:
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


def auto_transition(state, sflo_dir, gates=None):
    """If at gate-N and the artifact already exists, transition to check-N.

    Returns True if a transition was made.
    """
    _gates = gates if gates is not None else GATES
    gate_match = re.match(r"gate-(\d+\.?\d*)", state["current_state"])
    if gate_match:
        n_str = gate_match.group(1)
        n = float(n_str)
        n = int(n) if n == int(n) else n
        if n not in _gates:
            return False
        gate_info = _gates[n]
        if isinstance(gate_info, list):
            artifact_entries = [e for e in gate_info if e.get("artifact")]
            if not artifact_entries:
                return False
            all_exist = all(
                os.path.isfile(os.path.join(sflo_dir, e["artifact"]))
                for e in artifact_entries
            )
            if all_exist:
                state["current_state"] = f"check-{n_str}"
                write_state(sflo_dir, state)
                return True
        else:
            artifact = gate_info.get("artifact")
            if not artifact:
                return False
            artifact_path = os.path.join(sflo_dir, artifact)
            if os.path.isfile(artifact_path):
                state["current_state"] = f"check-{n_str}"
                write_state(sflo_dir, state)
                return True
    return False


def _resolve_agent_path(entry, sflo_base, roles, assignments):
    """Resolve agent path: pipeline.yaml agent > agents[0] > roles config > scout > default.

    Priority chain (first match wins):
      1. entry["agent"]   — singular explicit declaration
      2. entry["agents"][0] — first entry of plural list (primary agent)
      3. roles[role]["agent"] — role config default
      4. assignments[role]  — scout-assigned path
      5. sflo_base/agents/<role> — convention fallback
    """
    entry_role = entry.get("role", "unknown")
    role_cfg = roles.get(entry_role, {})

    # 1. Singular explicit agent
    pipeline_agent = entry.get("agent")
    if pipeline_agent:
        # YAML-derived paths use forward slashes; normpath converts the
        # joined result to OS-native separators (avoids mixed separators
        # like C:\x\agents/pm on Windows). Absolute paths pass through
        # unchanged in behaviour.
        return (
            os.path.normpath(os.path.join(sflo_base, pipeline_agent))
            if not os.path.isabs(pipeline_agent)
            else pipeline_agent
        )

    # 2. Plural agents list — first entry is the primary agent
    agents_list = entry.get("agents")
    if agents_list:
        primary = agents_list[0]
        return (
            os.path.normpath(os.path.join(sflo_base, primary))
            if not os.path.isabs(primary)
            else primary
        )

    # 3-5. Role config > scout assignment > convention default
    return role_cfg.get(
        "agent",
        assignments.get(
            entry_role,
            os.path.normpath(os.path.join(sflo_base, "agents", entry_role)),
        ),
    )


def _compute_scout(state, sflo_base, roles, **_kw):
    """Handle S_SCOUT state."""
    scout_cfg = SCOUT_CONFIG
    return {
        "state": "scout",
        "action": "spawn_agent",
        "agent": {
            "role": "scout",
            "path": scout_cfg.get(
                "agent",
                roles.get("scout", {}).get(
                    "agent", os.path.join(sflo_base, "agents", "scout")
                ),
            ),
            "model": scout_cfg.get("model", roles.get("scout", {}).get("model")),
            "tools_mode": scout_cfg.get(
                "tools", roles.get("scout", {}).get("tools", "readonly")
            ),
            "reads": [os.path.join(sflo_base, "agents", "scout", "SOUL.md")],
            "skills": resolve_skill_paths(scout_cfg.get("skills", []), sflo_base),
            "instruction": "Read user prompt, scan agents/ for matches, return structured assignments.",
        },
    }


def _compute_gate(n, n_str, sflo_dir, sflo_base, roles, assignments, gates, **_kw):
    """Handle gate-N state — dispatch to last-gate / parallel / custom / single."""
    _gates = gates
    last_gate = _last_gate(gates=gates)

    if n == last_gate:
        last_info = _gates[last_gate]
        last_info_dict = last_info[0] if isinstance(last_info, list) else last_info
        prior_reads = []
        for g in _sorted_gates(gates=gates):
            if g >= last_gate:
                continue
            g_info = _gates[g]
            if isinstance(g_info, list):
                for entry in g_info:
                    if entry.get("artifact"):
                        prior_reads.append(os.path.join(sflo_dir, entry["artifact"]))
            else:
                if g_info.get("artifact"):
                    prior_reads.append(os.path.join(sflo_dir, g_info["artifact"]))
        return {
            "state": f"gate-{n_str}",
            "action": "produce_artifact",
            "role": last_info_dict.get("role", "sflo"),
            "artifact": last_info_dict.get("artifact", f"gate-{n}"),
            "sflo_dir": sflo_dir,
            "reads": prior_reads,
            "gate_doc": os.path.join(sflo_base, last_info_dict.get("gate_doc", ""))
            if last_info_dict.get("gate_doc")
            else None,
            "skills": resolve_skill_paths(
                last_info_dict.get("skills", []), sflo_base
            ),
        }

    gate_info = _gates[n]

    # List-based parallel gates
    if isinstance(gate_info, list):
        agents = []
        for entry in gate_info:
            entry_role = entry.get("role", "unknown")
            entry_path = _resolve_agent_path(entry, sflo_base, roles, assignments)
            if entry.get("runner"):
                agents.append(
                    {
                        "role": entry_role,
                        "runner": entry["runner"],
                        "validator": entry.get("validator"),
                        "artifact": entry.get("artifact"),
                        "gate_doc": os.path.join(sflo_base, entry["gate_doc"])
                        if entry.get("gate_doc")
                        else None,
                        "gate_num": n,
                    }
                )
            else:
                role_cfg = roles.get(entry_role, {})
                agents.append(
                    {
                        "role": entry_role,
                        "path": entry_path,
                        "model": entry.get("model") or role_cfg.get("model"),
                        "tools_mode": entry.get("tools") or role_cfg.get("tools"),
                        "thinking": entry.get("thinking") or role_cfg.get("thinking"),
                        "effort": entry.get("effort") or role_cfg.get("effort"),
                        "reads": [
                            os.path.join(sflo_base, entry["gate_doc"])
                            if entry.get("gate_doc")
                            else "",
                            os.path.join(entry_path, "SOUL.md"),
                        ],
                        "artifact": entry.get("artifact"),
                        "produces": os.path.join(sflo_dir, entry["artifact"])
                        if entry.get("artifact")
                        else None,
                        "gate_num": n,
                        "skills": resolve_skill_paths(
                            entry.get("skills", []), sflo_base
                        ),
                        "agents": resolve_agent_paths(
                            entry.get("agents", []), sflo_base
                        ),
                        "mcp": entry.get("mcp"),
                        "allow_task": entry.get("allow_task"),
                    }
                )
        return {
            "state": f"gate-{n_str}",
            "action": "spawn_parallel",
            "agents": agents,
        }

    # Custom runner gate
    if gate_info.get("runner"):
        return {
            "state": f"gate-{n_str}",
            "action": "run_custom_gate",
            "runner": gate_info["runner"],
            "validator": gate_info.get("validator"),
            "gate_num": n,
            "gate_doc": os.path.join(sflo_base, gate_info["gate_doc"])
            if gate_info.get("gate_doc")
            else None,
            "sflo_dir": sflo_dir,
            "artifact": gate_info.get("artifact"),
            "on_reject_restart_at": gate_info.get("on_reject_restart_at"),
        }

    # Single-agent gate
    role = gate_info["role"]
    agent_path = _resolve_agent_path(gate_info, sflo_base, roles, assignments)
    role_cfg = roles.get(role, {})
    return {
        "state": f"gate-{n_str}",
        "action": "spawn_agent",
        "agent": {
            "role": role,
            "path": agent_path,
            "model": gate_info.get("model") or role_cfg.get("model"),
            "tools_mode": gate_info.get("tools") or role_cfg.get("tools"),
            "thinking": gate_info.get("thinking") or role_cfg.get("thinking"),
            "effort": gate_info.get("effort") or role_cfg.get("effort"),
            "reads": agent_reads(n, agent_path, sflo_base, sflo_dir, gates=gates),
            "produces": os.path.join(sflo_dir, gate_info["artifact"]),
            "gate_num": n,
            "skills": resolve_skill_paths(gate_info.get("skills", []), sflo_base),
            "agents": resolve_agent_paths(gate_info.get("agents", []), sflo_base),
            "mcp": gate_info.get("mcp"),
            "allow_task": gate_info.get("allow_task"),
        },
    }


def _compute_check(n, n_str, sflo_dir, gates, **_kw):
    """Handle check-N state — run validation."""
    passed, checks = validate_gate(n, sflo_dir, gates=gates)
    return {
        "state": f"check-{n_str}",
        "action": "validated" if passed else "check_failed",
        "gate": n,
        "pass": passed,
        "checks": checks,
    }


def _compute_escalate(state, **_kw):
    """Handle S_ESCALATE state."""
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


def compute_next(state, sflo_dir, gates=None):
    """Given current state, return the next action as a dict.

    Pure query — does NOT mutate state or write to disk.
    Dispatches to strategy handlers based on current_state prefix.
    """
    _gates = gates if gates is not None else GATES
    current = state["current_state"]
    sflo_base = resolve_sflo_base()
    assignments = state.get("assignments", {})
    roles = state.get("roles", state.get("bindings", {}))

    ctx = dict(
        sflo_dir=sflo_dir,
        sflo_base=sflo_base,
        roles=roles,
        assignments=assignments,
        gates=_gates,
        state=state,
    )

    if current == S_SCOUT:
        return _compute_scout(**ctx)

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
        if n not in _gates:
            return {
                "state": current,
                "action": "unknown",
                "error": f"Unknown gate: {n}",
            }
        return _compute_gate(n, n_str, **ctx)

    check_match = re.match(r"check-(\d+\.?\d*)", current)
    if check_match:
        n_str = check_match.group(1)
        n = float(n_str)
        n = int(n) if n == int(n) else n
        return _compute_check(n, n_str, **ctx)

    if current == S_DONE:
        return {"state": "done", "action": "pipeline_complete"}

    if current == S_ESCALATE:
        return _compute_escalate(**ctx)

    return {"state": current, "action": "unknown", "error": f"Unknown state: {current}"}


def apply_transition(state, result, sflo_dir, gates=None):
    """Apply the state transition implied by a compute_next result.

    Mutates state and writes to disk. Returns the enriched result dict.
    """
    _gates = gates if gates is not None else GATES
    action = result.get("action")
    n = result.get("gate")

    if action != "validated" and action != "check_failed":
        return result

    if action == "validated":
        state["gates"].setdefault(str(n), {"status": "waiting"})["status"] = "done"
        last_gate = _last_gate(gates=gates)
        if n == last_gate:
            state["current_state"] = S_DONE
        else:
            next_gate = _next_gate_after(n, gates=gates)
            state["current_state"] = f"gate-{next_gate}"
        write_state(sflo_dir, state)

        # Clean up feedback files once they've served their purpose
        sorted_gates = _sorted_gates(gates=gates)
        inner_loop_restart = sorted_gates[1] if len(sorted_gates) >= 2 else None
        inner_loop_gate = sorted_gates[-3] if len(sorted_gates) >= 3 else None
        outer_loop_gate = sorted_gates[-2] if len(sorted_gates) >= 2 else None

        # Archive feedback files to logs/ once they've served their purpose
        from .archive import archive_to_logs

        feedback_to_archive = list(feedback_paths_for_gate(sflo_dir, n, gates=gates))
        if n == inner_loop_restart:
            feedback_to_archive.extend(
                feedback_paths_for_gate(sflo_dir, outer_loop_gate, gates=gates)
            )
        if n == inner_loop_gate:
            feedback_to_archive.extend(
                feedback_paths_for_gate(sflo_dir, inner_loop_gate, gates=gates)
            )
        if feedback_to_archive:
            archive_to_logs(sflo_dir, list(dict.fromkeys(feedback_to_archive)))

        next_action = compute_next(state, sflo_dir, gates=gates)
        result["next"] = next_action
        return result

    if action == "check_failed":
        # Get second-to-last and third-to-last gate keys for inner/outer loop logic
        sorted_gates = _sorted_gates(gates=gates)
        last_gate = sorted_gates[-1] if sorted_gates else None
        # Inner loop gate is the one before the last (gate 3 in default pipeline)
        inner_loop_gate = sorted_gates[-3] if len(sorted_gates) >= 3 else None
        # Outer loop gate is the second to last (gate 4 in default pipeline)
        outer_loop_gate = sorted_gates[-2] if len(sorted_gates) >= 2 else None
        # Inner loop restart gate is gate 2 in default pipeline
        inner_loop_restart = sorted_gates[1] if len(sorted_gates) >= 2 else None

        # Config-driven restart: gates with on_reject_restart_at loop back
        # to a specific gate without touching inner_loops/outer_loops.
        raw_gate = _gates.get(n, {})
        gate_info = raw_gate[0] if isinstance(raw_gate, list) else raw_gate
        custom_restart = gate_info.get("on_reject_restart_at")
        if custom_restart is not None:
            gate_retries = state.get("gate_retries", {})
            gate_key = str(n)
            gate_retries[gate_key] = gate_retries.get(gate_key, 0) + 1
            state["gate_retries"] = gate_retries

            if gate_retries[gate_key] >= INNER_LOOP_MAX:
                artifact_name = gate_info.get("artifact", f"gate-{n}")
                state["current_state"] = S_ESCALATE
                state["escalate_reason"] = (
                    f"Gate {n} ({artifact_name}) rejected {gate_retries[gate_key]} "
                    f"rebuilds. Human decision needed."
                )
                state["escalate_options"] = [
                    "override (ship-anyway)",
                    "fix upstream gate",
                    "kill",
                ]
                write_state(sflo_dir, state)
                return compute_next(state, sflo_dir, gates=gates)
            else:
                restart_gate = custom_restart
                if restart_gate not in _gates:
                    state["current_state"] = S_ESCALATE
                    state["escalate_reason"] = (
                        f"Gate {n} on_reject_restart_at={restart_gate} "
                        f"references nonexistent gate. Check pipeline.yaml."
                    )
                    state["escalate_options"] = ["fix pipeline.yaml", "kill"]
                    write_state(sflo_dir, state)
                    return compute_next(state, sflo_dir, gates=gates)
                from .archive import archive_to_logs
                from .validate import save_gate_feedback

                # Preserve the rejecting review's evidence before its artifact
                # is archived and Developer is restarted.
                save_gate_feedback(sflo_dir, n, gates=_gates)

                artifacts_to_archive = []
                for gate_num in _sorted_gates(gates=_gates):
                    if gate_num < restart_gate:
                        continue
                    raw = _gates.get(gate_num, {})
                    entries = raw if isinstance(raw, list) else [raw]
                    for entry in entries:
                        artifact = entry.get("artifact") if isinstance(entry, dict) else None
                        if not artifact:
                            continue
                        artifact_path = os.path.join(sflo_dir, artifact)
                        if os.path.isfile(artifact_path):
                            artifacts_to_archive.append(artifact_path)
                    state["gates"].setdefault(str(gate_num), {})["status"] = "pending"
                if artifacts_to_archive:
                    archive_to_logs(sflo_dir, artifacts_to_archive)
                state["current_state"] = f"gate-{restart_gate}"
                write_state(sflo_dir, state)
                return {
                    **result,
                    "state": f"loop-gate-{n}",
                    "action": "loop_back",
                    "gate_retry_count": gate_retries[gate_key],
                    "max": INNER_LOOP_MAX,
                    "next": compute_next(state, sflo_dir, gates=gates),
                }

        if n == inner_loop_gate:
            state["inner_loops"] += 1
            if state["inner_loops"] >= INNER_LOOP_MAX:
                next_gate = _next_gate_after(n, gates=gates)
                state["current_state"] = f"gate-{next_gate}"
                write_state(sflo_dir, state)
                return {
                    **result,
                    "state": "loop-inner-exhausted",
                    "action": "proceed",
                    "note": f"Inner loop exhausted ({INNER_LOOP_MAX} Dev<>QA cycles). Proceeding to PM verification.",
                    "inner_count": state["inner_loops"],
                    "next": compute_next(state, sflo_dir, gates=gates),
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
                    "next": compute_next(state, sflo_dir, gates=gates),
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
                # Save the outer gate verdict before cleanup deletes the gate
                # artifact. The feedback copy persists for dev's context map.
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
                    "next": compute_next(state, sflo_dir, gates=gates),
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
                _raw = _gates.get(n)
                artifact_name = (
                    (
                        _raw[0].get("artifact", f"gate-{n}")
                        if isinstance(_raw, list)
                        else _raw.get("artifact", f"gate-{n}")
                    )
                    if _raw
                    else f"gate-{n}"
                )
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
                return compute_next(state, sflo_dir, gates=gates)
            else:
                # Loop back: re-run the gate's agent with the failed
                # artifact deleted so it rebuilds from scratch.
                failed_checks = [
                    c for c in result.get("checks", []) if not c.get("pass", True)
                ]
                failed_names = [c.get("name", "?") for c in failed_checks]
                _raw = _gates.get(n)
                artifact_name = (
                    (
                        _raw[0].get("artifact", f"gate-{n}")
                        if isinstance(_raw, list)
                        else _raw.get("artifact", f"gate-{n}")
                    )
                    if _raw
                    else f"gate-{n}"
                )
                artifact_path = os.path.join(sflo_dir, artifact_name)
                if os.path.isfile(artifact_path):
                    from .archive import archive_to_logs

                    archive_to_logs(sflo_dir, [artifact_path])
                state["gates"].setdefault(gate_key, {})["status"] = "pending"
                state["current_state"] = f"gate-{n}"
                write_state(sflo_dir, state)
                return {
                    **result,
                    "state": f"gate-retry-{n}",
                    "action": "loop_back",
                    "gate_retry_count": gate_retries[gate_key],
                    "max": INNER_LOOP_MAX,
                    "failed_checks": failed_names,
                    "next": compute_next(state, sflo_dir, gates=gates),
                }

    return result
