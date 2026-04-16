"""SFLO runtime adapters package.

Re-exports all adapter classes and factory functions so callers can do:
    from src.adapters import RuntimeAdapter, ClaudeCodeAdapter, get_adapter
"""

from .base import RuntimeAdapter
from .claude_code import ClaudeCodeAdapter
from .openclaw import OpenClawAdapter
from .ollama import OllamaAdapter


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
    elif runtime == "ollama":
        return OllamaAdapter()
    else:
        raise RuntimeError(
            "No runtime detected. Run setup.sh to provision the environment, "
            "or install manually: pip install claude-agent-sdk"
        )


__all__ = [
    "RuntimeAdapter",
    "ClaudeCodeAdapter",
    "OpenClawAdapter",
    "OllamaAdapter",
    "detect_runtime",
    "get_adapter",
]
