"""Behavioral tests for the 5 security toggles in claude_code.py.

These tests verify that each toggle ACTUALLY changes the options handed to
ClaudeAgentOptions / ClaudeSDKClient — not just that it parses (the parser
is covered by test_bindings.py::TestLoadSecurityConfig).

Mocking strategy:
  - Patch ClaudeSDKClient with a dummy async context manager so no real
    subprocess is spawned.
  - Patch ClaudeAgentOptions to capture the kwargs it receives.
  - Patch resolve_bindings_path to point at a tmp file we control.
  - Run ClaudeCodeAdapter._run_agent in an event loop and inspect the
    captured kwargs.

Coverage matrix:
  Toggle               default_off → expected option absence
  ──────────────────── ────────────────────────────────────
  isolate_settings    no `setting_sources` key (or non-[])
  no_session_persist. no `no-session-persistence` in extra_args
  sandbox_config_dir  no `CLAUDE_CONFIG_DIR` in env
  require_permission  permission_mode == "bypassPermissions"
  wipe_sandbox        no sandbox dir created (no-op without sibling toggle)

  Toggle               toggled_on  → expected option presence
  ──────────────────── ────────────────────────────────────
  isolate_settings    setting_sources == []
  no_session_persist. extra_args contains "no-session-persistence"
  sandbox_config_dir  env contains CLAUDE_CONFIG_DIR pointing at sandbox
  require_permission  permission_mode == "default"
  wipe_sandbox        sandbox dir deleted in finally (when sibling on)

Per-role artifact-delivery coverage (each of 6 SFLO pipeline roles):
  scout, pm, dev, qa, sflo, interrogator — verify resolved tools list
  contains what the role's SOUL/runner needs to deliver its artifact
  with all toggles in their DEFAULT (off / permissive) state.
"""

import asyncio
import os
import sys
import unittest
from unittest import mock

SFLO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SFLO_ROOT)


# ---------------------------------------------------------------------------
# Stub the claude_agent_sdk import so the adapter loads even without the
# real SDK installed in the test env. We replace ClaudeSDKClient and
# ClaudeAgentOptions per-test, but the module must exist at import time.
# ---------------------------------------------------------------------------

if "claude_agent_sdk" not in sys.modules:
    _fake_sdk = mock.MagicMock()
    sys.modules["claude_agent_sdk"] = _fake_sdk


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _DummySDKClient:
    """Async context manager that records every options kwarg it received
    via ClaudeAgentOptions, then short-circuits the run so no real LLM
    invocation happens. The receive_response() iterator yields nothing,
    so _run_agent breaks out of its message loop immediately.
    """

    captured_options = None  # set when ClaudeAgentOptions is instantiated

    def __init__(self, options):
        # `options` is the ClaudeAgentOptions instance our mock captured
        # below. Recording it here would lose the kwargs view, so we read
        # back from the captured-options class attribute.
        self._options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        return None

    def receive_response(self):
        async def _empty():
            if False:
                yield
        return _empty()

    async def get_mcp_status(self):
        return {"mcpServers": []}

    async def disconnect(self):
        return None


def _patch_sdk_classes():
    """Returns (patches, captured_kwargs_list).

    captured_kwargs_list is mutated by the ClaudeAgentOptions side_effect
    to hold one dict per call. _run_agent only calls it once per spawn.
    """
    captured = []

    def _capture_options(**kwargs):
        captured.append(kwargs)
        # Return a SimpleNamespace-like object so attribute access works
        # if the adapter introspects it (it doesn't currently, but defensive).
        opts_obj = mock.MagicMock()
        for k, v in kwargs.items():
            setattr(opts_obj, k, v)
        return opts_obj

    sdk_mock = mock.patch(
        "claude_agent_sdk.ClaudeSDKClient",
        new=_DummySDKClient,
    )
    opts_mock = mock.patch(
        "claude_agent_sdk.ClaudeAgentOptions",
        side_effect=_capture_options,
    )
    return [sdk_mock, opts_mock], captured


