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
    python3 sflo/src/runner.py "Build a click counter" [--sflo-dir .sflo] [--quiet]
"""

import asyncio
import json
import os
import sys

# Allow running as script or module
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.bindings import parse_bindings, resolve_bindings_path
    from src.state import read_state, write_state, make_initial_state
    from src.machine import auto_transition, compute_next, apply_transition
    from src.validate import validate_agent_path
    from src.constants import SFLO_ROOT
    from src.guardian import init_guardian, record_spawn
else:
    from .bindings import parse_bindings, resolve_bindings_path
    from .state import read_state, write_state, make_initial_state
    from .machine import auto_transition, compute_next, apply_transition
    from .validate import validate_agent_path
    from .constants import SFLO_ROOT
    from .guardian import init_guardian, record_spawn


# ---------------------------------------------------------------------------
# Runtime Adapters
# ---------------------------------------------------------------------------

class RuntimeAdapter:
    """Base class — spawn an agent and return its response text."""

    async def spawn_agent(self, model, system_prompt, user_prompt):
        """Spawn agent via runtime. Returns response text (str)."""
        raise NotImplementedError


class ClaudeCodeAdapter(RuntimeAdapter):
    """Uses Claude Agent SDK — runs inside Claude Code, no API key needed."""

    async def spawn_agent(self, model, system_prompt, user_prompt):
        try:
            from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition
        except ImportError:
            raise RuntimeError(
                "claude_agent_sdk not available. "
                "Run setup.sh or: pip install claude-agent-sdk"
            )

        stderr_lines = []
        self._last_stderr = []  # Preserve for crash diagnostics

        def capture_stderr(line):
            stderr_lines.append(line)

        result_text = ""
        try:
            async for message in query(
                prompt=user_prompt,
                options=ClaudeAgentOptions(
                    system_prompt=system_prompt,
                    model=model,
                    allowed_tools=["Read", "Write", "Edit", "Grep", "Glob", "Bash"],
                    permission_mode="bypassPermissions",
                    max_turns=50,
                    stderr=capture_stderr,
                ),
            ):
                if hasattr(message, "result") and message.result:
                    result_text = message.result
                elif hasattr(message, "content") and message.content:
                    for block in message.content:
                        if hasattr(block, "text") and block.text:
                            result_text += block.text
        except Exception:
            # Preserve stderr before re-raising — this is the only
            # diagnostic data for exit code 1 crashes
            self._last_stderr = list(stderr_lines)
            raise

        if stderr_lines:
            print(f"  [Agent stderr: {len(stderr_lines)} lines]", file=sys.stderr)
            for line in stderr_lines[-10:]:  # last 10 lines
                print(f"    {line.rstrip()}", file=sys.stderr)

        return result_text


class OpenClawAdapter(RuntimeAdapter):
    """Uses `openclaw agent` CLI — full tool access, real agent sessions."""

    async def spawn_agent(self, model, system_prompt, user_prompt):
        import subprocess as sp

        message = f"{system_prompt}\n\n---\n\n{user_prompt}"

        cmd = [
            "openclaw", "agent",
            "--message", message,
            "--session-id", f"sflo-{id(message) % 100000}",
            "--json",
        ]

        # Map bindings thinking mode
        thinking_map = {"off": "off", "adaptive": "adaptive", "extended": "extended"}
        # thinking is passed via model bindings — not directly available here
        # but the CLI defaults are reasonable

        try:
            result = sp.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 min max per gate
            )
        except sp.TimeoutExpired:
            raise RuntimeError("OpenClaw agent timed out after 10 minutes")
        except FileNotFoundError:
            raise RuntimeError(
                "openclaw CLI not found. "
                "Install OpenClaw or run inside an OpenClaw workspace."
            )

        if result.returncode != 0:
            raise RuntimeError(f"openclaw agent failed (exit {result.returncode}): {result.stderr}")

        # Parse output — always return string
        try:
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                return str(data.get("content", data.get("result", result.stdout)))
            return str(data)
        except json.JSONDecodeError:
            return result.stdout


def detect_runtime():
    """Auto-detect which runtime we're in."""
    import shutil
    if shutil.which("openclaw"):
        return "openclaw"
    try:
        from claude_agent_sdk import query  # noqa: F401
        return "claude-code"
    except ImportError:
        pass
    return None


