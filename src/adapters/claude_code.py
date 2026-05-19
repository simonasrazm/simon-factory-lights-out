"""ClaudeCodeAdapter — runs agents via Claude Agent SDK inside Claude Code."""

import asyncio
import os
import shutil
import tempfile
import time as _time
from pathlib import Path

from .base import RuntimeAdapter
from .errors import NonRetryableError
from ..security import load_security_config
from .._stderr import _safe_stderr, _scrub_secret
from ..evals import EvalAbortError
from ..evals.integration import run_tool_call_evals


# Max bytes the system_prompt may occupy when passed inline as a CLI arg.
# Windows' CreateProcess accepts <= 32,767 chars total for the entire
# command line; we keep half that as the prompt budget so model name,
# tool list, MCP config, and other args fit comfortably.
PROMPT_INLINE_LIMIT_BYTES = 16 * 1024


def _classify_sdk_error(exc):
    """Wrap permanent SDK failures as NonRetryableError so the runner skips
    its 3-attempt retry loop. Unknown errors pass through unchanged.

    Permanent failures:
      - CLINotFoundError: claude.exe missing or unreachable
      - CLIConnectionError: see deliberate fail-fast note below
      - FileNotFoundError WinError 206: command-line too long (the Windows
        kernel reports a missing-file error in this case). Retrying won't
        shorten the args; this is a config / size problem, not transient.

    CLIConnectionError — DELIBERATE non-retryable classification:
    The SDK raises this when it cannot establish the control connection to
    the `claude` CLI subprocess at all (the process failed to start, the
    handshake never completed, or the pipe was closed before any message).
    That is a structural/environment fault — a missing binary, a broken
    install, an incompatible SDK/CLI version, a sandbox blocking the spawn —
    not a transient network blip on a request already in flight. Retrying
    such a spawn 3x just burns time on the same broken environment. The
    runner already has its own per-message gap timeout + outer gate-retry
    loop for genuinely transient mid-stream stalls; this classifier is for
    "the agent could never start". Fail fast and escalate to a human who
    can fix the install. If a future SDK version starts raising
    CLIConnectionError for recoverable mid-session drops, reconsider and
    move it to TransientError.
    """
    # Try real isinstance against the SDK error classes first. String-name
    # matching alone is brittle: a vendor patch / subclass would silently
    # defeat the classifier.
    _sdk_cli_errors = ()
    try:
        from claude_agent_sdk._errors import (
            CLINotFoundError as _CLINotFoundError,
            CLIConnectionError as _CLIConnectionError,
        )
        _sdk_cli_errors = (_CLINotFoundError, _CLIConnectionError)
    except ImportError:
        try:
            from claude_agent_sdk._errors import (
                CLINotFoundError as _CLINotFoundError,
            )
            _sdk_cli_errors = (_CLINotFoundError,)
        except ImportError:
            pass

    if _sdk_cli_errors and isinstance(exc, _sdk_cli_errors):
        return NonRetryableError(f"Claude Code CLI not reachable: {exc}")

    # Fallback for SDK installs whose _errors module is private/restructured.
    if type(exc).__name__ in ("CLINotFoundError", "CLIConnectionError"):
        return NonRetryableError(f"Claude Code CLI not reachable: {exc}")

    if isinstance(exc, FileNotFoundError):
        winerr = getattr(exc, "winerror", None)
        msg = str(exc).lower()
        if winerr == 206 or "filename or extension is too long" in msg:
            return NonRetryableError(
                "Windows CreateProcess failed with ERROR_FILENAME_EXCED_RANGE "
                "(206) — the command line passed to claude.exe exceeds the "
                "32,767-char limit. Most common cause: a system prompt + skills "
                "bundle larger than ~30 KB inlined as a CLI arg. Consider "
                "reducing the skill set for this gate, or use --system-prompt-file."
            )
    return exc

# ---------------------------------------------------------------------------
# Tool policy — driven by `tools:` field in pipeline.yaml per role.
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
      tools_mode: string from pipeline.yaml `tools:` field (e.g. "readonly").
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


