#!/usr/bin/env python3
"""Simulation tests: verify content-depth validators catch scaffolding decoys
and pass real pipeline output.

These tests use two kinds of input:
1. Real artifacts from .sflo/ (a successful Nexus Analytics pipeline run)
2. Synthetic scaffolding-decoy artifacts that mimic the format-compliance
   problem: perfect headings, zero substance.
"""

import os
import shutil
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.validate import validate_gate, section_body, PLACEHOLDER_PATTERN


SFLO_DIR = os.path.join(os.path.dirname(__file__), "..", ".sflo")


class TestRealArtifactsPass(unittest.TestCase):
    """Real pipeline output from Nexus Analytics run must still pass validators."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Copy real artifacts if they exist
        if os.path.isdir(SFLO_DIR):
            for f in os.listdir(SFLO_DIR):
                if f.endswith(".md"):
                    shutil.copy2(os.path.join(SFLO_DIR, f), self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _has_artifact(self, name):
        return os.path.isfile(os.path.join(self.tmpdir, name))

    @unittest.skipUnless(os.path.isdir(SFLO_DIR), "No .sflo/ artifacts available")
    def test_real_scope_passes_gate1(self):
        if not self._has_artifact("SCOPE.md"):
            self.skipTest("SCOPE.md not found")
        passed, checks = validate_gate(1, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Real SCOPE.md failed checks: {failed}")

    @unittest.skipUnless(os.path.isdir(SFLO_DIR), "No .sflo/ artifacts available")
    def test_real_build_passes_gate2(self):
        if not self._has_artifact("BUILD-STATUS.md"):
            self.skipTest("BUILD-STATUS.md not found")
        passed, checks = validate_gate(2, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Real BUILD-STATUS.md failed checks: {failed}")

    @unittest.skipUnless(os.path.isdir(SFLO_DIR), "No .sflo/ artifacts available")
    def test_real_qa_passes_gate3(self):
        if not self._has_artifact("QA-REPORT.md"):
            self.skipTest("QA-REPORT.md not found")
        passed, checks = validate_gate(3, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Real QA-REPORT.md failed checks: {failed}")

    @unittest.skipUnless(os.path.isdir(SFLO_DIR), "No .sflo/ artifacts available")
    def test_real_pm_content_depth_gate4(self):
        """Real PM-VERIFY.md may have verdict NEEDS CHANGES (legit rejection),
        but the content-depth checks (ac_check_depth, scope_alignment_depth)
        should pass — the PM did real work."""
        if not self._has_artifact("PM-VERIFY.md"):
            self.skipTest("PM-VERIFY.md not found")
        _, checks = validate_gate(4, self.tmpdir)
        depth_checks = [c for c in checks if c["name"] in
                        ("ac_check_depth", "scope_alignment_depth", "has_ac_check",
                         "has_scope_alignment", "has_process_reflection")]
        failed = [c for c in depth_checks if not c["pass"]]
        self.assertEqual(failed, [], f"Real PM-VERIFY.md failed depth checks: {failed}")

    @unittest.skipUnless(os.path.isdir(SFLO_DIR), "No .sflo/ artifacts available")
    def test_real_ship_passes_gate5(self):
        if not self._has_artifact("SHIP-DECISION.md"):
            self.skipTest("SHIP-DECISION.md not found")
        passed, checks = validate_gate(5, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Real SHIP-DECISION.md failed checks: {failed}")


class TestScaffoldingDecoysRejected(unittest.TestCase):
    """Synthetic scaffolding-decoy artifacts must be caught by content-depth checks.

    These simulate the format-compliance decoy pattern: all headings present,
    structure matches the template perfectly, but content is empty or placeholder.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, name, content):
        with open(os.path.join(self.tmpdir, name), "w") as f:
            f.write(content)

    # -- Gate 1: PM Discovery scaffolding decoys --

    def test_gate1_template_placeholders(self):
        """SCOPE.md with perfect structure but placeholder content."""
        self.write("SCOPE.md", (
            "## Data Sources\n[URL] [source]\n"
            "## Acceptance Criteria\n- [x] AC1\n"
            "## Challenge Analysis\n[TODO]\n"
            "## What We're Building\nA thing.\n"
            "## Features\n- Feature 1\n"
            "## State Management\nState.\n"
        ))
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed, "Placeholder SCOPE.md should not pass")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("data_sources_real", failed_names)

    def test_gate1_empty_sections(self):
        """SCOPE.md with all headings but empty Challenge Analysis."""
        self.write("SCOPE.md", (
            "## Data Sources\nNone\n"
            "## Acceptance Criteria\n- [x] AC1\n"
            "## Challenge Analysis\n\n"
            "## What We're Building\nA widget.\n"
            "## Features\n- Feature 1\n"
            "## State Management\nLocal state.\n"
        ))
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed, "Empty Challenge Analysis should not pass")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("challenge_analysis_depth", failed_names)

    # -- Gate 2: Dev Build scaffolding decoys --

    def test_gate2_headings_only_no_checkmarks(self):
        """BUILD-STATUS.md with structure but no checked items."""
        self.write("BUILD-STATUS.md", (
            "Build: Success\nZero errors\n"
            "## 1. Core Functionality Check\n\n"
            "## 2. Accessibility Check\n\n"
        ))
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed, "BUILD-STATUS with no checked items should fail")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("has_checked_items", failed_names)
        self.assertIn("core_functionality_depth", failed_names)

    def test_gate2_claims_success_empty_sections(self):
        """BUILD-STATUS.md says 'Build: Success' but sections are empty scaffolding."""
        self.write("BUILD-STATUS.md", (
            "Build: Success\nZero errors\n- [x] done\n"
            "## 1. Core Functionality Check\n\n"
            "## 2. Accessibility Check\nOK\n"
        ))
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed, "Empty Core Functionality section should fail")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("core_functionality_depth", failed_names)

    # -- Gate 3: QA Report scaffolding decoys --

    def test_gate3_table_header_no_results(self):
        """QA-REPORT.md with Test Results table header but no PASS/FAIL rows.

        This is the exact qwen2.5:7b pattern: perfect template structure,
        table header row, but no actual test outcomes.
        """
        self.write("QA-REPORT.md", (
            "### Test Results\n"
            "| Test | Result |\n"
            "|------|--------|\n"
            "### Grade: A\n"
            "### Stranger Test\nYes — clear value.\n"
        ))
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed, "Empty test results table should not pass")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("test_results_real", failed_names)

    def test_gate3_one_word_stranger_test(self):
        """QA-REPORT.md with Stranger Test that is just 'Yes.' — rubber stamp."""
        self.write("QA-REPORT.md", (
            "### Test Results\n| Test | Result |\n|------|--------|\n| Core | PASS |\n"
            "### Grade: A\n"
            "### Stranger Test\nYes.\n"
        ))
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed, "One-word Stranger Test should not pass")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("stranger_test_depth", failed_names)

    def test_gate3_inflated_grade_no_evidence(self):
        """QA gives A but test results have no actual entries — grade inflation."""
        self.write("QA-REPORT.md", (
            "### Test Results\n"
            "All tests passed.\n"
            "### Grade: A\n"
            "### Stranger Test\nYes — very useful.\n"
        ))
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed, "No PASS/FAIL entries should fail even with Grade A")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("test_results_real", failed_names)

    # -- Gate 4: PM Verify scaffolding decoys --

    def test_gate4_rubber_stamp_approval(self):
        """PM-VERIFY.md that approves without checking any criteria."""
        self.write("PM-VERIFY.md", (
            "### Acceptance Criteria Check\nAll criteria met.\n"
            "### Scope Alignment\nIn scope.\n"
            "### Verdict: APPROVED\n"
            "## Process Reflection\nWent well.\n"
        ))
        passed, checks = validate_gate(4, self.tmpdir)
        self.assertFalse(passed, "PM approval without checked AC items should fail")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("ac_check_depth", failed_names)

    def test_gate4_empty_scope_alignment(self):
        """PM-VERIFY.md with Scope Alignment heading but no content."""
        self.write("PM-VERIFY.md", (
            "### Acceptance Criteria Check\n- [x] AC1 met\n"
            "### Scope Alignment\n\n"
            "### Verdict: APPROVED\n"
            "## Process Reflection\nWent well.\n"
        ))
        passed, checks = validate_gate(4, self.tmpdir)
        self.assertFalse(passed, "Empty Scope Alignment should fail")
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("scope_alignment_depth", failed_names)