def get_adapter(runtime=None):
    """Get the appropriate runtime adapter."""
    if runtime is None:
        runtime = detect_runtime()

    if runtime == "openclaw":
        return OpenClawAdapter()
    elif runtime == "claude-code":
        return ClaudeCodeAdapter()
    else:
        raise RuntimeError(
            "No runtime detected. Run setup.sh to provision the environment, "
            "or install manually: pip install claude-agent-sdk"
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
    """Create a logger that writes to stderr and .sflo/pipeline.log."""
    os.makedirs(sflo_dir, exist_ok=True)
    log_path = os.path.join(sflo_dir, "pipeline.log")
    log_file = open(log_path, "a", encoding="utf-8")

    def log(msg):
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        log_file.write(line + "\n")
        log_file.flush()
        if verbose:
            print(msg, file=sys.stderr)

    log._file = log_file  # keep reference to close later
    return log


def format_validation_feedback(checks):
    """Format failed validation checks into actionable feedback for the agent."""
    failed = [c for c in checks if not c.get("pass")]
    if not failed:
        return ""
    lines = ["## Validation Errors — Fix These\n",
             "Your artifact failed the following automated checks:\n"]
    for c in failed:
        name = c.get("name", "unknown")
        detail = c.get("detail", "")
        lines.append(f"- **{name}**: {detail}" if detail else f"- **{name}**")
    lines.append("\nRevise the artifact to pass all checks. "
                 "Write it to the EXACT same path. "
                 "Do NOT remove sections that already pass.")
    return "\n".join(lines)


def build_agent_prompt(agent_info, user_prompt, sflo_dir):
    """Build system prompt and user prompt for a gate agent."""
    reads = agent_info.get("reads", [])

    # System prompt: agent SOUL.md (second file in reads list)
    system_parts = []
    if len(reads) >= 2:
        soul_content = read_file(reads[1])
        system_parts.append(soul_content)

    # Gate doc (first file in reads list)
    gate_content = ""
    if len(reads) >= 1:
        gate_content = read_file(reads[0])
        system_parts.append(f"## Gate Document\n\n{gate_content}")

    system_prompt = "\n\n---\n\n".join(system_parts) if system_parts else ""

    # User prompt: original request + prior artifacts
    user_parts = [f"## User Request\n\n{user_prompt}"]

    # Prior artifacts (reads[2:] are prior gate artifacts)
    for artifact_path in reads[2:]:
        content = read_file(artifact_path)
        name = os.path.basename(artifact_path)
        user_parts.append(f"## Prior Artifact: {name}\n\n{content}")

    produces = agent_info.get("produces", "")
    if produces:
        abs_produces = os.path.abspath(produces)
        artifact_name = os.path.basename(produces)
        user_parts.append(
            f"\n## Your Task\n\n"
            f"Write the artifact `{artifact_name}` to this EXACT path: {abs_produces}\n"
            f"Use the Write tool to create the file. Follow the gate document template EXACTLY.\n"
            f"Every section in the template is REQUIRED — do not skip any.\n"
            f"The scaffold validates the artifact automatically. Missing sections cause gate failure.\n"
            f"Create the parent directory if it doesn't exist."
        )

    user_msg = "\n\n---\n\n".join(user_parts)
    return system_prompt, user_msg


# ---------------------------------------------------------------------------
# Pipeline Runner
# ---------------------------------------------------------------------------

async def run_pipeline(user_prompt, sflo_dir=".sflo", runtime=None, verbose=True):
    """Run the full SFLO pipeline.

    Args:
        user_prompt: What to build.
        sflo_dir: Where to store pipeline state and artifacts.
        runtime: "openclaw", "claude-code", or None (auto-detect).
        verbose: Print progress to stderr.

    Returns:
        dict with final state, artifacts, and pipeline summary.
    """
    adapter = get_adapter(runtime)
    log = make_logger(sflo_dir, verbose)

    # --- Init ---
    bindings_path = resolve_bindings_path()
    if not bindings_path:
        return {"ok": False, "error": "bindings.yaml not found"}

    roles, err = parse_bindings(bindings_path)
    if err:
        return {"ok": False, "error": err}

    os.makedirs(sflo_dir, exist_ok=True)
    init_guardian(sflo_dir)
    state = make_initial_state(roles)
    write_state(sflo_dir, state)

    log(f"SFLO Pipeline — {user_prompt[:60]}")

    # --- Scout ---
    scout_bindings = roles.get("scout", {})
    scout_model = scout_bindings.get("model", "sonnet")
    scout_agent_path = scout_bindings.get("agent", os.path.join(SFLO_ROOT, "agents", "scout"))
    scout_soul = read_file(os.path.join(scout_agent_path, "SOUL.md"))

    # Find available agent directories
    agent_dirs = []
    cwd = os.getcwd()
    for candidate in [
        os.path.join(cwd, "agents"),
        os.path.join(cwd, "sflo", "agents"),
        os.path.join(SFLO_ROOT, "agents"),
    ]:
        if os.path.isdir(candidate):
            agent_dirs.append(candidate)

    agent_listing = ""
    for d in agent_dirs:
        for entry in sorted(os.listdir(d)):
            if entry.startswith("_"):
                continue
            entry_path = os.path.join(d, entry)
            if os.path.isdir(entry_path):
                brief = os.path.join(entry_path, "BRIEF.md")
                if os.path.isfile(brief):
                    agent_listing += f"\n### {entry} ({d})\n{read_file(brief)}\n"

    trip = record_spawn(sflo_dir)
    if trip:
        log(f"  GUARDIAN: {trip}")
        return {"ok": False, "error": trip, "state": "escalate"}

    try:
        scout_response = await adapter.spawn_agent(
            model=scout_model,
            system_prompt=scout_soul,
            user_prompt=(
                f"User prompt: {user_prompt}\n\n"
                f"Available agents:\n{agent_listing}\n\n"
                f"Return a JSON object with role assignments: "
                f'{{"pm": "<agent_path>", "dev": "<agent_path>", "qa": "<agent_path>"}}'
            ),
        )
    except Exception as e:
        import traceback
        log(f"  Scout failed: {e}")
        log(f"  {traceback.format_exc()}")
        scout_response = ""

    # Parse Scout's assignments
    try:
        # Extract JSON from response (may have surrounding text)
        import re
        json_match = re.search(r'\{[^{}]*"pm"[^{}]*\}', scout_response)
        if json_match:
            assignments = json.loads(json_match.group())
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
    write_state(sflo_dir, state)

    log(f"  Scout: {', '.join(f'{k}={v}' for k, v in assignments.items())}")

    # --- Gate Loop ---
    max_iterations = 50  # safety limit
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        auto_transition(state, sflo_dir)
        result = compute_next(state, sflo_dir)
        action = result.get("action")

        if action == "pipeline_complete":
            log("Pipeline complete.")
            break

        if action == "ask_human":
            log(f"  ESCALATE: {result.get('reason', 'unknown')}")
            break

        if action == "spawn_agent":
            agent = result["agent"]
            role = agent["role"]
            model = agent.get("model", "sonnet")

            system_prompt, user_msg = build_agent_prompt(agent, user_prompt, sflo_dir)

            import time
            response = None
            crash_context = ""

            for attempt in range(3):
                if attempt > 0:
                    log(f"  Gate [{role}/{model}] resume attempt {attempt + 1}/3 ...")
                else:
                    log(f"  Gate [{role}/{model}] ...")

                trip = record_spawn(sflo_dir)
                if trip:
                    log(f"  GUARDIAN: {trip}")
                    break

                spawn_start = time.time()
                try:
                    response = await adapter.spawn_agent(
                        model=model,
                        system_prompt=system_prompt,
                        user_prompt=user_msg + crash_context,
                    )
                    break  # success
                except Exception as e:
                    import traceback
                    log(f"  Gate [{role}] agent crashed: {e}")
                    log(f"  {traceback.format_exc()}")
                    # Log stderr from the crashed CLI process — this is the
                    # only diagnostic data for exit code 1 crashes. The SDK's
                    # "Check stderr output for details" is a hardcoded string,
                    # not actual stderr. The real stderr is in adapter's callback.
                    if hasattr(adapter, '_last_stderr') and adapter._last_stderr:
                        log(f"  [CLI stderr ({len(adapter._last_stderr)} lines):]")
                        for sl in adapter._last_stderr[-20:]:
                            log(f"    {sl.rstrip()}")
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
                        log(f"  Resuming with crash context...")
                    else:
                        log(f"  All resume attempts exhausted — gate will fail validation")
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
                        import shutil
                        shutil.move(found, produces)
                        log(f"  {artifact_name} ✓ (moved from {found})")
                    else:
                        os.makedirs(os.path.dirname(produces) or ".", exist_ok=True)
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

            trip = record_spawn(sflo_dir)
            if trip:
                log(f"  GUARDIAN: {trip}")
                break

            import time
            spawn_start = time.time()
            gate5_prompt = (f"## User Request\n\n{user_prompt}\n\n{prior}\n\n"
                            f"Write {artifact_name} to this EXACT path: {abs_artifact}\n"
                            f"Use the Write tool. Follow the template EXACTLY. "
                            f"Create the parent directory if needed.")
            try:
                response = await adapter.spawn_agent(
                    model=roles.get("sflo", {}).get("model", "opus"),
                    system_prompt=system_prompt,
                    user_prompt=gate5_prompt,
                )
            except Exception as e:
                import traceback
                log(f"  Gate 5 [SFLO] agent crashed: {e}")
                log(f"  {traceback.format_exc()}")
                log(f"  Gate 5 will fail validation")
                response = f"[Agent error: {e}]"

            # Verify agent wrote it (same logic as spawn_agent gates)
            if os.path.isfile(artifact_path) and os.path.getmtime(artifact_path) > spawn_start:
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
                    import shutil
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

        elif action in ("validated", "check_failed"):
            # Already handled by apply_transition above
            result = apply_transition(state, result, sflo_dir)
            state = read_state(sflo_dir)

        else:
            log(f"  Unknown action: {action}")
            break

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

    parser = argparse.ArgumentParser(description="SFLO Runner — enforced pipeline execution")
    parser.add_argument("prompt", nargs="?", default=None, help="What to build (or pass via stdin)")
    parser.add_argument("--sflo-dir", default=".sflo", help="Pipeline state directory")
    parser.add_argument("--runtime", choices=["openclaw", "claude-code"], default=None)
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    prompt = args.prompt
    if not prompt or prompt == "-":
        prompt = sys.stdin.read().strip()
    if not prompt:
        parser.error("No prompt provided. Pass as argument or via stdin.")

    result = asyncio.run(run_pipeline(
        user_prompt=prompt,
        sflo_dir=args.sflo_dir,
        runtime=args.runtime,
        verbose=not args.quiet,
    ))

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