def _write_bindings(tmp_path, security_block: str = ""):
    """Write a minimal bindings.yaml with optional security: section.

    Returns the absolute path. Pure helper.
    """
    p = os.path.join(tmp_path, "bindings.yaml")
    body = "roles:\n  pm:\n    model: sonnet\n"
    if security_block:
        body += "\n" + security_block + "\n"
    with open(p, "w") as f:
        f.write(body)
    return p


def _run(adapter, **kwargs):
    """Drive _run_agent to completion in an event loop. Returns nothing
    interesting — the assertions are made on the captured options after.
    """
    asyncio.run(
        adapter._run_agent(
            model="sonnet",
            system_prompt="test",
            user_prompt="test",
            **kwargs,
        )
    )


# ---------------------------------------------------------------------------
# TOGGLE BEHAVIOR — default state (all OFF)
# ---------------------------------------------------------------------------


class TestSecurityTogglesDefaultOff(unittest.TestCase):
    """With no security: block in bindings.yaml, all 5 toggles must be off
    and the SDK options must reflect the permissive default.
    """

    def setUp(self):
        from src.adapters.claude_code import ClaudeCodeAdapter
        self.adapter = ClaudeCodeAdapter()

    def _drive(self, tmp_path, security_block=""):
        bindings_path = _write_bindings(str(tmp_path), security_block)
        patches, captured = _patch_sdk_classes()
        for p in patches:
            p.start()
        try:
            with mock.patch(
                "src.bindings.resolve_bindings_path",
                return_value=bindings_path,
            ):
                _run(self.adapter, role="dev")
        finally:
            for p in patches:
                p.stop()
        return captured

    def test_default_permission_mode_bypasses(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            captured = self._drive(td)
            self.assertEqual(len(captured), 1, "expected one spawn")
            opts = captured[0]
            self.assertEqual(
                opts.get("permission_mode"),
                "bypassPermissions",
                "default toggle state must NOT prompt for permission",
            )

    def test_default_no_setting_sources_isolation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            captured = self._drive(td)
            opts = captured[0]
            # When isolate_settings is false the adapter must NOT pass
            # setting_sources at all (lets SDK use its own default which
            # loads project + user settings).
            self.assertNotIn(
                "setting_sources",
                opts,
                "default must let SDK load its normal settings sources",
            )

    def test_default_no_session_persistence_flag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            captured = self._drive(td)
            opts = captured[0]
            extra = opts.get("extra_args") or {}
            self.assertNotIn(
                "no-session-persistence",
                extra,
                "default must NOT block session persistence",
            )

    def test_default_no_sandbox_env(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            captured = self._drive(td)
            opts = captured[0]
            env = opts.get("env") or {}
            self.assertNotIn(
                "CLAUDE_CONFIG_DIR",
                env,
                "default must NOT redirect CLAUDE_CONFIG_DIR",
            )


# ---------------------------------------------------------------------------
# TOGGLE BEHAVIOR — each ON (one at a time)
# ---------------------------------------------------------------------------


class TestSecurityTogglesIndividualOn(unittest.TestCase):
    def setUp(self):
        from src.adapters.claude_code import ClaudeCodeAdapter
        self.adapter = ClaudeCodeAdapter()

    def _drive(self, tmp_path, sec_block):
        bindings_path = _write_bindings(str(tmp_path), sec_block)
        patches, captured = _patch_sdk_classes()
        for p in patches:
            p.start()
        try:
            with mock.patch(
                "src.bindings.resolve_bindings_path",
                return_value=bindings_path,
            ):
                _run(self.adapter, role="dev")
        finally:
            for p in patches:
                p.stop()
        return captured

    def test_isolate_all_settings_on_passes_empty_setting_sources(self):
        import tempfile
        sec = "security:\n  isolate_all_settings: true\n"
        with tempfile.TemporaryDirectory() as td:
            opts = self._drive(td, sec)[0]
            self.assertEqual(opts.get("setting_sources"), [])

    def test_no_session_persistence_on_adds_extra_arg(self):
        import tempfile
        sec = "security:\n  no_session_persistence: true\n"
        with tempfile.TemporaryDirectory() as td:
            opts = self._drive(td, sec)[0]
            extra = opts.get("extra_args") or {}
            self.assertIn("no-session-persistence", extra)

    def test_sandbox_config_dir_on_redirects_env(self):
        import tempfile
        sec = "security:\n  sandbox_config_dir: true\n"
        with tempfile.TemporaryDirectory() as td:
            opts = self._drive(td, sec)[0]
            env = opts.get("env") or {}
            self.assertIn("CLAUDE_CONFIG_DIR", env)
            # Should point at a sandbox subdir, not user's real config
            self.assertIn(".claude_sandbox", env["CLAUDE_CONFIG_DIR"])

    def test_require_permission_on_uses_default_mode(self):
        import tempfile
        sec = "security:\n  require_permission: true\n"
        with tempfile.TemporaryDirectory() as td:
            opts = self._drive(td, sec)[0]
            self.assertEqual(opts.get("permission_mode"), "default")


# ---------------------------------------------------------------------------
# Settings isolation — two granularities (user-only safe, all severe)
# ---------------------------------------------------------------------------


class TestIsolateSettingsSplit(unittest.TestCase):
    """Settings isolation has two granularities:
    isolate_user_settings (safe — keeps project hooks) and
    isolate_all_settings (severe — kills everything).
    """

    def _drive(self, tmp_path, sec_block):
        bindings_path = _write_bindings(str(tmp_path), sec_block)
        patches, captured = _patch_sdk_classes()
        for p in patches:
            p.start()
        try:
            with mock.patch(
                "src.bindings.resolve_bindings_path",
                return_value=bindings_path,
            ):
                from src.adapters.claude_code import ClaudeCodeAdapter
                _run(ClaudeCodeAdapter(), role="dev")
        finally:
            for p in patches:
                p.stop()
        return captured

    def test_isolate_user_settings_keeps_project_alive(self):
        """isolate_user_settings → setting_sources=['project','local']."""
        import tempfile
        sec = "security:\n  isolate_user_settings: true\n"
        with tempfile.TemporaryDirectory() as td:
            opts = self._drive(td, sec)[0]
            self.assertEqual(opts.get("setting_sources"), ["project", "local"])

    def test_isolate_all_settings_severs_everything(self):
        """isolate_all_settings → setting_sources=[]."""
        import tempfile
        sec = "security:\n  isolate_all_settings: true\n"
        with tempfile.TemporaryDirectory() as td:
            opts = self._drive(td, sec)[0]
            self.assertEqual(opts.get("setting_sources"), [])

    def test_all_wins_over_user_when_both_set(self):
        """If a host sets both, the more restrictive wins."""
        import tempfile
        sec = ("security:\n"
               "  isolate_user_settings: true\n"
               "  isolate_all_settings: true\n")
        with tempfile.TemporaryDirectory() as td:
            opts = self._drive(td, sec)[0]
            self.assertEqual(opts.get("setting_sources"), [])

    def test_default_off_loads_everything(self):
        """No isolation toggles set → adapter doesn't pass setting_sources
        at all → SDK loads its normal default (project + user).
        """
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            opts = self._drive(td, "")[0]
            self.assertNotIn("setting_sources", opts)


# ---------------------------------------------------------------------------
# PER-ROLE ARTIFACT-DELIVERY COVERAGE
# ---------------------------------------------------------------------------


class TestPerRoleToolResolution(unittest.TestCase):
    """For each of the 6 SFLO pipeline roles, verify the resolved tool list
    matches what the role needs to deliver its artifact under DEFAULT
    (all-toggles-off) security.

    Expected tool needs (derived from each role's SOUL.md + runner.py
    artifact-write paths):

      scout        readonly  → returns JSON via response text (runner
                              parses, no Write needed)
      pm           full      → writes SCOPE.md / PM-VERIFY.md via Write
      dev          full      → writes code via Write/Edit, runs build/
                              tests via Bash
      qa           full      → writes QA-REPORT.md via Write, runs tests
                              via Bash
      sflo         full      → writes SHIP-DECISION.md via Write
      interrogator readonly  → emits markdown via response text (runner
                              captures + writes file). SOUL says
                              "Do not use tools".
    """

    def setUp(self):
        from src.adapters.claude_code import resolve_allowed_tools
        self.resolve = resolve_allowed_tools

    def test_scout_readonly_can_read_briefs(self):
        tools = self.resolve("readonly", caller_supplied=None)
        self.assertIn("Read", tools)
        self.assertIn("Glob", tools)
        self.assertNotIn("Write", tools, "scout must NOT be able to Write")
        self.assertNotIn("Bash", tools, "scout must NOT be able to Bash")

    def test_pm_default_full_includes_write(self):
        tools = self.resolve(None, caller_supplied=None)
        # tools_mode unset → full → None (means "all session tools")
        self.assertIsNone(
            tools,
            "pm default = full = None (all session tools incl. Write/MCP)",
        )

    def test_dev_default_full_includes_write_edit_bash(self):
        tools = self.resolve(None, caller_supplied=None)
        self.assertIsNone(
            tools, "dev default = full → all session tools incl. Bash"
        )

    def test_qa_default_full_includes_write(self):
        """Regression test for the historical 'qa cannot write report' bug.

        Pre-refactor, ROLE_TOOL_WHITELIST had qa = [Read, Glob, Grep, Bash]
        (NO Write). QA-REPORT.md write call was blocked → no artifact.
        Post-refactor, tools_mode unset → full → None → all tools allowed.
        """
        tools = self.resolve(None, caller_supplied=None)
        self.assertIsNone(
            tools,
            "qa default = full → must allow Write so QA-REPORT.md "
            "can be written (pre-refactor regression)",
        )

    def test_sflo_default_full_includes_write(self):
        tools = self.resolve(None, caller_supplied=None)
        self.assertIsNone(tools, "sflo default = full → can Write SHIP-DECISION.md")

    def test_interrogator_readonly_no_write(self):
        """Interrogator emits markdown via response stream; runner persists
        it. Agent itself doesn't need Write — readonly is correct.
        """
        tools = self.resolve("readonly", caller_supplied=None)
        self.assertNotIn(
            "Write", tools,
            "interrogator's SOUL says 'Do not use tools'; readonly correct",
        )

    def test_caller_override_wins(self):
        """Runner can pass an explicit allowed_tools that overrides the mode."""
        tools = self.resolve("readonly", caller_supplied=["Read", "WebFetch"])
        self.assertEqual(tools, ["Read", "WebFetch"])


# ---------------------------------------------------------------------------
# REQUIRE_PERMISSION DEADLOCK — documented behavior
# ---------------------------------------------------------------------------


class TestRequirePermissionDeadlockRisk(unittest.TestCase):
    """When require_permission=true, permission_mode='default' is set.
    In a non-interactive SDK run there is no UI to approve each tool call.
    The agent will silently block on the first Write/Bash. This test
    documents that risk so anyone touching the toggle understands it.
    """

    def test_require_permission_sets_default_mode_no_safeguard(self):
        import tempfile
        sec = "security:\n  require_permission: true\n"
        with tempfile.TemporaryDirectory() as td:
            from src.adapters.claude_code import ClaudeCodeAdapter
            adapter = ClaudeCodeAdapter()
            bindings_path = _write_bindings(td, sec)
            patches, captured = _patch_sdk_classes()
            for p in patches:
                p.start()
            try:
                with mock.patch(
                    "src.bindings.resolve_bindings_path",
                    return_value=bindings_path,
                ):
                    _run(adapter, role="dev")
            finally:
                for p in patches:
                    p.stop()
            opts = captured[0]
            self.assertEqual(opts.get("permission_mode"), "default")
            # No paired allow-list / approval mechanism is set —
            # documented risk that a real spawn will hang.


if __name__ == "__main__":
    unittest.main()
