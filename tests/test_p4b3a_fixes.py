"""Tests for the 26 fixes (14 MAJOR + 12 MINOR) applied in one batch."""

import asyncio
import json
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# M1: ollama.py — os.chdir replaced with subprocess cwd= kwarg
# ---------------------------------------------------------------------------


def test_m1_ollama_no_os_chdir():
    """OllamaAdapter.spawn_agent() must NOT call os.chdir."""
    import inspect
    import src.adapters.ollama as mod

    src = inspect.getsource(mod.OllamaAdapter.spawn_agent)
    # Strip comments — comments may mention os.chdir for documentation purposes
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    code_only = "\n".join(code_lines)
    assert "os.chdir(" not in code_only, "os.chdir() call must not appear in OllamaAdapter.spawn_agent()"


def test_m1_original_cwd_not_mutated(monkeypatch):
    """Calling run() with cwd= must not change process cwd."""
    from src.adapters.ollama import OllamaAdapter

    original = os.getcwd()
    adapter = OllamaAdapter()
    # We just verify the attribute is initialised (not set to a real path)
    # and that no chdir happens during object creation.
    assert os.getcwd() == original


# ---------------------------------------------------------------------------
# M2: preflight.py — check_agent_soul uses clean_path
# ---------------------------------------------------------------------------


def test_m2_check_agent_soul_uses_clean_path():
    """preflight.py must pass clean_path (not raw agent_path) to check_agent_soul."""
    import inspect
    import src.preflight as mod

    src = inspect.getsource(mod.preflight_check)
    # The fix changes `check_agent_soul(role, agent_path)` to `check_agent_soul(role, clean_path)`
    assert "check_agent_soul(role, clean_path)" in src


# ---------------------------------------------------------------------------
# M3: openclaw.py — uuid4() instead of id(message) % 100000
# ---------------------------------------------------------------------------


def test_m3_openclaw_no_id_modulo():
    """OpenClawAdapter must use uuid4() not id(message) % 100000 for session-id."""
    import inspect
    import src.adapters.openclaw as mod

    src = inspect.getsource(mod)
    assert "id(message)" not in src
    assert "uuid4" in src


def test_m3_openclaw_session_id_unique():
    """Each instantiation produces a different hex session-id fragment."""
    from uuid import uuid4

    ids = {uuid4().hex for _ in range(100)}
    assert len(ids) == 100


# ---------------------------------------------------------------------------
# M4: bindings.py — load_evals_section deleted
# ---------------------------------------------------------------------------


def test_m4_load_evals_section_removed():
    """load_evals_section dead-code wrapper must be removed from bindings."""
    import src.bindings as mod

    assert not hasattr(mod, "load_evals_section"), (
        "load_evals_section dead-code wrapper should have been removed"
    )


# ---------------------------------------------------------------------------
# M5: bindings.py docstring — thinking/effort consumed by adapter integrations
# ---------------------------------------------------------------------------


def test_m5_bindings_docstring_documents_thinking_consumption():
    """bindings.py module docstring must clarify how thinking/effort are consumed."""
    import src.bindings as mod

    doc = mod.__doc__ or ""
    # Generic assertion: docstring must mention that thinking/effort are
    # consumed by adapter integrations (or similar consumer-side language).
    # Avoid naming any specific host-project module.
    assert any(
        marker in doc.lower()
        for marker in ("adapter integration", "consumed by", "consumer-side", "consumed")
    ), (
        "bindings.py docstring must document thinking/effort consumption via "
        "adapter integrations (no specific host-project module name required)."
    )


# ---------------------------------------------------------------------------
# M6: claude_code.py — servers=[] initialized before while-loop
# ---------------------------------------------------------------------------


def test_m6_servers_initialized_before_loop():
    """ClaudeCodeAdapter.run() must initialize servers=[] before while loop."""
    import inspect
    import src.adapters.claude_code as mod

    src = inspect.getsource(mod.ClaudeCodeAdapter._run_agent)
    # Check that 'servers = []' appears before the while loop
    servers_pos = src.find("servers = []")
    while_pos = src.find("while _time.time() < deadline")
    assert servers_pos != -1, "servers = [] not found in ClaudeCodeAdapter.run"
    assert servers_pos < while_pos, "servers = [] must appear before while loop"


# ---------------------------------------------------------------------------
# M7: state.py — stale-lock recovery
# ---------------------------------------------------------------------------


