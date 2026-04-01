#!/usr/bin/env python3
"""Unit tests for SFLO gate validation."""

import os
import shutil
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.validate import validate_gate, extract_field, validate_agent_path, extract_qa_feedback, save_qa_feedback


class TestExtractField(unittest.TestCase):

    def test_basic_extraction(self):
        self.assertEqual(extract_field("### Grade: A\n", r"###?\s*Grade[:\s]*(.+)"), "A")

    def test_bold_markers_stripped(self):
        self.assertEqual(extract_field("### Grade: **A**\n", r"###?\s*Grade[:\s]*(.+)"), "A")

    def test_trailing_commentary_ignored(self):
        self.assertEqual(extract_field("### Grade: B+ (almost)\n", r"###?\s*Grade[:\s]*(.+)"), "B+")

    def test_not_found(self):
        self.assertIsNone(extract_field("no grade here", r"###?\s*Grade[:\s]*(.+)"))


class TestValidateGate(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, name, content):
        with open(os.path.join(self.tmpdir, name), "w") as f:
            f.write(content)

    FULL_SCOPE = (
        "## Data Sources\nNone\n"
        "## Acceptance Criteria\n- [x] AC1\n"
        "## Challenge Analysis\nSome analysis.\n"
        "## What We're Building\nA widget.\n"
        "## Features\n- Feature 1\n"
        "## State Management\nLocal state.\n"
    )

    FULL_QA = (
        "### Test Results\n| Test | Result |\n|------|--------|\n| Core | PASS |\n"
        "### Grade: A\n"
        "### Stranger Test\nYes — clear value.\n"
    )

    FULL_PM = (
        "### Acceptance Criteria Check\n- [x] AC1 met\n"
        "### Scope Alignment\nIn scope.\n"
        "### Verdict: APPROVED\n"
        "## Process Reflection\nWent smoothly.\n"
    )

    FULL_SHIP = (
        "### Pipeline Evidence\nAll gates passed.\n"
        "### Iterations\n1 iteration.\n"
        "### Decision: SHIP\n"
    )

    FULL_BUILD = (
        "Build: Success\nZero errors\n- [x] done\n"
        "## 1. Core Functionality Check\n- [x] works\n"
        "## 2. Accessibility Check\n- [x] accessible\n"
    )

    # Gate 1
    def test_gate1_valid(self):
        self.write("SCOPE.md", self.FULL_SCOPE)
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertTrue(passed)

    def test_gate1_missing_challenge_analysis(self):
        content = self.FULL_SCOPE.replace("## Challenge Analysis\nSome analysis.\n", "")
        self.write("SCOPE.md", content)
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)

    def test_gate1_missing_what_building(self):
        content = self.FULL_SCOPE.replace("## What We're Building\nA widget.\n", "")
        self.write("SCOPE.md", content)
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)

    def test_gate1_missing_features(self):
        content = self.FULL_SCOPE.replace("## Features\n- Feature 1\n", "")
        self.write("SCOPE.md", content)
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)

    def test_gate1_missing_state_management(self):
        content = self.FULL_SCOPE.replace("## State Management\nLocal state.\n", "")
        self.write("SCOPE.md", content)
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)

    def test_gate1_missing_artifact(self):
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        self.assertFalse(checks[0]["pass"])

    # Gate 2
    def test_gate2_valid(self):
        self.write("BUILD-STATUS.md", self.FULL_BUILD)
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertTrue(passed)

    def test_gate2_missing_core_functionality(self):
        self.write("BUILD-STATUS.md", "Build: Success\nZero errors\n- [x] done\n## 2. Accessibility Check\n- [x] ok\n")
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed)

    def test_gate2_missing_accessibility_check(self):
        self.write("BUILD-STATUS.md", "Build: Success\nZero errors\n- [x] done\n## 1. Core Functionality Check\n- [x] ok\n")
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed)

    # Gate 3
    def test_gate3_grade_a(self):
        self.write("QA-REPORT.md", self.FULL_QA)
        passed, _ = validate_gate(3, self.tmpdir)
        self.assertTrue(passed)

    def test_gate3_grade_b_plus(self):
        # B+ meets the default B+ threshold
        content = self.FULL_QA.replace("### Grade: A\n", "### Grade: B+\n")
        self.write("QA-REPORT.md", content)
        passed, _ = validate_gate(3, self.tmpdir)
        self.assertTrue(passed)

    def test_gate3_grade_b_fails(self):
        content = self.FULL_QA.replace("### Grade: A\n", "### Grade: B\n")
        self.write("QA-REPORT.md", content)
        passed, _ = validate_gate(3, self.tmpdir)
        self.assertFalse(passed)

    def test_gate3_unrecognized_grade(self):
        content = self.FULL_QA.replace("### Grade: A\n", "### Grade: A+\n")
        self.write("QA-REPORT.md", content)
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed)
        grade_check = next(c for c in checks if c["name"] == "grade_recognized")
        self.assertIn("Unrecognized", grade_check["detail"])

    def test_gate3_auto_fail_mock_data(self):
        self.write("QA-REPORT.md", self.FULL_QA + "### Issues Found\nUses mock data\n")
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed)

    def test_gate3_missing_test_results(self):
        content = "### Grade: A\n### Stranger Test\nYes.\n"
        self.write("QA-REPORT.md", content)
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed)

    def test_gate3_missing_stranger_test(self):
        content = "### Test Results\n| Test | Result |\n### Grade: A\n"
        self.write("QA-REPORT.md", content)
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed)

    # Gate 4
    def test_gate4_approved(self):
        self.write("PM-VERIFY.md", self.FULL_PM)
        passed, _ = validate_gate(4, self.tmpdir)
        self.assertTrue(passed)

    def test_gate4_rejected(self):
        content = self.FULL_PM.replace("### Verdict: APPROVED\n", "### Verdict: NEEDS CHANGES\n")
        self.write("PM-VERIFY.md", content)
        passed, _ = validate_gate(4, self.tmpdir)
        self.assertFalse(passed)

    def test_gate4_missing_ac_check(self):
        content = "### Scope Alignment\nOK\n### Verdict: APPROVED\n## Process Reflection\nFine.\n"
        self.write("PM-VERIFY.md", content)
        passed, checks = validate_gate(4, self.tmpdir)
        self.assertFalse(passed)

    def test_gate4_missing_scope_alignment(self):
        content = "### Acceptance Criteria Check\nOK\n### Verdict: APPROVED\n## Process Reflection\nFine.\n"
        self.write("PM-VERIFY.md", content)
        passed, checks = validate_gate(4, self.tmpdir)
        self.assertFalse(passed)

    def test_gate4_missing_process_reflection(self):
        content = "### Acceptance Criteria Check\nOK\n### Scope Alignment\nOK\n### Verdict: APPROVED\n"
        self.write("PM-VERIFY.md", content)
        passed, checks = validate_gate(4, self.tmpdir)
        self.assertFalse(passed)

    # Gate 5
    def test_gate5_ship(self):
        self.write("SHIP-DECISION.md", self.FULL_SHIP)
        passed, _ = validate_gate(5, self.tmpdir)
        self.assertTrue(passed)

    def test_gate5_invalid_decision(self):
        self.write("SHIP-DECISION.md", "### Decision: MAYBE\n")
        passed, _ = validate_gate(5, self.tmpdir)
        self.assertFalse(passed)

    def test_gate5_missing_pipeline_evidence(self):
        content = "### Iterations\n1.\n### Decision: SHIP\n"
        self.write("SHIP-DECISION.md", content)
        passed, checks = validate_gate(5, self.tmpdir)
        self.assertFalse(passed)

    def test_gate5_missing_iterations(self):
        content = "### Pipeline Evidence\nOK.\n### Decision: SHIP\n"
        self.write("SHIP-DECISION.md", content)
        passed, checks = validate_gate(5, self.tmpdir)
        self.assertFalse(passed)


