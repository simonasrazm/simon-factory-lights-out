"""Unit tests for the sflo eval framework.

Run with: pytest sflo/src/evals/test_evals.py -v

Tests:
  TC1  - SfloEval default methods all return triggered=False, action=PASS
  TC2  - Registry: empty/no evals section → empty list
  TC3  - Registry: 1 plugin entry → loaded + registered
  TC4  - Registry: bad import module → warning logged, no crash
  TC5  - Registry: bad class name → warning logged, no crash
  TC6  - Registry: priority sort works correctly
  TC7  - Registry: match.roles filter excludes/includes correctly
  TC8  - Registry: match.gates filter excludes/includes correctly
  TC9  - Decorator: wraps a function into SfloEval subclass with correct sites
  TC10 - EvalAction.MODIFY: result.payload replaces original
  TC11 - EvalAction.ABORT: EvalAbortError raised and propagated
  TC12 - Fail-safe: eval that raises is caught, pipeline continues
  TC13 - Registry: disabled entry is not loaded
  TC14 - Registry: non-SfloEval class is rejected with warning
  TC15 - matches_filter: no match block → always True
  TC16 - call_adapter_with_evals: no evals → passes through adapter response
  TC17 - call_adapter_with_evals: POST_RESPONSE MODIFY replaces response
  TC18 - call_adapter_with_evals: PRE_PROMPT MODIFY changes system_prompt
  TC19 - call_adapter_with_evals: ABORT propagates as EvalAbortError
  TC20 - call_adapter_with_evals: eval crash is caught, pipeline continues
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Ensure src/ is importable when running from any cwd (test lives at src/evals/test_evals.py)
_SFLO_DIR = Path(__file__).parent.parent.parent  # sflo/ root
if str(_SFLO_DIR) not in sys.path:
    sys.path.insert(0, str(_SFLO_DIR))

from src.evals import (  # noqa: E402
    EvalAbortError,
    EvalAction,
    EvalCategory,
    EvalContext,
    EvalResult,
    EvalSeverity,
    HookSite,
    SfloEval,
    clear_registry,
    eval as sflo_eval,
    load_evals_from_bindings,
    matches_filter,
    registered_evals_for_site,
)
from src.evals.registry import _LOADED_EVALS  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def _make_ctx(**kwargs) -> EvalContext:
    defaults = dict(
        role="dev",
        site=HookSite.POST_RESPONSE,
        payload={"response_text": "hello world"},
        metadata={"cwd": "."},
        config={},
    )
    defaults.update(kwargs)
    return EvalContext(**defaults)


# ---------------------------------------------------------------------------
# TC1 — Default methods return triggered=False, action=PASS
# ---------------------------------------------------------------------------


class _NoopEval(SfloEval):
    name = "noop"
    sites = [HookSite.POST_RESPONSE]


def test_tc1_default_methods_pass():
    inst = _NoopEval()
    ctx = _make_ctx()
    for method_name in (
        "pre_prompt",
        "post_response",
        "on_response_chunk",
        "pre_tool_call",
        "post_tool_call",
        "pre_artifact",
    ):
        result = run(getattr(inst, method_name)(ctx))
        assert isinstance(result, EvalResult)
        assert result.triggered is False
        assert result.action == EvalAction.PASS


# ---------------------------------------------------------------------------
# TC2 — Registry: empty / no evals section
# ---------------------------------------------------------------------------


def test_tc2_empty_evals_section(tmp_path):
    clear_registry()

    # No evals: key at all
    bindings = tmp_path / "bindings.yaml"
    bindings.write_text("roles:\n  dev:\n    model: sonnet\n")
    result = load_evals_from_bindings(bindings)
    assert result == []
    assert len(_LOADED_EVALS) == 0

    # Explicit empty list
    clear_registry()
    bindings.write_text("evals: []\n")
    result = load_evals_from_bindings(bindings)
    assert result == []


# ---------------------------------------------------------------------------
# TC3 — Registry: 1 plugin entry → loaded + registered
# ---------------------------------------------------------------------------


def test_tc3_one_plugin_loaded(tmp_path):
    clear_registry()

    # Use a unique module name per test to avoid importlib caching conflicts
    mod_name = f"tc3_myplugins_{tmp_path.name}"
    mod_dir = tmp_path / mod_name
    mod_dir.mkdir()
    (mod_dir / "__init__.py").write_text("")
    (mod_dir / "myplugin.py").write_text(
        textwrap.dedent(
            """
            from src.evals import SfloEval, HookSite, EvalCategory

            class MyEval(SfloEval):
                name = "my_eval"
                sites = [HookSite.POST_RESPONSE]
                category = EvalCategory.QUALITY
                priority = 10
            """
        )
    )

    # Clear any previous cached imports
    for key in list(sys.modules.keys()):
        if mod_name in key:
            del sys.modules[key]

    sys.path.insert(0, str(tmp_path))
    try:
        bindings = tmp_path / "bindings.yaml"
        bindings.write_text(
            f"evals:\n"
            f"  - name: my_eval\n"
            f"    module: {mod_name}.myplugin\n"
            f"    class: MyEval\n"
            f"    enabled: true\n"
            f"    priority: 10\n"
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = load_evals_from_bindings(bindings)
        if w:
            print(f"TC3 warnings: {[str(x.message) for x in w]}")
        assert len(result) == 1, (
            f"Expected 1 plugin, got {len(result)}; warnings: {[str(x.message) for x in w]}"
        )
        assert result[0].name == "my_eval"  # type: ignore[attr-defined]
        assert len(_LOADED_EVALS) == 1
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for key in list(sys.modules.keys()):
            if mod_name in key:
                del sys.modules[key]
        clear_registry()


# ---------------------------------------------------------------------------
# TC4 — Registry: bad import module → warning logged, no crash
# ---------------------------------------------------------------------------


def test_tc4_bad_module(tmp_path):
    clear_registry()
    bindings = tmp_path / "bindings.yaml"
    bindings.write_text(
        "evals:\n"
        "  - name: nonexistent\n"
        "    module: totally.nonexistent.module.xyz\n"
        "    class: SomeClass\n"
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = load_evals_from_bindings(bindings)
    assert result == []
    assert any(
        "cannot import" in str(warning.message).lower()
        or "nonexistent" in str(warning.message)
        for warning in w
    )
    clear_registry()


# ---------------------------------------------------------------------------
# TC5 — Registry: bad class name → warning logged, no crash
# ---------------------------------------------------------------------------


def test_tc5_bad_class_name(tmp_path):
    clear_registry()

    mod_name = f"tc5_goodmod_{tmp_path.name}"
    mod_dir = tmp_path / mod_name
    mod_dir.mkdir()
    (mod_dir / "__init__.py").write_text("")
    (mod_dir / "mymod.py").write_text("class RealClass: pass\n")

    for key in list(sys.modules.keys()):
        if mod_name in key:
            del sys.modules[key]

    sys.path.insert(0, str(tmp_path))
    try:
        bindings = tmp_path / "bindings.yaml"
        bindings.write_text(
            f"evals:\n"
            f"  - name: bad\n"
            f"    module: {mod_name}.mymod\n"
            f"    class: NonExistentClass\n"
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = load_evals_from_bindings(bindings)
        assert result == []
        assert any(
            "not found" in str(warning.message).lower()
            or "NonExistentClass" in str(warning.message)
            for warning in w
        )
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for key in list(sys.modules.keys()):
            if mod_name in key:
                del sys.modules[key]
        clear_registry()


# ---------------------------------------------------------------------------
# TC6 — Registry: priority sort
# ---------------------------------------------------------------------------


def test_tc6_priority_sort(tmp_path):
    clear_registry()

    mod_name = f"tc6_primod_{tmp_path.name}"
    mod_dir = tmp_path / mod_name
    mod_dir.mkdir()
    (mod_dir / "__init__.py").write_text("")
    (mod_dir / "evals.py").write_text(
        textwrap.dedent(
            """
            from src.evals import SfloEval, HookSite, EvalCategory

            class HighPriEval(SfloEval):
                name = "high_pri"
                sites = [HookSite.POST_RESPONSE]
                priority = 10

            class LowPriEval(SfloEval):
                name = "low_pri"
                sites = [HookSite.POST_RESPONSE]
                priority = 200

            class MidPriEval(SfloEval):
                name = "mid_pri"
                sites = [HookSite.POST_RESPONSE]
                priority = 50
            """
        )
    )

    for key in list(sys.modules.keys()):
        if mod_name in key:
            del sys.modules[key]

    sys.path.insert(0, str(tmp_path))
    try:
        bindings = tmp_path / "bindings.yaml"
        # Register in REVERSE priority order to verify sort
        bindings.write_text(
            f"evals:\n"
            f"  - name: low_pri\n"
            f"    module: {mod_name}.evals\n"
            f"    class: LowPriEval\n"
            f"    priority: 200\n"
            f"  - name: high_pri\n"
            f"    module: {mod_name}.evals\n"
            f"    class: HighPriEval\n"
            f"    priority: 10\n"
            f"  - name: mid_pri\n"
            f"    module: {mod_name}.evals\n"
            f"    class: MidPriEval\n"
            f"    priority: 50\n"
        )
        result = load_evals_from_bindings(bindings)
        assert len(result) == 3
        names = [e.name for e in result]  # type: ignore[attr-defined]
        assert names == ["high_pri", "mid_pri", "low_pri"]
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for key in list(sys.modules.keys()):
            if mod_name in key:
                del sys.modules[key]
        clear_registry()


# ---------------------------------------------------------------------------
# TC7 — Registry: match.roles filter
# ---------------------------------------------------------------------------


def test_tc7_match_roles_filter(tmp_path):
    clear_registry()

    mod_name = f"tc7_rolemod_{tmp_path.name}"
    mod_dir = tmp_path / mod_name
    mod_dir.mkdir()
    (mod_dir / "__init__.py").write_text("")
    (mod_dir / "evals.py").write_text(
        textwrap.dedent(
            """
            from src.evals import SfloEval, HookSite

            class DevOnlyEval(SfloEval):
                name = "dev_only"
                sites = [HookSite.POST_RESPONSE]
            """
        )
    )

    for key in list(sys.modules.keys()):
        if mod_name in key:
            del sys.modules[key]

    sys.path.insert(0, str(tmp_path))
    try:
        bindings = tmp_path / "bindings.yaml"
        bindings.write_text(
            f"evals:\n"
            f"  - name: dev_only\n"
            f"    module: {mod_name}.evals\n"
            f"    class: DevOnlyEval\n"
            f"    match:\n"
            f"      roles: [dev, qa]\n"
        )
        load_evals_from_bindings(bindings)

        # dev role — should see eval
        dev_evals = registered_evals_for_site(HookSite.POST_RESPONSE, role="dev")
        assert len(dev_evals) == 1

        # pm role — should NOT see eval
        pm_evals = registered_evals_for_site(HookSite.POST_RESPONSE, role="pm")
        assert len(pm_evals) == 0

        # No role specified — should see eval (role filter not applied)
        any_evals = registered_evals_for_site(HookSite.POST_RESPONSE)
        assert len(any_evals) == 1
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for key in list(sys.modules.keys()):
            if mod_name in key:
                del sys.modules[key]
        clear_registry()


# ---------------------------------------------------------------------------
# TC8 — Registry: match.gates filter
# ---------------------------------------------------------------------------


def test_tc8_match_gates_filter(tmp_path):
    clear_registry()

    mod_name = f"tc8_gatemod_{tmp_path.name}"
    mod_dir = tmp_path / mod_name
    mod_dir.mkdir()
    (mod_dir / "__init__.py").write_text("")
    (mod_dir / "evals.py").write_text(
        textwrap.dedent(
            """
            from src.evals import SfloEval, HookSite

            class Gate2Eval(SfloEval):
                name = "gate2_only"
                sites = [HookSite.POST_RESPONSE]
            """
        )
    )

    for key in list(sys.modules.keys()):
        if mod_name in key:
            del sys.modules[key]

    sys.path.insert(0, str(tmp_path))
    try:
        bindings = tmp_path / "bindings.yaml"
        bindings.write_text(
            f"evals:\n"
            f"  - name: gate2_only\n"
            f"    module: {mod_name}.evals\n"
            f"    class: Gate2Eval\n"
            f"    match:\n"
            f"      gates: [2, 3]\n"
        )
        load_evals_from_bindings(bindings)

        # Gate 2 — should match
        g2_evals = registered_evals_for_site(HookSite.POST_RESPONSE, gate=2)
        assert len(g2_evals) == 1

        # Gate 1 — should NOT match
        g1_evals = registered_evals_for_site(HookSite.POST_RESPONSE, gate=1)
        assert len(g1_evals) == 0

        # No gate specified — should see eval (gate filter not applied)
        all_evals = registered_evals_for_site(HookSite.POST_RESPONSE)
        assert len(all_evals) == 1
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for key in list(sys.modules.keys()):
            if mod_name in key:
                del sys.modules[key]
        clear_registry()


# ---------------------------------------------------------------------------
# TC9 — Decorator: wraps function into SfloEval subclass
# ---------------------------------------------------------------------------


def test_tc9_decorator():
    @sflo_eval(
        name="check_fn",
        sites=[HookSite.POST_RESPONSE],
        category=EvalCategory.QUALITY,
        priority=42,
    )
    async def check_fn(ctx: EvalContext) -> EvalResult:
        return EvalResult(
            triggered=True, severity=EvalSeverity.WARN, category=EvalCategory.QUALITY
        )

    assert issubclass(check_fn, SfloEval)
    assert check_fn.name == "check_fn"
    assert HookSite.POST_RESPONSE in check_fn.sites
    assert check_fn.priority == 42

    inst = check_fn()
    ctx = _make_ctx()

    # post_response fires the wrapped function
    result = run(inst.post_response(ctx))
    assert result.triggered is True
    assert result.severity == EvalSeverity.WARN

    # pre_prompt returns default PASS
    result2 = run(inst.pre_prompt(ctx))
    assert result2.triggered is False
    assert result2.action == EvalAction.PASS


# ---------------------------------------------------------------------------
# TC10 — EvalAction.MODIFY: payload replaces original
# ---------------------------------------------------------------------------


def test_tc10_modify_action():
    class ModifyEval(SfloEval):
        name = "modifier"
        sites = [HookSite.POST_RESPONSE]

        async def post_response(self, ctx: EvalContext) -> EvalResult:
            return EvalResult(
                triggered=True,
                severity=EvalSeverity.WARN,
                category=EvalCategory.SECURITY,
                payload={"response_text": "REDACTED"},
                action=EvalAction.MODIFY,
            )

    inst = ModifyEval()
    ctx = _make_ctx(payload={"response_text": "original text"})
    result = run(inst.post_response(ctx))

    assert result.action == EvalAction.MODIFY
    assert result.payload == {"response_text": "REDACTED"}

    # Simulate adapter: apply MODIFY
    response_text = ctx.payload.get("response_text", "")
    if result.action == EvalAction.MODIFY and result.payload:
        response_text = result.payload.get("response_text", response_text)
    assert response_text == "REDACTED"


# ---------------------------------------------------------------------------
# TC11 — EvalAction.ABORT: raises EvalAbortError
# ---------------------------------------------------------------------------


def test_tc11_abort_raises():
    class AbortEval(SfloEval):
        name = "aborter"
        sites = [HookSite.POST_RESPONSE]

        async def post_response(self, ctx: EvalContext) -> EvalResult:
            return EvalResult(
                triggered=True,
                severity=EvalSeverity.BLOCK,
                category=EvalCategory.SECURITY,
                action=EvalAction.ABORT,
                incident={"reason": "blocked by policy"},
            )

    inst = AbortEval()
    ctx = _make_ctx()
    result = run(inst.post_response(ctx))

    assert result.action == EvalAction.ABORT

    # Simulate adapter raising EvalAbortError on ABORT
    with pytest.raises(EvalAbortError) as exc_info:
        if result.action == EvalAction.ABORT:
            raise EvalAbortError(
                inst.name,
                result.incident.get("reason", "abort"),
                result.incident,
            )

    assert exc_info.value.eval_name == "aborter"
    assert "blocked by policy" in str(exc_info.value)


# ---------------------------------------------------------------------------
# TC12 — Fail-safe: eval that raises is caught, pipeline continues
# ---------------------------------------------------------------------------


def test_tc12_failsafe_eval_crash():
    class CrashEval(SfloEval):
        name = "crasher"
        sites = [HookSite.POST_RESPONSE]

        async def post_response(self, ctx: EvalContext) -> EvalResult:
            raise RuntimeError("simulated eval crash")

    inst = CrashEval()
    ctx = _make_ctx()
    original_text = "original response"

    # Simulate the adapter's fail-safe try/except block
    stderr_lines = []
    response_text = original_text
    try:
        result = run(inst.post_response(ctx))
        if result.action == EvalAction.MODIFY and result.payload:
            response_text = result.payload.get("response_text", response_text)
        elif result.action == EvalAction.ABORT:
            raise EvalAbortError(inst.name, "abort")
    except EvalAbortError:
        raise
    except Exception as e:
        # Fail-safe: crash never breaks pipeline
        stderr_lines.append(f"[Eval] {inst.name} crashed: {e}")

    # Pipeline continues with original payload
    assert response_text == original_text
    assert len(stderr_lines) == 1
    assert "crasher" in stderr_lines[0]
    assert "simulated eval crash" in stderr_lines[0]


# ---------------------------------------------------------------------------
# TC13 — Registry: disabled entry is not loaded
# ---------------------------------------------------------------------------


def test_tc13_disabled_entry(tmp_path):
    clear_registry()

    mod_name = f"tc13_dismod_{tmp_path.name}"
    mod_dir = tmp_path / mod_name
    mod_dir.mkdir()
    (mod_dir / "__init__.py").write_text("")
    (mod_dir / "evals.py").write_text(
        textwrap.dedent(
            """
            from src.evals import SfloEval, HookSite

            class MyEval(SfloEval):
                name = "disabled_eval"
                sites = [HookSite.POST_RESPONSE]
            """
        )
    )

    for key in list(sys.modules.keys()):
        if mod_name in key:
            del sys.modules[key]

    sys.path.insert(0, str(tmp_path))
    try:
        bindings = tmp_path / "bindings.yaml"
        bindings.write_text(
            f"evals:\n"
            f"  - name: disabled_eval\n"
            f"    module: {mod_name}.evals\n"
            f"    class: MyEval\n"
            f"    enabled: false\n"
        )
        result = load_evals_from_bindings(bindings)
        assert result == []
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for key in list(sys.modules.keys()):
            if mod_name in key:
                del sys.modules[key]
        clear_registry()


# ---------------------------------------------------------------------------
# TC14 — Registry: non-SfloEval class is rejected with warning
# ---------------------------------------------------------------------------


def test_tc14_non_sfloeval_class(tmp_path):
    clear_registry()

    mod_name = f"tc14_noteval_{tmp_path.name}"
    mod_dir = tmp_path / mod_name
    mod_dir.mkdir()
    (mod_dir / "__init__.py").write_text("")
    (mod_dir / "evals.py").write_text(
        "class NotAnEval:\n    name = 'not_an_eval'\n    sites = []\n"
    )

    for key in list(sys.modules.keys()):
        if mod_name in key:
            del sys.modules[key]

    sys.path.insert(0, str(tmp_path))
    try:
        bindings = tmp_path / "bindings.yaml"
        bindings.write_text(
            f"evals:\n"
            f"  - name: not_an_eval\n"
            f"    module: {mod_name}.evals\n"
            f"    class: NotAnEval\n"
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = load_evals_from_bindings(bindings)
        assert result == []
        assert any(
            "not a sfloeval" in str(warning.message).lower()
            or "SfloEval" in str(warning.message)
            for warning in w
        )
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for key in list(sys.modules.keys()):
            if mod_name in key:
                del sys.modules[key]
        clear_registry()


# ---------------------------------------------------------------------------
# TC15 — matches_filter: no match block → always True
# ---------------------------------------------------------------------------


def test_tc15_no_match_block_always_passes():
    class AnyEval(SfloEval):
        name = "any_eval"
        sites = [HookSite.POST_RESPONSE]

    inst = AnyEval()
    inst._match = {}  # type: ignore[attr-defined]

    # No role or gate filter → always passes
    assert matches_filter(inst) is True
    assert matches_filter(inst, role="dev") is True
    assert matches_filter(inst, role="pm", gate=1) is True


# ---------------------------------------------------------------------------
# TC16-TC20 — call_adapter_with_evals integration helper
# ---------------------------------------------------------------------------


class _MockAdapter:
    """Minimal mock adapter that returns a fixed response string."""

    def __init__(self, response: str = "mock response"):
        self._response = response
        self.last_call_kwargs: dict = {}

    async def spawn_agent(self, model, system_prompt, user_prompt, role=None, **kwargs):
        self.last_call_kwargs = dict(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            role=role,
            **kwargs,
        )
        return self._response


def test_tc16_call_adapter_with_evals_no_evals_passes_through():
    """With zero registered evals, helper just calls adapter and returns its result."""
    from src.evals.integration import call_adapter_with_evals

    clear_registry()
    adapter = _MockAdapter(response="raw response")

    result = run(
        call_adapter_with_evals(
            adapter,
            model="sonnet",
            system_prompt="You are helpful",
            user_prompt="Hello",
            role="dev",
        )
    )
    assert result == "raw response"
    assert adapter.last_call_kwargs["model"] == "sonnet"
    assert adapter.last_call_kwargs["role"] == "dev"
    clear_registry()


def test_tc17_call_adapter_with_evals_post_response_modify():
    """POST_RESPONSE MODIFY action replaces the adapter's response."""
    from src.evals.integration import call_adapter_with_evals

    clear_registry()

    class RedactEval(SfloEval):
        name = "redactor"
        sites = [HookSite.POST_RESPONSE]

        async def post_response(self, ctx: EvalContext) -> EvalResult:
            return EvalResult(
                triggered=True,
                severity=EvalSeverity.WARN,
                category=EvalCategory.SECURITY,
                payload={"response_text": "REDACTED"},
                action=EvalAction.MODIFY,
            )

    from src.evals.registry import _LOADED_EVALS

    inst = RedactEval()
    inst._match = {}
    inst._bindings_priority = 10
    _LOADED_EVALS.clear()
    _LOADED_EVALS.append(inst)

    adapter = _MockAdapter(response="original text with secrets")
    result = run(
        call_adapter_with_evals(
            adapter,
            model="sonnet",
            system_prompt="soul",
            user_prompt="task",
            role="dev",
        )
    )
    assert result == "REDACTED"
    clear_registry()


