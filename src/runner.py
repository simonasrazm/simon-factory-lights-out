#!/usr/bin/env python3
"""SFLO Runner — enforced pipeline execution.

Uses the runtime's own agent-spawning mechanism (Claude Agent SDK or
OpenClaw sessions_spawn) to run the pipeline. The runner controls what
goes in, what comes out, and where artifacts are written. Spawned agents
cannot bypass the pipeline.

Usage (called by runtime hook/skill, not directly):
    from src.runner import run_pipeline
    result = await run_pipeline("Build a click counter", sflo_dir=".sflo")

CLI (for testing):
    python3 sflo/src/runner.py "Build a click counter" [--sflo-dir .sflo] [--quiet] [--bindings PATH]

    --bindings PATH   Path to bindings.yaml (overrides auto-resolve).
    --sflo-dir PATH   Path to .sflo state directory (default: .sflo).
    --quiet           Suppress verbose logging to stderr.
"""

import asyncio
import atexit
import datetime as _datetime
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time as _time
import traceback

# Sentinel that uses `re` at module level so ruff won't strip the import.
# Module-level `import re` enforced by test_runner_re.py — required because
# inner functions reference re without their own import (post-fix).
_RE_MODULE_GUARD = re.compile(r"")


# ---------------------------------------------------------------------------
# Signal handler — log signal name + timestamp before exit so we know
# what killed the process (SIGHUP from terminal close, SIGTERM from
# Claude CLI cleanup, etc.). Without this, external kills leave zero
# trace in pipeline.log.
# ---------------------------------------------------------------------------


def install_signal_handler(sflo_dir=None):
    """Public alias — see _install_signal_handler docstring."""
    return _install_signal_handler(sflo_dir)


