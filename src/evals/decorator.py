"""sflo eval decorator — functional sugar for wrapping a single async function as a SfloEval.

Usage:
    from src.evals import eval, HookSite, EvalContext, EvalResult

    @eval(name="my_check", sites=[HookSite.POST_RESPONSE])
    async def my_check(ctx: EvalContext) -> EvalResult:
        ...

The decorator returns a SfloEval subclass that can be referenced from
bindings.yaml just like a hand-written class.
"""

from __future__ import annotations

import inspect
from typing import Callable, List

from .base import (
    EvalCategory,
    EvalContext,
    EvalResult,
    EvalSeverity,
    HookSite,
    SfloEval,
)


def eval(
    *,
    name: str,
    sites: List[HookSite],
    category: EvalCategory = EvalCategory.CUSTOM,
    priority: int = 100,
) -> Callable:
    """Decorator that wraps a single async function into a SfloEval subclass.

    Parameters
    ----------
    name:
        Unique identifier for this eval (appears in logs + incident records).
    sites:
        List of HookSite values where the wrapped function fires.
    category:
        EvalCategory for classification (default: CUSTOM).
    priority:
        Execution order — lower = runs first (default: 100).

    Returns
    -------
    A class decorator.  The decorated function is called when the adapter
    fires any site listed in `sites`; all other sites return PASS.
    """

    def decorator(fn: Callable) -> type:
        # Build a default (no-op) method
        async def _default_impl(self: SfloEval, ctx: EvalContext) -> EvalResult:
            return EvalResult(
                triggered=False,
                severity=EvalSeverity.INFO,
                category=category,
            )

        # Build the impl method that delegates to the wrapped function
        async def _fn_impl(self: SfloEval, ctx: EvalContext) -> EvalResult:
            if inspect.iscoroutinefunction(fn):
                return await fn(ctx)
            return fn(ctx)

        # Map site → method name
        _SITE_MAP = {
            HookSite.PRE_PROMPT: "pre_prompt",
            HookSite.POST_RESPONSE: "post_response",
            HookSite.ON_RESPONSE_CHUNK: "on_response_chunk",
            HookSite.PRE_TOOL_CALL: "pre_tool_call",
            HookSite.POST_TOOL_CALL: "post_tool_call",
            HookSite.PRE_ARTIFACT: "pre_artifact",
        }

        methods: dict = {
            "name": name,
            "sites": list(sites),
            "category": category,
            "priority": priority,
        }

        for site in HookSite:
            method_name = _SITE_MAP[site]
            methods[method_name] = _fn_impl if site in sites else _default_impl

        cls = type(name, (SfloEval,), methods)
        return cls

    return decorator
