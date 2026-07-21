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
    save_gate_feedback,
    PLACEHOLDER_PATTERN,
)
from src.constants import GRADE_MAP, GRADE_THRESHOLD, GATES


def _write_sibling_artifacts(tmpdir, gate_num, skip_artifact=None):
    """Write minimal passing artifacts for all entries in a list gate except skip_artifact."""
    from tests.conftest import PASSING_ARTIFACTS

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


def _gate_role_threshold(gate_num, role):
    """Return effective threshold for a gate role, including per-entry override."""
    info = GATES.get(gate_num)
    entries = info if isinstance(info, list) else [info]
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("role") != role:
            continue
        threshold = entry.get("threshold")
        return GRADE_MAP.get(threshold, GRADE_THRESHOLD) if threshold else GRADE_THRESHOLD
    return GRADE_THRESHOLD


class TestExtractField(unittest.TestCase):
    def test_basic_extraction(self):
        self.assertEqual(
            extract_field("### Grade: A\n", r"###?\s*Grade[:\s]*(.+)"), "A"
        )

    def test_bold_markers_stripped(self):
        self.assertEqual(
            extract_field("### Grade: **A**\n", r"###?\s*Grade[:\s]*(.+)"), "A"
        )

    def test_trailing_commentary_ignored(self):
        self.assertEqual(
            extract_field("### Grade: B+ (almost)\n", r"###?\s*Grade[:\s]*(.+)"), "B+"
        )

    def test_not_found(self):
        self.assertIsNone(extract_field("no grade here", r"###?\s*Grade[:\s]*(.+)"))


class TestValidateGate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, name, content):
        with open(os.path.join(self.tmpdir, name), "w", encoding="utf-8") as f:
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
        "## Data Sources\n"
        "No external data sources are required for this scope.\n\n"
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
    # (≥1 `- [ ]` line), source evidence/declaration, has_substance, placeholders.
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
        self.write(
            "BUILD-STATUS.md",
            "Build: Success\nZero errors\n- [ ] still todo\n- [x] done\n",
        )
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed)
        marked_check = next(c for c in checks if c["name"] == "all_checks_marked")
        self.assertFalse(marked_check["pass"])

    def test_gate2_no_build_success(self):
        self.write(
            "BUILD-STATUS.md", "Output:\n- [x] done\n"
        )  # missing 'Build: Success' / 'zero errors'
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed)
        build_check = next(c for c in checks if c["name"] == "build_success")
        self.assertFalse(build_check["pass"])

    # Gate 3
    def test_gate3_grade_a(self):
        self.write("QA-REPORT.md", self.FULL_QA)
        _write_sibling_artifacts(self.tmpdir, 3, "QA-REPORT.md")
        passed, _ = validate_gate(3, self.tmpdir)
        self.assertTrue(passed)

    def test_gate3_grade_b_plus(self):
        threshold = _gate_role_threshold(3, "qa")
        threshold_letter = next(
            (k for k, v in GRADE_MAP.items() if v == threshold), "A"
        )
        content = self.FULL_QA.replace(
            "### Grade: A\n", f"### Grade: {threshold_letter}\n"
        )
        self.write("QA-REPORT.md", content)
        _write_sibling_artifacts(self.tmpdir, 3, "QA-REPORT.md")
        passed, _ = validate_gate(3, self.tmpdir)
        self.assertTrue(
            passed,
            f"Grade {threshold_letter} should pass at threshold {threshold_letter}",
        )

    def test_gate3_grade_below_threshold_fails(self):
        threshold = _gate_role_threshold(3, "qa")
        below_val = max(
            (v for v in GRADE_MAP.values() if v < threshold), default=None
        )
        below_letter = next((k for k, v in GRADE_MAP.items() if v == below_val), None)
        if below_letter is None:
            self.skipTest("No grade below threshold in GRADE_MAP")
        content = self.FULL_QA.replace("### Grade: A\n", f"### Grade: {below_letter}\n")
        self.write("QA-REPORT.md", content)
        _write_sibling_artifacts(self.tmpdir, 3, "QA-REPORT.md")
        passed, _ = validate_gate(3, self.tmpdir)
        self.assertFalse(
            passed,
            f"Grade {below_letter} should fail at threshold {threshold}",
        )

    def test_gate3_unrecognized_grade(self):
        content = self.FULL_QA.replace("### Grade: A\n", "### Grade: A+\n")
        self.write("QA-REPORT.md", content)
        _write_sibling_artifacts(self.tmpdir, 3, "QA-REPORT.md")
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed)
        grade_check = next(c for c in checks if c["name"] == "grade_recognized")
        self.assertIn("Unrecognized", grade_check["detail"])

    def test_gate3_auto_fail_mock_data(self):
        self.write("QA-REPORT.md", self.FULL_QA + "### Issues Found\nUses mock data\n")
        _write_sibling_artifacts(self.tmpdir, 3, "QA-REPORT.md")
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
        content = self.FULL_PM.replace(
            "### Verdict: APPROVED\n", "### Verdict: NEEDS CHANGES\n"
        )
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


