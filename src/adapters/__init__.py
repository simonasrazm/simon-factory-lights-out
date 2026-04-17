"""SFLO runtime adapters package.

Re-exports all adapter classes and factory functions so callers can do:
    from src.adapters import RuntimeAdapter, ClaudeCodeAdapter, get_adapter
"""

import os

from .base import RuntimeAdapter
from .claude_code import ClaudeCodeAdapter
from .openclaw import OpenClawAdapter
from .ollama import OllamaAdapter
from .cursor import CursorAdapter


def detect_runtime():
    """Auto-detect which runtime we're in.

    Detection order is intentional:
      1. openclaw    — workspace-scoped CLI, only present if installed
      2. cursor      — `cursor-agent` on PATH; may co-exist with claude-code
      3. claude-code — Claude Agent SDK importable
      4. ollama      — local model server reachable

    `cursor` is checked before `claude-code` so users running SFLO from
    inside a Cursor IDE session pick the native adapter, not the Claude SDK
    fallback. Override with `--runtime` if needed.
    """
    import shutil
    # SFLO_PREFER_RUNTIME wins over auto-detect so users on multi-runtime
    # boxes (e.g. claude SDK + cursor-agent both installed) can pin a
    # specific runtime without uninstalling anything.
    preferred = (os.environ.get("SFLO_PREFER_RUNTIME") or "").strip().lower()
    if preferred in ("openclaw", "cursor", "claude-code", "ollama"):
        return preferred
    if shutil.which("openclaw"):
        return "openclaw"
    if shutil.which("cursor-agent"):
        return "cursor"
    try:
        from claude_agent_sdk import query  # noqa: F401
        return "claude-code"
    except ImportError:
        pass
    # Check Ollama: package importable AND server responding
    try:
        import ollama  # noqa: F401
        ollama.list()  # raises ConnectionError if server down
        return "ollama"
    except ImportError:
        pass
    except Exception:
        pass  # Server down or other error — fall through
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
            "No runtime detected. Run setup.sh to provision the environment, "
            "or install manually: pip install claude-agent-sdk, "
            "or install Cursor CLI from https://cursor.com/cli."
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
