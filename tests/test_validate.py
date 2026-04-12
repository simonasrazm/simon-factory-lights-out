#!/usr/bin/env python3
"""Unit tests for SFLO gate validation."""

import os
import shutil
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.validate import (
    validate_gate,
    extract_field,
    validate_agent_path,
    extract_qa_feedback,
    save_qa_feedback,
    PLACEHOLDER_PATTERN,
)


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

    # Must have at least one AC line and ≥50 words to satisfy the current
    # built-in gate 1 validator (has_acceptance_criteria, has_substance,
    # no_placeholders). Section headings are not validated by the current
    # validator — they're here as realistic shape, not as test targets.
    FULL_SCOPE = (
        "# Widget Project Scope\n\n"
        "## Summary\n"
        "This project builds a small but complete widget that the user can "
        "click to increment a visible counter. It lives in a single HTML "
        "file with inline styles and a tiny script block. No frameworks, "
        "no build step, no external dependencies. A single page, a single "
        "button, a single number.\n\n"
        "## Acceptance Criteria\n"
        "- [x] AC1: clicking the button increments the counter\n"
        "- [x] AC2: page loads without console errors\n"
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

    # Gate 1 — current validator checks: file_exists, has_acceptance_criteria
    # (≥1 `- [ ]` line), has_substance (≥50 words), no_placeholders.
    # Section-structure and content-depth checks were intentionally stripped
    # out; QA (gate 3) is the agent that evaluates quality, not validate.py.
    def test_gate1_valid(self):
        self.write("SCOPE.md", self.FULL_SCOPE)
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertTrue(passed)

    def test_gate1_missing_artifact(self):
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        self.assertFalse(checks[0]["pass"])

    def test_gate1_no_acceptance_criteria(self):
        content = "# Scope\n" + ("word " * 80)
        self.write("SCOPE.md", content)
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        ac_check = next(c for c in checks if c["name"] == "has_acceptance_criteria")
        self.assertFalse(ac_check["pass"])

    def test_gate1_too_short(self):
        content = "# Scope\n- [x] AC1: do things\n"  # only ~6 words
        self.write("SCOPE.md", content)
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        substance_check = next(c for c in checks if c["name"] == "has_substance")
        self.assertFalse(substance_check["pass"])

    # Gate 2 — current validator checks: file_exists, build_success regex,
    # all_checks_marked (no unchecked `- [ ]`), has_checked_items (≥1 `- [x]`),
    # and acs_addressed (if SCOPE.md exists alongside).
    def test_gate2_valid(self):
        self.write("BUILD-STATUS.md", self.FULL_BUILD)
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertTrue(passed)

    def test_gate2_no_checked_items(self):
        self.write("BUILD-STATUS.md", "Build: Success\nZero errors\n")
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed)
        checked_check = next(c for c in checks if c["name"] == "has_checked_items")
        self.assertFalse(checked_check["pass"])

    def test_gate2_unchecked_items_remain(self):
        self.write("BUILD-STATUS.md", "Build: Success\nZero errors\n- [ ] still todo\n- [x] done\n")
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed)
        marked_check = next(c for c in checks if c["name"] == "all_checks_marked")
        self.assertFalse(marked_check["pass"])

    def test_gate2_no_build_success(self):
        self.write("BUILD-STATUS.md", "Output:\n- [x] done\n")  # missing 'Build: Success' / 'zero errors'
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed)
        build_check = next(c for c in checks if c["name"] == "build_success")
        self.assertFalse(build_check["pass"])

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

    # Gate 3 — current validator checks grade presence, grade sufficiency,
    # and auto-fail patterns inside the Issues section. Section-presence and
    # content-depth checks (test_results_real, stranger_test_depth) were
    # removed. The remaining gate3 tests above cover grade handling.

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

    # Gate 4 — current validator checks verdict_present and verdict_approved
    # only. Section-presence and content-depth checks (ac_check_depth,
    # scope_alignment_depth) were removed.
    def test_gate4_missing_verdict(self):
        content = "### Acceptance Criteria Check\n- [x] AC1 met\n## Process Reflection\nFine.\n"
        self.write("PM-VERIFY.md", content)
        passed, checks = validate_gate(4, self.tmpdir)
        self.assertFalse(passed)
        verdict_check = next(c for c in checks if c["name"] == "verdict_present")
        self.assertFalse(verdict_check["pass"])

    # Gate 5
    def test_gate5_ship(self):
        self.write("SHIP-DECISION.md", self.FULL_SHIP)
        passed, _ = validate_gate(5, self.tmpdir)
        self.assertTrue(passed)

    def test_gate5_invalid_decision(self):
        self.write("SHIP-DECISION.md", "### Decision: MAYBE\n")
        passed, _ = validate_gate(5, self.tmpdir)
        self.assertFalse(passed)

    # Gate 5 — current validator checks decision_present and decision_valid
    # (SHIP|HOLD|KILL). Section-presence checks were removed.
    def test_gate5_missing_decision(self):
        content = "### Pipeline Evidence\nAll gates passed.\n### Iterations\n1.\n"
        self.write("SHIP-DECISION.md", content)
        passed, checks = validate_gate(5, self.tmpdir)
        self.assertFalse(passed)
        decision_check = next(c for c in checks if c["name"] == "decision_present")
        self.assertFalse(decision_check["pass"])


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


