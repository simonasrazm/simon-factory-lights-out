"""CodexAdapter - runs agents via the Codex CLI non-interactive runner."""

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time as _time

from .base import RuntimeAdapter
from .errors import NonRetryableError, TransientError
from .._stderr import _safe_stderr


def _output_dir(cwd=None, env=None):
    """Return the SFLO state directory for Codex scratch output files."""
    if env:
        venv = env.get("VIRTUAL_ENV", "")
        if os.path.basename(venv) == ".venv":
            out_dir = os.path.dirname(venv)
            os.makedirs(out_dir, exist_ok=True)
            return out_dir
    root = cwd or os.getcwd()
    out_dir = os.path.join(root, ".sflo")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _sandbox_for_tools_mode(tools_mode):
    """Map SFLO's tool mode to Codex sandbox policy."""
    override = (os.environ.get("SFLO_CODEX_SANDBOX") or "").strip()
    if override in ("read-only", "workspace-write", "danger-full-access"):
        return override
    if tools_mode == "readonly":
        return "read-only"
    return "workspace-write"


def _reasoning_effort(effort):
    """Map SFLO effort values to Codex reasoning levels."""
    return {
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
    }.get(effort)


def _role_slug(role):
    """Return a safe filename segment for role-specific Codex output logs."""
    slug = re.sub(r"[^a-z0-9]+", "-", (role or "agent").lower()).strip("-")
    return slug or "agent"


class CodexAdapter(RuntimeAdapter):
    """Spawn agents via `codex exec` and return the final assistant message."""

    MODEL_ALIASES = {
        "auto": "gpt-5.5",
        "gpt": "gpt-5.5",
        "codex": "gpt-5.3-codex",
        "gpt-codex": "gpt-5.3-codex",
        "gpt-5-codex": "gpt-5.3-codex",
    }

    SPAWN_TIMEOUT_SECONDS = int(os.environ.get("SFLO_CODEX_TIMEOUT", "1800"))
    BIN = os.environ.get("SFLO_CODEX_BIN", "codex")

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
        **kwargs,
    ):
        # allowed_tools, thinking, mcp_servers, and allow_task are accepted for
        # RuntimeAdapter parity. Codex CLI owns tool exposure through sandbox,
        # approvals, MCP config, and AGENTS.md/project instructions.
        del allowed_tools, thinking, mcp_servers, allow_task, kwargs

        resolved = shutil.which(self.BIN) or self.BIN
        if shutil.which(self.BIN) is None and not os.path.isfile(self.BIN):
            raise NonRetryableError(
                f"Codex CLI '{self.BIN}' not found on PATH. "
                "Install Codex or set SFLO_CODEX_BIN."
            )

        combined = (
            "# Role spec (you MUST follow this)\n\n"
            f"{system_prompt}\n\n"
            "---\n\n"
            f"{user_prompt}"
        )

        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        if child_env.get("TERM", "dumb") == "dumb":
            child_env["TERM"] = "xterm-256color"

        fd, output_path = tempfile.mkstemp(
            prefix=f"codex-last-message-{_role_slug(role)}-",
            suffix=".md",
            dir=_output_dir(cwd, child_env),
        )
        os.close(fd)

        sandbox = _sandbox_for_tools_mode(tools_mode)
        resolved_model = (
            self.resolve_model(model) or os.environ.get("SFLO_CODEX_MODEL") or "gpt-5.5"
        )
        cmd = [
            resolved,
            "-a",
            "never",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            sandbox,
            "--output-last-message",
            output_path,
            "--model",
            resolved_model,
        ]
        reasoning = _reasoning_effort(effort)
        if reasoning:
            cmd += ["-c", f'model_reasoning_effort="{reasoning}"']
        if cwd:
            cmd += ["-C", cwd]
        cmd.append("-")

        start = _time.time()
        try:
            stdout_b, stderr_b, returncode = await asyncio.to_thread(
                self._run_with_pipes,
                cmd,
                combined.encode("utf-8"),
                cwd,
                child_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise TransientError(
                f"codex exec timed out after {self.SPAWN_TIMEOUT_SECONDS}s "
                f"(role={role}, model={model})."
            ) from exc
        except FileNotFoundError as exc:
            raise NonRetryableError(
                f"Failed to spawn Codex CLI: {exc}. "
                "Verify `codex` is installed and on PATH."
            ) from exc

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        elapsed = _time.time() - start

        if returncode != 0:
            self._raise_for_failure(returncode, stdout, stderr, elapsed)

        text = self._read_output_file(output_path) or stdout.strip()
        _safe_stderr(
            f"  [Codex agent - role={role}, "
            f"model={resolved_model}, elapsed={elapsed:.0f}s, "
            f"chars={len(text)}]"
        )
        return text

    def _run_with_pipes(self, cmd, input_bytes, cwd=None, env=None):
        proc = subprocess.run(
            cmd,
            input=input_bytes,
            capture_output=True,
            cwd=cwd,
            env=env,
            timeout=self.SPAWN_TIMEOUT_SECONDS,
        )
        return proc.stdout, proc.stderr, proc.returncode

    @staticmethod
    def _read_output_file(path):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    @staticmethod
    def _raise_for_failure(returncode, stdout, stderr, elapsed):
        low = (stderr + stdout).lower()
        tail = (stderr or stdout).strip().splitlines()[-20:]
        msg = (
            f"codex exec failed (exit {returncode}, elapsed {elapsed:.0f}s)\n"
            "output (last 20 lines):\n  " + "\n  ".join(tail)
        )
        if any(
            marker in low
            for marker in (
                "unauthor",
                "forbidden",
                "login",
                "not authenticated",
                "invalid model",
                "unknown model",
            )
        ):
            raise NonRetryableError(msg)
        if any(marker in low for marker in ("429", "rate", "503", "502", "timeout")):
            raise TransientError(msg)
        raise TransientError(msg)