class TestContentDepthPassPath(unittest.TestCase):
    """Self-contained artifacts that exercise every content-depth check at its boundary.

    These don't depend on .sflo/ existing — they prove the minimum viable
    artifact for each gate passes all checks including the new depth ones.
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
        """SCOPE.md at minimum depth boundary: 2-word Challenge Analysis, real Data Sources."""
        self.write("SCOPE.md", (
            "## Data Sources\nNone needed\n"
            "## Acceptance Criteria\n- [x] AC1\n"
            "## Challenge Analysis\nTwo words\n"
            "## What We're Building\nA widget.\n"
            "## Features\n- Feature 1\n"
            "## State Management\nLocal state.\n"
        ))
        passed, checks = validate_gate(1, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Minimum viable SCOPE.md should pass: {failed}")

    def test_gate1_na_without_brackets_passes(self):
        """Data Sources with 'N/A' (no brackets) is real content, not a placeholder."""
        self.write("SCOPE.md", (
            "## Data Sources\nN/A — no external data required\n"
            "## Acceptance Criteria\n- [x] AC1\n"
            "## Challenge Analysis\nSome real analysis here.\n"
            "## What We're Building\nA widget.\n"
            "## Features\n- Feature 1\n"
            "## State Management\nLocal state.\n"
        ))
        passed, checks = validate_gate(1, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"N/A without brackets should pass: {failed}")

    def test_gate1_tbd_placeholder_rejected(self):
        """[TBD] in Data Sources is a placeholder."""
        self.write("SCOPE.md", (
            "## Data Sources\n[TBD]\n"
            "## Acceptance Criteria\n- [x] AC1\n"
            "## Challenge Analysis\nSome real analysis.\n"
            "## What We're Building\nA widget.\n"
            "## Features\n- Feature 1\n"
            "## State Management\nLocal state.\n"
        ))
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("data_sources_real", failed_names)

    def test_gate1_insert_placeholder_rejected(self):
        """[INSERT] in Data Sources is a placeholder."""
        self.write("SCOPE.md", (
            "## Data Sources\n[INSERT data source here]\n"
            "## Acceptance Criteria\n- [x] AC1\n"
            "## Challenge Analysis\nSome real analysis.\n"
            "## What We're Building\nA widget.\n"
            "## Features\n- Feature 1\n"
            "## State Management\nLocal state.\n"
        ))
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("data_sources_real", failed_names)

    def test_gate1_one_word_challenge_fails(self):
        """Challenge Analysis with exactly 1 word is below the 2-word minimum."""
        self.write("SCOPE.md", (
            "## Data Sources\nNone\n"
            "## Acceptance Criteria\n- [x] AC1\n"
            "## Challenge Analysis\nDifficult\n"
            "## What We're Building\nA widget.\n"
            "## Features\n- Feature 1\n"
            "## State Management\nLocal state.\n"
        ))
        passed, checks = validate_gate(1, self.tmpdir)
        self.assertFalse(passed)
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("challenge_analysis_depth", failed_names)

    # -- Gate 2: minimum passing artifact --

    def test_gate2_minimum_viable_passes(self):
        """BUILD-STATUS.md at minimum depth: 1 checked item, 2-word Core section."""
        self.write("BUILD-STATUS.md", (
            "Build: Success\nZero errors\n- [x] done\n"
            "## 1. Core Functionality Check\n- [x] works\n"
            "## 2. Accessibility Check\n- [x] accessible\n"
        ))
        passed, checks = validate_gate(2, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Minimum viable BUILD-STATUS.md should pass: {failed}")

    def test_gate2_one_word_core_fails(self):
        """Core Functionality with exactly 1 word is below minimum."""
        self.write("BUILD-STATUS.md", (
            "Build: Success\nZero errors\n- [x] done\n"
            "## 1. Core Functionality Check\nWorks\n"
            "## 2. Accessibility Check\n- [x] ok\n"
        ))
        passed, checks = validate_gate(2, self.tmpdir)
        self.assertFalse(passed)
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("core_functionality_depth", failed_names)

    # -- Gate 3: minimum passing artifact and boundary cases --

    def test_gate3_minimum_viable_passes(self):
        """QA-REPORT.md at minimum: 1 PASS entry, 2-word Stranger Test."""
        self.write("QA-REPORT.md", (
            "### Test Results\n| Test | Result |\n|------|--------|\n| Core | PASS |\n"
            "### Grade: A\n"
            "### Stranger Test\nYes — useful.\n"
        ))
        passed, checks = validate_gate(3, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Minimum viable QA-REPORT.md should pass: {failed}")

    def test_gate3_fail_entries_count_as_real(self):
        """FAIL entries are real test results — honest QA should not be penalized."""
        self.write("QA-REPORT.md", (
            "### Test Results\n"
            "| Test | Result |\n|------|--------|\n"
            "| Spacing | FAIL |\n"
            "| Error states | FAIL |\n"
            "| Core render | PASS |\n"
            "### Grade: C\n"
            "### Stranger Test\nNo — missing critical styling and error handling.\n"
        ))
        _, checks = validate_gate(3, self.tmpdir)
        real_check = next(c for c in checks if c["name"] == "test_results_real")
        self.assertTrue(real_check["pass"], "FAIL entries should count as real test results")
        self.assertEqual(real_check["detail"], "3 PASS/FAIL entries")

    def test_gate3_mixed_case_pass_fail(self):
        """PASS/FAIL matching should be case-insensitive."""
        self.write("QA-REPORT.md", (
            "### Test Results\n"
            "| Test | Result |\n|------|--------|\n"
            "| Core | Pass |\n"
            "| Edge | fail |\n"
            "### Grade: B+\n"
            "### Stranger Test\nYes — reasonable value.\n"
        ))
        _, checks = validate_gate(3, self.tmpdir)
        real_check = next(c for c in checks if c["name"] == "test_results_real")
        self.assertTrue(real_check["pass"], "Case-insensitive PASS/FAIL should work")

    def test_gate3_prose_only_no_table_fails(self):
        """QA that writes 'Everything works great' with no structured results."""
        self.write("QA-REPORT.md", (
            "### Test Results\n"
            "Everything looks good. The app works well and meets requirements.\n"
            "### Grade: A\n"
            "### Stranger Test\nYes — would recommend.\n"
        ))
        passed, checks = validate_gate(3, self.tmpdir)
        self.assertFalse(passed)
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("test_results_real", failed_names)

    # -- Gate 4: minimum passing artifact and boundary cases --

    def test_gate4_minimum_viable_passes(self):
        """PM-VERIFY.md at minimum: 1 checked AC, 2-word Scope Alignment."""
        self.write("PM-VERIFY.md", (
            "### Acceptance Criteria Check\n- [x] AC1 met\n"
            "### Scope Alignment\nIn scope.\n"
            "### Verdict: APPROVED\n"
            "## Process Reflection\nWent smoothly.\n"
        ))
        passed, checks = validate_gate(4, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Minimum viable PM-VERIFY.md should pass: {failed}")

    def test_gate4_multiple_ac_items(self):
        """PM-VERIFY.md with multiple checked and unchecked AC items passes depth."""
        self.write("PM-VERIFY.md", (
            "### Acceptance Criteria Check\n"
            "- [x] AC1: Core works\n"
            "- [x] AC2: Charts render\n"
            "- [~] AC3: Partial — values hardcoded in prose\n"
            "### Scope Alignment\nAll features within original scope boundaries.\n"
            "### Verdict: APPROVED\n"
            "## Process Reflection\nGood iteration.\n"
        ))
        passed, checks = validate_gate(4, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Multi-AC PM-VERIFY.md should pass: {failed}")

    def test_gate4_one_word_scope_fails(self):
        """Scope Alignment with exactly 1 word is below minimum."""
        self.write("PM-VERIFY.md", (
            "### Acceptance Criteria Check\n- [x] AC1 met\n"
            "### Scope Alignment\nAligned\n"
            "### Verdict: APPROVED\n"
            "## Process Reflection\nWent smoothly.\n"
        ))
        passed, checks = validate_gate(4, self.tmpdir)
        self.assertFalse(passed)
        failed_names = {c["name"] for c in checks if not c["pass"]}
        self.assertIn("scope_alignment_depth", failed_names)

    # -- Gate 5: no new depth checks, but verify baseline still works --

    def test_gate5_minimum_viable_passes(self):
        """SHIP-DECISION.md passes (no new depth checks for gate 5)."""
        self.write("SHIP-DECISION.md", (
            "### Pipeline Evidence\nAll gates passed.\n"
            "### Iterations\n1 iteration.\n"
            "### Decision: SHIP\n"
        ))
        passed, checks = validate_gate(5, self.tmpdir)
        failed = [c for c in checks if not c["pass"]]
        self.assertTrue(passed, f"Minimum viable SHIP-DECISION.md should pass: {failed}")


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
