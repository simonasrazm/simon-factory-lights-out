#!/usr/bin/env python3
"""Simulation tests: verify validators catch obvious bad artifacts
and pass real pipeline output.

These tests use two kinds of input:
1. Real artifacts from .sflo/ (a successful Nexus Analytics pipeline run)
2. Synthetic bad artifacts that should fail the current validators.

NOTE: Content-depth checks (data_sources_real, challenge_analysis_depth,
core_functionality_depth, test_results_real, stranger_test_depth,
ac_check_depth, scope_alignment_depth) were removed from validate.py.
QA (gate 3) is the agent that evaluates quality, not validate.py.
These tests now exercise the remaining built-in checks only.
"""

import os
import shutil
import tempfile
import unittest

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.validate import validate_gate, section_body, PLACEHOLDER_PATTERN


class TestScaffoldingDecoysRejected(unittest.TestCase):
    """Synthetic bad artifacts must be caught by the current built-in checks.

    Content-depth checks (section-specific depth validators) were removed.
    These tests now verify that the remaining checks catch obvious problems.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, name, content):
        with open(os.path.join(self.tmpdir, name), "w") as f:
            f.write(content)

    # -- Gate 1: PM Discovery --

    def test_gate1_template_placeholders(self):
        """SCOPE.md with placeholder content should fail no_placeholders check."""
        self.write(
            "SCOPE.md",
            (
                "## Data Sources\n[URL]\n"
                "## Acceptance Criteria\n- [x] AC1\n"
                "## Challenge Analysis\n[TODO]\n"
                "## What We're Building\nA thing.\n" + "word " * 50
            ),
        )
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed, "Placeholder SCOPE.md should not pass")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("no_placeholders", failed_names)

    def test_gate1_empty_sections_too_short(self):
        """SCOPE.md with empty sections and too few words fails has_substance."""
        self.write(
            "SCOPE.md", ("## Acceptance Criteria\n- [x] AC1\n## Challenge Analysis\n\n")
        )
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed, "Too-short SCOPE.md should not pass")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("has_substance", failed_names)

    # -- Gate 2: Dev Build --

    def test_gate2_headings_only_no_checkmarks(self):
        """BUILD-STATUS.md with structure but no checked items fails."""
        self.write(
            "BUILD-STATUS.md",
            (
                "Build: Success\nZero errors\n"
                "## 1. Core Functionality Check\n\n"
                "## 2. Accessibility Check\n\n"
            ),
        )
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed, "BUILD-STATUS with no checked items should fail")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("has_checked_items", failed_names)

    def test_gate2_claims_success_unchecked_items(self):
        """BUILD-STATUS.md with unchecked items fails all_checks_marked."""
        self.write(
            "BUILD-STATUS.md",
            ("Build: Success\nZero errors\n- [x] done\n- [ ] not done yet\n"),
        )
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed, "Unchecked items should fail")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("all_checks_marked", failed_names)

    # -- Gate 3: QA Report --

    def test_gate3_low_grade_fails(self):
        """QA-REPORT.md with grade below threshold fails grade_sufficient."""
        self.write(
            "QA-REPORT.md",
            (
                "### Test Results\n| Test | Result |\n|------|--------|\n| Core | PASS |\n"
                "### Grade: C\n"
                "### Stranger Test\nNo.\n"
            ),
        )
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed, "Grade C should fail")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("grade_sufficient", failed_names)

    def test_gate3_inflated_grade_with_mock_data(self):
        """QA gives A but issues mention mock data — auto-fail trigger."""
        self.write(
            "QA-REPORT.md",
            (
                "### Test Results\n| Test | Result |\n|------|--------|\n| Core | PASS |\n"
                "### Grade: A\n"
                "### Issues\nUses mock data\n"
                "### Stranger Test\nYes — very useful.\n"
            ),
        )
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed, "Mock data auto-fail should trigger")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("auto_fail_mock_data", failed_names)

    # -- Gate 4: PM Verify --

    def test_gate4_rubber_stamp_with_needs_changes(self):
        """PM-VERIFY.md with NEEDS CHANGES verdict fails verdict_approved."""
        self.write(
            "PM-VERIFY.md",
            (
                "### Acceptance Criteria Check\nAll criteria met.\n"
                "### Scope Alignment\nIn scope.\n"
                "### Verdict: NEEDS CHANGES\n"
                "## Process Reflection\nWent well.\n"
            ),
        )
        passed, checks = validate_gate(4, self.tmpdir)
        self.assertFalse(passed, "NEEDS CHANGES verdict should fail")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("verdict_approved", failed_names)

    def test_gate4_missing_verdict(self):
        """PM-VERIFY.md without a verdict fails verdict_present."""
        self.write(
            "PM-VERIFY.md",
            (
                "### Acceptance Criteria Check\n- [x] AC1 met\n"
                "### Scope Alignment\nOK.\n"
                "## Process Reflection\nWent well.\n"
            ),
        )
        passed, checks = validate_gate(4, self.tmpdir)
        self.assertFalse(passed, "Missing verdict should fail")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("verdict_present", failed_names)


class TestContentDepthPassPath(unittest.TestCase):
    """Self-contained artifacts that exercise every built-in check at its boundary.

    These don't depend on .sflo/ existing — they prove the minimum viable
    artifact for each gate passes all current checks.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, name, content):
        with open(os.path.join(self.tmpdir, name), "w") as f:
            f.write(content)

    # -- Gate 1: minimum passing artifact --

    def test_gate1_minimum_viable_passes(self):
        """SCOPE.md with ACs, substance, and no placeholders passes."""
        self.write(
            "SCOPE.md",
            (
                "## Acceptance Criteria\n- [x] AC1: clicking button increments counter\n"
                "## What We're Building\nA small widget with a button.\n" + "word " * 50
            ),
        )
        passed, checks = validate_gate(1, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Minimum viable SCOPE.md should pass: {failed}")

    def test_gate1_na_without_brackets_passes(self):
        """'N/A' without brackets in prose is real content, not a placeholder."""
        self.write(
            "SCOPE.md",
            (
                "## Data Sources\nN/A — no external data required\n"
                "## Acceptance Criteria\n- [x] AC1: do things\n" + "word " * 50
            ),
        )
        passed, checks = validate_gate(1, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"N/A without brackets should pass: {failed}")

    def test_gate1_tbd_placeholder_rejected(self):
        """[TBD] alone on a line is a placeholder — fails no_placeholders."""
        self.write(
            "SCOPE.md",
            (
                "## Data Sources\n[TBD]\n"
                "## Acceptance Criteria\n- [x] AC1: do things\n" + "word " * 50
            ),
        )
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("no_placeholders", failed_names)

    def test_gate1_insert_placeholder_rejected(self):
        """[INSERT ...] is always a placeholder — fails no_placeholders."""
        self.write(
            "SCOPE.md",
            (
                "## Data Sources\n[INSERT data source here]\n"
                "## Acceptance Criteria\n- [x] AC1: do things\n" + "word " * 50
            ),
        )
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("no_placeholders", failed_names)

    def test_gate1_one_word_challenge_too_short(self):
        """SCOPE.md with too few words fails has_substance."""
        self.write("SCOPE.md", ("## Acceptance Criteria\n- [x] AC1\nDifficult\n"))
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("has_substance", failed_names)

    # -- Gate 2: minimum passing artifact --

    def test_gate2_minimum_viable_passes(self):
        """BUILD-STATUS.md with build success, checked items passes."""
        self.write(
            "BUILD-STATUS.md",
            (
                "Build: Success\nZero errors\n- [x] done\n"
                "## 1. Core Functionality Check\n- [x] works\n"
                "## 2. Accessibility Check\n- [x] accessible\n"
            ),
        )
        passed, checks = validate_gate(2, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Minimum viable BUILD-STATUS.md should pass: {failed}")

    def test_gate2_no_build_success_fails(self):
        """BUILD-STATUS.md without build success marker fails."""
        self.write("BUILD-STATUS.md", ("Output:\n- [x] done\n"))
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed)
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("build_success", failed_names)

    # -- Gate 3: minimum passing artifact and boundary cases --

    def test_gate3_minimum_viable_passes(self):
        """QA-REPORT.md with grade A passes."""
        self.write(
            "QA-REPORT.md",
            (
                "### Test Results\n| Test | Result |\n|------|--------|\n| Core | PASS |\n"
                "### Grade: A\n"
                "### Stranger Test\nYes — useful.\n"
            ),
        )
        passed, checks = validate_gate(3, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Minimum viable QA-REPORT.md should pass: {failed}")

    def test_gate3_fail_entries_dont_block_grade(self):
        """FAIL entries in test results don't block if grade is sufficient."""
        self.write(
            "QA-REPORT.md",
            (
                "### Test Results\n"
                "| Test | Result |\n|------|--------|\n"
                "| Spacing | FAIL |\n"
                "| Error states | FAIL |\n"
                "| Core render | PASS |\n"
                "### Grade: B+\n"
                "### Stranger Test\nNo — missing critical styling.\n"
            ),
        )
        passed, checks = validate_gate(3, self.tmpdir)
        # B+ meets threshold, no auto-fail triggers
        grade_check = next(c for c in checks if c["name"] == "grade_sufficient")
        self.assertTrue(grade_check["pass"], "B+ should meet threshold")

    def test_gate3_mixed_case_grade(self):
        """Grade field extraction should work with various formats."""
        self.write(
            "QA-REPORT.md",
            (
                "### Test Results\n| Test | Result |\n|------|--------|\n| Core | PASS |\n"
                "### Grade: B+\n"
                "### Stranger Test\nYes — reasonable value.\n"
            ),
        )
        passed, checks = validate_gate(3, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"B+ grade should pass: {failed}")

    def test_gate3_prose_only_still_passes_if_grade_ok(self):
        """QA with prose-only test results still passes if grade meets threshold.
        Content depth checks were removed — validate.py only checks grade."""
        self.write(
            "QA-REPORT.md",
            (
                "### Test Results\n"
                "Everything looks good. The app works well.\n"
                "### Grade: A\n"
                "### Stranger Test\nYes — would recommend.\n"
            ),
        )
        passed, checks = validate_gate(3, self.tmpdir)
        # Grade A meets threshold, no auto-fail triggers
        self.assertTrue(
            passed, "Grade A should pass even without structured test results"
        )

    # -- Gate 4: minimum passing artifact --

    def test_gate4_minimum_viable_passes(self):
        """PM-VERIFY.md with APPROVED verdict passes."""
        self.write(
            "PM-VERIFY.md",
            (
                "### Acceptance Criteria Check\n- [x] AC1 met\n"
                "### Scope Alignment\nIn scope.\n"
                "### Verdict: APPROVED\n"
                "## Process Reflection\nWent smoothly.\n"
            ),
        )
        passed, checks = validate_gate(4, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Minimum viable PM-VERIFY.md should pass: {failed}")

    def test_gate4_multiple_ac_items(self):
        """PM-VERIFY.md with multiple checked AC items passes."""
        self.write(
            "PM-VERIFY.md",
            (
                "### Acceptance Criteria Check\n"
                "- [x] AC1: Core works\n"
                "- [x] AC2: Charts render\n"
                "### Scope Alignment\nAll features within original scope.\n"
                "### Verdict: APPROVED\n"
                "## Process Reflection\nGood iteration.\n"
            ),
        )
        passed, checks = validate_gate(4, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Multi-AC PM-VERIFY.md should pass: {failed}")

    def test_gate4_one_word_scope_still_passes(self):
        """Scope Alignment with short text passes — depth checks were removed."""
        self.write(
            "PM-VERIFY.md",
            (
                "### Acceptance Criteria Check\n- [x] AC1 met\n"
                "### Scope Alignment\nAligned\n"
                "### Verdict: APPROVED\n"
                "## Process Reflection\nWent smoothly.\n"
            ),
        )
        passed, checks = validate_gate(4, self.tmpdir)
        # Only verdict checks remain for gate 4
        self.assertTrue(
            passed, "Short scope alignment should pass without depth checks"
        )

    # -- Gate 5: baseline --

    def test_gate5_minimum_viable_passes(self):
        """SHIP-DECISION.md passes with valid decision."""
        self.write(
            "SHIP-DECISION.md",
            (
                "### Pipeline Evidence\nAll gates passed.\n"
                "### Iterations\n1 iteration.\n"
                "### Decision: SHIP\n"
            ),
        )
        passed, checks = validate_gate(5, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(
            passed, f"Minimum viable SHIP-DECISION.md should pass: {failed}"
        )


class TestSectionBodyHelper(unittest.TestCase):
    """Verify section_body extracts content correctly from real-world patterns."""

    def test_extracts_between_headings(self):
        content = "## Foo\nline 1\nline 2\n## Bar\nline 3\n"
        self.assertEqual(section_body(content, "Foo"), "line 1\nline 2")

    def test_extracts_to_end_of_file(self):
        content = "## Foo\nline 1\nline 2\n"
        self.assertEqual(section_body(content, "Foo"), "line 1\nline 2")

    def test_returns_empty_for_missing_section(self):
        content = "## Other\nstuff\n"
        self.assertEqual(section_body(content, "Foo"), "")

    def test_handles_numbered_headings(self):
        content = "## 1. Core Functionality Check\n- [x] works\n## 2. Next\nstuff\n"
        body = section_body(content, r"(1\.\s*)?Core Functionality")
        self.assertEqual(body, "- [x] works")

    def test_handles_h3_headings(self):
        content = "### Stranger Test\nYes — clear value.\n### Next Section\nstuff\n"
        body = section_body(content, "Stranger Test")
        self.assertIn("clear value", body)


class TestPlaceholderPattern(unittest.TestCase):
    """Verify placeholder regex catches known template patterns."""

    def test_catches_url_placeholder(self):
        self.assertTrue(PLACEHOLDER_PATTERN.search("[URL]"))

    def test_catches_na_placeholder(self):
        self.assertTrue(PLACEHOLDER_PATTERN.search("[N/A]"))
        self.assertTrue(PLACEHOLDER_PATTERN.search("[NA]"))

    def test_catches_todo_placeholder(self):
        self.assertTrue(PLACEHOLDER_PATTERN.search("[TODO]"))

    def test_catches_tbd_placeholder(self):
        self.assertTrue(PLACEHOLDER_PATTERN.search("[TBD]"))

    def test_catches_insert_placeholder(self):
        self.assertTrue(PLACEHOLDER_PATTERN.search("[INSERT]"))

    def test_allows_real_content(self):
        self.assertIsNone(PLACEHOLDER_PATTERN.search("No external data sources needed"))

    def test_allows_none(self):
        self.assertIsNone(PLACEHOLDER_PATTERN.search("None"))

    def test_allows_na_in_prose(self):
        """'N/A' without brackets is fine — it's real content."""
        self.assertIsNone(PLACEHOLDER_PATTERN.search("N/A — no data sources required"))


if __name__ == "__main__":
    unittest.main()
