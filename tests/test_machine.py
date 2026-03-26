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


if __name__ == "__main__":
    unittest.main()
