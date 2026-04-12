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
from src.machine import compute_next, apply_transition, auto_transition


class TestComputeNext(TempDirMixin, unittest.TestCase):
    """Test compute_next returns correct actions without mutating state."""

    def test_scout_state(self):
        self.write_state("scout")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(result["action"], "spawn_agent")
        self.assertEqual(result["agent"]["role"], "scout")

    def test_gate1_state(self):
        self.write_state("gate-1")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(result["action"], "spawn_agent")
        self.assertEqual(result["agent"]["role"], "pm")

    def test_gate5_produces_artifact(self):
        self.write_state("gate-5")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(result["action"], "produce_artifact")
        self.assertEqual(result["role"], "sflo")

    def test_done_state(self):
        self.write_state("done")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(result["action"], "pipeline_complete")

    def test_check_passed(self):
        self.write_state("check-3")
        self.write_artifact("QA-REPORT.md", PASSING_ARTIFACTS["QA-REPORT.md"])
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(result["action"], "validated")
        self.assertTrue(result["pass"])

    def test_check_failed(self):
        self.write_state("check-3")
        self.write_artifact("QA-REPORT.md", "### Test Results\n| T | R |\n### Grade: C\n### Stranger Test\nNo.\n")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(result["action"], "check_failed")
        self.assertFalse(result["pass"])

    def test_does_not_mutate_state(self):
        self.write_state("check-1")
        self.write_artifact("SCOPE.md", PASSING_ARTIFACTS["SCOPE.md"])
        state = self.read_state_file()
        original_state = state["current_state"]
        compute_next(state, self.sflo_dir)
        self.assertEqual(state["current_state"], original_state)


class TestApplyTransition(TempDirMixin, unittest.TestCase):
    """Test apply_transition correctly mutates state."""

    def test_validated_advances_gate(self):
        self.write_state("check-1")
        self.write_artifact("SCOPE.md", PASSING_ARTIFACTS["SCOPE.md"])
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(state["current_state"], "gate-2")
        self.assertIn("next", result)

    def test_gate5_validated_reaches_done(self):
        self.write_state("check-5")
        self.write_artifact("SHIP-DECISION.md", PASSING_ARTIFACTS["SHIP-DECISION.md"])
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)
        self.assertEqual(state["current_state"], "done")

    def test_qa_failure_loops_inner(self):
        self.write_state("check-3", inner=2)
        self.write_artifact("QA-REPORT.md", "### Test Results\n| T | R |\n### Grade: C\n### Stranger Test\nNo.\n")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(result["state"], "loop-inner")
        self.assertEqual(result["inner_count"], 3)
        self.assertEqual(state["current_state"], "gate-2")

    def test_qa_failure_exhausted(self):
        self.write_state("check-3", inner=9)
        self.write_artifact("QA-REPORT.md", "### Test Results\n| T | R |\n### Grade: C\n### Stranger Test\nNo.\n")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(result["state"], "loop-inner-exhausted")
        self.assertEqual(state["current_state"], "gate-4")

    def test_pm_rejection_loops_outer(self):
        self.write_state("check-4", inner=5, outer=1)
        self.write_artifact("PM-VERIFY.md", "### Acceptance Criteria Check\nOK\n### Scope Alignment\nOK\n### Verdict: NEEDS CHANGES\n## Process Reflection\nNeed fixes.\n")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(result["state"], "loop-outer")
        self.assertEqual(state["inner_loops"], 0)
        self.assertEqual(state["outer_loops"], 2)

    def test_pm_rejection_escalates(self):
        self.write_state("check-4", outer=9)
        self.write_artifact("PM-VERIFY.md", "### Acceptance Criteria Check\nOK\n### Scope Alignment\nOK\n### Verdict: NEEDS CHANGES\n## Process Reflection\nNeed fixes.\n")
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(result["state"], "escalate")
        self.assertEqual(state["current_state"], "escalate")

    def test_non_check_actions_pass_through(self):
        result = {"action": "spawn_agent", "agent": {"role": "pm"}}
        state = {"current_state": "gate-1"}
        returned = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(returned, result)


