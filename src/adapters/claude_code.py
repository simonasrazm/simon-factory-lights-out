"""ClaudeCodeAdapter — runs agents via Claude Agent SDK inside Claude Code."""

import asyncio
import sys
import time as _time

from .base import RuntimeAdapter


class ClaudeCodeAdapter(RuntimeAdapter):
    """Uses Claude Agent SDK — runs inside Claude Code, no API key needed."""

    # Default: None = use all tools available in the session (MCP, browser, etc.)
    # Per-role overrides (e.g. scout read-only) are passed via allowed_tools kwarg.
    ALLOWED_TOOLS = None

    async def spawn_agent(self, model, system_prompt, user_prompt, role=None,
                          allowed_tools=None):
        return await self._run_agent(
            model, system_prompt, user_prompt,
            allowed_tools=allowed_tools, role=role,
        )

    # Max seconds to wait for MCP servers to connect.
    MCP_READY_TIMEOUT = 30

    async def _run_agent(self, model, system_prompt, user_prompt,
                         allowed_tools=None, role=None):
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

        # Build options with MCP and extra args if configured.
        # MCP servers are only forwarded to roles that need them.
        # Scout is restricted to Read/Glob — no MCP, no browser tools.
        opts = dict(
            system_prompt=system_prompt,
            model=model,
            allowed_tools=allowed_tools if allowed_tools is not None else self.ALLOWED_TOOLS,
            permission_mode="bypassPermissions",
            stderr=capture_stderr,
        )
        needs_mcp = role != "scout"
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
                    (opts.get("system_prompt") or "") +
                    "\n\n" + " ".join(prompts)
                )
        if self._extra_cli_args and needs_mcp:
            opts["extra_args"] = self._extra_cli_args

        result_text = ""
        assistant_msgs = 0
        tool_calls = 0
        start_time = _time.time()
        try:
            async with ClaudeSDKClient(ClaudeAgentOptions(**opts)) as client:
                # Wait for MCP servers if configured and role needs them
                if self._mcp_servers and needs_mcp:
                    deadline = _time.time() + self.MCP_READY_TIMEOUT
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
                            s["name"] for s in servers
                            if s.get("status") != "connected"
                        ]
                        print(
                            f"  [MCP timeout {self.MCP_READY_TIMEOUT}s — "
                            f"pending: {', '.join(pending)}]",
                            file=sys.stderr,
                        )

                await client.query(user_prompt)
                async for message in client.receive_response():
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
