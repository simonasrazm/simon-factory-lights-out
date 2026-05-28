"""sflo eval framework — runner-level integration helper.

call_adapter_with_evals() wraps every adapter.spawn_agent() call in runner.py,
firing PRE_PROMPT evals (before the adapter call) and POST_RESPONSE evals
(after), with fail-safe exception handling per plugin.

run_tool_call_evals() dispatches PRE_TOOL_CALL evals for a single tool
invocation; runtime adapters wire it to their pre-tool-call hook (the
claude-code adapter wires it to Claude Code's native PreToolUse hook).

Hook-site dispatch coverage: PRE_PROMPT, POST_RESPONSE and PRE_TOOL_CALL are
dispatched. POST_TOOL_CALL / ON_RESPONSE_CHUNK / PRE_ARTIFACT are defined in
the framework (base.py HookSite) but not yet dispatched — no eval targets them.

Pattern credit: LangChain CallbackManager (callbacks around LLM call, not
inside LLM), Guardrails AI Guard.wrap() pattern, MS Semantic Kernel
Kernel.InvokeAsync orchestrator.

No security-specific logic lives here. Security/quality plugins are
provided by host projects through pipeline.yaml `evals:`.
"""

from __future__ import annotations

import json
from typing import Any

from .base import EvalAbortError, EvalAction, EvalContext, HookSite
from .registry import registered_evals_for_site
from .._stderr import _safe_stderr


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
        role: Agent role label (scout/pm/dev/qa/sflo) for eval filtering
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
                    _safe_stderr(f"  {_msg}")
        except EvalAbortError:
            raise  # propagate aborts to runner
        except Exception as exc:
            # Fail-safe: eval crash never blocks the pipeline
            _safe_stderr(
                f"  [Eval] {eval_inst.name} crashed "
                f"(pre_prompt — passing through original): {exc}"
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
                    _safe_stderr(f"  {_msg}")
        except EvalAbortError:
            raise  # propagate aborts to runner
        except Exception as exc:
            # Fail-safe: eval crash never blocks the pipeline
            _safe_stderr(
                f"  [Eval] {eval_inst.name} crashed "
                f"(post_response — passing through original): {exc}"
            )

    return response


async def run_tool_call_evals(
    tool_name: str,
    tool_args: dict,
    *,
    role: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Dispatch PRE_TOOL_CALL evals for a single tool invocation.

    Runtime-agnostic: a runtime adapter calls this from whatever pre-tool-call
    hook its runtime exposes. Mirrors the PRE_PROMPT / POST_RESPONSE dispatch
    inside call_adapter_with_evals — same fail-safe, same incident logging.

    A tool call is allowed or denied, not rewritten: only EvalAction.ABORT is
    acted on (raised as EvalAbortError for the adapter to translate into a tool
    denial); non-abort triggers are logged as incidents. MODIFY of tool args is
    intentionally unsupported — no eval needs it; add it here if one ever does.

    Raises:
        EvalAbortError: when a PRE_TOOL_CALL eval returns EvalAction.ABORT.
    """
    _metadata = dict(metadata) if metadata else {}
    for eval_inst in registered_evals_for_site(HookSite.PRE_TOOL_CALL, role=role):
        try:
            ctx = EvalContext(
                role=role or "unknown",
                site=HookSite.PRE_TOOL_CALL,
                payload={"tool_name": tool_name, "tool_args": tool_args},
                metadata=_metadata,
                config=eval_inst.config,
            )
            result = await eval_inst.pre_tool_call(ctx)
            if result.triggered:
                if result.action == EvalAction.ABORT:
                    _reason = (
                        (result.incident or {}).get("reason", "abort")
                        if result.incident
                        else "abort"
                    )
                    raise EvalAbortError(eval_inst.name, _reason, result.incident)
                if result.incident:
                    _safe_stderr(
                        f"  [Eval] {eval_inst.name} "
                        f"severity={result.severity.value} "
                        f"{json.dumps(result.incident)}"
                    )
        except EvalAbortError:
            raise  # propagate aborts to the adapter
        except Exception as exc:
            # Fail-safe: an eval crash never blocks the tool call
            _safe_stderr(
                f"  [Eval] {eval_inst.name} crashed "
                f"(pre_tool_call — passing through): {exc}"
            )