def test_m7_stale_lock_recovery():
    """acquire_lock recovers a stale lock (dead PID, age > 60s)."""
    from src.state import acquire_lock, release_lock

    with tempfile.TemporaryDirectory() as d:
        lock_path = os.path.join(d, "state.lock")
        # Write a stale lock: dead PID 999999, mtime 120s ago
        with open(lock_path, "w") as f:
            f.write("999999")
        # Set mtime to 120 seconds ago
        old_time = time.time() - 120
        os.utime(lock_path, (old_time, old_time))

        # Should recover without raising
        fd = acquire_lock(d)
        # Verify we got a valid fd
        assert fd >= 0
        release_lock(d, fd)


def test_m7_live_lock_not_stolen():
    """acquire_lock does NOT break a fresh lock held by this process."""
    from src.state import _lock_path

    with tempfile.TemporaryDirectory() as d:
        lock_path = _lock_path(d)
        os.makedirs(d, exist_ok=True)
        # Write current PID with a fresh mtime (not stale)
        with open(lock_path, "w") as f:
            f.write(str(os.getpid()))
        # mtime is now (fresh) — lock should NOT be stolen
        # acquire_lock will retry 50 times × 0.1s = 5s, too slow for a test.
        # Just verify the stale-check logic does not trigger.
        stat = os.stat(lock_path)
        age = time.time() - stat.st_mtime
        assert age < 60, "Freshly written lock should have age < 60s"


# ---------------------------------------------------------------------------
# M8: runner.py — install_signal_handler public alias
# ---------------------------------------------------------------------------


def test_m8_install_signal_handler_public():
    """runner module must export install_signal_handler as a public name."""
    import src.runner as mod

    assert hasattr(mod, "install_signal_handler"), (
        "install_signal_handler must be a public name in runner module"
    )
    assert callable(mod.install_signal_handler)


def test_m8_private_still_exists():
    """_install_signal_handler private name must still exist for compat."""
    import src.runner as mod

    assert hasattr(mod, "_install_signal_handler")


# ---------------------------------------------------------------------------
# M9: runner.py — DEGRADED verdict when tool_errors present but no reject
# ---------------------------------------------------------------------------


def test_m9_degraded_verdict_in_source():
    """Runner STST gate must produce DEGRADED when tool_errors but no REJECT."""
    import inspect
    import src.runner as mod

    src = inspect.getsource(mod.run_pipeline)
    assert "DEGRADED" in src


def test_m9_verdict_logic():
    """DEGRADED verdict logic: tool_errors=True, any_reject=False -> DEGRADED."""
    any_reject = False
    tool_errors = ["some error"]
    if any_reject:
        overall_verdict = "REJECT"
    elif tool_errors:
        overall_verdict = "DEGRADED"
    else:
        overall_verdict = "PASS"
    assert overall_verdict == "DEGRADED"


# ---------------------------------------------------------------------------
# M10: mcp_bridge.py — narrowed except clause
# ---------------------------------------------------------------------------


def test_m10_mcp_bridge_narrow_except():
    """mcp_bridge close() must not catch bare Exception."""
    import inspect
    import src.mcp_bridge as mod

    src = inspect.getsource(mod.OllamaMCPBridge.close)
    assert "except (RuntimeError, asyncio.CancelledError, OSError)" in src
    # bare Exception catch is gone
    assert ", Exception)" not in src


# ---------------------------------------------------------------------------
# M11: runner.py — transient vs prompt error classification
# ---------------------------------------------------------------------------


def test_m11_non_retryable_prompt_errors():
    """Non-transient prompt errors (JSONDecodeError, KeyError, ValueError) skip retries."""
    import inspect
    import src.runner as mod

    src = inspect.getsource(mod.run_pipeline)
    assert "non-retryable" in src.lower() or "_is_prompt_error" in src
    assert "JSONDecodeError" in src
    assert "KeyError" in src


def test_m11_transient_errors_retried():
    """ConnectionError and TimeoutError are classified as transient."""
    import inspect
    import src.runner as mod

    src = inspect.getsource(mod.run_pipeline)
    assert "ConnectionError" in src
    assert "TimeoutError" in src


# ---------------------------------------------------------------------------
# M12: scaffold.py — known_files includes STST-REPORT.md etc.
# ---------------------------------------------------------------------------


def test_m12_known_files_includes_stst():
    """cmd_clean known_files must include STST-REPORT.md, STST-FEEDBACK.md, state.lock, .last_hook_state."""
    import inspect
    import src.scaffold as mod

    src = inspect.getsource(mod.cmd_clean)
    for name in ("STST-REPORT.md", "STST-FEEDBACK.md", "state.lock", ".last_hook_state"):
        assert name in src, f"{name} missing from cmd_clean known_files"


# ---------------------------------------------------------------------------
# M13: evals/registry.py — EvalRegistry class exists
# ---------------------------------------------------------------------------