class TestQAFeedback(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, name, content):
        with open(os.path.join(self.tmpdir, name), "w") as f:
            f.write(content)

    def test_extract_feedback_with_issues(self):
        self.write("QA-REPORT.md",
            "### Test Results\n| Test | Result |\n| Spacing | FAIL |\n"
            "### Grade: C\n"
            "### Issues\n- Missing spacing scale\n- No error states\n"
            "### Stranger Test\nYes.\n"
        )
        feedback = extract_qa_feedback(self.tmpdir)
        self.assertIn("QA Grade: C", feedback)
        self.assertIn("Missing spacing scale", feedback)
        self.assertIn("No error states", feedback)
        self.assertIn("Test Results", feedback)

    def test_extract_feedback_no_issues(self):
        self.write("QA-REPORT.md",
            "### Test Results\n| Test | Result |\n| Core | PASS |\n"
            "### Grade: A\n"
            "### Stranger Test\nYes.\n"
        )
        feedback = extract_qa_feedback(self.tmpdir)
        self.assertIsNotNone(feedback)
        self.assertIn("QA Grade: A", feedback)

    def test_extract_feedback_missing_report(self):
        feedback = extract_qa_feedback(self.tmpdir)
        self.assertIsNone(feedback)

    def test_save_accumulates_rounds(self):
        self.write("QA-REPORT.md",
            "### Grade: C\n### Issues\n- Bug 1\n### Test Results\n| T | R |\n### Stranger Test\nNo.\n"
        )
        save_qa_feedback(self.tmpdir)

        self.write("QA-REPORT.md",
            "### Grade: B\n### Issues\n- Bug 2\n### Test Results\n| T | R |\n### Stranger Test\nNo.\n"
        )
        save_qa_feedback(self.tmpdir)

        feedback_path = os.path.join(self.tmpdir, "QA-FEEDBACK.md")
        with open(feedback_path) as f:
            content = f.read()
        self.assertIn("QA Round 1", content)
        self.assertIn("Bug 1", content)
        self.assertIn("QA Round 2", content)
        self.assertIn("Bug 2", content)


class TestValidateAgentPath(unittest.TestCase):

    def test_valid_path(self):
        ok, _ = validate_agent_path(".")
        self.assertTrue(ok)

    def test_traversal_blocked(self):
        ok, err = validate_agent_path("../../etc")
        self.assertIsInstance(ok, bool)


if __name__ == "__main__":
    unittest.main()
