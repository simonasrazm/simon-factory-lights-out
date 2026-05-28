"""SFLO runtime adapters package.

Re-exports all adapter classes and factory functions so callers can do:
    from src.adapters import RuntimeAdapter, ClaudeCodeAdapter, get_adapter
"""

from .base import RuntimeAdapter
from .claude_code import ClaudeCodeAdapter
from .openclaw import OpenClawAdapter
from .ollama import OllamaAdapter
from .cursor import CursorAdapter
from .codex import CodexAdapter


def get_adapter(runtime):
    """Get the appropriate runtime adapter."""
    if runtime == "openclaw":
        return OpenClawAdapter()
    elif runtime == "claude-code":
        return ClaudeCodeAdapter()
    elif runtime == "cursor":
        return CursorAdapter()
    elif runtime == "codex":
        return CodexAdapter()
    elif runtime == "ollama":
        return OllamaAdapter()
    else:
        raise RuntimeError(
            "Runtime is required. Supported runtimes: claude-code, codex, cursor, openclaw, ollama. "
            "Pass --runtime or call get_adapter(runtime). "
            "Run setup.sh to provision the environment, "
            "or install manually: pip install claude-agent-sdk (claude-code), "
            "codex CLI (codex), cursor-agent CLI (cursor), openclaw CLI (openclaw), "
            "or pip install ollama + ollama serve (ollama)."
        )


__all__ = [
    "RuntimeAdapter",
    "ClaudeCodeAdapter",
    "OpenClawAdapter",
    "OllamaAdapter",
    "CursorAdapter",
    "CodexAdapter",
    "get_adapter",
]
