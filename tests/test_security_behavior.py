"""Behavioral tests for the 5 security toggles in claude_code.py.

These tests verify that each toggle ACTUALLY changes the options handed to
ClaudeAgentOptions / ClaudeSDKClient — not just that it parses (the parser
is covered by test_bindings.py::TestLoadSecurityConfig).

Strategy: call build_sdk_options() directly with a security_config dict.
Pure function — no SDK import, no async, no mocks needed.

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

Per-role artifact-delivery coverage (each of 5 SFLO pipeline roles):
  scout, pm, dev, qa, sflo — verify resolved tools list
  contains what the role's SOUL/runner needs to deliver its artifact
  with all toggles in their DEFAULT (off / permissive) state.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.adapters.claude_code import build_sdk_options, resolve_allowed_tools


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _security_config(**overrides):
    """Return a security config dict matching load_security_config() defaults.

    All toggles default to False (permissive). Pass keyword overrides to
    flip individual toggles for the test.
    """
    cfg = {
        "require_permission": False,
        "isolate_user_settings": False,
        "isolate_all_settings": False,
        "no_session_persistence": False,
        "sandbox_config_dir": False,
        "wipe_sandbox": False,
    }
    cfg.update(overrides)
    return cfg


def _build(sec=None, **kwargs):
    """Shorthand: call build_sdk_options with sensible defaults.

    Returns (opts_dict, sandbox_dir_or_None).
    """
    if sec is None:
        sec = _security_config()
    opts, sandbox_dir, _needs_mcp, _temp_files = build_sdk_options(
        system_prompt=kwargs.pop("system_prompt", "test"),
        model=kwargs.pop("model", "sonnet"),
        security_config=sec,
        **kwargs,
    )
    return opts, sandbox_dir


# ---------------------------------------------------------------------------
# TOGGLE BEHAVIOR — default state (all OFF)
# ---------------------------------------------------------------------------


class TestSecurityTogglesDefaultOff(unittest.TestCase):
    """With all security toggles at default (False), the SDK options must
    reflect the permissive default — no isolation, no sandbox, bypass perms.
    """

    def test_default_permission_mode_bypasses(self):
        opts, _ = _build()
        self.assertEqual(
            opts.get("permission_mode"),
            "bypassPermissions",
            "default toggle state must NOT prompt for permission",
        )

    def test_default_no_setting_sources_isolation(self):
        opts, _ = _build()
        # When isolate_settings is false the adapter must NOT pass
        # setting_sources at all (lets SDK use its own default which
        # loads project + user settings).
        self.assertNotIn(
            "setting_sources",
            opts,
            "default must let SDK load its normal settings sources",
        )

    def test_default_no_session_persistence_flag(self):
        opts, _ = _build()
        extra = opts.get("extra_args") or {}
        self.assertNotIn(
            "no-session-persistence",
            extra,
            "default must NOT block session persistence",
        )

    def test_default_no_sandbox_env(self):
        opts, _ = _build()
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
    def test_isolate_all_settings_on_passes_empty_setting_sources(self):
        sec = _security_config(isolate_all_settings=True)
        opts, _ = _build(sec)
        self.assertEqual(
            opts.get("setting_sources"),
            [],
            "isolate_all_settings=True must yield empty setting_sources",
        )

    def test_no_session_persistence_on_adds_extra_arg(self):
        sec = _security_config(no_session_persistence=True)
        opts, _ = _build(sec)
        extra = opts.get("extra_args") or {}
        self.assertIn(
            "no-session-persistence",
            extra,
            "no_session_persistence=True must add flag to extra_args",
        )

    def test_sandbox_config_dir_on_redirects_env(self):
        import tempfile

        sec = _security_config(sandbox_config_dir=True)
        with tempfile.TemporaryDirectory() as td:
            opts, sandbox_dir = _build(sec, cwd=td)
            env = opts.get("env") or {}
            self.assertIn(
                "CLAUDE_CONFIG_DIR",
                env,
                "sandbox_config_dir=True must set CLAUDE_CONFIG_DIR in env",
            )
            # Should point at a sandbox subdir, not user's real config
            self.assertIn(
                ".claude_sandbox",
                env["CLAUDE_CONFIG_DIR"],
                "CLAUDE_CONFIG_DIR must point at .claude_sandbox subdir",
            )
            self.assertIsNotNone(
                sandbox_dir, "sandbox_dir must be returned when sandbox_config_dir=True"
            )

    def test_require_permission_on_uses_default_mode(self):
        sec = _security_config(require_permission=True)
        opts, _ = _build(sec)
        self.assertEqual(
            opts.get("permission_mode"),
            "default",
            "require_permission=True must set permission_mode to 'default'",
        )


# ---------------------------------------------------------------------------
# Settings isolation — two granularities (user-only safe, all severe)
# ---------------------------------------------------------------------------


class TestIsolateSettingsSplit(unittest.TestCase):
    """Settings isolation has two granularities:
    isolate_user_settings (safe — keeps project hooks) and
    isolate_all_settings (severe — kills everything).
    """

    def test_isolate_user_settings_keeps_project_alive(self):
        """isolate_user_settings → setting_sources=['project','local']."""
        sec = _security_config(isolate_user_settings=True)
        opts, _ = _build(sec)
        self.assertEqual(
            opts.get("setting_sources"),
            ["project", "local"],
            "isolate_user_settings must keep project and local sources",
        )

    def test_isolate_all_settings_severs_everything(self):
        """isolate_all_settings → setting_sources=[]."""
        sec = _security_config(isolate_all_settings=True)
        opts, _ = _build(sec)
        self.assertEqual(
            opts.get("setting_sources"),
            [],
            "isolate_all_settings must yield empty setting_sources",
        )

    def test_all_wins_over_user_when_both_set(self):
        """If a host sets both, the more restrictive wins."""
        sec = _security_config(
            isolate_user_settings=True,
            isolate_all_settings=True,
        )
        opts, _ = _build(sec)
        self.assertEqual(
            opts.get("setting_sources"),
            [],
            "both isolation toggles set must resolve to empty (most restrictive)",
        )

    def test_default_off_loads_everything(self):
        """No isolation toggles set → adapter doesn't pass setting_sources
        at all → SDK loads its normal default (project + user).
        """
        opts, _ = _build()
        self.assertNotIn(
            "setting_sources",
            opts,
            "no isolation toggles must omit setting_sources entirely",
        )


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
    """

    def test_scout_readonly_can_read_briefs(self):
        tools = resolve_allowed_tools("readonly", caller_supplied=None)
        self.assertIn("Read", tools, "scout readonly must include Read")
        self.assertIn("Glob", tools, "scout readonly must include Glob")
        self.assertNotIn("Write", tools, "scout must NOT be able to Write")
        self.assertNotIn("Bash", tools, "scout must NOT be able to Bash")

    def test_pm_default_full_includes_write(self):
        tools = resolve_allowed_tools(None, caller_supplied=None)
        # tools_mode unset → full → None (means "all session tools")
        self.assertIsNone(
            tools,
            "pm default = full = None (all session tools incl. Write/MCP)",
        )

    def test_dev_default_full_includes_write_edit_bash(self):
        tools = resolve_allowed_tools(None, caller_supplied=None)
        self.assertIsNone(tools, "dev default = full → all session tools incl. Bash")

    def test_qa_default_full_includes_write(self):
        """Regression test for the historical 'qa cannot write report' bug.

        Pre-refactor, ROLE_TOOL_WHITELIST had qa = [Read, Glob, Grep, Bash]
        (NO Write). QA-REPORT.md write call was blocked → no artifact.
        Post-refactor, tools_mode unset → full → None → all tools allowed.
        """
        tools = resolve_allowed_tools(None, caller_supplied=None)
        self.assertIsNone(
            tools,
            "qa default = full → must allow Write so QA-REPORT.md "
            "can be written (pre-refactor regression)",
        )

    def test_sflo_default_full_includes_write(self):
        tools = resolve_allowed_tools(None, caller_supplied=None)
        self.assertIsNone(tools, "sflo default = full → can Write SHIP-DECISION.md")

    def test_caller_override_wins(self):
        """Runner can pass an explicit allowed_tools that overrides the mode."""
        tools = resolve_allowed_tools("readonly", caller_supplied=["Read", "WebFetch"])
        self.assertEqual(
            tools,
            ["Read", "WebFetch"],
            "caller_supplied must override the tools_mode list",
        )


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
        sec = _security_config(require_permission=True)
        opts, _ = _build(sec)
        self.assertEqual(
            opts.get("permission_mode"),
            "default",
            "require_permission=True must set permission_mode='default'",
        )
        # No paired allow-list / approval mechanism is set —
        # documented risk that a real spawn will hang.