def test_tc18_call_adapter_with_evals_pre_prompt_modify():
    """PRE_PROMPT MODIFY action changes system_prompt before adapter call."""
    from src.evals.integration import call_adapter_with_evals

    clear_registry()

    class PromptRewriter(SfloEval):
        name = "rewriter"
        sites = [HookSite.PRE_PROMPT]

        async def pre_prompt(self, ctx: EvalContext) -> EvalResult:
            return EvalResult(
                triggered=True,
                severity=EvalSeverity.INFO,
                category=EvalCategory.QUALITY,
                payload={
                    "system_prompt": "MODIFIED SOUL",
                    "user_prompt": ctx.payload["user_prompt"],
                },
                action=EvalAction.MODIFY,
            )

    from src.evals.registry import _LOADED_EVALS

    inst = PromptRewriter()
    inst._match = {}
    inst._bindings_priority = 5
    _LOADED_EVALS.clear()
    _LOADED_EVALS.append(inst)

    adapter = _MockAdapter(response="ok")
    run(
        call_adapter_with_evals(
            adapter,
            model="sonnet",
            system_prompt="ORIGINAL SOUL",
            user_prompt="task",
            role="pm",
        )
    )
    assert adapter.last_call_kwargs["system_prompt"] == "MODIFIED SOUL"
    clear_registry()


