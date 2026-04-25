"""ClaudeCodeAdapter — runs agents via Claude Agent SDK inside Claude Code."""

import asyncio
import os
import shutil
import sys
import time as _time
from pathlib import Path

from .base import RuntimeAdapter
from ..bindings import load_security_config

# ---------------------------------------------------------------------------
# Tool policy — driven by `tools:` field in bindings.yaml per role.
#
# Philosophy: workhorse agents need almost-absolute access to do their job.
# Restriction is a security exception, not a default. Sflo only enforces
# ONE restricted preset (`readonly`) for scout-style recon; everything else
# gets the full session toolset (built-in + MCP + skills).
#
# Built-in Claude Code tools available with `tools: full`:
#   Read, Write, Edit, MultiEdit, NotebookEdit  (filesystem + notebooks)
#   Glob, Grep                                  (search)
#   Bash, BashOutput, KillShell                 (shell + long-running procs)
#   WebFetch, WebSearch                         (web)
#   TodoWrite                                   (task tracking)
#   Task                                        (subagent spawning)
# Plus dynamically-registered MCP tools (mcp__<server>__<tool>) — any tool
# from the MCP servers the session has connected (chrome-devtools, computer-
# use, playwright, computer, etc.) is automatically available because we
# pass allowed_tools=None which means "all tools available in the session".
# Plus any skills registered via the Skill mechanism.
#
# Bindings.yaml field:
#   roles:
#     scout:
#       tools: readonly      # Read/Glob/Grep only — no Write, no Bash, no Web, no MCP
#     pm:
#       tools: full          # all tools (default if omitted)
#     dev:
#       tools: full          # all tools
#     qa:
#       tools: full          # all tools (incl. Write — needs to write reports)
#     # ... omit `tools:` for default (full)
#
# Caller-supplied allowed_tools kwarg overrides bindings (backward-compat).
# ---------------------------------------------------------------------------

# Mode → allowed_tools list. None = all tools available in session (incl. MCP).
TOOL_MODE_PRESETS = {
    "readonly": ["Read", "Glob", "Grep"],
    "full": None,  # None = unrestricted (all built-in + MCP + skills)
}


def resolve_allowed_tools(tools_mode, caller_supplied=None):
    """Resolve allowed_tools list. Caller-supplied wins; else apply preset.

    Args:
      tools_mode: string from bindings.yaml `tools:` field (e.g. "readonly").
                  Unknown / None / "full" → None (all tools available).
      caller_supplied: list of tool names from runner kwarg (overrides mode).

    Returns: list of tool name strings, OR None to mean "all tools".
    """
    if caller_supplied is not None:
        return caller_supplied
    if tools_mode in TOOL_MODE_PRESETS:
        return TOOL_MODE_PRESETS[tools_mode]
    # Unknown mode (or None) → full access. Don't second-guess the operator.
    return None