def _install_signal_handler(sflo_dir=None):
    """Install signal handlers + atexit + DEATH-MARKER writer.

    Hardened against the H6′ silent-death class: when the controlling pty
    is closed (e.g. Claude Desktop's `beforeQuitForUpdate` running
    `local-session-pty-cleanup`), stderr writes to that pty raise or
    block — the prior handler crashed on its first `print` and never
    reached `sys.exit`, leaving the kernel's default SIGHUP terminate
    to run silently. This version:

      • Wraps stderr writes in try/except so a broken pty doesn't kill
        the handler before it logs.
      • Always writes to pipeline.log (regular file, robust) and
        DEATH-MARKER.json (forensic trail for external monitors).
      • Exits via os._exit() — bypasses Python's signal-handling state
        machine so we never end up returning into interrupted bytecode.
      • Catches SIGQUIT in addition to SIGHUP/SIGTERM/SIGINT.
      • Per CR2-6: SIGPIPE is NOT trapped — Python's default-ignore for
        SIGPIPE is load-bearing for normal BrokenPipeError flow.
      • atexit hook fires on clean exits + any uncaught exception path
        (caveat: not on os._exit / SIGKILL).
    """

    def _safe_stderr(msg):
        """Print to stderr, swallowing OSError when stdio is broken."""
        try:
            print(msg, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass

    def _write_death_marker(reason, sig_num=None):
        if not sflo_dir:
            return
        try:
            payload = {
                "pid": os.getpid(),
                "reason": reason,
                "wall_time": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
            }
            if sig_num is not None:
                sig_name = (
                    signal.Signals(sig_num).name
                    if hasattr(signal, "Signals")
                    else str(sig_num)
                )
                payload["signal"] = sig_num
                payload["signal_name"] = sig_name
            marker_path = os.path.join(sflo_dir, "DEATH-MARKER.json")
            with open(marker_path, "w") as f:
                json.dump(payload, f, indent=2)
        except OSError:
            pass

    def _handler(signum, frame):
        sig_name = (
            signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        )
        ts = _time.strftime("%H:%M:%S")
        msg = f"[{ts}]   SIGNAL: received {sig_name} (sig {signum}) — exiting\n"
        _safe_stderr(msg)
        # Append to pipeline.log (regular file — robust to closed pty).
        if sflo_dir:
            try:
                log_path = os.path.join(sflo_dir, "pipeline.log")
                with open(log_path, "a") as f:
                    f.write(msg)
            except OSError:
                pass
        # Forensic marker — survives even when pipeline.log path is unwritable.
        _write_death_marker(f"signal_{sig_name}", signum)
        # os._exit, NOT sys.exit: skip Python interpreter shutdown machinery
        # which can re-enter signal-handling and re-block on closed stdio.
        os._exit(128 + signum)

    # Per CR2-6: SIGPIPE intentionally NOT in this list — Python's default
    # SIG_IGN for SIGPIPE lets normal pipe-write code raise BrokenPipeError
    # which higher layers handle. Trapping it here would convert every
    # broken-pipe write into immediate exit, breaking asyncio flow.
    sig_candidates = (
        getattr(signal, "SIGHUP", None),
        getattr(signal, "SIGTERM", None),
        getattr(signal, "SIGINT", None),
        getattr(signal, "SIGQUIT", None),
    )
    for sig in sig_candidates:
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (OSError, ValueError):
            pass  # some signals can't be caught in certain contexts

    # Clean-exit + uncaught-exception path. NOT triggered by os._exit or
    # SIGKILL, but covers the normal `sys.exit` / `raise SystemExit` cases
    # where pipeline.log may already say what happened — DEATH-MARKER then
    # corroborates with a structured "atexit_clean" reason.
    if sflo_dir:
        atexit.register(_write_death_marker, "atexit_clean")


# Allow running as script or module
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.bindings import (
        parse_bindings,
        resolve_bindings_path,
        load_exclude_agents,
        load_exclude_agent_dirs,
    )
    from src.state import (
        read_state,
        write_state,
        make_initial_state,
        acquire_lock,
        release_lock,
        state_path,
    )
    from src.machine import (
        auto_transition,
        compute_next,
        apply_transition,
        build_context_map,
    )
    from src.constants import SFLO_ROOT, S_DONE, S_ESCALATE, GATES, INNER_LOOP_MAX
    from src.archive import archive_to_logs
    from src.preflight import preflight_check, check_browser
    from src import evals as _evals
    from src.evals.integration import call_adapter_with_evals
else:
    from .bindings import (
        parse_bindings,
        resolve_bindings_path,
        load_exclude_agents,
        load_exclude_agent_dirs,
    )
    from .state import (
        read_state,
        write_state,
        make_initial_state,
        acquire_lock,
        release_lock,
        state_path,
    )
    from .machine import (
        auto_transition,
        compute_next,
        apply_transition,
        build_context_map,
    )
    from .constants import SFLO_ROOT, S_DONE, S_ESCALATE, GATES, INNER_LOOP_MAX
    from .archive import archive_to_logs
    from .preflight import preflight_check, check_browser
    from . import evals as _evals
    from .evals.integration import call_adapter_with_evals


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def _locked_write_state(sflo_dir, state):
    """Write state.json under a file lock to prevent race with stop-hook re-injection."""
    fd = acquire_lock(sflo_dir)
    try:
        write_state(sflo_dir, state)
    finally:
        release_lock(sflo_dir, fd)


# ---------------------------------------------------------------------------
# Runtime Adapters — imported from adapters package
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from src.adapters import (
        RuntimeAdapter,
        get_adapter,
    )
else:
    from .adapters import (
        RuntimeAdapter,
        get_adapter,
    )


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def read_file(path):
    """Read a file, return content or error message."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, FileNotFoundError) as e:
        return f"[ERROR reading {path}: {e}]"


def make_logger(sflo_dir, verbose=True):
    """Create a logger that writes to stderr and .sflo/pipeline.log.

    The returned callable has a ``close()`` method to flush and close the
    underlying file handle.  Callers should invoke ``log.close()`` (or use
    ``atexit``) to ensure all buffered lines reach disk before the process
    exits.
    """
    import logging as _logging

    os.makedirs(sflo_dir, exist_ok=True)
    log_path = os.path.join(sflo_dir, "pipeline.log")
    log_file = open(log_path, "a", encoding="utf-8")

    # Register atexit shutdown so the file is flushed even on unclean exits.
    import atexit as _atexit

    def _close_log():
        try:
            if not log_file.closed:
                log_file.flush()
                log_file.close()
        except OSError:
            pass

    _atexit.register(_close_log)

    def log(msg):
        ts = _datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        log_file.write(line + "\n")
        log_file.flush()
        if verbose:
            print(msg, file=sys.stderr)

    log._file = log_file  # keep reference to close later
    log.close = _close_log
    return log


def format_validation_feedback(checks):
    """Format failed validation checks into actionable feedback for the agent."""
    failed = [c for c in checks if not c.get("pass")]
    if not failed:
        return ""
    lines = [
        "## Validation Errors — Fix These\n",
        "Your artifact failed the following automated checks:\n",
    ]
    for c in failed:
        name = c.get("name", "unknown")
        detail = c.get("detail", "")
        lines.append(f"- **{name}**: {detail}" if detail else f"- **{name}**")
    lines.append(
        "\nRevise the artifact to pass all checks. "
        "Write it to the EXACT same path. "
        "Do NOT remove sections that already pass."
    )
    return "\n".join(lines)


def build_agent_prompt(
    agent_info, user_prompt, sflo_dir, runtime=None, output_dir=None
):
    """Build system prompt and user prompt for a gate agent.

    Agents get: SOUL + gate doc as system prompt, user request + context
    map + task as user prompt. No artifact content is injected — agents
    pull what they need on demand using the file paths in the context map.

    When output_dir is set and agent role is dev/qa, an explicit instruction
    tells the agent to put user deliverables (app code, HTML, data files)
    into output_dir. Pipeline artifacts still go to sflo_dir via absolute paths.
    """
    reads = agent_info.get("reads", [])
    gate_num = agent_info.get("gate_num")

    # System prompt: agent SOUL.md + gate doc
    system_parts = []
    if len(reads) >= 2:
        soul_content = read_file(reads[1])
        system_parts.append(soul_content)
    if len(reads) >= 1:
        gate_content = read_file(reads[0])
        system_parts.append(f"## Gate Document\n\n{gate_content}")

    system_prompt = "\n\n---\n\n".join(system_parts) if system_parts else ""

    # User prompt: request + context map + task
    user_parts = [f"## User Request\n\n{user_prompt}"]

    # Context: for Claude, give file paths (agent reads on demand).
    # For ollama, inject actual content — small models don't proactively read files.
    if gate_num is not None:
        _mode, context_text = build_context_map(gate_num, sflo_dir)
        if runtime == "ollama":
            # Don't inject artifact content — models have Read tool and
            # can read files themselves. Injecting makes models lazy.
            # Instead, add explicit instruction to read the files.
            user_parts.append(context_text)
            user_parts.append(
                "\nYou MUST use the read tool to read each prior artifact listed above "
                "before starting your work. Do not guess what they contain."
            )
        else:
            user_parts.append(context_text)

    produces = agent_info.get("produces", "")
    if produces:
        abs_produces = os.path.abspath(produces)
        artifact_name = os.path.basename(produces)
        role = agent_info.get("role", "")
        if runtime == "ollama":
            write_instruction = (
                f"You MUST write the file using bash:\n"
                f"  mkdir -p {os.path.dirname(abs_produces)}\n"
                f"  cat <<'ARTIFACT_EOF' > {abs_produces}\n"
                f"  <your content here>\n"
                f"  ARTIFACT_EOF\n"
                f"Do NOT put the artifact content in your response — write it to the file."
            )
            # PM: acceptance criteria MUST use checkbox format
            if role == "pm" and artifact_name == "SCOPE.md":
                write_instruction += (
                    "\n\nAcceptance criteria MUST use this exact format:\n"
                    "- [ ] AC1: description\n"
                    "- [ ] AC2: description\n"
                    "Do NOT use numbered lists or plain dashes for ACs."
                )
            # Dev: read SCOPE ACs, build deliverable, verify, write status
            if role == "dev":
                scope_path = os.path.join(sflo_dir, "SCOPE.md")
                write_instruction = (
                    f"Follow this order:\n"
                    f"1. Read {scope_path} to see the acceptance criteria.\n"
                    f"2. Build the deliverable the user asked for. "
                    f"Use `write` for the first part, `append` for subsequent parts "
                    f"if the file is large. Write COMPLETE code, no placeholders.\n"
                    f"3. Verify it works — run it, check output, confirm no errors.\n"
                    f"4. Write {artifact_name} to {abs_produces}. "
                    f"List each AC from SCOPE.md with [x] and how it was addressed."
                )
            # QA must actually test the deliverable, not just read BUILD-STATUS
            elif role == "qa":
                write_instruction = (
                    f"IMPORTANT: You are QA. Do NOT just read BUILD-STATUS.md and grade it.\n"
                    f"You MUST use tools to verify the actual deliverable:\n"
                    f"1. Find the output file the developer created (check BUILD-STATUS.md for path).\n"
                    f"2. Read the source code — check for syntax errors, missing logic.\n"
                    f"3. If executable — run it and check output.\n"
                    f"4. If browser tools are available — use them to open and test web deliverables.\n"
                    f"5. THEN write {artifact_name} to {abs_produces} with SPECIFIC evidence from your tests.\n"
                    f"Grade F if deliverable missing. Grade D if errors found. Generic 'PASS' without "
                    f"evidence = not acceptable."
                )
        else:
            write_instruction = (
                "Use the available tools to create the file (Write tool, or bash: "
                "cat <<'EOF' > path)."
            )
        task_text = (
            f"\n## Your Task\n\n"
            f"Write the artifact `{artifact_name}` to this EXACT path: {abs_produces}\n"
            f"{write_instruction}\n"
            f"Follow the gate document template EXACTLY.\n"
            f"Every section in the template is REQUIRED — do not skip any.\n"
            f"The scaffold validates the artifact automatically. Missing sections cause gate failure.\n"
            f"Create the parent directory if it doesn't exist."
        )
        # Tell dev/qa where user-facing deliverables (built app code, HTML, data) belong.
        # Pipeline artifacts (SCOPE.md, BUILD-STATUS.md) still go to sflo_dir via abs paths above.
        if output_dir and role in ("dev", "qa"):
            abs_output = os.path.abspath(output_dir)
            task_text += (
                f"\n\n## User Deliverables Directory\n\n"
                f"Put all USER-FACING project files (app code, HTML, CSS, JS, data files, "
                f"subdirectories for the build) under: `{abs_output}`\n"
                f"This is a SEPARATE location from the pipeline artifact path above. "
                f"The artifact `{artifact_name}` goes to `{abs_produces}`. "
                f"Everything else the user asked you to build goes to `{abs_output}`.\n"
                f"Use absolute paths rooted at `{abs_output}` (e.g. `{abs_output}/index.html`)."
            )
        user_parts.append(task_text)

    user_msg = "\n\n---\n\n".join(user_parts)
    return system_prompt, user_msg


# ---------------------------------------------------------------------------
# Pipeline Runner
# ---------------------------------------------------------------------------


async def run_pipeline(
    user_prompt,
    sflo_dir=".sflo",
    output_dir=None,
    runtime=None,
    verbose=True,
    assignments=None,
    bindings=None,
):
    """Run the full SFLO pipeline.

    Args:
        user_prompt: What to build.
        sflo_dir: Where to store pipeline state and artifacts.
        output_dir: User deliverables directory (agent cwd). If None, uses inherited cwd.
        runtime: "openclaw", "claude-code", or None (auto-detect).
        verbose: Print progress to stderr.
        assignments: Optional dict with pre-computed agent assignments
            (keys: pm, dev, qa). When provided, core's scout LLM call is
            skipped entirely. Used by extended runners to avoid the
            double-scout waste: ext's run_scout_with_complexity has already
            picked agents AND classified complexity, so core re-running
            scout would be pure overhead. Stale-detect still runs against
            the real on-disk state.json — prior-run artifacts get wiped or
            reused based on prompt match, independent of this kwarg.

    Returns:
        dict with final state, artifacts, and pipeline summary.
    """
    adapter = get_adapter(runtime)
    log = make_logger(sflo_dir, verbose)

    # --- Init ---
    bindings_path = resolve_bindings_path(explicit=bindings)
    if not bindings_path:
        return {"ok": False, "error": "bindings.yaml not found"}

    roles, err = parse_bindings(bindings_path)
    if err:
        return {"ok": False, "error": err}

    # --- Eval framework: load plugins from bindings.yaml `evals:` section ---
    # Fail-safe: any load error logs a warning; pipeline always continues.
    # Empty / missing `evals:` section = no-op (zero overhead).
    try:
        from pathlib import Path as _Path

        _evals.load_evals_from_bindings(_Path(bindings_path))
    except Exception as _eval_load_err:
        # Non-fatal: warn but never block pipeline startup
        print(f"  [evals] load warning: {_eval_load_err}", file=sys.stderr)

    os.makedirs(sflo_dir, exist_ok=True)

    # --- Stale-artifact detection ---
    #
    # Compare the current user prompt against the prompt stored in state.json
    # from the prior run. Three cases:
    #
    #   (a) No prior state.json -> first run in this dir, nothing to compare
    #   (b) Prior prompt matches current prompt (after whitespace normalize)
    #       -> resume mode, keep all gate artifacts intact, reuse cached
    #          assignments to skip scout
    #   (c) Prior prompt differs -> stale artifacts from a different task,
    #       wipe gate artifacts so the pipeline rebuilds from scratch
    #
    # Direct byte compare (after normalizing whitespace runs) — simpler than
    # hashing, debuggable on disk, equivalent semantics. Single-word edits
    # trigger fresh runs because we cannot tell whether the meaning changed
    # without an LLM call; safe over-regeneration is the cheaper failure mode.

    def _norm_prompt(s):
        return " ".join((s or "").split())

    # --- Resume detection ---
    #
    # Compare current prompt to prior state. Three cases:
    #   (a) Same prompt → resume: restore full prior state (assignments,
    #       loop counters, gate statuses, current_state). Avoids resetting
    #       counters on crash-resume, which would allow infinite loops.
    #   (b) Different prompt → fresh: archive old artifacts, start clean.
    #   (c) No prior state → fresh run.
    #
    # Safety net: Warn if state.json exists at project root (not in .sflo/).
    # Archive if state.json is stale (>7 days old or wrong prompt context).
    cached_assignments = None
    is_resume = False
    resumed_state = None
    prior_state_path = state_path(sflo_dir)

    # Check if state.json exists at wrong location (project root instead of .sflo/)
    if sflo_dir == ".sflo":
        project_root_state = "state.json"
        if os.path.isfile(project_root_state) and os.path.abspath(
            project_root_state
        ) != os.path.abspath(prior_state_path):
            if verbose:
                print(
                    "  WARNING: state.json found at project root (should be in .sflo/)",
                    file=sys.stderr,
                )
            # Archive stale root-level state.json
            archive_to_logs(sflo_dir, [project_root_state])
            if verbose:
                print(
                    "  Archived stale state.json from project root to logs/",
                    file=sys.stderr,
                )

    if os.path.isfile(prior_state_path):
        try:
            # Check file age (safety net for stale state)
            file_stat = os.stat(prior_state_path)
            file_age_days = (_time.time() - file_stat.st_mtime) / 86400.0
            STATE_MAX_AGE_DAYS = 7

            if file_age_days > STATE_MAX_AGE_DAYS:
                # State too old — archive and start fresh
                if verbose:
                    print(
                        f"  Stale state — state.json is {file_age_days:.1f} days old (max {STATE_MAX_AGE_DAYS}), archiving",
                        file=sys.stderr,
                    )
                _stale_names = [
                    "state.json",
                    "SCOPE.md",
                    "BUILD-STATUS.md",
                    "QA-REPORT.md",
                    "PM-VERIFY.md",
                    "SHIP-DECISION.md",
                    "QA-FEEDBACK.md",
                    "PM-FEEDBACK.md",
                    "pipeline.log",
                ]
                _stale_paths = [os.path.join(sflo_dir, n) for n in _stale_names]
                archive_to_logs(sflo_dir, _stale_paths)
            else:
                # State recent enough — check prompt
                with open(prior_state_path, "r") as f:
                    prior_state = json.load(f)
                prior_prompt = prior_state.get("prompt")
                prior_assignments = prior_state.get("assignments") or {}

                if prior_prompt is not None and _norm_prompt(
                    prior_prompt
                ) == _norm_prompt(user_prompt):
                    # Same task — full resume
                    is_resume = True
                    resumed_state = prior_state
                    if all(prior_assignments.get(k) for k in ("pm", "dev", "qa")):
                        cached_assignments = prior_assignments
                elif prior_prompt is not None:
                    # Prompt changed — archive stale gate artifacts
                    _stale_names = [
                        "SCOPE.md",
                        "BUILD-STATUS.md",
                        "QA-REPORT.md",
                        "PM-VERIFY.md",
                        "SHIP-DECISION.md",
                        "QA-FEEDBACK.md",
                        "PM-FEEDBACK.md",
                        "pipeline.log",
                    ]
                    _stale_paths = [os.path.join(sflo_dir, n) for n in _stale_names]
                    _archived = archive_to_logs(sflo_dir, _stale_paths)
                    if _archived and verbose:
                        print(
                            f"  Stale state — prompt changed, archived to logs/: "
                            f"{', '.join(_archived)}",
                            file=sys.stderr,
                        )
                elif all(prior_assignments.get(k) for k in ("pm", "dev", "qa")):
                    cached_assignments = prior_assignments
        except Exception:
            cached_assignments = None
            is_resume = False

    if resumed_state:
        prior_cs = resumed_state.get("current_state", "")
        if prior_cs in ("done", S_DONE):
            # Pipeline already completed — start fresh
            state = make_initial_state(roles)
            state["prompt"] = user_prompt
            is_resume = False
            resumed_state = None
        elif prior_cs == S_ESCALATE:
            # Pipeline escalated — start fresh (human already intervened)
            state = make_initial_state(roles)
            state["prompt"] = user_prompt
            is_resume = False
            resumed_state = None
        else:
            # Restore prior state — preserves loop counters, gate statuses,
            # current_state. Only update bindings (may have changed).
            state = resumed_state
            state["bindings"] = roles
            state["prompt"] = user_prompt
            log_parts = [
                f"inner={state.get('inner_loops', 0)}",
                f"outer={state.get('outer_loops', 0)}",
                f"state={prior_cs}",
            ]
            retries = state.get("gate_retries", {})
            if retries:
                log_parts.append(f"retries={retries}")
            if verbose:
                print(f"  Resuming: {', '.join(log_parts)}", file=sys.stderr)
    else:
        state = make_initial_state(roles)
        state["prompt"] = user_prompt
    _locked_write_state(sflo_dir, state)

    log(f"SFLO Pipeline — {user_prompt[:60]}")

    # --- Chrome extension check (inform only, never block) ---
    if RuntimeAdapter._extra_cli_args.get("chrome") is not None:
        browser_ok, browser_msg = check_browser()
        if browser_ok:
            log(f"  Chrome extension: {browser_msg}")
        else:
            log(f"  NOTICE: Chrome extension not connected — {browser_msg}")

    # --- Scout ---
    scout_bindings = roles.get("scout", {})
    scout_model = scout_bindings.get("model", "sonnet")
    scout_agent_path = scout_bindings.get(
        "agent", os.path.join(SFLO_ROOT, "agents", "scout")
    )
    scout_soul = read_file(os.path.join(scout_agent_path, "SOUL.md"))

    # Find available agent directories (respecting exclude_agent_dirs from
    # bindings.yaml — configured dirs are filtered out so scout never sees
    # their agents in the listing).
    #
    # Search chain — first hit wins for duplicate role names (de-dup is not
    # applied; scout sees all of them as alternatives). Entries may be
    # excluded by the exclude_agent_dirs setting.
    #
    #   1. cwd/agents                      — project-local agents
    #   2. cwd/sflo/agents                 — legacy layout (sflo as subdir)
    #   3. SFLO_PARENT/agents              — host project agents (one level above submodule)
    #   4. SFLO_ROOT/agents                — submodule default (sflo/agents/)
    #
    # #3 was added so that local agent dirs in the host project are
    # discoverable when the pipeline runs from a project subfolder. Without
    # it, only the submodule's agents are visible when cwd differs.
    excluded_agents = load_exclude_agents(bindings_path)
    excluded_dirs = load_exclude_agent_dirs(bindings_path)

    agent_dirs = []
    seen_real = set()  # dedup via realpath — when sflo is a submodule of
                       # cwd, candidates 1 and 3 both resolve to cwd/agents,
                       # which would list every agent twice and confuse scout.
    cwd = os.getcwd()
    sflo_parent = os.path.dirname(SFLO_ROOT)
    for candidate in [
        os.path.join(cwd, "agents"),
        os.path.join(cwd, "sflo", "agents"),
        os.path.join(sflo_parent, "agents"),
        os.path.join(SFLO_ROOT, "agents"),
    ]:
        if not os.path.isdir(candidate):
            continue
        # Skip this dir if any excluded substring matches its path
        if any(ex and ex in candidate for ex in excluded_dirs):
            continue
        real = os.path.realpath(candidate)
        if real in seen_real:
            continue
        seen_real.add(real)
        agent_dirs.append(candidate)

    agent_listing = ""
    for d in agent_dirs:
        for entry in sorted(os.listdir(d)):
            if entry.startswith("_") or entry in excluded_agents:
                continue
            entry_path = os.path.join(d, entry)
            if os.path.isdir(entry_path):
                brief = os.path.join(entry_path, "BRIEF.md")
                if os.path.isfile(brief):
                    agent_listing += f"\n### {entry} ({d})\n{read_file(brief)}\n"

    # Caller-supplied assignments take precedence over everything else.
    # This is how extended runners avoid the double-scout call: they
    # already ran its extended scout for complexity classification and has
    # pm/dev/qa in hand, so core's scout would be redundant.
    if assignments and all(assignments.get(k) for k in ("pm", "dev", "qa")):
        log("  Scout: assignments supplied by caller, skipping LLM call")
    elif cached_assignments:
        log("  Scout: cache hit — reusing prior assignments, skipping LLM call")
        assignments = cached_assignments
    else:
        try:
            scout_response = await call_adapter_with_evals(
                adapter,
                model=scout_model,
                system_prompt=scout_soul,
                user_prompt=(
                    f"User prompt: {user_prompt}\n\n"
                    f"Available agents:\n{agent_listing}\n\n"
                    f"Return ONLY a JSON object with role assignments, no other text, no tool calls: "
                    f'{{"pm": "<agent_path>", "dev": "<agent_path>", "qa": "<agent_path>"}}'
                ),
                role="scout",
                # Scout is hard-coded readonly — it's pure recon, returns JSON
                # via assistant text, never touches files. Bindings.yaml can
                # bump this via `scout: tools: full` if a host extends scout
                # to need more (e.g. classifier reading project HLA docs).
                tools_mode=scout_bindings.get("tools", "readonly"),
                metadata={"session_id": sflo_dir, "output_dir": output_dir},
            )
        except Exception as e:
            log(f"  Scout failed: {e}")
            log(f"  {traceback.format_exc()}")
            scout_response = ""

        # Parse Scout's assignments using a sliding-window json.loads approach
        # so nested braces in paths are handled correctly (regex {[^{}]*} fails
        # for any value that itself contains braces).
        def _extract_json_obj(text):
            """Return first valid JSON object parsed from text, or None."""
            start = text.find("{")
            while start != -1:
                for end in range(len(text), start, -1):
                    try:
                        obj = json.loads(text[start:end])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        pass
                start = text.find("{", start + 1)
            return None

        try:
            extracted = _extract_json_obj(scout_response)
            if extracted is not None:
                assignments = extracted
            else:
                assignments = json.loads(scout_response)
        except (json.JSONDecodeError, AttributeError):
            # Fallback to generic agents
            sflo_base = SFLO_ROOT
            assignments = {
                "pm": os.path.join(sflo_base, "agents", "pm"),
                "dev": os.path.join(sflo_base, "agents", "dev"),
                "qa": os.path.join(sflo_base, "agents", "qa"),
            }

    state["assignments"] = assignments
    state["current_state"] = "gate-1"
    state["gates"]["1"]["status"] = "in_progress"
    _locked_write_state(sflo_dir, state)

    log(f"  Scout: {', '.join(f'{k}={v}' for k, v in assignments.items())}")

    # --- Pre-flight SOUL validation ---
    preflight_issues = preflight_check(assignments, sflo_dir)
    if preflight_issues:
        for issue in preflight_issues:
            log(f"  PREFLIGHT: {issue}")
        return {
            "ok": False,
            "error": f"Pre-flight validation failed: {'; '.join(preflight_issues)}",
            "preflight_issues": preflight_issues,
        }

    # --- Gate Loop ---
    max_iterations = 50  # safety limit
    iteration = 0

    # Terminal actions — iterations reaching these break out of the loop.
    # Any non-terminal action MUST mutate state (current_state or gate status)
    # or the non-progress guard below raises. This invariant prevents silent
    # infinite loops from branches that forget to advance state.
    TERMINAL_ACTIONS = {"pipeline_complete", "ask_human"}

    while iteration < max_iterations:
        iteration += 1

        # Snapshot state BEFORE this iteration so we can detect non-progress
        # after the dispatch. State changes via auto_transition, apply_transition,
        # or explicit mutation inside the spawn_agent/produce_artifact branches.
        # gate_retries is included because a gate retrying IS legitimate
        # progress — without it, the second retry of any failing gate trips
        # the non-progress guard even though the retry counter advanced.
        # INNER_LOOP_MAX in constants.py bounds real infinite-retry loops.
        pre_snapshot = (
            state.get("current_state"),
            json.dumps(state.get("gates", {}), sort_keys=True),
            json.dumps(state.get("gate_retries", {}), sort_keys=True),
        )

        auto_transition(state, sflo_dir)
        result = compute_next(state, sflo_dir)
        action = result.get("action")

        if action == "pipeline_complete":
            log("Pipeline complete.")
            break

        if action == "ask_human":
            log(f"  ESCALATE: {result.get('reason', 'unknown')}")
            for opt in result.get("options", []) or []:
                log(f"    option: {opt}")
            break

        if action == "spawn_agent":
            agent = result["agent"]
            role = agent["role"]
            model = agent.get("model", "sonnet")

            system_prompt, user_msg = build_agent_prompt(
                agent, user_prompt, sflo_dir, runtime=runtime, output_dir=output_dir
            )

            response = None
            crash_context = ""

            for attempt in range(3):
                if attempt > 0:
                    log(f"  Gate [{role}/{model}] resume attempt {attempt + 1}/3 ...")
                else:
                    log(f"  Gate [{role}/{model}] ...")

                spawn_start = _time.time()
                try:
                    # Dev/QA agents get output_dir as cwd (user deliverables land there).
                    # PM/scout write to sflo_dir via absolute paths (no cwd override needed).
                    spawn_kwargs = dict(
                        model=model,
                        system_prompt=system_prompt,
                        user_prompt=user_msg + crash_context,
                        role=role,
                        # tools_mode flows from agent dict (machine.py reads
                        # it from bindings.yaml `tools:` field). Unset = full
                        # access; "readonly" clamps to Read/Glob/Grep.
                        tools_mode=agent.get("tools_mode"),
                    )
                    if role in ("dev", "qa") and output_dir is not None:
                        spawn_kwargs["cwd"] = output_dir
                    response = await call_adapter_with_evals(
                        adapter,
                        **spawn_kwargs,
                        metadata={"session_id": sflo_dir, "output_dir": output_dir},
                    )
                    break  # success
                except Exception as e:
                    log(f"  Gate [{role}] agent crashed: {e}")
                    log(f"  {traceback.format_exc()}")
                    # Log stderr from the crashed CLI process — this is the
                    # only diagnostic data for exit code 1 crashes. The SDK's
                    # "Check stderr output for details" is a hardcoded string,
                    # not actual stderr. The real stderr is in adapter's callback.
                    if hasattr(adapter, "_last_stderr") and adapter._last_stderr:
                        log(f"  [CLI stderr ({len(adapter._last_stderr)} lines):]")
                        for sl in adapter._last_stderr[-20:]:
                            log(f"    {sl.rstrip()}")

                    # Classify error: only retry transient failures.
                    # Prompt/parse errors (bad JSON, missing key, value mismatch)
                    # will not self-heal with retries — skip remaining attempts.
                    _err_str = str(e)
                    _is_transient = isinstance(
                        e, (ConnectionError, TimeoutError)
                    ) or any(
                        marker in _err_str
                        for marker in (
                            "HTTP 5", "HTTP 429", "503", "502", "429",
                            "timeout", "connection", "Connection",
                        )
                    )
                    _is_prompt_error = isinstance(
                        e, (json.JSONDecodeError, KeyError, ValueError)
                    )
                    if _is_prompt_error and not _is_transient:
                        log(
                            f"  Non-transient prompt error — skipping retries: {type(e).__name__}"
                        )
                        response = f"[Agent error (non-retryable): {e}]"
                        break

                    if attempt < 2:
                        crash_context = (
                            f"\n\n---\n\n## IMPORTANT: Previous attempt crashed\n\n"
                            f"Your previous attempt crashed with this error:\n"
                            f"```\n{e}\n```\n"
                            f"Your partial work (files on disk) is still intact. "
                            f"Read the existing files to understand what was already done. "
                            f"Do NOT start from scratch — continue from where the crash happened. "
                            f"Avoid the command or approach that caused the crash. "
                            f"If a CLI tool failed, check its help/docs before retrying."
                        )
                        log("  Resuming with crash context...")
                    else:
                        log(
                            "  All resume attempts exhausted — gate will fail validation"
                        )
                        response = f"[Agent error after 3 attempts: {e}]"

            # Verify agent wrote the artifact
            produces = agent.get("produces", "")
            if produces:
                artifact_name = os.path.basename(produces)
                if os.path.isfile(produces):
                    log(f"  {artifact_name} ✓")
                else:
                    # Agent didn't write to expected path — check common locations
                    cwd = os.getcwd()
                    candidates = [
                        os.path.join(cwd, artifact_name),
                        os.path.join(cwd, ".sflo", artifact_name),
                    ]
                    found = None
                    for c in candidates:
                        if os.path.isfile(c) and os.path.getmtime(c) > spawn_start:
                            found = c
                            break

                    if found:
                        os.makedirs(os.path.dirname(produces) or ".", exist_ok=True)
                        shutil.move(found, produces)
                        log(f"  {artifact_name} ✓ (moved from {found})")
                    else:
                        os.makedirs(os.path.dirname(produces) or ".", exist_ok=True)
                        # Guard: if model created artifact path as directory, remove it
                        if os.path.isdir(produces):
                            shutil.rmtree(produces)
                        with open(produces, "w", encoding="utf-8") as f:
                            f.write(response)
                        log(f"  {artifact_name} (from response)")

            # Validate
            auto_transition(state, sflo_dir)
            state = read_state(sflo_dir)
            result = compute_next(state, sflo_dir)
            result = apply_transition(state, result, sflo_dir)
            state = read_state(sflo_dir)

            gate_num = result.get("gate")
            passed = result.get("pass", False)
            if passed and gate_num:
                log(f"  Gate {gate_num} ✓")
            elif not passed and gate_num:
                checks = result.get("checks", [])
                loop_action = result.get("action", "")
                if "loop" in loop_action:
                    retry_count = result.get("gate_retry_count")
                    retry_max = result.get("max")
                    failed_names = result.get("failed_checks", [])
                    if retry_count:
                        log(
                            f"  Gate {gate_num} ✗ — retry {retry_count}/{retry_max} ({', '.join(failed_names) or 'validation failed'})"
                        )
                    else:
                        log(f"  Gate {gate_num} ✗ — looping back")
                else:
                    # Log why it failed
                    failed = [c for c in checks if not c.get("pass")]
                    if failed:
                        details = ", ".join(c.get("name", "?") for c in failed)
                        log(f"  Gate {gate_num} ✗ — failed: {details}")
                    else:
                        log(f"  Gate {gate_num} ✗")

        elif action == "produce_artifact":
            # Last gate — SFLO produces decision artifact
            gate_doc = result.get("gate_doc", "")
            reads = result.get("reads", [])
            artifact_name = result.get("artifact", "SHIP-DECISION.md")
            artifact_path = os.path.join(sflo_dir, artifact_name)
            abs_artifact = os.path.abspath(artifact_path)

            system_prompt = read_file(gate_doc) if gate_doc else ""
            prior = "\n\n---\n\n".join(
                f"## {os.path.basename(r)}\n\n{read_file(r)}" for r in reads
            )

            spawn_start = _time.time()
            if runtime == "ollama":
                write_instr = (
                    f"You MUST write the file using bash:\n"
                    f"  mkdir -p {os.path.dirname(abs_artifact)}\n"
                    f"  cat <<'ARTIFACT_EOF' > {abs_artifact}\n"
                    f"  <your content>\n"
                    f"  ARTIFACT_EOF\n"
                    f"Do NOT put artifact content in your response — write it to the file."
                )
            else:
                write_instr = (
                    "Use the Write tool. Create the parent directory if needed."
                )
            gate5_prompt = (
                f"## User Request\n\n{user_prompt}\n\n{prior}\n\n"
                f"Write {artifact_name} to this EXACT path: {abs_artifact}\n"
                f"{write_instr}\n"
                f"Follow the template EXACTLY."
            )
            try:
                response = await call_adapter_with_evals(
                    adapter,
                    model=roles.get("sflo", {}).get("model", "opus"),
                    system_prompt=system_prompt,
                    user_prompt=gate5_prompt,
                    role="sflo",
                    tools_mode=roles.get("sflo", {}).get("tools"),
                    metadata={"session_id": sflo_dir, "output_dir": output_dir},
                )
            except Exception as e:
                log(f"  Gate 5 [SFLO] agent crashed: {e}")
                log(f"  {traceback.format_exc()}")
                log("  Gate 5 will fail validation")
                response = f"[Agent error: {e}]"

            # Verify agent wrote it (same logic as spawn_agent gates)
            if (
                os.path.isfile(artifact_path)
                and os.path.getmtime(artifact_path) > spawn_start
            ):
                log(f"  Gate 5 [SFLO] ... {artifact_name} ✓")
            else:
                cwd = os.getcwd()
                candidates = [
                    os.path.join(cwd, artifact_name),
                    os.path.join(cwd, ".sflo", artifact_name),
                ]
                found = None
                for c in candidates:
                    if os.path.isfile(c) and os.path.getmtime(c) > spawn_start:
                        found = c
                        break
                if found:
                    os.makedirs(os.path.dirname(artifact_path) or ".", exist_ok=True)
                    shutil.move(found, artifact_path)
                    log(f"  Gate 5 [SFLO] ... {artifact_name} ✓ (moved)")
                else:
                    os.makedirs(os.path.dirname(artifact_path) or ".", exist_ok=True)
                    with open(artifact_path, "w", encoding="utf-8") as f:
                        f.write(response)
                    log(f"  Gate 5 [SFLO] ... {artifact_name} (from response)")

            # Validate Gate 5
            auto_transition(state, sflo_dir)
            state = read_state(sflo_dir)
            result = compute_next(state, sflo_dir)
            result = apply_transition(state, result, sflo_dir)
            state = read_state(sflo_dir)

            if result.get("pass"):
                log("  Gate 5 ✓")

        elif action == "run_stst_gate":
            # Gate 2.5: STST static filter — runs stst CLI per test file,
            # aggregates results, writes STST-REPORT.md. No LLM invocation.
            gate_num = result.get("gate_num", 2.5)
            gate_key_str = str(gate_num)
            log(f"  Gate {gate_num} [STST filter] ...")

            stst_bin = shutil.which("stst")
            tool_errors = []
            if stst_bin is None:
                tool_errors.append(
                    "`stst` not found on PATH — gate degraded to PASS. Install STST per its project README."
                )
                log(
                    f"  Gate {gate_num} [STST] WARNING: stst not on PATH — degrading to PASS"
                )

            # Discover test files in output_dir
            test_files = []
            if output_dir and os.path.isdir(output_dir) and stst_bin:
                for pattern in ["**/test_*.py", "**/*_test.py"]:
                    found = glob.glob(os.path.join(output_dir, pattern), recursive=True)
                    test_files.extend(found)
                test_files = sorted(set(test_files))

            if not test_files and not tool_errors:
                # No test files found — degrade to PASS with note
                log(
                    f"  Gate {gate_num} [STST] No test files discovered — skipping (PASS)"
                )
                tool_errors.append(
                    "No test files found in output directory — STST gate skipped."
                )

            # Run stst gate per test file
            WHOLE_GATE_TIMEOUT = 600
            PER_TEST_TIMEOUT = 120
            gate_start = _time.time()
            all_results = []  # list of {file, verdict, rule_hits, findings}

            for test_path in test_files:
                if _time.time() - gate_start > WHOLE_GATE_TIMEOUT:
                    tool_errors.append(
                        f"Whole-gate timeout ({WHOLE_GATE_TIMEOUT}s) exceeded — remaining tests skipped."
                    )
                    log(f"  Gate {gate_num} [STST] Whole-gate timeout reached")
                    break

                # Pair with SUT via naming convention: test_foo.py -> foo.py
                sut_path = None
                test_basename = os.path.basename(test_path)
                stem = test_basename
                if stem.startswith("test_"):
                    stem = stem[5:]
                elif stem.endswith("_test.py"):
                    stem = stem[:-8] + ".py"
                if stem and stem != test_basename:
                    # Search for SUT in output_dir
                    candidates = glob.glob(
                        os.path.join(output_dir or ".", "**", stem), recursive=True
                    )
                    # Exclude test files from candidates
                    candidates = [
                        c
                        for c in candidates
                        if "test_" not in os.path.basename(c)
                        and "_test" not in os.path.basename(c)
                    ]
                    if len(candidates) == 1:
                        sut_path = candidates[0]

                cmd = [stst_bin, "gate", test_path]
                if sut_path:
                    cmd += ["--sut", sut_path]

                try:
                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=PER_TEST_TIMEOUT,
                    )
                    exit_code = proc.returncode
                    stdout = proc.stdout.strip()
                    stderr = proc.stderr.strip()

                    if exit_code == 0:
                        all_results.append(
                            {
                                "file": os.path.relpath(test_path, output_dir or "."),
                                "verdict": "PASS",
                                "rule_hits": "—",
                                "findings": [],
                            }
                        )
                    elif exit_code == 1:
                        # Real rejection — parse rule hits from stdout
                        rule_hits = []
                        findings_text = []
                        for line in stdout.splitlines():
                            if line.strip() and not line.startswith("#"):
                                findings_text.append(line)
                                # Try to extract rule IDs (e.g., B3, H1, F3)
                                m = re.findall(r"\b([A-Z]\d+)\b", line)
                                rule_hits.extend(m)
                        rule_hits_str = (
                            ", ".join(sorted(set(rule_hits)))
                            if rule_hits
                            else "see output"
                        )
                        all_results.append(
                            {
                                "file": os.path.relpath(test_path, output_dir or "."),
                                "verdict": "REJECT",
                                "rule_hits": rule_hits_str,
                                "findings": findings_text,
                            }
                        )
                    else:
                        # exit code 2 or other = tool error → degrade-open (PASS)
                        detail = stderr or stdout or f"exit code {exit_code}"
                        tool_errors.append(
                            f"`stst gate {os.path.basename(test_path)}` exited {exit_code}: {detail[:200]}"
                        )
                        all_results.append(
                            {
                                "file": os.path.relpath(test_path, output_dir or "."),
                                "verdict": "PASS",
                                "rule_hits": f"tool-error(exit {exit_code})",
                                "findings": [],
                            }
                        )
                except subprocess.TimeoutExpired:
                    tool_errors.append(
                        f"`stst gate {os.path.basename(test_path)}` timed out after {PER_TEST_TIMEOUT}s — degraded to PASS."
                    )
                    all_results.append(
                        {
                            "file": os.path.relpath(test_path, output_dir or "."),
                            "verdict": "PASS",
                            "rule_hits": "timeout",
                            "findings": [],
                        }
                    )
                except Exception as exc:
                    tool_errors.append(
                        f"`stst gate {os.path.basename(test_path)}` error: {exc}"
                    )
                    all_results.append(
                        {
                            "file": os.path.relpath(test_path, output_dir or "."),
                            "verdict": "PASS",
                            "rule_hits": f"error: {exc}",
                            "findings": [],
                        }
                    )

            # Determine overall verdict
            any_reject = any(r["verdict"] == "REJECT" for r in all_results)
            if any_reject:
                overall_verdict = "REJECT"
            elif tool_errors:
                overall_verdict = "DEGRADED"
            else:
                overall_verdict = "PASS"
            n_files = len(all_results)
            n_violations = sum(1 for r in all_results if r["verdict"] == "REJECT")

            # Build STST-REPORT.md
            summary_line = f"Verdict: {overall_verdict}"
            if n_files == 0:
                summary_detail = "No test files evaluated — STST gate skipped."
            elif any_reject:
                summary_detail = f"STST ran against {n_files} test file(s). {n_violations} violation(s) found."
            else:
                summary_detail = f"STST ran against {n_files} test file(s). 0 violations — all clean."

            table_rows = []
            for r in all_results:
                table_rows.append(
                    f"| {r['file']} | {r['verdict']} | {r['rule_hits']} |"
                )

            rejection_lines = []
            for r in all_results:
                if r["verdict"] == "REJECT":
                    for finding in r["findings"]:
                        rejection_lines.append(f"- **{r['file']}** — {finding}")

            tool_error_lines = [f"- {e}" for e in tool_errors]

            stst_report_content = f"""## Summary

{summary_line}

{summary_detail}

## Tests Evaluated

| File | Verdict | Rule Hits |
|------|---------|-----------|
{chr(10).join(table_rows) if table_rows else "| (none) | — | — |"}

## Rejection Reasons

{chr(10).join(rejection_lines) if rejection_lines else "(none — all tests passed STST static checks)"}

## Tool Errors

{chr(10).join(tool_error_lines) if tool_error_lines else "(none)"}

## Action

{"REJECT → address rejection reasons listed above, rebuild, re-submit BUILD-STATUS.md." if any_reject else "PASS → proceed to Gate 3 (QA)."}
"""

            stst_report_path = os.path.join(sflo_dir, "STST-REPORT.md")
            os.makedirs(os.path.dirname(stst_report_path) or ".", exist_ok=True)
            with open(stst_report_path, "w", encoding="utf-8") as f:
                f.write(stst_report_content)
            log(f"  STST-REPORT.md written ({overall_verdict})")

            # Mutate state (required to satisfy non-progress guard)
            if "gate_retries" not in state:
                state["gate_retries"] = {}
            if gate_key_str not in state["gates"]:
                state["gates"][gate_key_str] = {
                    "status": "waiting",
                    "artifact": "STST-REPORT.md",
                }

            if any_reject:
                # Increment STST retry counter (not outer_loops)
                state["gate_retries"][gate_key_str] = (
                    state["gate_retries"].get(gate_key_str, 0) + 1
                )
                stst_retry = state["gate_retries"][gate_key_str]
                state["gates"][gate_key_str]["status"] = "rejected"

                if stst_retry >= INNER_LOOP_MAX:
                    state["current_state"] = S_ESCALATE
                    state["escalate_reason"] = (
                        f"STST rejected {stst_retry} DEV rebuilds — "
                        f"likely prompt/SUT mismatch. Human decision needed."
                    )
                    state["escalate_options"] = [
                        "override (ship-anyway)",
                        "fix DEV test generation prompt",
                        "kill",
                    ]
                    _locked_write_state(sflo_dir, state)
                    log(f"  Gate {gate_num} [STST] ESCALATE after {stst_retry} rejects")
                else:
                    # Write STST-FEEDBACK.md for DEV context
                    stst_feedback_path = os.path.join(sflo_dir, "STST-FEEDBACK.md")
                    feedback_lines = [
                        f"# STST Feedback (Retry {stst_retry}/{INNER_LOOP_MAX})\n",
                        "STST static filter rejected your test output.\n",
                        "Fix the issues below, then rebuild.\n\n",
                    ]
                    if rejection_lines:
                        feedback_lines.append("## Rejection Reasons\n\n")
                        feedback_lines.extend(l + "\n" for l in rejection_lines)
                    with open(stst_feedback_path, "w", encoding="utf-8") as f:
                        f.write("".join(feedback_lines))

                    # Archive BUILD-STATUS.md and STST-REPORT.md
                    build_status_path = os.path.join(sflo_dir, "BUILD-STATUS.md")
                    to_archive = [
                        p
                        for p in [stst_report_path, build_status_path]
                        if os.path.isfile(p)
                    ]
                    if to_archive:
                        archive_to_logs(sflo_dir, to_archive)

                    # Loop back to gate-2 (DEV)
                    sorted_gs = sorted(GATES.keys())
                    restart_gate = sorted_gs[1] if len(sorted_gs) >= 2 else 2
                    state["current_state"] = f"gate-{restart_gate}"
                    _locked_write_state(sflo_dir, state)
                    log(
                        f"  Gate {gate_num} [STST] REJECT ({stst_retry}/{INNER_LOOP_MAX}) — looping back to gate-{restart_gate}"
                    )

            else:
                # All pass — advance to gate-3 (QA)
                state["gates"][gate_key_str]["status"] = "done"
                sorted_gs = sorted(GATES.keys())
                stst_idx = sorted_gs.index(gate_num) if gate_num in sorted_gs else -1
                next_gate = (
                    sorted_gs[stst_idx + 1]
                    if stst_idx >= 0 and stst_idx + 1 < len(sorted_gs)
                    else 3
                )
                state["current_state"] = f"gate-{next_gate}"
                _locked_write_state(sflo_dir, state)
                log(f"  Gate {gate_num} [STST] PASS — advancing to gate-{next_gate}")

            state = read_state(sflo_dir)

        elif action in ("validated", "check_failed"):
            # First iteration of gate loop: state auto-transitioned to check-N
            # because an artifact already existed on disk. compute_next already
            # called validate_gate. Now apply_transition mutates state based
            # on pass/fail.
            gate_num = result.get("gate")
            if action == "validated":
                log(f"  Gate {gate_num} ✓ (existing artifact validated)")
            else:
                failed = [
                    c.get("name", "?")
                    for c in result.get("checks", [])
                    if not c.get("pass", True)
                ]
                log(
                    f"  Gate {gate_num} ✗ (existing artifact failed checks: {', '.join(failed)})"
                )
            result = apply_transition(state, result, sflo_dir)
            state = read_state(sflo_dir)
            # If apply_transition escalated (gate 1 / gate 5 validation failure
            # on a non-loop gate), honor the ask_human signal immediately so
            # the user sees the correct reason and the loop does not re-query
            # compute_next (whose S_ESCALATE branch prints a PM-rejection
            # message that is wrong for this case).
            if result.get("action") == "ask_human":
                log(f"  ESCALATE: {result.get('reason', 'unknown')}")
                break

        else:
            log(f"  Unknown action: {action}")
            break

        # --- Non-progress guard ---
        #
        # Every non-terminal iteration MUST advance state (current_state or
        # gate status). If nothing changed, some compute_next/apply_transition
        # branch silently returned without mutating state. That's the exact
        # bug class that caused the Apr 11 silent 50-iteration spin.
        #
        # Detection: snapshot pre and post, compare. If identical AND action
        # was not terminal AND we didn't break out of the loop above, raise
        # loudly with enough context to debug.
        post_snapshot = (
            state.get("current_state"),
            json.dumps(state.get("gates", {}), sort_keys=True),
            json.dumps(state.get("gate_retries", {}), sort_keys=True),
        )
        if post_snapshot == pre_snapshot and action not in TERMINAL_ACTIONS:
            log(
                f"  ABORT: iteration {iteration} made no state progress "
                f"(action={action}, state={state.get('current_state')})"
            )
            log(
                "  This is a state-machine bug — some compute_next or "
                "apply_transition branch returned without mutating state."
            )
            raise RuntimeError(
                f"SFLO loop non-progress at iteration {iteration}. "
                f"action={action}, state={state.get('current_state')}. "
                f"Inspect {sflo_dir}/pipeline.log and the corresponding "
                f"compute_next/apply_transition code path. "
                f"See sflo/src/runner.py non-progress guard."
            )

    # --- Final state ---
    final_state = read_state(sflo_dir)
    return {
        "ok": final_state.get("current_state") == "done",
        "state": final_state.get("current_state"),
        "gates": final_state.get("gates", {}),
        "inner_loops": final_state.get("inner_loops", 0),
        "outer_loops": final_state.get("outer_loops", 0),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="SFLO Runner — enforced pipeline execution"
    )
    parser.add_argument(
        "prompt", nargs="?", default=None, help="What to build (or pass via stdin)"
    )
    parser.add_argument("--sflo-dir", default=".sflo", help="Pipeline state directory")
    parser.add_argument(
        "--runtime",
        choices=["openclaw", "claude-code", "cursor", "ollama"],
        default=None,
    )
    parser.add_argument("--bindings", default=None, help="Path to bindings YAML file")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    _install_signal_handler(args.sflo_dir)

    # Forward user MCP servers + enable Chrome extension for agents
    # Disable with SFLO_CHROME=0
    chrome_args = {"chrome": None}
    if os.environ.get("SFLO_CHROME", "").lower() in ("0", "false", "no", "off"):
        chrome_args = {}
    RuntimeAdapter.configure_mcp(
        load_user_mcp=True,
        extra_cli_args=chrome_args if chrome_args else None,
    )

    prompt = args.prompt
    if not prompt or prompt == "-":
        prompt = sys.stdin.read().strip()
    if not prompt:
        parser.error("No prompt provided. Pass as argument or via stdin.")

    result = asyncio.run(
        run_pipeline(
            user_prompt=prompt,
            sflo_dir=args.sflo_dir,
            runtime=args.runtime,
            verbose=not args.quiet,
            bindings=args.bindings,
        )
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