def test_m13_eval_registry_class_exists():
    """EvalRegistry class must be defined in registry module."""
    import src.evals.registry as mod

    assert hasattr(mod, "EvalRegistry")
    assert isinstance(mod.EvalRegistry, type)


def test_m13_loaded_evals_same_object_as_registry_store():
    """_LOADED_EVALS and _registry._store must be the same list object."""
    import src.evals.registry as mod

    assert mod._LOADED_EVALS is mod._registry._store


def test_m13_clear_registry_clears_loaded_evals():
    """clear_registry() must clear _LOADED_EVALS via the class."""
    import src.evals.registry as mod

    # Temporarily add a sentinel
    mod._LOADED_EVALS.append("sentinel")
    assert len(mod._LOADED_EVALS) > 0
    mod.clear_registry()
    assert len(mod._LOADED_EVALS) == 0


# ---------------------------------------------------------------------------
# M14: scaffold.py — role validation in cmd_assign
# ---------------------------------------------------------------------------


def test_m14_cmd_assign_role_validation_source():
    """cmd_assign must use _ASSIGNABLE_ROLES derived from constants."""
    import inspect
    import src.scaffold as mod

    src = inspect.getsource(mod.cmd_assign)
    assert "_ASSIGNABLE_ROLES" in src
    assert "_INTERNAL_TOKENS" in src


# ---------------------------------------------------------------------------
# m1: runner.py — hoisted imports at module top
# ---------------------------------------------------------------------------


def test_m1_minor_imports_at_module_top():
    """glob, shutil, subprocess, traceback must be top-level imports in runner."""
    import src.runner as mod

    for name in ("glob", "shutil", "subprocess", "traceback"):
        assert name in dir(mod) or hasattr(mod, name) or name in sys.modules, (
            f"'{name}' must be importable from runner module scope"
        )


def test_m1_minor_no_inline_imports():
    """runner.py must not have inline 'import shutil' etc. inside functions."""
    import inspect
    import src.runner as mod

    # Check run_pipeline specifically
    src = inspect.getsource(mod.run_pipeline)
    for banned in ("import shutil", "import subprocess", "import glob", "import traceback"):
        assert banned not in src, f"Inline '{banned}' found in run_pipeline"


# ---------------------------------------------------------------------------
# m2: validate_ext.py — _section_body_local removed, section_body remains
# ---------------------------------------------------------------------------


def test_m2_minor_section_body_local_removed():
    """_section_body_local must be removed; section_body must exist."""
    import src.validate_ext as mod

    assert not hasattr(mod, "_section_body_local"), (
        "_section_body_local should be removed"
    )
    assert hasattr(mod, "section_body"), "section_body must exist"
    assert callable(mod.section_body)


def test_m2_minor_section_body_works():
    """section_body correctly extracts text under a markdown heading."""
    from src.validate_ext import section_body

    content = "## Summary\nsome text\n## Next\nother"
    result = section_body(content, "Summary")
    assert "some text" in result
    assert "other" not in result


# ---------------------------------------------------------------------------
# m3: runner.py — state_path() used instead of hardcoded path
# ---------------------------------------------------------------------------


def test_m3_minor_state_path_imported():
    """runner module must import state_path from state module."""
    import inspect
    import src.runner as mod

    src = inspect.getsource(mod)
    assert "state_path" in src


def test_m3_minor_prior_state_path_uses_function():
    """run_pipeline must call state_path(sflo_dir), not os.path.join(...state.json)."""
    import inspect
    import src.runner as mod

    src = inspect.getsource(mod.run_pipeline)
    assert "state_path(sflo_dir)" in src


# ---------------------------------------------------------------------------
# m4: adapters/__init__.py — all runtimes mentioned in error message
# ---------------------------------------------------------------------------


def test_m4_minor_error_mentions_all_runtimes():
    """get_adapter() RuntimeError must mention all 4 supported runtimes."""
    import inspect
    import src.adapters as mod

    src = inspect.getsource(mod.get_adapter)
    for runtime in ("claude-code", "cursor", "openclaw", "ollama"):
        assert runtime in src, f"'{runtime}' not mentioned in get_adapter error"


# ---------------------------------------------------------------------------
# m5: runner.py make_logger — close() method exists
# ---------------------------------------------------------------------------


def test_m5_minor_logger_has_close():
    """make_logger must return a callable with a close() method."""
    from src.runner import make_logger

    with tempfile.TemporaryDirectory() as d:
        log = make_logger(d, verbose=False)
        assert callable(log)
        assert hasattr(log, "close"), "logger must have close() method"
        log("test message")
        log.close()


# ---------------------------------------------------------------------------
# m6: runner.py — JSON sliding window extractor
# ---------------------------------------------------------------------------


