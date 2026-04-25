"""sflo eval framework — typed plugin extension point for evals, guardrails, and filters.

Public API (everything here is stable; internals in base.py / registry.py may change):

    from src.evals import (
        SfloEval,
        HookSite,
        EvalContext,
        EvalResult,
        EvalAction,
        EvalSeverity,
        EvalCategory,
        EvalAbortError,
        load_evals_from_bindings,
        registered_evals_for_site,
        call_adapter_with_evals,
    )

Design borrowings:
  LangChain Callbacks: typed ABC with multi-site lifecycle methods
  Guardrails AI: bindings.yaml `evals:` section, severity + category taxonomy
  Anthropic Claude Code Hooks: `match:` selector for fire-only-when-relevant
"""

from .base import (
    EvalAbortError,
    EvalAction,
    EvalCategory,
    EvalContext,
    EvalResult,
    EvalSeverity,
    HookSite,
    SfloEval,
)
from .decorator import eval
from .registry import (
    clear_registry,
    load_evals_from_bindings,
    matches_filter,
    registered_evals_for_site,
)
from .integration import call_adapter_with_evals

__all__ = [
    # Core types
    "SfloEval",
    "HookSite",
    "EvalContext",
    "EvalResult",
    "EvalAction",
    "EvalSeverity",
    "EvalCategory",
    "EvalAbortError",
    # Registry API
    "load_evals_from_bindings",
    "registered_evals_for_site",
    "matches_filter",
    "clear_registry",
    # Decorator sugar
    "eval",
    # Runner-level integration helper
    "call_adapter_with_evals",
]
