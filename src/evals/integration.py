"""sflo eval framework — runner-level integration helper.

call_adapter_with_evals() is the SINGLE eval call site for all adapters.
Every adapter.spawn_agent() call in runner.py goes through this helper,
which fires PRE_PROMPT evals (before the adapter call) and POST_RESPONSE
evals (after), with fail-safe exception handling per plugin.

Pattern credit: LangChain CallbackManager (callbacks around LLM call, not
inside LLM), Guardrails AI Guard.wrap() pattern, MS Semantic Kernel
Kernel.InvokeAsync orchestrator.

No security-specific logic lives here. Security/quality plugins are
provided by host projects via the eval registry.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .base import EvalAbortError, EvalAction, EvalContext, HookSite
from .registry import registered_evals_for_site


async def call_adapter_with_evals(
    adapter: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    role: str | None = None,
    metadata: dict | None = None,
    **adapter_kwargs: Any,
) -> Any:
    """Wrap any RuntimeAdapter.spawn_agent() call with the full eval lifecycle.

    Lifecycle:
      1. PRE_PROMPT evals — can MODIFY system_prompt/user_prompt or ABORT
      2. adapter.spawn_agent() — adapter is eval-unaware (clean separation)
      3. POST_RESPONSE evals — can MODIFY response or ABORT

    Fail-safe: any per-eval crash is logged to stderr; pipeline continues with
    the original payload. EvalAbortError is propagated to the caller unchanged
    (runner.py treats it as a gate failure).

    Args:
        adapter: Any RuntimeAdapter subclass (ClaudeCodeAdapter, OllamaAdapter, …)
        model: Model identifier forwarded to adapter.spawn_agent()
        system_prompt: Agent soul/system prompt (PRE_PROMPT evals may modify)
        user_prompt: User request / task description (PRE_PROMPT evals may modify)
        role: Agent role label (scout/pm/dev/qa/sflo/interrogator) for eval filtering
        metadata: Contextual data forwarded to EvalContext.metadata
                  (session_id, output_dir, gate_num, cwd, etc.)
        **adapter_kwargs: Forwarded verbatim to adapter.spawn_agent()
                         (cwd, allowed_tools, etc.)

    Returns:
        Response text from adapter.spawn_agent(), possibly modified by POST_RESPONSE evals.

    Raises:
        EvalAbortError: when any eval returns EvalAction.ABORT.
    """
    _metadata = dict(metadata) if metadata else {}

    # ------------------------------------------------------------------ #
    # Step 1: PRE_PROMPT evals
    # Can MODIFY system_prompt / user_prompt, or ABORT before adapter call.
    # ------------------------------------------------------------------ #
    pre_evals = registered_evals_for_site(HookSite.PRE_PROMPT, role=role)
    for eval_inst in pre_evals:
        try:
            ctx = EvalContext(
                role=role or "unknown",
                site=HookSite.PRE_PROMPT,
                payload={
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                metadata=_metadata,
                config=eval_inst.config,
            )
            result = await eval_inst.pre_prompt(ctx)
            if result.triggered:
                if result.action == EvalAction.MODIFY and result.payload:
                    system_prompt = result.payload.get("system_prompt", system_prompt)
                    user_prompt = result.payload.get("user_prompt", user_prompt)
                elif result.action == EvalAction.ABORT:
                    _reason = (
                        (result.incident or {}).get("reason", "abort")
                        if result.incident
                        else "abort"
                    )
                    raise EvalAbortError(eval_inst.name, _reason, result.incident)
                if result.incident:
                    _msg = (
                        f"[Eval] {eval_inst.name} "
                        f"severity={result.severity.value} "
                        f"{json.dumps(result.incident)}"
                    )
                    print(f"  {_msg}", file=sys.stderr)
        except EvalAbortError:
            raise  # propagate aborts to runner
        except Exception as exc:
            # Fail-safe: eval crash never blocks the pipeline
            print(
                f"  [Eval] {eval_inst.name} crashed "
                f"(pre_prompt — passing through original): {exc}",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------ #
    # Step 2: Adapter call — adapter is ZERO-AWARENESS of evals
    # ------------------------------------------------------------------ #
    response = await adapter.spawn_agent(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        role=role,
        **adapter_kwargs,
    )

    # ------------------------------------------------------------------ #
    # Step 3: POST_RESPONSE evals
    # Can MODIFY the response text, or ABORT after adapter call.
    # ------------------------------------------------------------------ #
    post_evals = registered_evals_for_site(HookSite.POST_RESPONSE, role=role)
    for eval_inst in post_evals:
        try:
            ctx = EvalContext(
                role=role or "unknown",
                site=HookSite.POST_RESPONSE,
                payload={
                    "response_text": response,
                    "user_prompt": user_prompt,
                    "model": model,
                },
                metadata=_metadata,
                config=eval_inst.config,
            )
            result = await eval_inst.post_response(ctx)
            if result.triggered:
                if result.action == EvalAction.MODIFY and result.payload:
                    response = result.payload.get("response_text", response)
                elif result.action == EvalAction.ABORT:
                    _reason = (
                        (result.incident or {}).get("reason", "abort")
                        if result.incident
                        else "abort"
                    )
                    raise EvalAbortError(eval_inst.name, _reason, result.incident)
                if result.incident:
                    _msg = (
                        f"[Eval] {eval_inst.name} "
                        f"severity={result.severity.value} "
                        f"{json.dumps(result.incident)}"
                    )
                    print(f"  {_msg}", file=sys.stderr)
        except EvalAbortError:
            raise  # propagate aborts to runner
        except Exception as exc:
            # Fail-safe: eval crash never blocks the pipeline
            print(
                f"  [Eval] {eval_inst.name} crashed "
                f"(post_response — passing through original): {exc}",
                file=sys.stderr,
            )

    return response