def build_sdk_options(
    system_prompt,
    model,
    security_config,
    tools_mode=None,
    allowed_tools=None,
    cwd=None,
    mcp_servers=None,
    mcp_defaults=None,
    extra_cli_args=None,
    stderr_callback=None,
    thinking=None,
    effort=None,
):
    """Build the kwargs dict for ClaudeAgentOptions.

    Pure function — no side effects, no SDK import, fully testable.
    Returns (opts_dict, sandbox_dir_or_None).
    """
    resolved_tools = resolve_allowed_tools(tools_mode, allowed_tools)
    sec = security_config

    opts = dict(
        system_prompt=system_prompt,
        model=model,
        permission_mode=(
            "default" if sec["require_permission"] else "bypassPermissions"
        ),
    )
    if stderr_callback is not None:
        opts["stderr"] = stderr_callback
    if resolved_tools is not None:
        opts["allowed_tools"] = resolved_tools
    if cwd is not None:
        opts["cwd"] = cwd

    # Per-gate thinking and effort settings
    _THINKING_MAP = {
        "off": {"type": "disabled"},
        "adaptive": {"type": "adaptive"},
        "extended": {"type": "enabled", "budget_tokens": 32768},
    }
    if thinking and thinking in _THINKING_MAP:
        opts["thinking"] = _THINKING_MAP[thinking]
    if effort and effort in ("low", "medium", "high", "max"):
        opts["effort"] = effort

    # readonly mode opts out of MCP — scout-style recon stays Read/Glob/Grep only.
    needs_mcp = tools_mode != "readonly"
    if mcp_servers and needs_mcp:
        opts["mcp_servers"] = mcp_servers
        # Append tool usage notes from mcp-defaults
        prompts = []
        for name in mcp_servers:
            note = (mcp_defaults or {}).get(name, {}).get("system_prompt_append")
            if note:
                prompts.append(note)
        if prompts:
            opts["system_prompt"] = (
                (opts.get("system_prompt") or "") + "\n\n" + " ".join(prompts)
            )
    if extra_cli_args and needs_mcp:
        opts["extra_args"] = extra_cli_args

    if sec["no_session_persistence"]:
        opts["extra_args"] = {
            **(opts.get("extra_args") or {}),
            "no-session-persistence": None,
        }

    # Settings isolation. all-mode wins over user-mode if both set.
    if sec["isolate_all_settings"]:
        opts["setting_sources"] = []
    elif sec["isolate_user_settings"]:
        opts["setting_sources"] = ["project", "local"]

    sandbox_dir = None
    if sec["sandbox_config_dir"]:
        sandbox_dir = Path(cwd if cwd is not None else os.getcwd()) / ".claude_sandbox"
        sandbox_dir.mkdir(exist_ok=True)
        opts["env"] = {**(opts.get("env") or {}), "CLAUDE_CONFIG_DIR": str(sandbox_dir)}

    # If the final system_prompt exceeds the inline budget, write it to a
    # temp file and reference it via the SDK's --system-prompt-file primitive.
    # Without this, Windows CreateProcess raises ERROR_FILENAME_EXCED_RANGE
    # (WinError 206) when the inline arg pushes the command line past 32 KB.
    # Caller is responsible for cleaning up paths returned in `temp_files`
    # after the SDK call completes.
    temp_files = []
    sp = opts.get("system_prompt")
    if isinstance(sp, str) and len(sp.encode("utf-8")) > PROMPT_INLINE_LIMIT_BYTES:
        tmpdir = os.environ.get("SFLO_PROMPT_TMPDIR") or tempfile.gettempdir()
        os.makedirs(tmpdir, exist_ok=True)
        fd, path = tempfile.mkstemp(
            prefix="sflo-prompt-", suffix=".txt", dir=tmpdir, text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(sp)
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        opts["system_prompt"] = {"type": "file", "path": path}
        temp_files.append(path)

    return opts, sandbox_dir, needs_mcp, temp_files


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
        thinking=None,
        effort=None,
        mcp_servers=None,
        allow_task=None,
        env=None,
    ):
        return await self._run_agent(
            model,
            system_prompt,
            user_prompt,
            cwd=cwd,
            allowed_tools=allowed_tools,
            tools_mode=tools_mode,
            role=role,
            thinking=thinking,
            effort=effort,
            mcp_servers=mcp_servers,
            allow_task=allow_task,
            env=env,
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
        thinking=None,
        effort=None,
        allow_task=None,
        mcp_servers=None,
        env=None,
    ):
        try:
            from claude_agent_sdk import (
                ClaudeSDKClient,
                ClaudeAgentOptions,
                HookMatcher,
            )
        except ImportError:
            raise RuntimeError(
                "claude_agent_sdk not available. "
                "Run setup.sh or: pip install claude-agent-sdk"
            )

        stderr_lines = []
        self._last_stderr = []  # Preserve for crash diagnostics

        def capture_stderr(line):
            stderr_lines.append(line)

        sec = load_security_config()

        if sec["require_permission"]:
            _safe_stderr(
                "  [security] require_permission=true — non-interactive runs "
                "will hang on tool calls without an allow-list / prompt tool."
            )

        # Per-gate MCP override: if gate declares mcp: list, use only those servers.
        # If mcp_servers kwarg is None, fall back to class-level (all servers).
        effective_mcp = mcp_servers if mcp_servers is not None else self._mcp_servers

        # Pre-bind cleanup-relevant locals so the `finally` below can run
        # even if build_sdk_options() itself raises. build_sdk_options writes
        # the >16 KB system prompt to a tempfile and returns its path in
        # temp_files; that creation must live INSIDE the try whose finally
        # unlinks temp_files, otherwise an exception between the temp-file
        # write and the ClaudeAgentOptions construction orphans the file
        # (security LOW-2). sandbox_dir is likewise referenced in finally.
        sandbox_dir = None
        needs_mcp = True
        temp_files = []
        result_text = ""
        assistant_msgs = 0
        tool_calls = 0
        total_tokens = 0
        start_time = _time.time()
        try:
            opts, sandbox_dir, needs_mcp, temp_files = build_sdk_options(
                system_prompt=system_prompt,
                model=model,
                security_config=sec,
                tools_mode=tools_mode,
                allowed_tools=allowed_tools,
                cwd=cwd,
                mcp_servers=effective_mcp,
                mcp_defaults=self._load_mcp_defaults() if effective_mcp else None,
                extra_cli_args=self._extra_cli_args,
                stderr_callback=capture_stderr,
                thinking=thinking,
                effort=effort,
            )

            if env:
                opts["env"] = {**(opts.get("env") or {}), **env}

            # --- PRE_TOOL_CALL eval dispatch — claude-code runtime wiring ---
            # run_tool_call_evals (eval framework) is runtime-agnostic; this hook
            # is the claude-code glue. Claude Code fires PreToolUse before a tool
            # runs; on EvalAbortError we return permissionDecision="deny" so the
            # tool is blocked BEFORE execution (path_traversal_blocker,
            # shell_metachar_guard). The eval registry is already loaded
            # in-process — no reload, no subprocess.
            async def _pretooluse_eval_hook(hook_input, _tool_use_id, _hook_ctx):
                try:
                    await run_tool_call_evals(
                        hook_input.get("tool_name", ""),
                        hook_input.get("tool_input", {}) or {},
                        role=role,
                        metadata={"role": role or "unknown"},
                    )
                except EvalAbortError as _abort:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                f"sflo eval '{_abort.eval_name}' blocked this "
                                f"tool call: {_abort.reason}"
                            ),
                        }
                    }
                return {}

            opts["hooks"] = {
                "PreToolUse": [
                    HookMatcher(
                        matcher="Read|Write|Edit|Glob|Bash",
                        hooks=[_pretooluse_eval_hook],
                    )
                ]
            }

            if sec["isolate_all_settings"]:
                _safe_stderr(
                    "  [security] isolate_all_settings=true — project settings "
                    "severed in spawned agents (interactive Stop hook only)."
                )

            async with ClaudeSDKClient(ClaudeAgentOptions(**opts)) as client:
                # Wait for MCP servers if configured and role needs them
                if effective_mcp and needs_mcp:
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
                                _safe_stderr(f"  [MCP ready: {info}]")
                            break
                        await asyncio.sleep(1)
                    else:
                        pending = [
                            s["name"] for s in servers if s.get("status") != "connected"
                        ]
                        _safe_stderr(
                            f"  [MCP timeout {self.MCP_READY_TIMEOUT}s — "
                            f"pending: {', '.join(pending)}]"
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
                        msg_text_len = 0
                        for block in message.content:
                            if hasattr(block, "text") and block.text:
                                result_text += block.text
                                msg_text_len += len(block.text)
                            block_type = type(block).__name__
                            if block_type == "ToolUseBlock" or (
                                hasattr(block, "name") and hasattr(block, "input")
                            ):
                                tool_calls += 1
                                # Tool name for observability
                                tool_name = getattr(block, "name", "?")
                                # Extract tool input for security-sensitive tools
                                tool_input = getattr(block, "input", None) or {}
                                tool_detail = ""
                                if tool_name == "Bash":
                                    cmd = tool_input.get("command", "")
                                    # Log first 200 chars of command for audit
                                    cmd_short = cmd[:200] + (
                                        "…" if len(cmd) > 200 else ""
                                    )
                                    tool_detail = f", cmd={cmd_short}"
                                elif tool_name == "Task":
                                    # Task = subagent spawn — MUST log for
                                    # security parity with parent agents.
                                    prompt = tool_input.get("prompt", "")
                                    prompt_short = prompt[:200] + (
                                        "…" if len(prompt) > 200 else ""
                                    )
                                    tool_detail = f", subagent_prompt={prompt_short}"
                                    if allow_task is False:
                                        _safe_stderr(
                                            f"  [WARNING] role={role} used Task "
                                            f"tool but allow_task=false for this gate"
                                        )
                                elif tool_name == "Write":
                                    path = tool_input.get("file_path", "?")
                                    tool_detail = f", path={path}"
                                elif tool_name == "Edit":
                                    path = tool_input.get("file_path", "?")
                                    tool_detail = f", path={path}"
                                # Emit live progress so the Mac UI can update
                                # agent card tool counts mid-run (AC3).
                                elapsed_now = _time.time() - start_time
                                _safe_stderr(
                                    f"  [Agent metrics — role={role}, model={model}, "
                                    f"msgs={assistant_msgs}, tools={tool_calls}, "
                                    f"elapsed={elapsed_now:.0f}s, "
                                    f"tool={tool_name}{tool_detail}]"
                                )
                        # Log text generation length when substantial
                        if msg_text_len > 500:
                            _safe_stderr(
                                f"  [Agent text — role={role}, "
                                f"msg={assistant_msgs}, chars={msg_text_len}]"
                            )

                    # Extract token usage from various SDK message shapes
                    if hasattr(message, "usage") and message.usage:
                        usage = message.usage
                        if isinstance(usage, dict):
                            total_tokens = (
                                usage.get(
                                    "total_tokens",
                                    usage.get("input_tokens", 0)
                                    + usage.get("output_tokens", 0),
                                )
                                or total_tokens
                            )
                        elif hasattr(usage, "total_tokens"):
                            total_tokens = usage.total_tokens or total_tokens
                    # Fallback: some SDK versions expose cost/tokens at message level
                    if not total_tokens and hasattr(message, "token_count"):
                        total_tokens = message.token_count
        except Exception as e:
            # Classify permanent SDK errors (CLINotFoundError, WinError 206)
            # as NonRetryableError so the runner skips retries — there's no
            # point burning 30 attempts on a missing binary or a too-long
            # command line.
            classified = _classify_sdk_error(e)
            if classified is not e:
                e = classified

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
            token_str = f", tokens={total_tokens}" if total_tokens else ", tokens=n/a"
            _safe_stderr(
                f"  [Agent metrics at crash — role={role}, model={model}, "
                f"msgs={assistant_msgs}, tools={tool_calls}{token_str}, "
                f"elapsed={elapsed:.0f}s]"
            )
            self._last_stderr = list(stderr_lines)
            if stderr_lines:
                _safe_stderr(
                    f"  [Agent stderr on crash — {len(stderr_lines)} lines, "
                    f"role={role}, model={model}]"
                )
                # Scrub each captured stderr line before it reaches the
                # pipeline log: SDK / CLI stderr can echo a Bearer header,
                # an sk- token, or a credentialed URL.
                for line in stderr_lines[-30:]:
                    _safe_stderr(f"    {_scrub_secret(line.rstrip())}")
                tail = "\n".join(line.rstrip() for line in stderr_lines[-20:])
                # Scrub the enriched message too — it becomes the exception
                # text, which the runner persists into state.json
                # (escalation.cause). Scrubbing here keeps the secret out of
                # the exception object itself, not only the JSON copy.
                enriched = _scrub_secret(
                    f"{type(e).__name__}: {e}\n"
                    f"--- captured stderr (last 20 of {len(stderr_lines)} lines) ---\n"
                    f"{tail}"
                )
                # Preserve a NonRetryableError classification (set by
                # _classify_sdk_error above). Re-raising as a bare
                # RuntimeError here would discard it, and the runner would
                # then burn its full 3-attempt retry budget on a permanent
                # failure — the exact retry-skip _classify_sdk_error exists
                # to enable.
                if isinstance(e, NonRetryableError):
                    raise NonRetryableError(enriched) from e
                raise RuntimeError(enriched) from e
            else:
                _safe_stderr(
                    f"  [Agent crash with EMPTY stderr — role={role}, "
                    f"model={model}, exception={type(e).__name__}: {e}]"
                )
            raise
        finally:
            if sec["wipe_sandbox"] and sandbox_dir is not None:
                shutil.rmtree(sandbox_dir, ignore_errors=True)
            # Clean up temp prompt files written by build_sdk_options when the
            # system prompt exceeded the inline CLI budget. SDK has consumed
            # them by the time we reach here; remove silently on failure.
            for path in temp_files:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        elapsed = _time.time() - start_time
        token_str = f", tokens={total_tokens}" if total_tokens else ", tokens=n/a"
        _safe_stderr(
            f"  [Agent metrics — role={role}, model={model}, "
            f"msgs={assistant_msgs}, tools={tool_calls}{token_str}, "
            f"elapsed={elapsed:.0f}s]"
        )

        if stderr_lines:
            _safe_stderr(f"  [Agent stderr: {len(stderr_lines)} lines]")
            for line in stderr_lines[-10:]:  # last 10 lines
                _safe_stderr(f"    {line.rstrip()}")

        return result_text
