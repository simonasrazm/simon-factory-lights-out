#!/usr/bin/env python3
"""Unit tests for SFLO state machine — compute_next and apply_transition."""

import json
import os
import unittest

import sys

# Add sflo/ to path so we can import src as a package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from conftest import TempDirMixin, PASSING_ARTIFACTS
from src.machine import (
    compute_next,
    apply_transition,
    auto_transition,
    build_context_map,
)
from src.constants import GATES


def _write_sibling_artifacts(tmpdir, gate_num, skip_artifact=None):
    info = GATES.get(gate_num)
    if not isinstance(info, list):
        return
    for entry in info:
        artifact = entry.get("artifact")
        if not artifact or artifact == skip_artifact:
            continue
        path = os.path.join(tmpdir, artifact)
        if not os.path.isfile(path):
            content = PASSING_ARTIFACTS.get(
                artifact, f"# {artifact}\n\nMinimal content.\n### Grade: A\n"
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)


class TestComputeNext(TempDirMixin, unittest.TestCase):
    """Test compute_next returns correct actions without mutating state."""

    def test_scout_state(self):
        self.write_state("scout")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(
            result["action"],
            "spawn_agent",
            "scout state should produce spawn_agent action",
        )
        self.assertEqual(
            result["agent"]["role"], "scout", "scout state should spawn scout role"
        )

    def test_gate1_state(self):
        self.write_state("gate-1")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(
            result["action"], "spawn_agent", "gate-1 should produce spawn_agent action"
        )
        self.assertEqual(result["agent"]["role"], "pm", "gate-1 should spawn pm role")

    def test_gate5_produces_artifact(self):
        self.write_state("gate-5")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(
            result["action"],
            "produce_artifact",
            "gate-5 should produce produce_artifact action",
        )
        self.assertEqual(
            result["role"], "sflo", "gate-5 artifact producer should be sflo role"
        )

    def test_done_state(self):
        self.write_state("done")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(
            result["action"],
            "pipeline_complete",
            "done state should produce pipeline_complete action",
        )

    def test_build_feedback_file_enters_rebuild_context(self):
        path = os.path.join(self.sflo_dir, "BUILD-STATUS-FEEDBACK.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Fix AC coverage.\n")

        mode, context = build_context_map(2, self.sflo_dir)

        self.assertEqual(mode, "rebuild")
        self.assertIn("BUILD-STATUS-FEEDBACK.md", context)
        self.assertIn("gate 2 found issues", context)

    def test_check_passed(self):
        self.write_state("check-3")
        self.write_artifact("QA-REPORT.md", PASSING_ARTIFACTS["QA-REPORT.md"])
        _write_sibling_artifacts(self.sflo_dir, 3, "QA-REPORT.md")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(
            result["action"],
            "validated",
            "passing QA check should produce validated action",
        )
        self.assertTrue(result["pass"], "passing QA check should set pass to True")

    def test_check_failed(self):
        self.write_state("check-3")
        self.write_artifact(
            "QA-REPORT.md",
            "### Test Results\n| T | R |\n### Grade: C\n### Stranger Test\nNo.\n",
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(
            result["action"],
            "check_failed",
            "failing QA check should produce check_failed action",
        )
        self.assertFalse(result["pass"], "failing QA check should set pass to False")

    def test_check_uses_factory_output_directory_for_deliverables(self):
        self.write_state("check-2")
        self.write_artifact(
            "SCOPE.md",
            PASSING_ARTIFACTS["SCOPE.md"]
            + "\n## Deliverables\n- `hello.txt`\n",
        )
        self.write_artifact("BUILD-STATUS.md", PASSING_ARTIFACTS["BUILD-STATUS.md"])
        state = self.read_state_file()
        state["output_dir"] = self.tmpdir

        result = compute_next(state, self.sflo_dir)

        self.assertEqual(result["action"], "check_failed")
        self.assertIn(
            "deliverable_exists:hello.txt",
            [check["name"] for check in result["checks"] if not check["pass"]],
        )

    def test_does_not_mutate_state(self):
        self.write_state("check-1")
        self.write_artifact("SCOPE.md", PASSING_ARTIFACTS["SCOPE.md"])
        state = self.read_state_file()
        original_state = state["current_state"]
        compute_next(state, self.sflo_dir)
        self.assertEqual(
            state["current_state"],
            original_state,
            "compute_next should not mutate current_state",
        )


class TestApplyTransition(TempDirMixin, unittest.TestCase):
    """Test apply_transition correctly mutates state."""

    def test_validated_advances_gate(self):
        self.write_state("check-1")
        self.write_artifact("SCOPE.md", PASSING_ARTIFACTS["SCOPE.md"])
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(
            state["current_state"],
            "gate-2",
            "validated check-1 should advance state to gate-2",
        )
        self.assertIn("next", result, "validated transition should include next key")

    def test_gate5_validated_reaches_done(self):
        self.write_state("check-5")
        self.write_artifact("SHIP-DECISION.md", PASSING_ARTIFACTS["SHIP-DECISION.md"])
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)
        self.assertEqual(
            state["current_state"],
            "done",
            "validated check-5 should advance state to done",
        )

    def test_qa_failure_loops_inner(self):
        self.write_state("check-3")
        self.write_artifact(
            "QA-REPORT.md",
            "### Test Results\n| T | R |\n### Grade: C\n### Stranger Test\nNo.\n",
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(
            result["state"],
            "loop-gate-3",
            "QA failure with retries left should use configured restart",
        )
        self.assertEqual(
            result["gate_retry_count"], 1, "QA retry count should increment"
        )
        self.assertEqual(
            state["current_state"], "gate-2", "inner loop should reset state to gate-2"
        )

    def test_qa_failure_with_gate_1_5_still_loops_to_dev(self):
        gates = {
            1: {"artifact": "SCOPE.md", "role": "pm"},
            1.5: {
                "artifact": "WORK-BREAKDOWN.md",
                "role": "decomposer",
                "on_reject_restart_at": 1.5,
            },
            2: {"artifact": "BUILD-STATUS.md", "role": "dev"},
            2.5: {
                "artifact": "STST-REPORT.md",
                "runner": "tools/stst/sflo_driver.py",
                "on_reject_restart_at": 2,
            },
            3: [{"artifact": "QA-REPORT.md", "role": "qa"}],
            4: {"artifact": "PM-VERIFY.md", "role": "pm"},
            5: {"artifact": "SHIP-DECISION.md", "role": "sflo"},
        }
        self.write_state("check-3", inner=2)
        self.write_artifact(
            "QA-REPORT.md",
            "### Test Results\n| T | R |\n### Grade: C\n### Stranger Test\nNo.\n",
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir, gates=gates)
        result = apply_transition(state, result, self.sflo_dir, gates=gates)

        self.assertEqual(result["state"], "loop-inner")
        self.assertEqual(
            state["current_state"],
            "gate-2",
            "gate 1.5 must not become the dev rebuild target",
        )

    def test_custom_restart_clears_rebuild_artifacts(self):
        gates = {
            1: {"artifact": "SCOPE.md", "role": "pm"},
            2: {"artifact": "BUILD-STATUS.md", "role": "dev"},
            2.1: {"artifact": "DEVLOOP-REPORT.md", "runner": "tools/devloop.py"},
            2.5: {
                "artifact": "STST-REPORT.md",
                "runner": "tools/stst/sflo_driver.py",
                "on_reject_restart_at": 2,
            },
            3: [{"artifact": "QA-REPORT.md", "role": "qa"}],
        }
        self.write_state("check-2.5")
        self.write_artifact("BUILD-STATUS.md", PASSING_ARTIFACTS["BUILD-STATUS.md"])
        self.write_artifact("DEVLOOP-REPORT.md", "devloop ok")
        self.write_artifact("STST-REPORT.md", "## Summary\n\nVerdict: REJECT\n")
        state = self.read_state_file()
        result = {
            "action": "check_failed",
            "gate": 2.5,
            "checks": [{"name": "stst_verdict", "pass": False}],
        }

        result = apply_transition(state, result, self.sflo_dir, gates=gates)

        self.assertEqual(result["state"], "loop-gate-2.5")
        self.assertEqual(state["current_state"], "gate-2")
        self.assertFalse(os.path.exists(os.path.join(self.sflo_dir, "BUILD-STATUS.md")))
        self.assertFalse(
            os.path.exists(os.path.join(self.sflo_dir, "DEVLOOP-REPORT.md"))
        )
        self.assertFalse(os.path.exists(os.path.join(self.sflo_dir, "STST-REPORT.md")))
        feedback_path = os.path.join(self.sflo_dir, "STST-REPORT-FEEDBACK.md")
        self.assertTrue(os.path.exists(feedback_path))
        with open(feedback_path, encoding="utf-8") as f:
            feedback = f.read()
        self.assertIn("stst_verdict", feedback)
        self.assertIn("Verdict: REJECT", feedback)

    def test_qa_failure_exhausted(self):
        self.write_state("check-3")
        self.write_artifact(
            "QA-REPORT.md",
            "### Test Results\n| T | R |\n### Grade: C\n### Stranger Test\nNo.\n",
        )
        state = self.read_state_file()
        state["gate_retries"] = {"3": 9}
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(
            result["action"],
            "ask_human",
            "QA failure at max retries should escalate",
        )
        self.assertEqual(
            state["current_state"],
            "escalate",
            "exhausted QA retries must not bypass review",
        )

    def test_pm_rejection_loops_outer(self):
        self.write_state("check-4", inner=5, outer=1)
        self.write_artifact(
            "PM-VERIFY.md",
            "### Acceptance Criteria Check\nOK\n### Scope Alignment\nOK\n### Verdict: NEEDS CHANGES\n## Process Reflection\nNeed fixes.\n",
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(
            result["state"],
            "loop-outer",
            "PM rejection with retries left should loop outer",
        )
        self.assertEqual(
            state["inner_loops"], 0, "outer loop should reset inner_loops to 0"
        )
        self.assertEqual(
            state["outer_loops"], 2, "outer loop count should increment to 2"
        )

    def test_pm_rejection_escalates(self):
        self.write_state("check-4", outer=9)
        self.write_artifact(
            "PM-VERIFY.md",
            "### Acceptance Criteria Check\nOK\n### Scope Alignment\nOK\n### Verdict: NEEDS CHANGES\n## Process Reflection\nNeed fixes.\n",
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(
            result["state"], "escalate", "PM rejection at max retries should escalate"
        )
        self.assertEqual(
            state["current_state"],
            "escalate",
            "escalated state should be written to current_state",
        )

    def test_non_check_actions_pass_through(self):
        result = {"action": "spawn_agent", "agent": {"role": "pm"}}
        state = {"current_state": "gate-1"}
        returned = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(
            returned, result, "non-check actions should pass through unchanged"
        )

    def test_pm_precise_escalation_reroutes_to_full_pm(self):
        self.write_state("check-1")
        self.write_artifact(
            "SCOPE.md",
            "## VERDICT: ESCALATE\n\nReason: architectural choice needed.\n",
        )
        state = self.read_state_file()
        state["assignments"] = {
            "pm": "/tmp/agents/pm-precise",
            "pm_fallback": "/tmp/agents/pm",
            "scope_tier": "precise",
        }
        result = {
            "action": "check_failed",
            "gate": 1,
            "checks": [{"name": "pm_precise_not_escalated", "pass": False}],
        }

        result = apply_transition(state, result, self.sflo_dir)

        self.assertEqual(result["state"], "pm-precise-escalated")
        self.assertEqual(state["current_state"], "gate-1")
        self.assertEqual(state["assignments"]["pm"], "/tmp/agents/pm")
        self.assertEqual(state["assignments"]["scope_tier"], "standard")


class TestQAFeedbackPreservation(TempDirMixin, unittest.TestCase):
    """Test that judge findings survive the inner loop for dev to use."""

    def test_qa_failure_saves_feedback(self):
        """When QA gives a low grade, feedback is saved before artifacts are archived."""
        self.write_state("check-3")
        self.write_artifact(
            "QA-REPORT.md",
            "### Test Results\n| Test | Result |\n| Spacing | FAIL |\n"
            "### Grade: C\n"
            "### Issues\n- Missing spacing scale\n- No error states\n"
            "### Stranger Test\nNo.\n",
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(
            result["state"], "loop-gate-3", "QA failure should restart Developer"
        )

        # QA-REPORT.md should be archived (moved to logs/, not at top level)
        self.assertFalse(
            os.path.isfile(os.path.join(self.sflo_dir, "QA-REPORT.md")),
            "QA-REPORT.md should be archived from top level after failure",
        )
        self.assertTrue(
            os.path.isfile(os.path.join(self.sflo_dir, "logs", "QA-REPORT.md")),
            "QA-REPORT.md should be moved to logs/ directory",
        )

        # But artifact feedback should exist with the findings (preserved in place)
        feedback_path = os.path.join(self.sflo_dir, "QA-REPORT-FEEDBACK.md")
        self.assertTrue(
            os.path.isfile(feedback_path),
            "QA-REPORT-FEEDBACK.md should be created with findings",
        )
        with open(feedback_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(
            "Missing spacing scale",
            content,
            "feedback should contain 'Missing spacing scale' finding",
        )
        self.assertIn(
            "No error states",
            content,
            "feedback should contain 'No error states' finding",
        )

    def test_qa_pass_cleans_feedback(self):
        """When QA finally passes, feedback file is removed."""
        self.write_state("check-3")
        self.write_artifact("QA-REPORT.md", PASSING_ARTIFACTS["QA-REPORT.md"])
        _write_sibling_artifacts(self.sflo_dir, 3, "QA-REPORT.md")
        self.write_artifact(
            "QA-REPORT-FEEDBACK.md", "## Feedback Round 1 — qa\n### QA Grade: C\n"
        )

        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)

        # Feedback should be cleaned up
        self.assertFalse(
            os.path.isfile(os.path.join(self.sflo_dir, "QA-REPORT-FEEDBACK.md")),
            "QA-REPORT-FEEDBACK.md should be removed after QA passes",
        )

    def test_feedback_accumulates_across_retries(self):
        """Multiple QA failures accumulate findings in artifact feedback."""
        # First failure
        self.write_state("check-3", inner=0)
        self.write_artifact(
            "QA-REPORT.md",
            "### Grade: C\n### Issues\n- Bug A\n### Test Results\n| T | R |\n### Stranger Test\nNo.\n",
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)

        # Second failure
        self.write_state("check-3", inner=1)
        self.write_artifact(
            "QA-REPORT.md",
            "### Grade: B\n### Issues\n- Bug B\n### Test Results\n| T | R |\n### Stranger Test\nNo.\n",
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)

        feedback_path = os.path.join(self.sflo_dir, "QA-REPORT-FEEDBACK.md")
        with open(feedback_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(
            "Bug A", content, "feedback should accumulate Bug A from first failure"
        )
        self.assertIn(
            "Bug B", content, "feedback should accumulate Bug B from second failure"
        )
        self.assertIn(
            "Feedback Round 1", content, "feedback should contain round 1 header"
        )
        self.assertIn(
            "Feedback Round 2", content, "feedback should contain round 2 header"
        )


class TestAutoTransition(TempDirMixin, unittest.TestCase):
    def test_transitions_when_artifact_exists(self):
        self.write_state("gate-1")
        self.write_artifact("SCOPE.md", "content")
        state = self.read_state_file()
        changed = auto_transition(state, self.sflo_dir)
        self.assertTrue(
            changed, "auto_transition should return True when artifact exists"
        )
        self.assertEqual(
            state["current_state"],
            "check-1",
            "auto_transition should advance gate-1 to check-1",
        )

    def test_no_transition_without_artifact(self):
        self.write_state("gate-1")
        state = self.read_state_file()
        changed = auto_transition(state, self.sflo_dir)
        self.assertFalse(
            changed, "auto_transition should return False without artifact"
        )
        self.assertEqual(
            state["current_state"],
            "gate-1",
            "state should remain gate-1 without artifact",
        )

    def test_no_transition_for_non_gate_state(self):
        self.write_state("done")
        state = self.read_state_file()
        changed = auto_transition(state, self.sflo_dir)
        self.assertFalse(
            changed, "auto_transition should return False for non-gate state"
        )


class TestNonLoopGateRetry(TempDirMixin, unittest.TestCase):
    """Non-loop gate failures (e.g. gate 1, 5) now retry via loop_back
    instead of immediately escalating. Escalation only happens after
    INNER_LOOP_MAX retries.

    Exercises real apply_transition + compute_next + validate_gate.
    """

    def _gate1_failure_state(self):
        """Set up state at check-1 with a SCOPE.md that will fail validation
        because of a real (field-label form) placeholder."""
        scope = (
            "# SCOPE\n\n## ACs\n- [ ] AC1: do things\n\nOwner: [TBD]\n\n" + "word " * 60
        )
        self.write_artifact("SCOPE.md", scope)
        self.write_state("check-1")
        return self.read_state_file()

    def test_compute_next_returns_check_failed(self):
        state = self._gate1_failure_state()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(
            result["action"],
            "check_failed",
            "gate-1 with placeholder should produce check_failed",
        )
        self.assertEqual(
            result["gate"], 1, "check_failed result should reference gate 1"
        )
        self.assertFalse(
            result["pass"], "gate-1 with placeholder should fail validation"
        )

    def test_first_failure_retries_with_loop_back(self):
        """First gate-1 failure should loop_back (retry), not escalate."""
        state = self._gate1_failure_state()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)

        self.assertEqual(
            result["action"],
            "loop_back",
            "first gate-1 failure should loop back, not escalate",
        )
        self.assertEqual(
            result["gate_retry_count"],
            1,
            "gate retry count should be 1 after first failure",
        )
        on_disk = self.read_state_file()
        self.assertEqual(
            on_disk["current_state"], "gate-1", "loop_back should reset state to gate-1"
        )
        self.assertEqual(
            on_disk["gates"]["1"]["status"],
            "pending",
            "loop_back should reset gate-1 status to pending",
        )

    def test_retries_exhaust_then_escalate(self):
        """After INNER_LOOP_MAX retries, gate-1 failure escalates to ask_human."""
        from src.constants import INNER_LOOP_MAX

        state = self._gate1_failure_state()
        # Pre-set gate_retries to just below the limit
        state["gate_retries"] = {"1": INNER_LOOP_MAX - 1}
        with open(
            os.path.join(self.sflo_dir, "state.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(state, f)
        state = self.read_state_file()

        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)

        self.assertEqual(
            result["action"],
            "ask_human",
            "exhausted retries should produce ask_human action",
        )
        on_disk = self.read_state_file()
        self.assertEqual(
            on_disk["current_state"],
            "escalate",
            "exhausted retries should set state to escalate",
        )
        self.assertIn(
            "escalate_reason", on_disk, "escalated state should contain escalate_reason"
        )
        self.assertIn(
            "SCOPE.md",
            on_disk["escalate_reason"],
            "escalate_reason should reference SCOPE.md",
        )
        self.assertIn(
            "escalate_options",
            on_disk,
            "escalated state should contain escalate_options",
        )
        self.assertGreaterEqual(
            len(on_disk["escalate_options"]),
            1,
            "escalate_options should have at least one option",
        )
        self.assertIn(
            "escalate_failed_checks",
            on_disk,
            "escalated state should contain escalate_failed_checks",
        )
        self.assertGreaterEqual(
            len(on_disk["escalate_failed_checks"]),
            1,
            "escalate_failed_checks should have at least one entry",
        )

    def test_escalated_state_returns_stored_reason(self):
        """Once escalated, compute_next on escalate state returns the gate-specific reason."""
        from src.constants import INNER_LOOP_MAX

        state = self._gate1_failure_state()
        state["gate_retries"] = {"1": INNER_LOOP_MAX - 1}
        with open(
            os.path.join(self.sflo_dir, "state.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(state, f)
        state = self.read_state_file()

        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)

        state2 = self.read_state_file()
        result2 = compute_next(state2, self.sflo_dir)

        self.assertEqual(
            result2["action"],
            "ask_human",
            "escalated state should return ask_human on re-read",
        )
        self.assertIn(
            "SCOPE.md",
            result2["reason"],
            "escalated reason should reference the failing artifact",
        )
        self.assertNotIn(
            "PM rejected",
            result2["reason"],
            "gate-specific escalation should not use PM rejection message",
        )

    def test_compute_next_on_escalate_falls_back_when_no_stored_reason(self):
        """Backwards compat: if state.escalate_reason is missing (old
        state.json without our new fields), compute_next still returns the
        outer-loop PM-rejection default."""
        self.write_state("escalate")
        state = self.read_state_file()
        for key in ("escalate_reason", "escalate_options", "escalate_failed_checks"):
            state.pop(key, None)
        with open(
            os.path.join(self.sflo_dir, "state.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(state, f)
        state = self.read_state_file()

        result = compute_next(state, self.sflo_dir)
        self.assertEqual(
            result["action"],
            "ask_human",
            "escalate state without stored reason should still return ask_human",
        )
        self.assertIn(
            "PM rejected",
            result["reason"],
            "missing escalate_reason should fall back to PM rejection default",
        )

    def test_gate1_failure_does_not_silent_spin(self):
        """Regression guard: apply_transition on a gate-1 check_failed must
        mutate state (change current_state or gate status)."""
        state = self._gate1_failure_state()
        state["current_state"]

        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)
        state_after = self.read_state_file()

        # State MUST have changed — either current_state looped back to gate-1
        # with gate status reset to pending, or escalated
        # In retry case, current_state stays gate-1 but gate status changes to pending
        self.assertEqual(
            state_after["gates"]["1"]["status"],
            "pending",
            "apply_transition failed to reset gate 1 status — would cause silent spin",
        )


class TestResolveAgentPath(unittest.TestCase):
    """Test _resolve_agent_path priority chain including agents: plural."""

    def setUp(self):
        from src.machine import _resolve_agent_path

        self.resolve = _resolve_agent_path
        self.sflo_base = "/fake/sflo"

    def test_singular_agent_wins(self):
        """agent: (singular) takes highest priority."""
        entry = {"role": "qa", "agent": "agents/custom-qa"}
        result = self.resolve(entry, self.sflo_base, {}, {})
        # Build expected with normpath(join(...)) so the separator matches the
        # platform — _resolve_agent_path normalizes the joined result, which
        # yields backslashes on Windows.
        self.assertEqual(
            result,
            os.path.normpath(os.path.join(self.sflo_base, "agents", "custom-qa")),
        )

    def test_agents_plural_first_entry(self):
        """agents: list uses first entry as primary when no singular agent:."""
        entry = {
            "role": "qa",
            "agents": ["agents/qa-combo", "vendor/x/agents/reviewer"],
        }
        result = self.resolve(entry, self.sflo_base, {}, {})
        self.assertEqual(
            result,
            os.path.normpath(os.path.join(self.sflo_base, "agents", "qa-combo")),
        )

    def test_singular_takes_precedence_over_plural(self):
        """agent: (singular) wins even when agents: (plural) also present."""
        entry = {
            "role": "qa",
            "agent": "agents/explicit",
            "agents": ["agents/from-list", "agents/other"],
        }
        result = self.resolve(entry, self.sflo_base, {}, {})
        self.assertEqual(
            result,
            os.path.normpath(os.path.join(self.sflo_base, "agents", "explicit")),
        )

    def test_empty_agents_list_falls_through(self):
        """Empty agents: [] should fall through to role config / scout."""
        entry = {"role": "qa", "agents": []}
        assignments = {"qa": "/assigned/by/scout"}
        result = self.resolve(entry, self.sflo_base, {}, assignments)
        self.assertEqual(result, "/assigned/by/scout")

    def test_no_agent_fields_uses_scout_assignment(self):
        """No agent: or agents: → scout assignment."""
        entry = {"role": "dev"}
        assignments = {"dev": "/scout/picked/this"}
        result = self.resolve(entry, self.sflo_base, {}, assignments)
        self.assertEqual(result, "/scout/picked/this")

    def test_no_agent_no_scout_convention_default(self):
        """No agent:, no agents:, no scout → convention: sflo_base/agents/<role>."""
        entry = {"role": "pm"}
        result = self.resolve(entry, self.sflo_base, {}, {})
        self.assertEqual(
            result, os.path.normpath(os.path.join(self.sflo_base, "agents", "pm"))
        )

    def test_absolute_agents_path_preserved(self):
        """Absolute path in agents: list is not joined with sflo_base."""
        absolute_agent = os.path.abspath(
            os.path.join(os.sep, "abs", "path", "to", "agent")
        )
        entry = {"role": "qa", "agents": [absolute_agent]}
        result = self.resolve(entry, self.sflo_base, {}, {})
        self.assertEqual(result, absolute_agent)


class TestRolesWithExplicitAgents(unittest.TestCase):
    """Test _roles_with_explicit_agents extracts pre-assigned roles from gates config."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.runner import _roles_with_explicit_agents

        self.extract = _roles_with_explicit_agents

    def test_singular_agent_detected(self):
        """Gate with agent: (singular) marks role as pre-assigned."""
        gates = {1: {"role": "dev", "agent": "agents/dev", "artifact": "X.md"}}
        self.assertEqual(self.extract(gates), {"dev"})

    def test_plural_agents_detected(self):
        """Gate with agents: (plural list) marks role as pre-assigned."""
        gates = {
            3: [
                {"role": "qa", "agents": ["agents/qa-combo"], "artifact": "QA.md"},
                {"role": "security", "artifact": "SEC.md"},
            ]
        }
        self.assertEqual(self.extract(gates), {"qa"})

    def test_no_agents_not_detected(self):
        """Gate without agent:/agents: is NOT pre-assigned."""
        gates = {1: {"role": "pm", "artifact": "SCOPE.md"}}
        self.assertEqual(self.extract(gates), set())

    def test_mixed_gates(self):
        """Mix of pre-assigned and discoverable roles."""
        gates = {
            1: {"role": "pm", "artifact": "SCOPE.md"},
            2: {"role": "dev", "agent": "agents/dev", "artifact": "BUILD.md"},
            3: [
                {"role": "qa", "agents": ["agents/qa-h17"], "artifact": "QA.md"},
                {"role": "security", "artifact": "SEC.md"},
            ],
        }
        self.assertEqual(self.extract(gates), {"dev", "qa"})


if __name__ == "__main__":
    unittest.main()