def test_tc19_call_adapter_with_evals_abort_raises():
    """EvalAction.ABORT propagates as EvalAbortError to caller."""
    from src.evals.integration import call_adapter_with_evals

    clear_registry()

    class BlockEval(SfloEval):
        name = "blocker"
        sites = [HookSite.POST_RESPONSE]

        async def post_response(self, ctx: EvalContext) -> EvalResult:
            return EvalResult(
                triggered=True,
                severity=EvalSeverity.BLOCK,
                category=EvalCategory.SECURITY,
                action=EvalAction.ABORT,
                incident={"reason": "blocked by policy"},
            )

    from src.evals.registry import _LOADED_EVALS

    inst = BlockEval()
    inst._match = {}
    inst._bindings_priority = 10
    _LOADED_EVALS.clear()
    _LOADED_EVALS.append(inst)

    adapter = _MockAdapter(response="suspicious output")
    with pytest.raises(EvalAbortError) as exc_info:
        run(
            call_adapter_with_evals(
                adapter,
                model="sonnet",
                system_prompt="soul",
                user_prompt="task",
                role="dev",
            )
        )
    assert exc_info.value.eval_name == "blocker"
    assert "blocked by policy" in str(exc_info.value)
    clear_registry()


def test_tc20_call_adapter_with_evals_crash_logs_and_continues():
    """Eval crash is caught; pipeline continues with original adapter response."""
    from src.evals.integration import call_adapter_with_evals

    clear_registry()

    class CrashyEval(SfloEval):
        name = "crashy"
        sites = [HookSite.POST_RESPONSE]

        async def post_response(self, ctx: EvalContext) -> EvalResult:
            raise RuntimeError("simulated crash in eval")

    from src.evals.registry import _LOADED_EVALS

    inst = CrashyEval()
    inst._match = {}
    inst._bindings_priority = 10
    _LOADED_EVALS.clear()
    _LOADED_EVALS.append(inst)

    adapter = _MockAdapter(response="original response")
    result = run(
        call_adapter_with_evals(
            adapter,
            model="sonnet",
            system_prompt="soul",
            user_prompt="task",
            role="dev",
        )
    )
    # Pipeline continues with original response despite eval crash
    assert result == "original response"
    clear_registry()
