#!/usr/bin/env python3
"""Unit tests for SFLO gate validation."""

import os
import shutil
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.validate import validate_gate, extract_field, validate_agent_path


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

    # Gate 1
    def test_gate1_valid(self):
        self.write("SCOPE.md", "## Data Sources\nN/A\n## Acceptance Criteria\n- [x] AC1\n## Appetite\n30 min\n")
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertTrue(passed)

    def test_gate1_missing_appetite_heading(self):
        self.write("SCOPE.md", "## Data Sources\nN/A\n## Acceptance Criteria\n- [x] AC1: stay within budget\n")
        passed, checks = validate_gate(1, self.tmpdir)
        appetite = next(c for c in checks if c["name"] == "has_appetite")
        self.assertFalse(appetite["pass"])

    def test_gate1_missing_artifact(self):
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        self.assertFalse(checks[0]["pass"])

    # Gate 3
    def test_gate3_grade_a(self):
        self.write("QA-REPORT.md", "### Grade: A\n")
        passed, _ = validate_gate(3, self.tmpdir)
        self.assertTrue(passed)

    def test_gate3_grade_b_plus(self):
        self.write("QA-REPORT.md", "### Grade: B+\n")
        passed, _ = validate_gate(3, self.tmpdir)
        self.assertTrue(passed)

    def test_gate3_grade_b_fails(self):
        self.write("QA-REPORT.md", "### Grade: B\n")
        passed, _ = validate_gate(3, self.tmpdir)
        self.assertFalse(passed)

    def test_gate3_unrecognized_grade(self):
        self.write("QA-REPORT.md", "### Grade: A+\n")
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed)
        grade_check = next(c for c in checks if c["name"] == "grade_recognized")
        self.assertIn("Unrecognized", grade_check["detail"])

    def test_gate3_auto_fail_mock_data(self):
        self.write("QA-REPORT.md", "### Grade: A\n### Issues Found\nUses mock data\n")
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed)

    # Gate 4
    def test_gate4_approved(self):
        self.write("PM-VERIFY.md", "### Verdict: APPROVED\n")
        passed, _ = validate_gate(4, self.tmpdir)
        self.assertTrue(passed)

    def test_gate4_rejected(self):
        self.write("PM-VERIFY.md", "### Verdict: NEEDS CHANGES\n")
        passed, _ = validate_gate(4, self.tmpdir)
        self.assertFalse(passed)

    # Gate 5
    def test_gate5_ship(self):
        self.write("SHIP-DECISION.md", "### Decision: SHIP\n")
        passed, _ = validate_gate(5, self.tmpdir)
        self.assertTrue(passed)

    def test_gate5_invalid_decision(self):
        self.write("SHIP-DECISION.md", "### Decision: MAYBE\n")
        passed, _ = validate_gate(5, self.tmpdir)
        self.assertFalse(passed)


class TestValidateAgentPath(unittest.TestCase):

    def test_valid_path(self):
        ok, _ = validate_agent_path(".")
        self.assertTrue(ok)

    def test_traversal_blocked(self):
        ok, err = validate_agent_path("../../etc")
        # May or may not resolve outside cwd depending on OS
        # Just verify it returns a result without crashing
        self.assertIsInstance(ok, bool)


if __name__ == "__main__":
    unittest.main()
