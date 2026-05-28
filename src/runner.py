#!/usr/bin/env python3
"""SFLO Runner — enforced pipeline execution.

Uses the selected runtime adapter to spawn agents and run the pipeline.
The runner controls what goes in, what comes out, and where artifacts are
written. Spawned agents cannot bypass the pipeline.

Usage (called by runtime hook/skill, not directly):
    from src.runner import run_pipeline
    result = await run_pipeline(
        "Build a click counter",
        sflo_dir=".sflo",
        runtime="<openclaw|claude-code|codex|cursor|ollama>",
    )

CLI (for testing):
    python3 src/runner.py "Build a click counter" --runtime <runtime> [--sflo-dir .sflo] [--quiet]

    --runtime NAME    Required for pipeline starts. Runtime adapter to use.
    --sflo-dir PATH   Path to .sflo state directory (default: .sflo).
    --quiet           Suppress verbose logging to stderr.
"""

import asyncio
import atexit
import datetime as _datetime
import json
import os
import re
import shutil
import signal
import sys
import time as _time
import traceback


# Local fix: allow invocation as a script (`python src/runner.py`) in addition
# to module mode (`python -m src.runner`). Without this, the relative imports
# below fail with "attempted relative import with no known parent package".
# Remove once upstream supports script invocation natively.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "src"


from ._stderr import _safe_stderr, _scrub_secret  # noqa: E402 — must be early, before any stderr use


# ---------------------------------------------------------------------------
# Signal handler — log signal name + timestamp before exit so we know
# what killed the process (SIGHUP from terminal close, SIGTERM from
# Claude CLI cleanup, etc.). Without this, external kills leave zero
# trace in pipeline.log.
# ---------------------------------------------------------------------------


def install_signal_handler(sflo_dir=None):
    """Public alias — see _install_signal_handler docstring."""
    return _install_signal_handler(sflo_dir)


def _install_signal_handler(sflo_dir=None, on_signal_exit=None):
    """Install signal handlers and record signal exits in pipeline.log.

    Hardened against the H6′ silent-death class: when the controlling pty
    is closed (e.g. Claude Desktop's `beforeQuitForUpdate` running
    `local-session-pty-cleanup`), stderr writes to that pty raise or
    block — the prior handler crashed on its first `print` and never
    reached `sys.exit`, leaving the kernel's default SIGHUP terminate
    to run silently. This version:

      • Wraps stderr writes in try/except so a broken pty doesn't kill
        the handler before it logs.
      • Always writes to pipeline.log (regular file, robust).
      • Optionally reports structured signal facts to the caller before exit.
      • Exits via os._exit() — bypasses Python's signal-handling state
        machine so we never end up returning into interrupted bytecode.
      • Catches SIGQUIT in addition to SIGHUP/SIGTERM/SIGINT.
      • Per CR2-6: SIGPIPE is NOT trapped — Python's default-ignore for
        SIGPIPE is load-bearing for normal BrokenPipeError flow.
    """

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
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(msg)
            except OSError:
                pass
        if on_signal_exit:
            try:
                on_signal_exit(signum, sig_name)
            except Exception:
                pass
        # Best-effort: drop this run's instance lock so a signal-killed run
        # does not strand .sflo/runner.pid (os._exit below skips main()'s
        # normal release). Only remove it when it still holds OUR pid — never
        # delete a lock a different live runner owns.
        if sflo_dir:
            try:
                _lock = os.path.join(sflo_dir, "runner.pid")
                with open(_lock, encoding="utf-8") as _lf:
                    _own = _lf.read().strip() == str(os.getpid())
                if _own:
                    os.remove(_lock)
            except OSError:
                pass
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


# Allow running as script or module
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    from src.constants import SFLO_ROOT, S_DONE, S_ESCALATE, GATES
    from src.config import (
        derive_roles_from_pipeline,
        load_pipeline_config as _load_pipeline_config,
    )
    from src.archive import archive_to_logs
    from src.preflight import preflight_check, check_browser
    from src import evals as _evals
    from src.evals.integration import call_adapter_with_evals
    from src.adapters.errors import (
        ErrorDeduper,
        GateAgentFailure,
        NonRetryableError,
    )
    from src.factory_registry import (
        FactoryError,
        FactoryRegistry,
        final_status_from_pipeline_state,
        format_registry_table,
        slug_from_prompt,
        validate_factory_name,
    )
else:
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
    from .constants import SFLO_ROOT, S_DONE, S_ESCALATE, GATES
    from .config import (
        derive_roles_from_pipeline,
        load_pipeline_config as _load_pipeline_config,
    )
    from .archive import archive_to_logs
    from .preflight import preflight_check, check_browser
    from . import evals as _evals
    from .evals.integration import call_adapter_with_evals
    from .adapters.errors import (
        ErrorDeduper,
        GateAgentFailure,
        NonRetryableError,
    )
    from .factory_registry import (
        FactoryError,
        FactoryRegistry,
        final_status_from_pipeline_state,
        format_registry_table,
        slug_from_prompt,
        validate_factory_name,
    )


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


def _roles_with_explicit_agents(gates):
    """Return set of roles that have agent: or agents: declared in pipeline.yaml.

    These roles are pre-assigned by config — scout should not re-assign them.
    Handles both single-gate entries and list-based parallel gate entries.
    """
    pre_assigned = set()
    for gate_info in gates.values():
        entries = gate_info if isinstance(gate_info, list) else [gate_info]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("agent") or entry.get("agents"):
                role = entry.get("role")
                if role:
                    pre_assigned.add(role)
    return pre_assigned


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


# ---------------------------------------------------------------------------
# Pluggable runner/validator loaders
# ---------------------------------------------------------------------------