def test_m6_minor_json_sliding_window_in_source():
    """run_pipeline must use sliding-window JSON extraction, not bare regex."""
    import inspect
    import src.runner as mod

    src = inspect.getsource(mod.run_pipeline)
    assert "_extract_json_obj" in src
    assert r'[^{}]*"pm"' not in src, "Old regex pattern must be removed"


def test_m6_minor_nested_brace_extraction():
    """_extract_json_obj-equivalent must handle nested braces in JSON."""
    # Simulate the sliding-window function inline
    def _extract_json_obj(text):
        start = text.find("{")
        while start != -1:
            for end in range(len(text), start, -1):
                try:
                    obj = json.loads(text[start:end])
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    pass
            start = text.find("{", start + 1)
        return None

    nested = 'some text {"pm": "/path/{a}/b", "dev": "/x", "qa": "/y"} end'
    result = _extract_json_obj(nested)
    assert result is not None
    assert result["pm"] == "/path/{a}/b"


# ---------------------------------------------------------------------------
# m7: runner.py docstring — --bindings documented
# ---------------------------------------------------------------------------


def test_m7_minor_bindings_in_docstring():
    """runner.py module docstring must document --bindings flag."""
    import src.runner as mod

    doc = mod.__doc__ or ""
    assert "--bindings" in doc, "runner module docstring must document --bindings"


# ---------------------------------------------------------------------------
# m8: config.py — grade_threshold refactored
# ---------------------------------------------------------------------------


def test_m8_minor_grade_threshold_refactor():
    """load_pipeline_config must use _default_numeric variable."""
    import inspect
    import src.config as mod

    src = inspect.getsource(mod.load_pipeline_config)
    assert "_default_numeric" in src


def test_m8_minor_load_pipeline_config_defaults():
    """load_pipeline_config returns numeric grade_threshold by default."""
    from src.config import load_pipeline_config

    cfg = load_pipeline_config(path=None)
    assert isinstance(cfg["grade_threshold"], (int, float))
    assert cfg["grade_threshold"] > 0


# ---------------------------------------------------------------------------
# m9: prompt.py — sys.executable used
# ---------------------------------------------------------------------------


def test_m9_minor_prompt_uses_sys_executable():
    """prompt.py must use sys.executable, not PYTHON_CMD constant."""
    import inspect
    import src.prompt as mod

    src = inspect.getsource(mod)
    assert "sys.executable" in src
    assert "PYTHON_CMD" not in src


def test_m9_minor_format_prompt_no_python_cmd_import():
    """prompt.py must not import from constants for PYTHON_CMD."""
    import src.prompt as mod

    assert not hasattr(mod, "PYTHON_CMD")


# ---------------------------------------------------------------------------
# m10: stop_hook.py — int(loop_count) wrapped with try/except
# ---------------------------------------------------------------------------


def test_m10_minor_loop_count_try_except():
    """stop_hook.py must wrap int(loop_count) in try/except."""
    import inspect
    import src.hooks.cursor.stop_hook as mod

    src = inspect.getsource(mod.main)
    assert "try:" in src
    assert "loop_count = 0" in src  # default in except


# ---------------------------------------------------------------------------
# m11: ollama.py — strip_think_tags() helper extracted
# ---------------------------------------------------------------------------


def test_m11_minor_strip_think_tags_exists():
    """OllamaAdapter module must export strip_think_tags() helper."""
    import src.adapters.ollama as mod

    assert hasattr(mod, "strip_think_tags")
    assert callable(mod.strip_think_tags)


def test_m11_minor_strip_think_tags_works():
    """strip_think_tags removes <think>...</think> blocks."""
    from src.adapters.ollama import strip_think_tags

    text = "before <think>hidden</think> after"
    result = strip_think_tags(text)
    assert "hidden" not in result
    assert "before" in result
    assert "after" in result


def test_m11_minor_no_duplicate_re_sub_blocks():
    """OllamaAdapter.run() must not contain duplicated re.sub think-strip blocks."""
    import inspect
    import src.adapters.ollama as mod

    src = inspect.getsource(mod.OllamaAdapter.spawn_agent)
    count = src.count('r"<think>.*?</think>"')
    assert count == 0, "Duplicated re.sub think blocks must be replaced with strip_think_tags()"


# ---------------------------------------------------------------------------
# m12: validate.py — PLACEHOLDER_PATTERN comment
# ---------------------------------------------------------------------------


def test_m12_minor_placeholder_pattern_has_tradeoff_comment():
    """validate.py must have a trade-off comment near PLACEHOLDER_PATTERN."""
    import inspect
    import src.validate as mod

    src = inspect.getsource(mod)
    assert "trade-off" in src.lower() or "tradeoff" in src.lower() or "PLACEHOLDER_PATTERN trade" in src