# ---------------------------------------------------------------------------
# MCP and extra_args pass-through
# ---------------------------------------------------------------------------


class TestMCPAndExtraArgs(unittest.TestCase):
    """Verify MCP servers and extra CLI args are wired through build_sdk_options."""

    def test_mcp_servers_passed_when_not_readonly(self):
        sec = _security_config()
        mcp = {"chrome-devtools": {"command": "npx", "args": []}}
        opts, _ = _build(sec, mcp_servers=mcp, tools_mode="full")
        self.assertEqual(
            opts.get("mcp_servers"),
            mcp,
            "mcp_servers must pass through in full tools_mode",
        )

    def test_mcp_servers_excluded_in_readonly_mode(self):
        sec = _security_config()
        mcp = {"chrome-devtools": {"command": "npx", "args": []}}
        opts, _ = _build(sec, mcp_servers=mcp, tools_mode="readonly")
        self.assertNotIn(
            "mcp_servers", opts, "mcp_servers must be excluded in readonly mode"
        )

    def test_extra_cli_args_passed_when_not_readonly(self):
        sec = _security_config()
        extra = {"verbose": None}
        opts, _ = _build(sec, extra_cli_args=extra, tools_mode="full")
        self.assertIn(
            "verbose",
            opts.get("extra_args", {}),
            "extra_cli_args must pass through in full tools_mode",
        )

    def test_sandbox_dir_returned_when_sandbox_config_on(self):
        import tempfile

        sec = _security_config(sandbox_config_dir=True)
        with tempfile.TemporaryDirectory() as td:
            _, sandbox_dir = _build(sec, cwd=td)
            self.assertIsNotNone(
                sandbox_dir, "sandbox_dir must be returned when toggle is on"
            )
            self.assertTrue(sandbox_dir.exists(), "sandbox_dir path must exist on disk")

    def test_no_sandbox_dir_when_toggle_off(self):
        sec = _security_config()
        _, sandbox_dir = _build(sec)
        self.assertIsNone(sandbox_dir, "sandbox_dir must be None when toggle is off")

    def test_mcp_defaults_append_system_prompt(self):
        sec = _security_config()
        mcp = {"test-server": {"command": "test"}}
        defaults = {"test-server": {"system_prompt_append": "Use test-server for X."}}
        opts, _ = _build(sec, mcp_servers=mcp, mcp_defaults=defaults, tools_mode="full")
        self.assertIn(
            "Use test-server for X.",
            opts.get("system_prompt", ""),
            "mcp_defaults system_prompt_append must be merged into system_prompt",
        )


if __name__ == "__main__":
    unittest.main()