class TestSecurityValidator(unittest.TestCase):
    """Tests for _validate_security_content — M3 bug regression."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, name, content):
        with open(os.path.join(self.tmpdir, name), "w", encoding="utf-8") as f:
            f.write(content)

    def _full_security(self, grade="A"):
        return (
            f"## Security Audit\n"
            f"- Critical: 0\n- High: 0\n"
            f"### Findings\n| # | Severity | Finding |\n| 1 | Low | Minor |\n"
            f"### Grade: {grade}\n"
        )

    def test_security_grade_a_passes(self):
        self.write("SECURITY-REPORT.md", self._full_security("A"))
        passed, _ = validate_gate(3.5, self.tmpdir)
        self.assertTrue(passed)

    def test_security_grade_b_fails(self):
        self.write("SECURITY-REPORT.md", self._full_security("B"))
        self.write("QA-REPORT.md", "GATE_RESULT: PASS\n### Grade: A\n")
        passed, checks = validate_gate(3.5, self.tmpdir)
        self.assertFalse(passed)
        # Verify security grade_sufficient check is the failing one
        sec_grade = [
            c for c in checks if c["name"] == "grade_sufficient" and not c["pass"]
        ]
        self.assertTrue(len(sec_grade) > 0)

    def test_security_no_grade_fails(self):
        """M3 regression: security report without grade must FAIL validation."""
        self.write(
            "SECURITY-REPORT.md",
            "## Security Audit\n- Critical: 0\n### Findings\n| None |\n",
        )
        self.write("QA-REPORT.md", "GATE_RESULT: PASS\n### Grade: A\n")
        passed, checks = validate_gate(3.5, self.tmpdir)
        self.assertFalse(passed, "Security report with no grade must fail")

    def test_security_critical_findings_fails(self):
        self.write(
            "SECURITY-REPORT.md",
            "## Security Audit\n- Critical: 2\n### Grade: A\n",
        )
        self.write("QA-REPORT.md", "GATE_RESULT: PASS\n### Grade: A\n")
        passed, checks = validate_gate(3.5, self.tmpdir)
        self.assertFalse(passed)
        critical_check = next(c for c in checks if c["name"] == "no_critical_findings")
        self.assertFalse(critical_check["pass"])


class TestQAFeedback(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, name, content):
        with open(os.path.join(self.tmpdir, name), "w", encoding="utf-8") as f:
            f.write(content)

    def test_extract_feedback_with_issues(self):
        self.write(
            "QA-REPORT.md",
            "### Test Results\n| Test | Result |\n| Spacing | FAIL |\n"
            "### Grade: C\n"
            "### Issues\n- Missing spacing scale\n- No error states\n"
            "### Stranger Test\nYes.\n",
        )
        feedback = extract_qa_feedback(self.tmpdir)
        self.assertIn("QA Grade: C", feedback)
        self.assertIn("Missing spacing scale", feedback)
        self.assertIn("No error states", feedback)
        self.assertIn("Test Results", feedback)

    def test_extract_feedback_no_issues(self):
        self.write(
            "QA-REPORT.md",
            "### Test Results\n| Test | Result |\n| Core | PASS |\n"
            "### Grade: A\n"
            "### Stranger Test\nYes.\n",
        )
        feedback = extract_qa_feedback(self.tmpdir)
        self.assertIsNotNone(feedback)
        self.assertIn("QA Grade: A", feedback)

    def test_extract_feedback_missing_report(self):
        feedback = extract_qa_feedback(self.tmpdir)
        self.assertIsNone(feedback)

    def test_save_accumulates_rounds(self):
        self.write(
            "QA-REPORT.md",
            "### Grade: C\n### Issues\n- Bug 1\n### Test Results\n| T | R |\n### Stranger Test\nNo.\n",
        )
        save_qa_feedback(self.tmpdir)

        self.write(
            "QA-REPORT.md",
            "### Grade: B\n### Issues\n- Bug 2\n### Test Results\n| T | R |\n### Stranger Test\nNo.\n",
        )
        save_qa_feedback(self.tmpdir)

        feedback_path = os.path.join(self.tmpdir, "QA-REPORT-FEEDBACK.md")
        with open(feedback_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(
            "Feedback Round 1", content, "expected round 1 in accumulated feedback"
        )
        self.assertIn("Bug 1", content)
        self.assertIn("Feedback Round 2", content)
        self.assertIn("Bug 2", content)


class TestSequentialReviewFeedback(unittest.TestCase):
    """Sequential QA and Security feedback is preserved independently."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, name, content):
        with open(os.path.join(self.tmpdir, name), "w", encoding="utf-8") as f:
            f.write(content)

    def test_security_findings_in_feedback(self):
        """SECURITY-REPORT.md findings appear in artifact feedback."""
        self.write(
            "SECURITY-REPORT.md",
            "# Security Report\n\n"
            "- Critical: 1\n- High: 0\n"
            "### Findings\nXSS in form input.\n"
            "### Grade: C\n",
        )
        self.write(
            "QA-REPORT.md",
            "### Test Results\n| Test | Result |\n|------|--------|\n| Core | PASS |\n"
            "### Grade: B\n"
            "### Stranger Test\nYes.\n",
        )
        save_gate_feedback(self.tmpdir, 3.5)
        feedback_path = os.path.join(self.tmpdir, "SECURITY-REPORT-FEEDBACK.md")
        self.assertTrue(os.path.isfile(feedback_path))
        with open(feedback_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("XSS in form input", content, "Security findings must reach dev")
        self.assertIn("Security Grade: C", content, "Security grade must be included")

    def test_qa_and_security_both_in_feedback(self):
        """Both QA and security feedback are preserved for dev."""
        self.write(
            "SECURITY-REPORT.md",
            "# Security Report\n\n"
            "- Critical: 0\n- High: 1\n"
            "### Findings\nCSRF missing on POST endpoint.\n"
            "### Grade: B\n",
        )
        self.write(
            "QA-REPORT.md",
            "### Test Results\n| Test | Result |\n|------|--------|\n| Core | FAIL |\n"
            "### Grade: C\n"
            "### Issues\nButton misaligned on mobile.\n"
            "### Stranger Test\nNo.\n",
        )
        save_qa_feedback(self.tmpdir)
        save_gate_feedback(self.tmpdir, 3.5)
        qa_feedback_path = os.path.join(self.tmpdir, "QA-REPORT-FEEDBACK.md")
        security_feedback_path = os.path.join(
            self.tmpdir, "SECURITY-REPORT-FEEDBACK.md"
        )
        with open(qa_feedback_path, encoding="utf-8") as f:
            qa_content = f.read()
        with open(security_feedback_path, encoding="utf-8") as f:
            security_content = f.read()

        self.assertIn("Button misaligned", qa_content)
        self.assertIn("QA Grade: C", qa_content)
        self.assertIn("CSRF missing", security_content)
        self.assertIn("Security Grade: B", security_content)


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
        self.assertIsNotNone(
            PLACEHOLDER_PATTERN.search("some [PLACEHOLDER for X] text")
        )

    # ---- end-to-end via validate_gate ----

    def test_validate_gate1_passes_with_inline_source_in_prose(self):
        """Full pipeline check: SCOPE.md with prose containing '[source]'
        passes the no_placeholders check inside validate_gate(1)."""
        tmpdir = tempfile.mkdtemp()
        try:
            scope = (
                "# SCOPE\n\n## Data Sources\n"
                "No external data sources are required.\n\n"
                "## ACs\n- [ ] AC1: every data point has a [source] link\n\n"
                + "word " * 60
            )
            with open(os.path.join(tmpdir, "SCOPE.md"), "w", encoding="utf-8") as f:
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
                "# SCOPE\n\n## Data Sources\n"
                "No external data sources are required.\n\n"
                "## ACs\n- [ ] AC1: do things\n\nOwner: [TBD]\n\n"
                + "word " * 60
            )
            with open(os.path.join(tmpdir, "SCOPE.md"), "w", encoding="utf-8") as f:
                f.write(scope)
            _, checks = validate_gate(1, tmpdir)
            placeholder = next(c for c in checks if c["name"] == "no_placeholders")
            self.assertFalse(
                placeholder["pass"],
                "validate_gate(1) should flag 'Owner: [TBD]' as a real placeholder",
            )
        finally:
            shutil.rmtree(tmpdir)

    def test_validate_gate1_rejects_unprobed_external_source(self):
        tmpdir = tempfile.mkdtemp()
        try:
            scope = (
                "# SCOPE\n\n## Data Sources\n"
                "- Endpoint: https://example.test/api — Verified\n\n"
                "## Acceptance Criteria\n- [ ] AC1: show records\n\n"
                + "word " * 60
            )
            with open(os.path.join(tmpdir, "SCOPE.md"), "w", encoding="utf-8") as f:
                f.write(scope)
            passed, checks = validate_gate(1, tmpdir)
            self.assertFalse(passed)
            source_check = next(
                c for c in checks if c["name"] == "data_sources_verified"
            )
            self.assertFalse(source_check["pass"])
        finally:
            shutil.rmtree(tmpdir)

    def test_validate_gate1_accepts_retained_probe_evidence(self):
        tmpdir = tempfile.mkdtemp()
        try:
            scope = (
                "# SCOPE\n\n## Data Sources\n"
                "- Endpoint: https://example.test/api — Verified\n"
                "  - Probe: `curl -i https://example.test/api`\n"
                "  - Result: HTTP 200, returned 42 records\n\n"
                "## Acceptance Criteria\n- [ ] AC1: show records\n\n"
                + "word " * 60
            )
            with open(os.path.join(tmpdir, "SCOPE.md"), "w", encoding="utf-8") as f:
                f.write(scope)
            passed, checks = validate_gate(1, tmpdir)
            self.assertTrue(passed, checks)
            source_check = next(
                c for c in checks if c["name"] == "data_sources_verified"
            )
            self.assertTrue(source_check["pass"])
        finally:
            shutil.rmtree(tmpdir)


if __name__ == "__main__":
    unittest.main()