class ClaudeCodeAdapter(RuntimeAdapter):
    """Uses Claude Agent SDK — runs inside Claude Code, no API key needed."""

    # Default: None = use all tools available in the session (MCP, browser, etc.)
    # Per-role overrides (e.g. scout read-only) are passed via allowed_tools kwarg.
    ALLOWED_TOOLS = None

    async def spawn_agent(
        self,
        model,
        system_prompt,
        user_prompt,
        cwd=None,
        role=None,
        allowed_tools=None,
        tools_mode=None,
    ):
        return await self._run_agent(
            model,
            system_prompt,
            user_prompt,
            cwd=cwd,
            allowed_tools=allowed_tools,
            tools_mode=tools_mode,
            role=role,
        )

    # Max seconds to wait for MCP servers to connect.
    MCP_READY_TIMEOUT = 30

    async def _run_agent(
        self,
        model,
        system_prompt,
        user_prompt,
        cwd=None,
        allowed_tools=None,
        tools_mode=None,
        role=None,
    ):
        try:
            from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
        except ImportError:
            raise RuntimeError(
                "claude_agent_sdk not available. "
                "Run setup.sh or: pip install claude-agent-sdk"
            )

        stderr_lines = []
        self._last_stderr = []  # Preserve for crash diagnostics

        def capture_stderr(line):
            stderr_lines.append(line)

        # Load security toggles from bindings.yaml. Defaults are all-false
        # (host-trusted: agents inherit user PAI surface, no isolation).
        # Each isolation layer is opt-in via bindings.yaml `security:` block.
        sec = load_security_config()

        # Build options with MCP and extra args if configured.
        # MCP servers are forwarded only to roles whose tools_mode permits them.
        # readonly mode = no MCP (no browser tools, no shell tools, etc.).
        # full mode (or unspecified) = all MCP servers attached.
        resolved_tools = resolve_allowed_tools(tools_mode, allowed_tools)
        opts = dict(
            system_prompt=system_prompt,
            model=model,
            allowed_tools=resolved_tools,
            # require_permission=true → "default" (interactive approval per
            # tool action; deadlocks background pipelines).
            # require_permission=false → "bypassPermissions" (auto-approve
            # all writes; default for host-trusted single-user setup).
            permission_mode=(
                "default" if sec["require_permission"] else "bypassPermissions"
            ),
            stderr=capture_stderr,
        )
        if cwd is not None:
            opts["cwd"] = cwd
        # MCP attaches by default; readonly mode opts out so scout-style recon
        # truly has no remote tool surface beyond Read/Glob/Grep.
        needs_mcp = tools_mode != "readonly"
        if self._mcp_servers and needs_mcp:
            opts["mcp_servers"] = self._mcp_servers
            # Append tool usage notes from mcp-defaults.json
            defaults = self._load_mcp_defaults()
            prompts = []
            for name in self._mcp_servers:
                note = defaults.get(name, {}).get("system_prompt_append")
                if note:
                    prompts.append(note)
            if prompts:
                opts["system_prompt"] = (
                    (opts.get("system_prompt") or "") + "\n\n" + " ".join(prompts)
                )
        if self._extra_cli_args and needs_mcp:
            opts["extra_args"] = self._extra_cli_args

        # no_session_persistence=true → block ~/.claude/projects/*.jsonl
        # session dump. Merge (not overwrite) so MCP extra_args above are preserved.
        if sec["no_session_persistence"]:
            opts["extra_args"] = {
                **(opts.get("extra_args") or {}),
                "no-session-persistence": None,
            }

        # isolate_settings=true → setting_sources=[] severs the user PAI
        # surface (hooks, MCPs, skills, slash, CLAUDE.md memory, perms).
        # Default false: agent inherits user settings.json normally.
        if sec["isolate_settings"]:
            opts["setting_sources"] = []

        # sandbox_config_dir=true → override CLAUDE_CONFIG_DIR to a per-spawn
        # .claude_sandbox/ inside cwd so the CLI cannot find user config.
        # `sandbox_dir` stays None when the toggle is off so the wipe step
        # below has nothing to clean up.
        sandbox_dir = None
        if sec["sandbox_config_dir"]:
            sandbox_dir = Path(cwd if cwd is not None else os.getcwd()) / ".claude_sandbox"
            sandbox_dir.mkdir(exist_ok=True)
            opts["env"] = {**(opts.get("env") or {}), "CLAUDE_CONFIG_DIR": str(sandbox_dir)}

        # Web-tool gating is now driven by tools_mode (see top of file).
        # A blanket disallowed_tools=["WebFetch","WebSearch"] used to live here
        # as defense-in-depth, but it second-guessed the operator's bindings
        # config — roles that don't have web in their resolved tool list can't
        # call those tools anyway because the SDK only exposes allowed_tools.

        result_text = ""
        assistant_msgs = 0
        tool_calls = 0
        start_time = _time.time()
        try:
            async with ClaudeSDKClient(ClaudeAgentOptions(**opts)) as client:
                # Wait for MCP servers if configured and role needs them
                if self._mcp_servers and needs_mcp:
                    deadline = _time.time() + self.MCP_READY_TIMEOUT
                    servers = []  # initialize before loop so else-clause can reference it
                    while _time.time() < deadline:
                        status = await client.get_mcp_status()
                        servers = status.get("mcpServers", [])
                        if not servers or all(
                            s.get("status") == "connected" for s in servers
                        ):
                            if servers:
                                info = ", ".join(
                                    f"{s['name']}({len(s.get('tools', []))})"
                                    for s in servers
                                )
                                print(f"  [MCP ready: {info}]", file=sys.stderr)
                            break
                        await asyncio.sleep(1)
                    else:
                        pending = [
                            s["name"] for s in servers if s.get("status") != "connected"
                        ]
                        print(
                            f"  [MCP timeout {self.MCP_READY_TIMEOUT}s — "
                            f"pending: {', '.join(pending)}]",
                            file=sys.stderr,
                        )

                await client.query(user_prompt)
                # Per-message gap timeout — guards against the silent-hang class.
                # Without this, an external pty hangup (e.g. Claude Desktop's
                # auto-update closing the controlling tty) can leave the SDK's
                # async iterator wedged on a half-closed anyio stream that
                # never delivers EOF — the parent process becomes silent
                # with registry stuck "active" and no traceback.
                #
                # IMPORTANT: this is the gap BETWEEN messages, NOT total runtime.
                # A day-long agent that streams continuously is fine; the timer
                # only fires when no message arrives for this many seconds in a
                # row. For deep-thinking on opus + max effort, or long-running
                # bash tool calls that don't stream output, a higher value is
                # needed — that's what the env override is for.
                #
                # Default 600s (10 min) tolerates slow tool calls and deep
                # thinking gaps. Override via SFLO_PER_MESSAGE_TIMEOUT, e.g.
                # `SFLO_PER_MESSAGE_TIMEOUT=3600` for hour-long bash tool calls.
                # Set to 0 to disable the guard entirely (use only when you
                # know the parent will reap zombies via signal).
                #
                # On timeout: we MUST `await client.disconnect()` in finally
                # so the spawned `claude` CLI subprocess is reaped, not
                # leaked as a zombie (CR2-5). The RuntimeError then falls
                # into the existing `except Exception` path below which
                # prints `[Agent metrics at crash]` and propagates so the
                # runner can mark the factory aborted.
                _PER_MESSAGE_TIMEOUT_S = int(
                    os.environ.get("SFLO_PER_MESSAGE_TIMEOUT", "600")
                )
                # 0 = disabled (no per-message guard). asyncio.wait_for
                # treats timeout=None as "wait forever".
                _wait_for_timeout = (
                    None if _PER_MESSAGE_TIMEOUT_S <= 0 else _PER_MESSAGE_TIMEOUT_S
                )
                _response_iter = client.receive_response().__aiter__()
                while True:
                    try:
                        message = await asyncio.wait_for(
                            _response_iter.__anext__(),
                            timeout=_wait_for_timeout,
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        try:
                            await client.disconnect()
                        finally:
                            raise RuntimeError(
                                f"Agent silent for {_PER_MESSAGE_TIMEOUT_S}s — "
                                "likely upstream runtime restart, broken pipe, "
                                "or runner SIGHUP'd via pty cleanup. "
                                "If this was a slow tool call or deep thinking, "
                                "raise SFLO_PER_MESSAGE_TIMEOUT (seconds) or "
                                "set it to 0 to disable the guard."
                            )

                    if hasattr(message, "result") and message.result:
                        result_text = message.result
                    elif hasattr(message, "content") and message.content:
                        assistant_msgs += 1
                        for block in message.content:
                            if hasattr(block, "text") and block.text:
                                result_text += block.text
                            block_type = type(block).__name__
                            if block_type == "ToolUseBlock" or (
                                hasattr(block, "name") and hasattr(block, "input")
                            ):
                                tool_calls += 1
                                # Emit live progress so the Mac UI can update
                                # agent card tool counts mid-run (AC3).
                                elapsed_now = _time.time() - start_time
                                print(
                                    f"  [Agent metrics — role={role}, model={model}, "
                                    f"msgs={assistant_msgs}, tools={tool_calls}, "
                                    f"elapsed={elapsed_now:.0f}s]",
                                    file=sys.stderr,
                                )
        except Exception as e:
            # Enrich known crash types with actionable guidance so the
            # retry's crash_context helps the next attempt avoid the same
            # failure. The original exception propagates unchanged for
            # unknown errors.
            err_str = str(e)
            if "maximum buffer size" in err_str:
                e = RuntimeError(
                    f"{err_str}\n\n"
                    "CAUSE: A tool returned a response larger than 1MB (likely "
                    "take_screenshot returning a full PNG). On retry, use "
                    "take_snapshot (DOM text) instead of take_screenshot, or "
                    "pass format='jpeg' and quality=50 to take_screenshot."
                )
            elapsed = _time.time() - start_time
            print(
                f"  [Agent metrics at crash — role={role}, model={model}, "
                f"msgs={assistant_msgs}, tools={tool_calls}, "
                f"elapsed={elapsed:.0f}s]",
                file=sys.stderr,
            )
            self._last_stderr = list(stderr_lines)
            if stderr_lines:
                print(
                    f"  [Agent stderr on crash — {len(stderr_lines)} lines, "
                    f"role={role}, model={model}]",
                    file=sys.stderr,
                )
                for line in stderr_lines[-30:]:
                    print(f"    {line.rstrip()}", file=sys.stderr)
                tail = "\n".join(line.rstrip() for line in stderr_lines[-20:])
                raise RuntimeError(
                    f"{type(e).__name__}: {e}\n"
                    f"--- captured stderr (last 20 of {len(stderr_lines)} lines) ---\n"
                    f"{tail}"
                ) from e
            else:
                print(
                    f"  [Agent crash with EMPTY stderr — role={role}, "
                    f"model={model}, exception={type(e).__name__}: {e}]",
                    file=sys.stderr,
                )
            raise
        finally:
            # wipe_sandbox=true (and sandbox actually created) → rm -rf the
            # per-spawn config dir so no forensic residue remains. No-op when
            # sandbox_config_dir was off.
            if sec["wipe_sandbox"] and sandbox_dir is not None:
                shutil.rmtree(sandbox_dir, ignore_errors=True)

        elapsed = _time.time() - start_time
        print(
            f"  [Agent metrics — role={role}, model={model}, "
            f"msgs={assistant_msgs}, tools={tool_calls}, "
            f"elapsed={elapsed:.0f}s]",
            file=sys.stderr,
        )

        if stderr_lines:
            print(f"  [Agent stderr: {len(stderr_lines)} lines]", file=sys.stderr)
            for line in stderr_lines[-10:]:  # last 10 lines
                print(f"    {line.rstrip()}", file=sys.stderr)

        return result_text