def _load_custom_runner(runner_path):
    """Load a custom runner module from a relative file path via importlib.

    Returns (module, error_string). Rejects absolute paths and '..' traversal.
    Path resolved relative to cwd, contained within cwd or SFLO_ROOT.
    """
    import importlib.util

    if not runner_path:
        return None, "runner path is empty"
    if os.path.isabs(runner_path):
        return None, f"Runner path must be relative: {runner_path}"
    parts = runner_path.replace("\\", "/").split("/")
    if ".." in parts:
        return None, f"Runner path must not contain '..': {runner_path}"

    abs_path = os.path.realpath(os.path.join(os.getcwd(), runner_path))
    cwd_real = os.path.realpath(os.getcwd())
    sflo_root_real = os.path.realpath(
        os.environ.get("SFLO_ROOT")
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    if not (
        abs_path.startswith(cwd_real + os.sep)
        or abs_path == cwd_real
        or abs_path.startswith(sflo_root_real + os.sep)
        or abs_path == sflo_root_real
    ):
        return None, f"Runner path resolves outside project: {abs_path}"

    if not os.path.isfile(abs_path):
        return None, f"Runner file not found: {abs_path}"

    spec = importlib.util.spec_from_file_location("_sflo_runner", abs_path)
    if spec is None:
        return None, f"Cannot load module from {abs_path}"
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        return None, f"Failed to load runner {abs_path}: {e}"

    if not hasattr(module, "run_gate") and not hasattr(module, "run"):
        return None, f"Runner {abs_path} has no run_gate() or run() function"

    return module, None


# Public alias for testing / external callers
load_custom_runner = _load_custom_runner


def _recover_artifact(produces, spawn_start, response, log):
    """Verify agent wrote artifact; recover from wrong location or response fallback.

    Shared logic for spawn_agent, produce_artifact, and spawn_parallel gates.
    """
    if not produces:
        return
    artifact_name = os.path.basename(produces)
    if os.path.isfile(produces) and os.path.getmtime(produces) > spawn_start:
        log(f"  {artifact_name} ✓")
        return
    # Search candidate locations
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
        if os.path.isdir(produces):
            shutil.rmtree(produces)
        with open(produces, "w", encoding="utf-8") as f:
            f.write(response or "")
        log(f"  {artifact_name} (from response)")


def _filter_mcp_for_gate(agent_info, all_mcp_servers):
    """Filter MCP servers based on gate's mcp: config list.

    If gate declares mcp: [server1, server2], only those servers are passed.
    If gate has no mcp: field (None), all servers pass (backward compat).
    If gate declares mcp: [] (empty list), no MCP servers are passed.

    Returns filtered dict or None (meaning use all/class-level).
    """
    gate_mcp = agent_info.get("mcp")
    if gate_mcp is None:
        return None  # no filter → adapter uses class-level (all servers)
    if not gate_mcp:
        return {}  # explicit empty → no servers
    if not all_mcp_servers:
        return None
    filtered = {k: v for k, v in all_mcp_servers.items() if k in gate_mcp}
    if gate_mcp and not filtered:
        from ._stderr import _safe_stderr

        unknown = [s for s in gate_mcp if s not in all_mcp_servers]
        _safe_stderr(
            f"  [MCP warning] gate requests {gate_mcp} "
            f"but none matched available servers. Unknown: {unknown}"
        )
    return filtered


# ---------------------------------------------------------------------------
# Factory venv — pass <factory-dir>/.venv to spawned agents
# ---------------------------------------------------------------------------


def _get_factory_env(sflo_dir):
    """Return env dict for the factory venv, creating it if absent."""
    if not sflo_dir:
        return None
    venv_path = os.path.join(sflo_dir, ".venv")
    if not os.path.isdir(venv_path):
        import subprocess as _sp
        import sys as _sys
        try:
            _sp.run([_sys.executable, "-m", "venv", venv_path],
                    check=True, capture_output=True)
        except _sp.CalledProcessError:
            return None
    venv_bin = (
        os.path.join(venv_path, "Scripts")
        if os.name == "nt"
        else os.path.join(venv_path, "bin")
    )
    return {
        "VIRTUAL_ENV": venv_path,
        "PATH": f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    }


def _apply_runtime_spawn_kwargs(spawn_kwargs, runtime):
    """Add runtime-specific kwargs to a gate-agent spawn call.

    cursor-agent takes --workspace to discover project-level .cursor/ rules
    and MCP config. ClaudeCodeAdapter has no such kwarg (and no **kwargs to
    absorb extras), so workspace is passed only for the cursor runtime.
    """
    if runtime == "cursor":
        spawn_kwargs["workspace"] = SFLO_ROOT


async def default_agent_runner(
    agent, sflo_dir, output_dir, *, adapter, runtime, user_prompt, log
):
    """Default runner: spawns an LLM agent for a gate.

    Extracted standalone so external code can import and wrap this function
    to override default agent behaviour without forking run_pipeline.

    Handles: prompt building, 3-attempt retry with crash context, artifact
    verification/recovery. Does NOT handle validation/transition (caller does that).
    """
    role = agent["role"]
    model = agent.get("model")

    system_prompt, user_msg = build_agent_prompt(
        agent, user_prompt, sflo_dir, runtime=runtime, output_dir=output_dir
    )

    # Log skill injection summary
    skills = agent.get("skills", [])
    if skills:
        skill_names = [os.path.basename(os.path.dirname(p)) for p in skills]
        log(f"  Skills injected [{role}]: {', '.join(skill_names)}")
    gate_mcp_cfg = agent.get("mcp")
    if gate_mcp_cfg is not None:
        log(f"  MCP filter [{role}]: {gate_mcp_cfg or '(none)'}")

    agent_env = _get_factory_env(sflo_dir)

    response = None
    crash_context = ""
    deduper = ErrorDeduper()
    last_exc = None

    for attempt in range(3):
        if attempt > 0:
            log(f"  Gate [{role}/{model}] resume attempt {attempt + 1}/3 ...")
        else:
            log(f"  Gate [{role}/{model}] ...")

        spawn_start = _time.time()
        try:
            # Filter MCP servers per gate config
            gate_mcp = _filter_mcp_for_gate(agent, adapter._mcp_servers)
            spawn_kwargs = dict(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_msg + crash_context,
                role=role,
                tools_mode=agent.get("tools_mode"),
                thinking=agent.get("thinking"),
                effort=agent.get("effort"),
                allow_task=agent.get("allow_task"),
            )
            if gate_mcp is not None:
                spawn_kwargs["mcp_servers"] = gate_mcp
            if role in ("dev", "qa") and output_dir is not None:
                spawn_kwargs["cwd"] = output_dir
            if agent_env is not None:
                spawn_kwargs["env"] = agent_env
            _apply_runtime_spawn_kwargs(spawn_kwargs, runtime)
            response = await call_adapter_with_evals(
                adapter,
                **spawn_kwargs,
                metadata={"session_id": sflo_dir, "output_dir": output_dir},
            )
            break  # success
        except Exception as e:
            last_exc = e
            # Suppress repeated identical tracebacks within this attempt loop.
            # Without this, a permanent error (auth, missing CLI) produced 30+
            # copies of the same traceback per failed gate (10 outer retries
            # x 3 inner resume attempts), burying the actual root cause.
            if deduper.should_emit(e):
                log(f"  Gate [{role}] agent crashed: {e}")
                log(f"  {traceback.format_exc()}")
                if hasattr(adapter, "_last_stderr") and adapter._last_stderr:
                    log(f"  [CLI stderr ({len(adapter._last_stderr)} lines):]")
                    for sl in adapter._last_stderr[-20:]:
                        log(f"    {sl.rstrip()}")
            else:
                log(f"  Gate [{role}] same error repeated (attempt {attempt + 1}/3)")

            # Typed exceptions: adapters classify their own errors.
            # NonRetryableError = auth/config/permanent system error (abort).
            # Anything else gets the 3-attempt retry budget.
            if isinstance(e, NonRetryableError):
                log(f"  Non-retryable error — skipping retries: {e}")
                raise GateAgentFailure(
                    role=role, gate=agent.get("gate_num"),
                    attempts=attempt + 1, cause=e,
                ) from e

            if isinstance(e, (json.JSONDecodeError, KeyError, ValueError)):
                log(f"  Prompt/parse error — skipping retries: {type(e).__name__}")
                raise GateAgentFailure(
                    role=role, gate=agent.get("gate_num"),
                    attempts=attempt + 1, cause=e,
                ) from e

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
                # All 3 attempts exhausted. Raise instead of stuffing the
                # error text into `response` (the "credulity bug" — that
                # error text used to flow into `_recover_artifact` and end
                # up written into the gate's output file as if it were
                # agent output, polluting the gate validation cycle).
                suppressed = deduper.suppressed_count
                log(
                    f"  All resume attempts exhausted — escalating gate "
                    f"({suppressed} duplicate traceback(s) suppressed)"
                )
                raise GateAgentFailure(
                    role=role, gate=agent.get("gate_num"),
                    attempts=3, cause=last_exc,
                ) from last_exc

    # Verify agent wrote the artifact
    produces = agent.get("produces", "")
    _recover_artifact(produces, spawn_start, response, log)


def make_logger(sflo_dir, verbose=True):
    """Create a logger that writes to stderr and .sflo/pipeline.log.

    Each ``log()`` call opens the file in append mode, writes one line, and
    closes the handle before returning. No file handle is held open across
    calls — this guarantees the OS handle is released deterministically and
    pipeline.log can be deleted between runs (a lingering append-mode handle
    blocks deletion on Windows). Logging volume is low, so per-call open/close
    cost is negligible.

    The returned callable exposes a no-op ``close()`` for callers that still
    invoke it; there is no persistent handle to close.
    """

    os.makedirs(sflo_dir, exist_ok=True)
    log_path = os.path.join(sflo_dir, "pipeline.log")

    def log(msg):
        ts = _datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        try:
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(line + "\n")
        except OSError:
            pass
        if verbose:
            _safe_stderr(msg)

    def _close_log():
        # No persistent handle is held; nothing to close. Kept for callers
        # that still invoke log.close() at shutdown.
        pass

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


def write_validation_feedback(sflo_dir, gate_num, checks):
    """Write artifact-specific feedback for the next gate retry."""
    feedback = format_validation_feedback(checks)
    if not feedback or not gate_num:
        return None
    gate_info = GATES.get(gate_num)
    if isinstance(gate_info, list):
        gate_info = gate_info[0] if gate_info else {}
    artifact = (gate_info or {}).get("artifact")
    if not artifact:
        return None
    feedback_name = artifact.replace(".md", "-FEEDBACK.md")
    path = os.path.join(sflo_dir, feedback_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(feedback)
        f.write("\n")
    return path


def _resolve_skill_references(skill_path, skill_content):
    """Extract relative .md references from skill content and resolve to absolute paths.

    Skills may reference companion documents (e.g. `references/testing-patterns.md`).
    This function finds those references, resolves them relative to the vendor root
    (two levels up from SKILL.md: vendor/<name>/skills/<skill>/SKILL.md), and returns
    a list of (ref_text, absolute_path) tuples for existing files.

    Pattern: backtick-wrapped paths ending in .md, e.g. `references/foo.md`
    """
    if not skill_content:
        return []

    # Vendor root is 3 levels up from SKILL.md:
    # vendor/<vendor>/skills/<skill>/SKILL.md → vendor/<vendor>/
    skill_dir = os.path.dirname(skill_path)
    vendor_root = os.path.dirname(os.path.dirname(skill_dir))

    refs = []
    seen = set()
    for match in re.findall(r"`([^`]+\.md)`", skill_content):
        if match in seen or ".." in match:
            continue
        seen.add(match)
        # split on "/" so os.path.join builds a native path on Windows too
        abs_path = os.path.normpath(os.path.join(vendor_root, *match.split("/")))
        if os.path.isfile(abs_path):
            refs.append((match, abs_path))
    return refs


def build_agent_prompt(agent_info, user_prompt, sflo_dir, runtime=None, output_dir=None):
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

    # System prompt: own SOUL + additional agents + vendor skills + gate doc
    system_parts = []
    primary_soul = None
    if len(reads) >= 2:
        soul_content = read_file(reads[1])
        system_parts.append(soul_content)
        primary_soul = os.path.realpath(reads[1])

    # Additional agents (loaded from per-gate agents: list in pipeline.yaml)
    # Skip any agent that resolves to the same file as the primary SOUL (dedup)
    for agent_path in agent_info.get("agents", []):
        if os.path.isfile(agent_path):
            if primary_soul and os.path.realpath(agent_path) == primary_soul:
                continue
            agent_content = read_file(agent_path)
            agent_name = os.path.splitext(os.path.basename(agent_path))[0]
            if agent_name == "SOUL":
                agent_name = os.path.basename(os.path.dirname(agent_path))
            system_parts.append(f"## Agent: {agent_name}\n\n{agent_content}")

    # Vendor methodology skills (loaded from per-gate skills: list in pipeline.yaml)
    for skill_path in agent_info.get("skills", []):
        if os.path.isfile(skill_path):
            skill_content = read_file(skill_path)
            skill_name = os.path.basename(os.path.dirname(skill_path))
            # Resolve references mentioned in skill content → absolute paths
            refs = _resolve_skill_references(skill_path, skill_content)
            ref_section = ""
            if refs:
                ref_lines = [f"  - `{abs_p}` ({name})" for name, abs_p in refs]
                ref_section = "\n\n### Reference files (read on demand)\n" + "\n".join(
                    ref_lines
                )
            system_parts.append(
                f"## Methodology: {skill_name}\n\n{skill_content}{ref_section}"
            )

    if len(reads) >= 1:
        gate_content = read_file(reads[0])
        system_parts.append(f"## Gate Document\n\n{gate_content}")

    # Task subagent restriction — inject when gate disallows Task spawning.
    # Advisory: model follows the instruction; violations are logged as warnings.
    if agent_info.get("allow_task") is False:
        system_parts.append(
            "## Tool Restriction\n\n"
            "DO NOT use the Task tool to spawn subagents. "
            "All work must be done directly — no delegation to child agents. "
            "This gate requires direct execution for security and observability."
        )

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

# Gate artifacts eligible for archive on stale-detection or prompt-change.
# Single definition; used in two places within run_pipeline.
_ARCHIVABLE_ARTIFACTS = [
    "SCOPE.md",
    "BUILD-STATUS.md",
    "QA-REPORT.md",
    "PM-VERIFY.md",
    "SHIP-DECISION.md",
    "QA-FEEDBACK.md",
    "PM-FEEDBACK.md",
    "pipeline.log",
]


async def run_pipeline(
    user_prompt,
    sflo_dir=".sflo",
    output_dir=None,
    runtime=None,
    verbose=True,
    assignments=None,
):
    """Run the full SFLO pipeline.

    Args:
        user_prompt: What to build.
        sflo_dir: Where to store pipeline state and artifacts.
        output_dir: User deliverables directory (agent cwd). If None, uses inherited cwd.
        runtime: Required. "openclaw", "claude-code", "codex", "cursor", or "ollama".
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

    # --- Init: role config from pipeline.yaml ---
    roles = derive_roles_from_pipeline()

    # --- Eval framework: load plugins from pipeline.yaml (if present) ---
    # Fail-safe: any load error logs a warning; pipeline always continues.
    # Eval status is ALWAYS announced to stderr — a missing evals section must
    # never silently disable the security guards (path-traversal, shell-metachar,
    # etc.). A silent no-op here once hid that 0 evals were active.
    try:
        from pathlib import Path as _Path
        from .config import resolve_pipeline_path as _resolve_pp

        _pp = _resolve_pp()
        if _pp and os.path.isfile(_pp):
            _loaded = _evals.load_evals_from_config(_Path(_pp))
            if _loaded:
                _safe_stderr(
                    f"  [evals] loaded {len(_loaded)} eval(s) from pipeline.yaml"
                )
            else:
                _safe_stderr(
                    "  [evals] WARNING: no evals section in pipeline.yaml — "
                    "0 eval plugins active"
                )
        else:
            _safe_stderr(
                "  [evals] WARNING: pipeline.yaml not found — "
                "0 eval plugins active"
            )
    except Exception as _eval_load_err:
        # Non-fatal: warn but never block pipeline startup
        _safe_stderr(f"  [evals] load warning: {_eval_load_err}")

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
    resumed_state = None
    prior_state_path = state_path(sflo_dir)

    # Check if state.json exists at wrong location (project root instead of .sflo/)
    if sflo_dir == ".sflo":
        project_root_state = "state.json"
        if os.path.isfile(project_root_state) and os.path.abspath(
            project_root_state
        ) != os.path.abspath(prior_state_path):
            if verbose:
                _safe_stderr(
                    "  WARNING: state.json found at project root (should be in .sflo/)"
                )
            # Archive stale root-level state.json
            archive_to_logs(sflo_dir, [project_root_state])
            if verbose:
                _safe_stderr("  Archived stale state.json from project root to logs/")

    if os.path.isfile(prior_state_path):
        try:
            # Check file age (safety net for stale state)
            file_stat = os.stat(prior_state_path)
            file_age_days = (_time.time() - file_stat.st_mtime) / 86400.0
            STATE_MAX_AGE_DAYS = 7

            if file_age_days > STATE_MAX_AGE_DAYS:
                # State too old — archive and start fresh
                if verbose:
                    _safe_stderr(
                        f"  Stale state — state.json is {file_age_days:.1f} days old (max {STATE_MAX_AGE_DAYS}), archiving"
                    )
                _stale_names = ["state.json"] + _ARCHIVABLE_ARTIFACTS
                archive_to_logs(
                    sflo_dir, [os.path.join(sflo_dir, n) for n in _stale_names]
                )
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
                    resumed_state = prior_state
                    if prior_assignments:
                        cached_assignments = prior_assignments
                elif prior_prompt is not None:
                    # Prompt changed — archive stale gate artifacts
                    _stale_paths = [
                        os.path.join(sflo_dir, n) for n in _ARCHIVABLE_ARTIFACTS
                    ]
                    _archived = archive_to_logs(sflo_dir, _stale_paths)
                    if _archived and verbose:
                        _safe_stderr(
                            f"  Stale state — prompt changed, archived to logs/: "
                            f"{', '.join(_archived)}"
                        )
                elif prior_assignments:
                    cached_assignments = prior_assignments
        except Exception:
            cached_assignments = None

    if resumed_state:
        prior_cs = resumed_state.get("current_state", "")
        if prior_cs in ("done", S_DONE):
            # Pipeline already completed — start fresh
            state = make_initial_state(roles)
            state["prompt"] = user_prompt
            resumed_state = None
        elif prior_cs == S_ESCALATE:
            # Pipeline escalated — start fresh (human already intervened)
            state = make_initial_state(roles)
            state["prompt"] = user_prompt
            resumed_state = None
        else:
            # Restore prior state — preserves loop counters, gate statuses,
            # current_state. Only update roles (may have changed).
            state = resumed_state
            state["roles"] = roles
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
                _safe_stderr(f"  Resuming: {', '.join(log_parts)}")
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
    scout_config = roles.get("scout", {})
    scout_model = scout_config.get("model")
    scout_agent_path = scout_config.get(
        "agent", os.path.join(SFLO_ROOT, "agents", "scout")
    )
    scout_soul = read_file(os.path.join(scout_agent_path, "SOUL.md"))

    # Find available agent directories (respecting exclude_agent_dirs from
    # pipeline.yaml — configured dirs are filtered out so scout never sees
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
    _pipeline_cfg = _load_pipeline_config()
    excluded_agents = set(_pipeline_cfg.get("exclude_agents", []))
    excluded_dirs = set(_pipeline_cfg.get("exclude_agent_dirs", []))

    # Dedup via os.path.realpath — under some workspace layouts two of
    # the candidates below can resolve to the same physical directory
    # (e.g. cwd/agents == sflo_parent/agents when SFLO_ROOT lives one
    # level under cwd). Without this guard scout would see every agent
    # listed twice in its prompt.
    agent_dirs = []
    seen_real = set()
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

    # Determine which roles need scout assignment vs. which are pre-declared
    # in pipeline.yaml (have explicit agent: or agents: fields).
    pre_assigned_roles = _roles_with_explicit_agents(GATES)
    all_gate_roles = set()
    for gate_info in GATES.values():
        entries = gate_info if isinstance(gate_info, list) else [gate_info]
        for entry in entries:
            if isinstance(entry, dict) and entry.get("role"):
                all_gate_roles.add(entry["role"])
    # Roles that scout must discover — exclude pre-assigned and meta-roles
    discoverable_roles = (all_gate_roles - pre_assigned_roles) - {"sflo"}
    if pre_assigned_roles:
        log(
            f"  Scout: roles pre-assigned by pipeline.yaml: {sorted(pre_assigned_roles)}"
        )

    # Caller-supplied assignments take precedence over everything else.
    # This is how extended runners avoid the double-scout call: they
    # already ran its extended scout for complexity classification and has
    # pm/dev/qa in hand, so core's scout would be redundant.
    if assignments and all(assignments.get(k) for k in discoverable_roles):
        log("  Scout: assignments supplied by caller, skipping LLM call")
    elif cached_assignments:
        log("  Scout: cache hit — reusing prior assignments, skipping LLM call")
        assignments = cached_assignments
    elif not discoverable_roles:
        log("  Scout: all roles pre-assigned by pipeline.yaml, skipping LLM call")
        assignments = {}
    else:
        # Build dynamic JSON template — only roles that need discovery
        json_template = ", ".join(
            f'"{role}": "<agent_path>"' for role in sorted(discoverable_roles)
        )
        try:
            scout_env = _get_factory_env(sflo_dir)
            scout_response = await call_adapter_with_evals(
                adapter,
                model=scout_model,
                system_prompt=scout_soul,
                user_prompt=(
                    f"User prompt: {user_prompt}\n\n"
                    f"Available agents:\n{agent_listing}\n\n"
                    f"Return ONLY a JSON object with role assignments, no other text, no tool calls: "
                    f"{{{json_template}}}"
                ),
                role="scout",
                # Scout is hard-coded readonly — it's pure recon, returns JSON
                # via assistant text, never touches files. pipeline.yaml can
                # bump this via `scout: tools: full` if a host extends scout
                # to need more (e.g. classifier reading project HLA docs).
                tools_mode=scout_config.get("tools", "readonly"),
                thinking=scout_config.get("thinking"),
                effort=scout_config.get("effort"),
                env=scout_env,
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
            # Fallback: assign convention-default agents for discoverable roles only
            sflo_base = SFLO_ROOT
            assignments = {
                role: os.path.join(sflo_base, "agents", role)
                for role in discoverable_roles
            }

        # Normalize agent paths against SFLO_ROOT. Scout is LLM-driven and
        # non-deterministically emits absolute or relative paths between runs;
        # downstream preflight/gate code must see a stable absolute-path
        # contract. Skip non-string values (host projects may extend scout
        # with metadata fields like complexity scores).
        for role, value in list(assignments.items()):
            if not isinstance(value, str) or not value:
                continue
            if os.path.isabs(value):
                continue
            candidate = os.path.join(SFLO_ROOT, value)
            if os.path.isdir(candidate):
                assignments[role] = os.path.abspath(candidate)

    state["assignments"] = assignments
    if resumed_state is None:
        first_gate = min(GATES.keys()) if GATES else 1
        first_gate_key = str(first_gate)
        state["current_state"] = f"gate-{first_gate_key}"
        if first_gate_key in state.get("gates", {}):
            state["gates"][first_gate_key]["status"] = "in_progress"
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
        log(f"--- Round {iteration} ---")

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

            try:
                await default_agent_runner(
                    agent,
                    sflo_dir,
                    output_dir,
                    adapter=adapter,
                    runtime=runtime,
                    user_prompt=user_prompt,
                    log=log,
                )
            except GateAgentFailure as gaf:
                # Adapter / SDK / environment failure that retries cannot fix.
                # Escalate the pipeline cleanly — do NOT write the error text
                # into the gate artifact (the credulity bug). The artifact
                # simply doesn't exist, which is itself a meaningful signal.
                log(f"  ESCALATE: {gaf}")
                log("    option: fix the underlying environment issue and retry")
                log("    option: delete .sflo/ and retry from scratch")

                # Persist escalate state to disk so a subsequent invocation
                # doesn't silently resume the same broken gate from
                # in_progress. Without this, state.json says
                # current_state="gate-N" status="in_progress" and the
                # resume path retries the same failing config.
                state = read_state(sflo_dir) or state
                state["current_state"] = S_ESCALATE
                gate_key = str(gaf.gate) if gaf.gate is not None else None
                if gate_key and gate_key in state.get("gates", {}):
                    state["gates"][gate_key]["status"] = "failed"
                state["escalation"] = {
                    "role": gaf.role,
                    "gate": gaf.gate,
                    "attempts": gaf.attempts,
                    # Scrub before persisting to state.json — an SDK error
                    # string can carry a token / Bearer header / credentialed
                    # URL, and this value lands verbatim on disk.
                    "cause": _scrub_secret(
                        f"{type(gaf.cause).__name__}: {gaf.cause}"
                    ),
                }
                _locked_write_state(sflo_dir, state)

                return {
                    "ok": False,
                    "state": "escalate",
                    "error": str(gaf),
                    "role": gaf.role,
                    "gate": gaf.gate,
                    "attempts": gaf.attempts,
                    "gates": state.get("gates", {}),
                    "inner_loops": state.get("inner_loops", 0),
                    "outer_loops": state.get("outer_loops", 0),
                }

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
                    result_state = result.get("state", "")
                    feedback_path = (
                        write_validation_feedback(sflo_dir, gate_num, checks)
                        if result_state.startswith(("gate-retry-", "loop-gate-"))
                        else None
                    )
                    retry_count = result.get("gate_retry_count")
                    retry_max = result.get("max")
                    failed_names = result.get("failed_checks", [])
                    if retry_count:
                        log(
                            f"  Gate {gate_num} ✗ — retry {retry_count}/{retry_max} ({', '.join(failed_names) or 'validation failed'})"
                        )
                        if feedback_path:
                            log(f"  Feedback: {feedback_path}")
                    else:
                        log(f"  Gate {gate_num} ✗ — looping back")
                        if feedback_path:
                            log(f"  Feedback: {feedback_path}")
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

            _recover_artifact(artifact_path, spawn_start, response, log)

            # Validate Gate 5
            auto_transition(state, sflo_dir)
            state = read_state(sflo_dir)
            result = compute_next(state, sflo_dir)
            result = apply_transition(state, result, sflo_dir)
            state = read_state(sflo_dir)

            if result.get("pass"):
                log("  Gate 5 ✓")

        elif action == "run_custom_gate":
            gate_num = result.get("gate_num")
            runner_path = result.get("runner")
            gate_key_str = str(gate_num)
            artifact_name = result.get("artifact")
            log(f"  Gate {gate_num} [custom runner: {runner_path}] ...")

            runner_module, load_err = _load_custom_runner(runner_path)
            if load_err:
                log(f"  Gate {gate_num} runner load FAILED: {load_err}")
                if artifact_name:
                    err_artifact_path = os.path.join(sflo_dir, artifact_name)
                    os.makedirs(
                        os.path.dirname(err_artifact_path) or ".", exist_ok=True
                    )
                    with open(err_artifact_path, "w", encoding="utf-8") as f:
                        f.write(f"# Runner Error\n\nVerdict: DEGRADED\n\n{load_err}\n")
            else:
                try:
                    import inspect as _inspect_mod

                    run_fn = (
                        getattr(runner_module, "run_gate", None) or runner_module.run
                    )
                    result_or_coro = run_fn(GATES[gate_num], sflo_dir, output_dir)
                    if _inspect_mod.iscoroutine(result_or_coro):
                        await result_or_coro
                except Exception as e:
                    log(f"  Gate {gate_num} custom runner FAILED (DEGRADED): {e}")
                    log(f"  {traceback.format_exc()}")
                    if artifact_name:
                        err_artifact_path = os.path.join(sflo_dir, artifact_name)
                        os.makedirs(
                            os.path.dirname(err_artifact_path) or ".", exist_ok=True
                        )
                        with open(err_artifact_path, "w", encoding="utf-8") as f:
                            f.write(
                                f"# Runner Error\n\nVerdict: DEGRADED\n\n{e}\n\n```\n{traceback.format_exc()}\n```\n"
                            )

            # Mutate state for non-progress guard
            if "gate_retries" not in state:
                state["gate_retries"] = {}
            if gate_key_str not in state["gates"]:
                state["gates"][gate_key_str] = {
                    "status": "waiting",
                    "artifact": artifact_name or f"gate-{gate_num}",
                }

            # Validate and transition
            auto_transition(state, sflo_dir)
            state = read_state(sflo_dir)
            result = compute_next(state, sflo_dir)
            result = apply_transition(state, result, sflo_dir)
            state = read_state(sflo_dir)

            gate_check_num = result.get("gate")
            passed = result.get("pass", False)
            if passed and gate_check_num:
                log(f"  Gate {gate_check_num} ✓")
            elif not passed and gate_check_num:
                loop_action = result.get("action", "")
                if "loop" in loop_action:
                    retry_count = result.get("gate_retry_count")
                    retry_max = result.get("max")
                    log(f"  Gate {gate_check_num} ✗ — retry {retry_count}/{retry_max}")
                else:
                    log(f"  Gate {gate_check_num} ✗")

        elif action == "spawn_parallel":
            agents_list = result.get("agents", [])
            gate_num_par = None
            if agents_list:
                gate_num_par = agents_list[0].get("gate_num")
            log(f"  Gate {gate_num_par} [parallel: {len(agents_list)} agents] ...")
            # Log skill injection for each parallel agent
            for _ag in agents_list:
                _ag_skills = _ag.get("skills", [])
                if _ag_skills:
                    _skill_names = [
                        os.path.basename(os.path.dirname(p)) for p in _ag_skills
                    ]
                    log(
                        f"  Skills injected [{_ag.get('role', '?')}]: {', '.join(_skill_names)}"
                    )
                _ag_mcp = _ag.get("mcp")
                if _ag_mcp is not None:
                    log(f"  MCP filter [{_ag.get('role', '?')}]: {_ag_mcp or '(none)'}")

            async def _spawn_one(ag_info):
                ag_role = ag_info.get("role", "unknown")
                if ag_info.get("runner"):
                    runner_module, load_err = _load_custom_runner(ag_info["runner"])
                    if load_err:
                        raise RuntimeError(
                            f"[{ag_role}] runner load failed: {load_err}"
                        )
                    import inspect as _inspect_par

                    run_fn = (
                        getattr(runner_module, "run_gate", None) or runner_module.run
                    )
                    gate_config = GATES.get(ag_info.get("gate_num"), {})
                    result_or_coro = run_fn(
                        gate_config if not isinstance(gate_config, list) else ag_info,
                        sflo_dir,
                        output_dir,
                    )
                    if _inspect_par.iscoroutine(result_or_coro):
                        await result_or_coro
                    return f"{ag_role}: custom runner done"
                else:
                    ag_model = ag_info.get("model")
                    ag_system, ag_user = build_agent_prompt(
                        ag_info,
                        user_prompt,
                        sflo_dir,
                        runtime=runtime,
                        output_dir=output_dir,
                    )
                    # Filter MCP servers per gate config
                    ag_gate_mcp = _filter_mcp_for_gate(ag_info, adapter._mcp_servers)
                    spawn_kwargs = dict(
                        model=ag_model,
                        system_prompt=ag_system,
                        user_prompt=ag_user,
                        role=ag_role,
                        tools_mode=ag_info.get("tools_mode"),
                        thinking=ag_info.get("thinking"),
                        effort=ag_info.get("effort"),
                        allow_task=ag_info.get("allow_task"),
                    )
                    if ag_gate_mcp is not None:
                        spawn_kwargs["mcp_servers"] = ag_gate_mcp
                    if ag_role in ("dev", "qa") and output_dir is not None:
                        spawn_kwargs["cwd"] = output_dir
                    if par_factory_env is not None:
                        spawn_kwargs["env"] = par_factory_env
                    _apply_runtime_spawn_kwargs(spawn_kwargs, runtime)
                    ag_spawn_start = _time.time()
                    resp = await call_adapter_with_evals(
                        adapter,
                        **spawn_kwargs,
                        metadata={"session_id": sflo_dir, "output_dir": output_dir},
                    )
                    produces = ag_info.get("produces", "")
                    _recover_artifact(produces, ag_spawn_start, resp, log)
                    return f"{ag_role}: agent done"

            par_factory_env = _get_factory_env(sflo_dir)
            tasks = [_spawn_one(ag) for ag in agents_list]
            gather_results = await asyncio.gather(*tasks, return_exceptions=True)
            par_failures = []
            for i, gr in enumerate(gather_results):
                ag_role = agents_list[i].get("role", "?")
                if isinstance(gr, Exception):
                    log(f"  Gate {gate_num_par} [{ag_role}] FAILED: {gr}")
                    par_failures.append((agents_list[i], gr))
                else:
                    log(f"  Gate {gate_num_par} [{ag_role}] OK")

            if par_failures:
                # A parallel agent hit an unrecoverable error. Escalate the
                # pipeline cleanly — do NOT write the error text into the
                # gate artifact (the "credulity bug": error text masquerading
                # as agent output poisons validation and spins the retry
                # loop). Mirrors the serial spawn_agent escalation path.
                first_info, first_exc = par_failures[0]
                log(
                    f"  ESCALATE: parallel gate {gate_num_par} — "
                    f"{len(par_failures)}/{len(agents_list)} agent(s) failed"
                )
                log("    option: fix the underlying environment issue and retry")
                log("    option: delete .sflo/ and retry from scratch")
                state = read_state(sflo_dir) or state
                state["current_state"] = S_ESCALATE
                gate_key = str(gate_num_par) if gate_num_par is not None else None
                if gate_key and gate_key in state.get("gates", {}):
                    state["gates"][gate_key]["status"] = "failed"
                state["escalation"] = {
                    "role": first_info.get("role"),
                    "gate": gate_num_par,
                    # Scrub before persisting — same credential-leak guard as
                    # the serial escalation path above.
                    "cause": _scrub_secret(
                        f"{type(first_exc).__name__}: {first_exc}"
                    ),
                }
                _locked_write_state(sflo_dir, state)
                return {
                    "ok": False,
                    "state": "escalate",
                    "error": _scrub_secret(
                        f"parallel gate {gate_num_par}: "
                        f"{type(first_exc).__name__}: {first_exc}"
                    ),
                    "role": first_info.get("role"),
                    "gate": gate_num_par,
                    "gates": state.get("gates", {}),
                    "inner_loops": state.get("inner_loops", 0),
                    "outer_loops": state.get("outer_loops", 0),
                }

            auto_transition(state, sflo_dir)
            state = read_state(sflo_dir)
            result = compute_next(state, sflo_dir)
            result = apply_transition(state, result, sflo_dir)
            state = read_state(sflo_dir)

            gate_check_num = result.get("gate")
            passed = result.get("pass", False)
            if passed and gate_check_num:
                log(f"  Gate {gate_check_num} ✓")
            elif not passed and gate_check_num:
                log(f"  Gate {gate_check_num} ✗")

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
                f"See runner non-progress guard."
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
    parser.add_argument(
        "--sflo-dir",
        default=".sflo",
        help="Parent directory for factory state dirs. Default: .sflo.",
    )
    parser.add_argument(
        "--factory",
        default=None,
        metavar="NAME",
        help="Factory name. Defaults to an auto-slug from the prompt.",
    )
    parser.add_argument(
        "--resume",
        default=None,
        metavar="NAME",
        help="Resume an existing factory by name.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List factories under --sflo-dir and exit.",
    )
    parser.add_argument(
        "--kill",
        default=None,
        metavar="NAME",
        help="Mark a factory aborted and clear its runner lock.",
    )
    parser.add_argument(
        "--clean-stale",
        action="store_true",
        help="Remove stale and aborted factories from the registry.",
    )
    parser.add_argument(
        "--runtime",
        choices=["openclaw", "claude-code", "codex", "cursor", "ollama"],
        default=None,
        help="Runtime adapter for pipeline starts.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    # Force UTF-8 stdout so non-ASCII JSON output (e.g. checkmarks) does not
    # raise UnicodeEncodeError on a Windows console.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass

    # Forward user MCP servers + enable Chrome extension for agents
    # Disable with SFLO_CHROME=0
    chrome_args = {"chrome": None}
    if os.environ.get("SFLO_CHROME", "").lower() in ("0", "false", "no", "off"):
        chrome_args = {}
    RuntimeAdapter.configure_mcp(
        load_user_mcp=True,
        extra_cli_args=chrome_args if chrome_args else None,
    )

    sflo_parent = os.path.abspath(args.sflo_dir)
    registry = FactoryRegistry(sflo_parent)

    if args.list:
        registry.migrate_legacy()
        print(format_registry_table(registry.list_all()))
        return

    if args.kill:
        if registry.kill(args.kill):
            print(f"Factory {args.kill!r} marked aborted.")
            return
        print(f"Factory {args.kill!r} not found.", file=sys.stderr)
        sys.exit(1)

    if args.clean_stale:
        removed = registry.clean_stale()
        if removed:
            print(f"Removed stale/aborted factories: {', '.join(removed)}")
        else:
            print("No stale or aborted factories to clean.")
        return

    if not args.runtime:
        parser.error(
            "--runtime is required for pipeline starts. "
            "Pass one of: claude-code, codex, cursor, openclaw, ollama."
        )

    prompt = args.prompt
    if not prompt or prompt == "-":
        prompt = sys.stdin.read().strip()
    if not prompt:
        parser.error("No prompt provided. Pass as argument or via stdin.")

    registry.migrate_legacy()
    if args.resume:
        proposed = args.resume
        is_explicit = True
        is_resume = True
    elif args.factory:
        proposed = args.factory
        is_explicit = True
        is_resume = False
        if not validate_factory_name(proposed):
            parser.error(
                "--factory must be 2-40 lowercase letters, numbers, and hyphens"
            )
    else:
        proposed = slug_from_prompt(prompt) or (
            "run-" + _datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        is_explicit = False
        is_resume = False
    try:
        factory_name = registry.resolve_name(
            proposed, is_explicit=is_explicit, is_resume=is_resume
        )
    except FactoryError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(3)
    sflo_dir = os.path.join(sflo_parent, factory_name)

    def _record_signal_exit(signum, sig_name):
        registry.register_end(
            factory_name,
            FactoryRegistry.STATUS_ABORTED,
            exit_kind="signal",
            exit_details={
                "signal": signum,
                "signal_name": sig_name,
                "pid": os.getpid(),
            },
        )

    _install_signal_handler(sflo_dir, on_signal_exit=_record_signal_exit)

    # One runner per state directory — two concurrent runners interleave
    # read-modify-write on state.json and corrupt it. Fail fast instead.
    from .state import acquire_instance_lock, release_instance_lock

    try:
        instance_lock = acquire_instance_lock(sflo_dir)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        sys.exit(1)

    if factory_name:
        registry.register_start(factory_name, sflo_dir, prompt, os.getpid())

        def _registry_atexit():
            entry = registry.get(factory_name)
            if not entry or entry.get("status") != FactoryRegistry.STATUS_ACTIVE:
                return
            current_state = ""
            try:
                with open(state_path(sflo_dir), encoding="utf-8") as f:
                    current_state = json.load(f).get("current_state", "") or ""
            except (OSError, json.JSONDecodeError):
                pass
            registry.register_end(
                factory_name,
                final_status_from_pipeline_state(current_state),
                exit_kind="process_exit",
                exit_details={"state": current_state, "pid": os.getpid()},
            )

        atexit.register(_registry_atexit)

    try:
        result = asyncio.run(
            run_pipeline(
                user_prompt=prompt,
                sflo_dir=sflo_dir,
                runtime=args.runtime,
                verbose=not args.quiet,
            )
        )
        if factory_name:
            result["factory"] = factory_name
            result["sflo_dir"] = sflo_dir
            registry.register_end(
                factory_name,
                final_status_from_pipeline_state(result.get("state", "")),
                exit_kind="completed",
                exit_details={"state": result.get("state", ""), "pid": os.getpid()},
            )
    except BaseException as exc:
        if factory_name:
            registry.register_end(
                factory_name,
                FactoryRegistry.STATUS_ABORTED,
                exit_kind="exception",
                exit_details={"exception_type": type(exc).__name__, "pid": os.getpid()},
            )
        raise
    finally:
        release_instance_lock(sflo_dir, instance_lock)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
