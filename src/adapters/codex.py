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


def _is_windows():
    """Return whether executable resolution uses Windows semantics."""
    return os.name == "nt"


def _windows_which(command, env):
    """Resolve *command* using only the supplied Windows PATH and PATHEXT.

    ``shutil.which(..., path=...)`` accepts an explicit PATH but reads PATHEXT
    from the parent Python process. SFLO passes a per-factory environment, so
    both values must come from the same effective child environment. We also
    intentionally do not prepend the current directory.
    """
    has_dir = any(sep in command for sep in ("/", "\\"))
    directories = [""] if has_dir else env.get("PATH", "").split(";")
    _, extension = os.path.splitext(command)
    if extension:
        suffixes = [""]
    else:
        suffixes = [
            item
            for item in env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
            if item
        ]

    for directory in directories:
        base = command if has_dir else os.path.join(directory, command)
        for suffix in suffixes:
            candidate = base + suffix
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
    return None


def _powershell_prefix(script, env):
    """Return a shell-safe PowerShell argv prefix for a .ps1 script."""
    for command in ("pwsh", "powershell"):
        resolved = _windows_which(command, env)
        if resolved:
            return [
                resolved,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script,
            ]
    raise NonRetryableError(
        "Codex CLI resolved to a PowerShell script, but neither pwsh.exe nor "
        "powershell.exe was found on PATH. Set SFLO_CODEX_BIN to a native codex.exe."
    )


def resolve_codex_argv(env):
    """Resolve the Codex CLI to a shell-free argv prefix.

    ``SFLO_CODEX_BIN`` is a single executable name or path, not a shell
    command. Resolution happens for every spawn against the merged child
    environment so setup changes made after module import take effect.
    """
    configured = (env.get("SFLO_CODEX_BIN") or "codex").strip()
    if not configured or "\0" in configured or "\n" in configured or "\r" in configured:
        raise NonRetryableError(
            "SFLO_CODEX_BIN must contain one Codex executable name or path."
        )
    if configured[0] in ("'", '"') or configured[-1] in ("'", '"'):
        raise NonRetryableError(
            "SFLO_CODEX_BIN must not include shell quotes; set it to the raw path."
        )

    configured = os.path.expanduser(configured)
    if _is_windows():
        resolved = _windows_which(configured, env)
    else:
        resolved = shutil.which(configured, path=env.get("PATH"))
        if resolved is None and os.path.isfile(configured):
            if not os.access(configured, os.X_OK):
                raise NonRetryableError(
                    f"Codex CLI '{configured}' exists but is not executable."
                )
            resolved = os.path.abspath(configured)

    if not resolved:
        raise NonRetryableError(
            f"Codex CLI '{configured}' not found on PATH. "
            "Install Codex or set SFLO_CODEX_BIN to its executable path."
        )

    if not _is_windows():
        if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
            raise NonRetryableError(
                f"Codex CLI '{resolved}' exists but is not executable."
            )
        return [os.path.abspath(resolved)]

    extension = os.path.splitext(resolved)[1].lower()
    if extension in (".exe", ".com"):
        return [resolved]
    if extension == ".ps1":
        return _powershell_prefix(resolved, env)
    if extension in (".cmd", ".bat"):
        stem = os.path.splitext(resolved)[0]
        native = stem + ".exe"
        if os.path.isfile(native):
            return [os.path.abspath(native)]
        script = stem + ".ps1"
        if os.path.isfile(script):
            return _powershell_prefix(os.path.abspath(script), env)
        raise NonRetryableError(
            f"Codex CLI batch shim '{resolved}' has no safe native or PowerShell "
            "launcher. Set SFLO_CODEX_BIN to a native codex.exe; SFLO will not "
            "route project paths through cmd.exe."
        )
    raise NonRetryableError(
        f"Unsupported Windows Codex launcher '{resolved}'. Set SFLO_CODEX_BIN "
        "to codex.exe, codex.com, codex.ps1, codex.cmd, or codex.bat."
    )


def _safe_unlink(path):
    """Best-effort removal for a private Codex scratch file."""
    try:
        os.unlink(path)
    except OSError:
        pass


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


def _disabled_features():
    """Return Codex features disabled for SFLO child agents."""
    raw = os.environ.get("SFLO_CODEX_DISABLE_FEATURES")
    if raw is None:
        raw = "plugins"
    return [
        item.strip()
        for item in raw.split(",")
        if item.strip() and item.strip().lower() not in ("0", "false", "none", "off")
    ]


class CodexAdapter(RuntimeAdapter):
    """Spawn agents via `codex exec` and return the final assistant message."""

    MODEL_ALIASES = {
        "auto": "gpt-5.6-sol",
        "gpt": "gpt-5.6-sol",
        "codex": "gpt-5.6-sol",
        "gpt-codex": "gpt-5.6-sol",
        "gpt-5-codex": "gpt-5.6-sol",
    }

    SPAWN_TIMEOUT_SECONDS = int(os.environ.get("SFLO_CODEX_TIMEOUT", "1800"))
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

        combined = (
            "# Role spec (you MUST follow this)\n\n"
            f"{system_prompt}\n\n"
            "---\n\n"
            f"{user_prompt}"
        )

        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        codex_home = child_env.get("SFLO_CODEX_HOME")
        if codex_home:
            child_env["CODEX_HOME"] = os.path.expanduser(codex_home)
        if child_env.get("TERM", "dumb") == "dumb":
            child_env["TERM"] = "xterm-256color"

        command_prefix = resolve_codex_argv(child_env)
        output_path = None
        fd = None
        try:
            fd, output_path = tempfile.mkstemp(
                prefix=f"codex-last-message-{_role_slug(role)}-",
                suffix=".md",
            )
            os.close(fd)
            fd = None
            sandbox = _sandbox_for_tools_mode(tools_mode)
            resolved_model = (
                self.resolve_model(model)
                or os.environ.get("SFLO_CODEX_MODEL")
                or "gpt-5.6-sol"
            )
            cmd = [
                *command_prefix,
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
            for feature in _disabled_features():
                cmd += ["--disable", feature]
            reasoning = _reasoning_effort(effort)
            if reasoning:
                cmd += ["-c", f'model_reasoning_effort="{reasoning}"']
            if cwd:
                cmd += ["-C", cwd]
            cmd.append("-")

            start = _time.time()
            try:
                stdout_b, stderr_b, returncode = await self._run_with_pipes(
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
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if output_path is not None:
                _safe_unlink(output_path)

    async def _run_with_pipes(self, cmd, input_bytes, cwd=None, env=None):
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input_bytes),
                timeout=self.SPAWN_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise subprocess.TimeoutExpired(cmd, self.SPAWN_TIMEOUT_SECONDS) from exc
        except asyncio.CancelledError:
            proc.kill()
            try:
                await asyncio.shield(proc.communicate())
            except (ProcessLookupError, asyncio.CancelledError):
                pass
            raise
        return stdout, stderr, proc.returncode

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