class TestQAFeedbackPreservation(TempDirMixin, unittest.TestCase):
    """Test that QA findings survive the inner loop for dev to use."""

    def test_qa_failure_saves_feedback(self):
        """When QA gives a low grade, feedback is saved before artifacts are archived."""
        self.write_state("check-3", inner=0)
        self.write_artifact("QA-REPORT.md",
            "### Test Results\n| Test | Result |\n| Spacing | FAIL |\n"
            "### Grade: C\n"
            "### Issues\n- Missing spacing scale\n- No error states\n"
            "### Stranger Test\nNo.\n"
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)
        self.assertEqual(result["state"], "loop-inner")

        # QA-REPORT.md should be archived (moved to logs/, not at top level)
        self.assertFalse(os.path.isfile(os.path.join(self.sflo_dir, "QA-REPORT.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.sflo_dir, "logs", "QA-REPORT.md")))

        # But QA-FEEDBACK.md should exist with the findings (preserved in place)
        feedback_path = os.path.join(self.sflo_dir, "QA-FEEDBACK.md")
        self.assertTrue(os.path.isfile(feedback_path))
        with open(feedback_path) as f:
            content = f.read()
        self.assertIn("Missing spacing scale", content)
        self.assertIn("No error states", content)

    def test_qa_pass_cleans_feedback(self):
        """When QA finally passes, feedback file is removed."""
        self.write_state("check-3")
        self.write_artifact("QA-REPORT.md", PASSING_ARTIFACTS["QA-REPORT.md"])
        # Simulate leftover feedback from prior failed round
        self.write_artifact("QA-FEEDBACK.md", "## QA Round 1\n### QA Grade: C\n")

        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)

        # Feedback should be cleaned up
        self.assertFalse(os.path.isfile(os.path.join(self.sflo_dir, "QA-FEEDBACK.md")))

    def test_feedback_accumulates_across_retries(self):
        """Multiple QA failures accumulate findings in QA-FEEDBACK.md."""
        # First failure
        self.write_state("check-3", inner=0)
        self.write_artifact("QA-REPORT.md",
            "### Grade: C\n### Issues\n- Bug A\n### Test Results\n| T | R |\n### Stranger Test\nNo.\n"
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)

        # Second failure
        self.write_state("check-3", inner=1)
        self.write_artifact("QA-REPORT.md",
            "### Grade: B\n### Issues\n- Bug B\n### Test Results\n| T | R |\n### Stranger Test\nNo.\n"
        )
        state = self.read_state_file()
        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)

        feedback_path = os.path.join(self.sflo_dir, "QA-FEEDBACK.md")
        with open(feedback_path) as f:
            content = f.read()
        self.assertIn("Bug A", content)
        self.assertIn("Bug B", content)
        self.assertIn("QA Round 1", content)
        self.assertIn("QA Round 2", content)


class TestAutoTransition(TempDirMixin, unittest.TestCase):

    def test_transitions_when_artifact_exists(self):
        self.write_state("gate-1")
        self.write_artifact("SCOPE.md", "content")
        state = self.read_state_file()
        changed = auto_transition(state, self.sflo_dir)
        self.assertTrue(changed)
        self.assertEqual(state["current_state"], "check-1")

    def test_no_transition_without_artifact(self):
        self.write_state("gate-1")
        state = self.read_state_file()
        changed = auto_transition(state, self.sflo_dir)
        self.assertFalse(changed)
        self.assertEqual(state["current_state"], "gate-1")

    def test_no_transition_for_non_gate_state(self):
        self.write_state("done")
        state = self.read_state_file()
        changed = auto_transition(state, self.sflo_dir)
        self.assertFalse(changed)


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
            "# SCOPE\n\n## ACs\n- [ ] AC1: do things\n\nOwner: [TBD]\n\n"
            + "word " * 60
        )
        self.write_artifact("SCOPE.md", scope)
        self.write_state("check-1")
        return self.read_state_file()

    def test_compute_next_returns_check_failed(self):
        state = self._gate1_failure_state()
        result = compute_next(state, self.sflo_dir)
        self.assertEqual(result["action"], "check_failed")
        self.assertEqual(result["gate"], 1)
        self.assertFalse(result["pass"])

    def test_first_failure_retries_with_loop_back(self):
        """First gate-1 failure should loop_back (retry), not escalate."""
        state = self._gate1_failure_state()
        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)

        self.assertEqual(result["action"], "loop_back")
        self.assertEqual(result["gate_retry_count"], 1)
        on_disk = self.read_state_file()
        self.assertEqual(on_disk["current_state"], "gate-1")
        self.assertEqual(on_disk["gates"]["1"]["status"], "pending")

    def test_retries_exhaust_then_escalate(self):
        """After INNER_LOOP_MAX retries, gate-1 failure escalates to ask_human."""
        from src.constants import INNER_LOOP_MAX

        state = self._gate1_failure_state()
        # Pre-set gate_retries to just below the limit
        state["gate_retries"] = {"1": INNER_LOOP_MAX - 1}
        with open(os.path.join(self.sflo_dir, "state.json"), "w") as f:
            json.dump(state, f)
        state = self.read_state_file()

        result = compute_next(state, self.sflo_dir)
        result = apply_transition(state, result, self.sflo_dir)

        self.assertEqual(result["action"], "ask_human")
        on_disk = self.read_state_file()
        self.assertEqual(on_disk["current_state"], "escalate")
        self.assertIn("escalate_reason", on_disk)
        self.assertIn("SCOPE.md", on_disk["escalate_reason"])
        self.assertIn("escalate_options", on_disk)
        self.assertGreaterEqual(len(on_disk["escalate_options"]), 1)
        self.assertIn("escalate_failed_checks", on_disk)
        self.assertGreaterEqual(len(on_disk["escalate_failed_checks"]), 1)

    def test_escalated_state_returns_stored_reason(self):
        """Once escalated, compute_next on escalate state returns the gate-specific reason."""
        from src.constants import INNER_LOOP_MAX

        state = self._gate1_failure_state()
        state["gate_retries"] = {"1": INNER_LOOP_MAX - 1}
        with open(os.path.join(self.sflo_dir, "state.json"), "w") as f:
            json.dump(state, f)
        state = self.read_state_file()

        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)

        state2 = self.read_state_file()
        result2 = compute_next(state2, self.sflo_dir)

        self.assertEqual(result2["action"], "ask_human")
        self.assertIn("SCOPE.md", result2["reason"])
        self.assertNotIn("PM rejected", result2["reason"])

    def test_compute_next_on_escalate_falls_back_when_no_stored_reason(self):
        """Backwards compat: if state.escalate_reason is missing (old
        state.json without our new fields), compute_next still returns the
        outer-loop PM-rejection default."""
        self.write_state("escalate")
        state = self.read_state_file()
        for key in ("escalate_reason", "escalate_options", "escalate_failed_checks"):
            state.pop(key, None)
        with open(os.path.join(self.sflo_dir, "state.json"), "w") as f:
            json.dump(state, f)
        state = self.read_state_file()

        result = compute_next(state, self.sflo_dir)
        self.assertEqual(result["action"], "ask_human")
        self.assertIn("PM rejected", result["reason"])

    def test_gate1_failure_does_not_silent_spin(self):
        """Regression guard: apply_transition on a gate-1 check_failed must
        mutate state (change current_state or gate status)."""
        state = self._gate1_failure_state()
        pre_current = state["current_state"]

        result = compute_next(state, self.sflo_dir)
        apply_transition(state, result, self.sflo_dir)
        state_after = self.read_state_file()

        # State MUST have changed — either current_state looped back to gate-1
        # with gate status reset to pending, or escalated
        # In retry case, current_state stays gate-1 but gate status changes to pending
        self.assertEqual(state_after["gates"]["1"]["status"], "pending",
            "apply_transition failed to reset gate 1 status — would cause silent spin")


if __name__ == "__main__":
    unittest.main()
