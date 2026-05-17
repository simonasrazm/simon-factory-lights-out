#!/usr/bin/env python3
"""Unit tests for SFLO prompt generation (src/prompt.py).

Tests the pure text-assembly functions that translate state machine
output into reinjectable instruction strings.  No subprocess or
external dependency needed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.prompt import format_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _python_cmd():
    """Return the python command that format_prompt will embed."""
    return sys.executable or "python3"


# ---------------------------------------------------------------------------
# Terminal / no-op actions -> None
# ---------------------------------------------------------------------------


class TestTerminalActionsReturnNone:
    """Actions that represent pipeline completion yield no prompt."""

    def test_pipeline_complete_returns_none(self):
        result = format_prompt({"action": "pipeline_complete"})
        assert result is None, (
            "pipeline_complete should return None since pipeline is done"
        )

    def test_waiting_returns_none(self):
        result = format_prompt({"action": "waiting"})
        assert result is None, (
            "waiting action should return None since no prompt is needed"
        )

    def test_ask_human_returns_none(self):
        result = format_prompt({"action": "ask_human"})
        assert result is None, (
            "ask_human action should return None since human input is needed"
        )


# ---------------------------------------------------------------------------
# spawn_agent action
# ---------------------------------------------------------------------------


class TestSpawnAgentPrompt:
    """format_prompt correctly assembles spawn_agent instructions."""

    def test_minimal_spawn(self):
        action_dict = {
            "action": "spawn_agent",
            "agent": {"role": "dev", "model": "sonnet", "path": "agents/dev"},
        }
        prompt = format_prompt(action_dict)
        assert prompt is not None, "spawn_agent should produce a non-None prompt"
        assert "DEV" in prompt, "prompt should contain uppercased role 'DEV'"
        assert "sonnet" in prompt, "prompt should contain model name 'sonnet'"
        assert "agents/dev" in prompt, "prompt should contain agent path 'agents/dev'"

    def test_spawn_includes_reads(self):
        action_dict = {
            "action": "spawn_agent",
            "agent": {
                "role": "qa",
                "model": "opus",
                "path": "agents/qa",
                "reads": ["SCOPE.md", "BUILD-STATUS.md"],
            },
        }
        prompt = format_prompt(action_dict)
        assert "SCOPE.md" in prompt, "prompt should list read file 'SCOPE.md'"
        assert "BUILD-STATUS.md" in prompt, (
            "prompt should list read file 'BUILD-STATUS.md'"
        )
        assert "reads:" in prompt, "prompt should contain 'reads:' section header"

    def test_spawn_includes_produces(self):
        action_dict = {
            "action": "spawn_agent",
            "agent": {
                "role": "pm",
                "model": "opus",
                "path": "agents/pm",
                "produces": "PM-VERIFY.md",
            },
        }
        prompt = format_prompt(action_dict)
        assert "produces: PM-VERIFY.md" in prompt, (
            "prompt should contain 'produces: PM-VERIFY.md' directive"
        )

    def test_spawn_includes_instruction(self):
        action_dict = {
            "action": "spawn_agent",
            "agent": {
                "role": "dev",
                "model": "sonnet",
                "path": "agents/dev",
                "instruction": "Focus on security review",
            },
        }
        prompt = format_prompt(action_dict)
        assert "Focus on security review" in prompt, (
            "prompt should embed the agent instruction text"
        )

    def test_spawn_includes_scaffold_next_command(self):
        action_dict = {
            "action": "spawn_agent",
            "agent": {"role": "dev", "model": "sonnet", "path": "agents/dev"},
        }
        prompt = format_prompt(action_dict)
        assert "sflo/src/scaffold.py next" in prompt, (
            "prompt should contain scaffold next command"
        )
        assert _python_cmd() in prompt, (
            f"prompt should contain python command '{_python_cmd()}'"
        )

    def test_spawn_no_reads_omits_reads_section(self):
        action_dict = {
            "action": "spawn_agent",
            "agent": {"role": "dev", "model": "sonnet", "path": "agents/dev"},
        }
        prompt = format_prompt(action_dict)
        assert "reads:" not in prompt, (
            "prompt should omit 'reads:' section when no reads are specified"
        )

    def test_spawn_empty_reads_omits_reads_section(self):
        action_dict = {
            "action": "spawn_agent",
            "agent": {
                "role": "dev",
                "model": "sonnet",
                "path": "agents/dev",
                "reads": [],
            },
        }
        prompt = format_prompt(action_dict)
        assert "reads:" not in prompt, (
            "prompt should omit 'reads:' section when reads list is empty"
        )

    def test_spawn_role_uppercased_in_prompt(self):
        action_dict = {
            "action": "spawn_agent",
            "agent": {"role": "extra", "model": "haiku", "path": "agents/extra"},
        }
        prompt = format_prompt(action_dict)
        assert "EXTRA" in prompt, (
            "prompt should contain role 'extra' uppercased to 'EXTRA'"
        )

    def test_spawn_missing_fields_use_defaults(self):
        action_dict = {
            "action": "spawn_agent",
            "agent": {},
        }
        prompt = format_prompt(action_dict)
        assert prompt is not None, (
            "spawn_agent with empty agent dict should still produce a prompt"
        )
        # Empty fields: pipeline.yaml owns model/role config
        assert "Spawn the" in prompt, "prompt should contain spawn instruction"
        assert "agent with the Agent tool" in prompt, (
            "prompt should contain agent tool instruction"
        )


# ---------------------------------------------------------------------------
# produce_artifact action
# ---------------------------------------------------------------------------


class TestProduceArtifactPrompt:
    """format_prompt correctly assembles produce_artifact instructions."""

    def test_basic_produce_artifact(self):
        action_dict = {
            "action": "produce_artifact",
            "artifact": "SCOPE.md",
            "gate_doc": "gates/discovery.md",
            "reads": ["README.md"],
        }
        prompt = format_prompt(action_dict)
        assert "SCOPE.md" in prompt, "prompt should reference artifact name 'SCOPE.md'"
        assert "gates/discovery.md" in prompt, (
            "prompt should reference gate doc 'gates/discovery.md'"
        )
        assert "README.md" in prompt, "prompt should list read file 'README.md'"
        assert "sflo/src/scaffold.py next" in prompt, (
            "prompt should contain scaffold next command"
        )

    @pytest.mark.parametrize(
        "read_file", ["SCOPE.md", "BUILD-STATUS.md", "changes.diff"]
    )
    def test_produce_artifact_with_multiple_reads(self, read_file):
        action_dict = {
            "action": "produce_artifact",
            "artifact": "QA-REPORT.md",
            "gate_doc": "gates/test.md",
            "reads": ["SCOPE.md", "BUILD-STATUS.md", "changes.diff"],
        }
        prompt = format_prompt(action_dict)
        assert read_file in prompt, f"prompt should list read file '{read_file}'"

    def test_produce_artifact_sflo_path(self):
        action_dict = {
            "action": "produce_artifact",
            "artifact": "BUILD-STATUS.md",
            "gate_doc": "gates/build.md",
            "reads": [],
        }
        prompt = format_prompt(action_dict)
        assert ".sflo/BUILD-STATUS.md" in prompt, (
            "prompt should prefix artifact with .sflo/ path"
        )


# ---------------------------------------------------------------------------
# validated action
# ---------------------------------------------------------------------------


class TestValidatedPrompt:
    """format_prompt handles the validated action with chained next."""

    def test_validated_with_next_action(self):
        action_dict = {
            "action": "validated",
            "gate": 1,
            "next": {
                "action": "spawn_agent",
                "agent": {"role": "dev", "model": "sonnet", "path": "agents/dev"},
            },
        }
        prompt = format_prompt(action_dict)
        assert prompt is not None, (
            "validated with spawn_agent next should produce a prompt"
        )
        assert "Gate 1 passed" in prompt, "prompt should announce 'Gate 1 passed'"
        assert "DEV" in prompt, (
            "prompt should include the chained spawn_agent role 'DEV'"
        )

    def test_validated_with_terminal_next(self):
        action_dict = {
            "action": "validated",
            "gate": 5,
            "next": {"action": "pipeline_complete"},
        }
        prompt = format_prompt(action_dict)
        assert prompt is None, (
            "validated chaining to pipeline_complete should return None"
        )

    def test_validated_with_empty_next(self):
        action_dict = {
            "action": "validated",
            "gate": 3,
            "next": {},
        }
        prompt = format_prompt(action_dict)
        # Empty action dict -> fallback prompt
        assert prompt is not None, (
            "validated with empty next should still produce a fallback prompt"
        )
        assert "Gate 3 passed" in prompt, (
            "prompt should announce 'Gate 3 passed' even with empty next"
        )


# ---------------------------------------------------------------------------
# loop_back action
# ---------------------------------------------------------------------------


class TestLoopBackPrompt:
    """format_prompt handles the loop_back action correctly."""

    def test_loop_back_with_failed_checks(self):
        action_dict = {
            "action": "loop_back",
            "gate": 3,
            "checks": [
                {"name": "grade_check", "pass": False},
                {"name": "stranger_test", "pass": True},
            ],
            "inner_count": 2,
            "max": 10,
            "next": {
                "action": "spawn_agent",
                "agent": {"role": "qa", "model": "sonnet", "path": "agents/qa"},
            },
        }
        prompt = format_prompt(action_dict)
        assert "Gate 3 FAILED" in prompt, "prompt should announce 'Gate 3 FAILED'"
        assert "grade_check" in prompt, "prompt should list failed check 'grade_check'"
        # stranger_test passed, so it should not be in the failed list
        assert "stranger_test" not in prompt, (
            "passing check 'stranger_test' should not appear in failed list"
        )
        assert "2/10" in prompt, "prompt should show loop iteration count '2/10'"

    def test_loop_back_with_no_failed_checks(self):
        action_dict = {
            "action": "loop_back",
            "gate": 2,
            "checks": [{"name": "build_ok", "pass": True}],
            "inner_count": 1,
            "max": 10,
            "next": {},
        }
        prompt = format_prompt(action_dict)
        assert "Gate 2 FAILED" in prompt, (
            "loop_back should announce 'Gate 2 FAILED' even when all checks pass"
        )

    def test_loop_back_outer_count_fallback(self):
        action_dict = {
            "action": "loop_back",
            "gate": 1,
            "checks": [{"name": "word_count", "pass": False}],
            "outer_count": 3,
            "max": 10,
            "next": {},
        }
        prompt = format_prompt(action_dict)
        assert "3/10" in prompt, "prompt should use outer_count fallback showing '3/10'"

    def test_loop_back_includes_chained_next(self):
        action_dict = {
            "action": "loop_back",
            "gate": 2,
            "checks": [],
            "inner_count": 1,
            "max": 5,
            "next": {
                "action": "spawn_agent",
                "agent": {"role": "dev", "model": "sonnet", "path": "agents/dev"},
            },
        }
        prompt = format_prompt(action_dict)
        assert "DEV" in prompt, (
            "loop_back prompt should include chained spawn_agent role 'DEV'"
        )


# ---------------------------------------------------------------------------
# Unknown / fallback action
# ---------------------------------------------------------------------------


class TestFallbackPrompt:
    """Unrecognized actions get a generic continue prompt."""

    def test_unknown_action_returns_continue(self):
        prompt = format_prompt({"action": "something_unexpected"})
        assert prompt is not None, "unknown action should produce a fallback prompt"
        assert "continue" in prompt.lower(), (
            "fallback prompt should contain 'continue' instruction"
        )
        assert "sflo/src/scaffold.py next" in prompt, (
            "fallback prompt should contain scaffold next command"
        )

    def test_empty_action_returns_continue(self):
        prompt = format_prompt({})
        assert prompt is not None, "empty action dict should produce a fallback prompt"
        assert "sflo/src/scaffold.py next" in prompt, (
            "fallback prompt should contain scaffold next command"
        )

    def test_fallback_uses_correct_python_cmd(self):
        prompt = format_prompt({"action": "unknown"})
        assert _python_cmd() in prompt, (
            f"fallback prompt should contain python command '{_python_cmd()}'"
        )
