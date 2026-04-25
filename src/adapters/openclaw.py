"""OpenClawAdapter — runs agents via openclaw CLI."""

import json
import subprocess as sp
from uuid import uuid4

from .base import RuntimeAdapter


class OpenClawAdapter(RuntimeAdapter):
    """Uses `openclaw agent` CLI — full tool access, real agent sessions."""

    async def spawn_agent(
        self, model, system_prompt, user_prompt, cwd=None, role=None, allowed_tools=None
    ):
        # role/allowed_tools/cwd accepted for API compatibility
        # with ClaudeCodeAdapter; openclaw CLI doesn't currently honor them.
        # If cwd support is added to openclaw CLI in future, pass it via --working-dir or similar.

        message = f"{system_prompt}\n\n---\n\n{user_prompt}"

        cmd = [
            "openclaw",
            "agent",
            "--message",
            message,
            "--session-id",
            f"sflo-{uuid4().hex}",
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
            )
        except FileNotFoundError:
            raise RuntimeError(
                "openclaw CLI not found. "
                "Install OpenClaw or run inside an OpenClaw workspace."
            )

        if result.returncode != 0:
            raise RuntimeError(
                f"openclaw agent failed (exit {result.returncode}): {result.stderr}"
            )

        # Parse output — always return string
        try:
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                return str(data.get("content", data.get("result", result.stdout)))
            return str(data)
        except json.JSONDecodeError:
            return result.stdout
