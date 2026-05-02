"""SFLO runtime adapters package.

Re-exports all adapter classes and factory functions so callers can do:
    from src.adapters import RuntimeAdapter, ClaudeCodeAdapter, get_adapter
"""

import logging
import os
import socket
import subprocess

from .base import RuntimeAdapter
from .claude_code import ClaudeCodeAdapter
from .openclaw import OpenClawAdapter
from .ollama import OllamaAdapter
from .cursor import CursorAdapter

_log = logging.getLogger("sflo.detect")


# -- per-runtime liveness prechecks ------------------------------------------
# Every precheck returns True only if the runtime is *actually* usable, not
# merely installed. Precheck failures fall through to the next option instead
# of crashing the pipeline later.


def _claude_code_usable():
    """Claude SDK importable AND auth credential present."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    # SDK can refresh OAuth tokens via the parent process (subscription auth).
    # This is the normal path when spawned from Claude Desktop's Bash tool.
    if os.environ.get("CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH") == "1":
        return True
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    return api_key.startswith("sk-ant-")


def _cursor_logged_in(timeout=3.0):
    """cursor-agent binary on PATH AND reports a logged-in session.

    `cursor-agent status` exits 0 regardless of auth state, so we grep stdout/
    stderr for the "Not logged in" marker. String-fragile by necessity — Cursor
    has no machine-readable status command as of 2026-01.
    """
    import shutil

    if not shutil.which("cursor-agent"):
        return False
    try:
        r = subprocess.run(
            ["cursor-agent", "status"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = (r.stdout or "") + (r.stderr or "")
        return "Not logged in" not in combined and "login" not in combined.lower()
    except Exception:
        return False


def _openclaw_alive(timeout=0.2):
    """openclaw binary on PATH AND local server socket accepts a connection.

    Port TBD — openclaw has no documented port constant; probe common ports
    and fall through if none respond. Users who know their port can set
    SFLO_PREFER_RUNTIME=openclaw to bypass the probe.
    """
    import shutil

    if not shutil.which("openclaw"):
        return False
    for port in (7777, 8080):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except Exception:
            continue
    return False


def _ollama_alive():
    """ollama package importable AND server answers list()."""
    try:
        import ollama  # noqa: F401

        ollama.list()
        return True
    except Exception:
        return False


# -- caller-identity signals -------------------------------------------------
# Strongest signal: the process that spawned the SFLO runner explicitly
# identified itself via environment variables. When present, prefer that
# runtime even if other tools exist on PATH — the caller's intent trumps
# filesystem presence of alternatives.


def _spawned_by_claude_code():
    """True if Claude Code spawned this Python process.

    Claude Code sets CLAUDECODE=1 and CLAUDE_CODE_ENTRYPOINT in every spawned
    subprocess. Either marker is sufficient.
    """
    return os.environ.get("CLAUDECODE") == "1" or bool(
        os.environ.get("CLAUDE_CODE_ENTRYPOINT")
    )


def _spawned_by_cursor():
    """True if Cursor's integrated agent/terminal spawned this process."""
    return bool(
        os.environ.get("CURSOR_TRACE_ID") or os.environ.get("CURSOR_SESSION_ID")
    )


# -- main entry point --------------------------------------------------------


def detect_runtime():
    """Auto-detect which runtime we're in.

    Decision order (first match wins):

      1. SFLO_PREFER_RUNTIME env var — explicit user pin.
      2. Caller-identity env markers (CLAUDECODE, CURSOR_TRACE_ID, ...).
         If the caller told us who it is AND its runtime is usable, trust it.
      3. PATH + liveness precheck, in priority order:
           a. openclaw (server alive)
           b. claude-code (SDK importable + auth present)
           c. cursor (binary on PATH + logged in)
           d. ollama (server answers)

    Never crashes when one option is misconfigured; falls through to next.
    Logs the chosen runtime and the reason at INFO level.
    """
    # (1) explicit override
    preferred = (os.environ.get("SFLO_PREFER_RUNTIME") or "").strip().lower()
    if preferred in ("openclaw", "cursor", "claude-code", "ollama"):
        _log.info("runtime=%s reason=SFLO_PREFER_RUNTIME", preferred)
        return preferred

    # (2) caller-identity signals — strongest
    if _spawned_by_claude_code() and _claude_code_usable():
        _log.info("runtime=claude-code reason=spawned-by-claude-code")
        return "claude-code"
    if _spawned_by_cursor() and _cursor_logged_in():
        _log.info("runtime=cursor reason=spawned-by-cursor")
        return "cursor"

    # (3) PATH + precheck fallback. claude-code is preferred over cursor
    # because most users run SFLO via Claude Code; cursor-agent on PATH is
    # often installed-but-unused baggage.
    if _openclaw_alive():
        _log.info("runtime=openclaw reason=path+alive")
        return "openclaw"
    if _claude_code_usable():
        _log.info("runtime=claude-code reason=sdk+auth")
        return "claude-code"
    if _cursor_logged_in():
        _log.info("runtime=cursor reason=path+auth")
        return "cursor"
    if _ollama_alive():
        _log.info("runtime=ollama reason=server-up")
        return "ollama"

    _log.warning("no runtime detected")
    return None


def get_adapter(runtime=None):
    """Get the appropriate runtime adapter."""
    if runtime is None:
        runtime = detect_runtime()

    if runtime == "openclaw":
        return OpenClawAdapter()
    elif runtime == "claude-code":
        return ClaudeCodeAdapter()
    elif runtime == "cursor":
        return CursorAdapter()
    elif runtime == "ollama":
        return OllamaAdapter()
    else:
        raise RuntimeError(
            "No runtime detected. Supported runtimes: claude-code, cursor, openclaw, ollama. "
            "Run setup.sh to provision the environment, "
            "or install manually: pip install claude-agent-sdk (claude-code), "
            "cursor-agent CLI (cursor), openclaw CLI (openclaw), "
            "or pip install ollama + ollama serve (ollama)."
        )


__all__ = [
    "RuntimeAdapter",
    "ClaudeCodeAdapter",
    "OpenClawAdapter",
    "OllamaAdapter",
    "CursorAdapter",
    "detect_runtime",
    "get_adapter",
]