class TestPlaceholderPatternContextAware(unittest.TestCase):
    """Q1 fix: PLACEHOLDER_PATTERN must distinguish template tokens from
    bracket-wrapped prose. Apr 11 false positive on '[source]' inline broke
    a real pipeline run; this guards the regression.

    Exercises src.validate.PLACEHOLDER_PATTERN directly — no reimplementation.
    """

    # ---- prose / inline cases — MUST NOT match ----

    def test_inline_source_in_prose(self):
        """Apr 11 regression: '[source]' inline must NOT trigger."""
        text = "Every data point → [source] link to data.gov.lt API endpoint"
        self.assertIsNone(PLACEHOLDER_PATTERN.search(text))

    def test_inline_url_in_prose(self):
        text = "the [URL] field is validated by regex"
        self.assertIsNone(PLACEHOLDER_PATTERN.search(text))

    def test_inline_todo_in_prose(self):
        text = "add [TODO] comments where needed"
        self.assertIsNone(PLACEHOLDER_PATTERN.search(text))

    def test_inline_tbd_in_prose(self):
        text = "use [TBD] semantics for deferred work"
        self.assertIsNone(PLACEHOLDER_PATTERN.search(text))

    def test_inline_source_with_following_text(self):
        text = "a small [source] link affordance next to each value"
        self.assertIsNone(PLACEHOLDER_PATTERN.search(text))

    # ---- alone-on-line cases — MUST match ----

    def test_alone_on_line_url(self):
        self.assertIsNotNone(PLACEHOLDER_PATTERN.search("\n[URL]\n"))

    def test_alone_on_line_with_whitespace(self):
        self.assertIsNotNone(PLACEHOLDER_PATTERN.search("   [TBD]   \n"))

    def test_alone_on_line_in_middle_of_doc(self):
        text = "first paragraph\n[TODO]\nsecond paragraph"
        self.assertIsNotNone(PLACEHOLDER_PATTERN.search(text))

    def test_alone_on_line_source(self):
        self.assertIsNotNone(PLACEHOLDER_PATTERN.search("\n[source]\n"))

    # ---- field-label-colon cases — MUST match ----

    def test_field_label_grade_tbd(self):
        self.assertIsNotNone(PLACEHOLDER_PATTERN.search("\nGrade: [TBD]\n"))

    def test_field_label_owner_na(self):
        self.assertIsNotNone(PLACEHOLDER_PATTERN.search("Owner: [N/A]"))

    def test_field_label_no_space(self):
        self.assertIsNotNone(PLACEHOLDER_PATTERN.search("Owner:[N/A]"))

    # ---- explicit insert / placeholder forms — ALWAYS match ----

    def test_explicit_insert(self):
        self.assertIsNotNone(PLACEHOLDER_PATTERN.search("[INSERT company name here]"))

    def test_explicit_placeholder(self):
        self.assertIsNotNone(PLACEHOLDER_PATTERN.search("some [PLACEHOLDER for X] text"))

    # ---- end-to-end via validate_gate ----

    def test_validate_gate1_passes_with_inline_source_in_prose(self):
        """Full pipeline check: SCOPE.md with prose containing '[source]'
        passes the no_placeholders check inside validate_gate(1)."""
        tmpdir = tempfile.mkdtemp()
        try:
            scope = (
                "# SCOPE\n\n## ACs\n- [ ] AC1: every data point has a [source] link\n\n"
                + "word " * 60
            )
            with open(os.path.join(tmpdir, "SCOPE.md"), "w") as f:
                f.write(scope)
            _, checks = validate_gate(1, tmpdir)
            placeholder = next(c for c in checks if c["name"] == "no_placeholders")
            self.assertTrue(
                placeholder["pass"],
                f"validate_gate(1) wrongly flagged inline [source] as placeholder: {placeholder}",
            )
        finally:
            shutil.rmtree(tmpdir)

    def test_validate_gate1_fails_with_real_field_template(self):
        """SCOPE.md with a real field-label placeholder still fails the
        no_placeholders check."""
        tmpdir = tempfile.mkdtemp()
        try:
            scope = (
                "# SCOPE\n\n## ACs\n- [ ] AC1: do things\n\nOwner: [TBD]\n\n"
                + "word " * 60
            )
            with open(os.path.join(tmpdir, "SCOPE.md"), "w") as f:
                f.write(scope)
            _, checks = validate_gate(1, tmpdir)
            placeholder = next(c for c in checks if c["name"] == "no_placeholders")
            self.assertFalse(
                placeholder["pass"],
                "validate_gate(1) should flag 'Owner: [TBD]' as a real placeholder",
            )
        finally:
            shutil.rmtree(tmpdir)


if __name__ == "__main__":
    unittest.main()
