"""Tests for _resolve_skill_references() in runner.py.

Verifies:
1. Skills with known references resolve correctly
2. Skills with multiple references resolve all
3. Skills with NO references return empty list
4. Traversal paths (../secret.md) are rejected
5. Non-existent referenced files are NOT included
6. Duplicate references in content are deduplicated
7. Empty content → empty result
8. Vendor root calculation (3 levels up from SKILL.md path)
"""

import os
import tempfile
import shutil
import unittest

# Repo root added to sys.path by tests/conftest.py — no hardcoded absolute
# path here so the suite stays portable across machines.
from src.runner import _resolve_skill_references


class TestSkillReferencesVendorRoot(unittest.TestCase):
    """Test 8: Verify vendor root is calculated as 3 levels up from SKILL.md."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Structure: vendor_root/skills/my-skill/SKILL.md
        # vendor_root is 3 levels up: dirname(dirname(dirname(SKILL.md)))
        #   SKILL.md → my-skill/ → skills/ → vendor_root/
        self.vendor_root = self.tmpdir
        self.skill_dir = os.path.join(self.tmpdir, "skills", "test-skill")
        os.makedirs(self.skill_dir, exist_ok=True)
        self.skill_path = os.path.join(self.skill_dir, "SKILL.md")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_vendor_root_is_three_levels_up(self):
        """Vendor root = dirname(dirname(dirname(SKILL.md dir))) via code logic.

        Code does: skill_dir = dirname(skill_path)
                   vendor_root = dirname(dirname(skill_dir))
        So for /tmp/X/skills/test-skill/SKILL.md:
          skill_dir = /tmp/X/skills/test-skill
          dirname(skill_dir) = /tmp/X/skills
          dirname(dirname(skill_dir)) = /tmp/X  ← vendor root
        """
        # Create a reference file at vendor_root/references/guide.md
        ref_dir = os.path.join(self.vendor_root, "references")
        os.makedirs(ref_dir, exist_ok=True)
        ref_file = os.path.join(ref_dir, "guide.md")
        with open(ref_file, "w", encoding="utf-8") as f:
            f.write("# Guide")

        content = "See `references/guide.md` for details."
        result = _resolve_skill_references(self.skill_path, content)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "references/guide.md")
        self.assertEqual(result[0][1], ref_file)


class TestSkillReferencesSingleRef(unittest.TestCase):
    """Test 1: Skill with a single known reference resolves correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Simulate: vendor/agent-skills/skills/test-driven-development/SKILL.md
        # Vendor root: vendor/agent-skills/
        self.vendor_root = os.path.join(self.tmpdir, "vendor", "agent-skills")
        self.skill_dir = os.path.join(
            self.vendor_root, "skills", "test-driven-development"
        )
        os.makedirs(self.skill_dir, exist_ok=True)
        self.skill_path = os.path.join(self.skill_dir, "SKILL.md")

        # Create the referenced file
        ref_dir = os.path.join(self.vendor_root, "references")
        os.makedirs(ref_dir, exist_ok=True)
        self.ref_file = os.path.join(ref_dir, "testing-patterns.md")
        with open(self.ref_file, "w", encoding="utf-8") as f:
            f.write("# Testing Patterns\nContent here.")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_single_reference_resolves(self):
        content = (
            "# Test-Driven Development\n\n"
            "Follow patterns in `references/testing-patterns.md` for guidance.\n"
        )
        result = _resolve_skill_references(self.skill_path, content)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "references/testing-patterns.md")
        self.assertEqual(result[0][1], self.ref_file)

    def test_reference_tuple_structure(self):
        """Each result is (ref_text, absolute_path) tuple."""
        content = "Use `references/testing-patterns.md` as your guide."
        result = _resolve_skill_references(self.skill_path, content)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        ref_text, abs_path = result[0]
        self.assertEqual(ref_text, "references/testing-patterns.md")
        self.assertTrue(os.path.isabs(abs_path))
        self.assertTrue(os.path.isfile(abs_path))


class TestSkillReferencesMultipleRefs(unittest.TestCase):
    """Test 2: Skill with multiple references resolves all of them."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.vendor_root = os.path.join(self.tmpdir, "vendor", "agent-skills")
        self.skill_dir = os.path.join(
            self.vendor_root, "skills", "code-review-and-quality"
        )
        os.makedirs(self.skill_dir, exist_ok=True)
        self.skill_path = os.path.join(self.skill_dir, "SKILL.md")

        # Create two referenced files
        ref_dir = os.path.join(self.vendor_root, "references")
        os.makedirs(ref_dir, exist_ok=True)
        self.ref1 = os.path.join(ref_dir, "review-checklist.md")
        self.ref2 = os.path.join(ref_dir, "quality-standards.md")
        with open(self.ref1, "w", encoding="utf-8") as f:
            f.write("# Review Checklist")
        with open(self.ref2, "w", encoding="utf-8") as f:
            f.write("# Quality Standards")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_multiple_references_resolve(self):
        content = (
            "# Code Review\n\n"
            "See `references/review-checklist.md` for the checklist.\n"
            "Quality bar defined in `references/quality-standards.md`.\n"
        )
        result = _resolve_skill_references(self.skill_path, content)

        self.assertEqual(len(result), 2)
        ref_texts = [r[0] for r in result]
        self.assertIn("references/review-checklist.md", ref_texts)
        self.assertIn("references/quality-standards.md", ref_texts)

    def test_multiple_refs_all_paths_exist(self):
        content = (
            "Use `references/review-checklist.md` and `references/quality-standards.md`."
        )
        result = _resolve_skill_references(self.skill_path, content)

        for ref_text, abs_path in result:
            self.assertTrue(
                os.path.isfile(abs_path), f"{abs_path} should exist on disk"
            )


class TestSkillReferencesNoRefs(unittest.TestCase):
    """Test 3: Skills with NO .md references return empty list."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.vendor_root = os.path.join(self.tmpdir, "vendor", "plain-skill")
        self.skill_dir = os.path.join(self.vendor_root, "skills", "basic")
        os.makedirs(self.skill_dir, exist_ok=True)
        self.skill_path = os.path.join(self.skill_dir, "SKILL.md")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_no_backtick_refs_returns_empty(self):
        content = "# Basic Skill\n\nNo references here, just plain text."
        result = _resolve_skill_references(self.skill_path, content)
        self.assertEqual(result, [])

    def test_backtick_code_not_md_returns_empty(self):
        """Backtick-wrapped text that isn't .md should not match."""
        content = "Use `console.log()` and `myFunction()` in your code."
        result = _resolve_skill_references(self.skill_path, content)
        self.assertEqual(result, [])

    def test_inline_code_blocks_not_matched(self):
        """Non-path .md mentions shouldn't match if file doesn't exist."""
        content = "The file `CHANGELOG.md` should be updated."
        result = _resolve_skill_references(self.skill_path, content)
        # CHANGELOG.md doesn't exist at vendor_root/CHANGELOG.md
        self.assertEqual(result, [])


class TestSkillReferencesTraversal(unittest.TestCase):
    """Test 4: Path traversal (..) in references is rejected."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.vendor_root = os.path.join(self.tmpdir, "vendor", "evil-skill")
        self.skill_dir = os.path.join(self.vendor_root, "skills", "exploit")
        os.makedirs(self.skill_dir, exist_ok=True)
        self.skill_path = os.path.join(self.skill_dir, "SKILL.md")

        # Create a file that traversal would reach
        secret_file = os.path.join(self.tmpdir, "secret.md")
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write("SECRET DATA")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_dotdot_prefix_rejected(self):
        content = "Read `../../../secret.md` for secrets."
        result = _resolve_skill_references(self.skill_path, content)
        self.assertEqual(result, [])

    def test_dotdot_midpath_rejected(self):
        content = "See `references/../../../secret.md` for info."
        result = _resolve_skill_references(self.skill_path, content)
        self.assertEqual(result, [])

    def test_dotdot_simple_rejected(self):
        content = "Load `../secret.md` please."
        result = _resolve_skill_references(self.skill_path, content)
        self.assertEqual(result, [])

    def test_mixed_valid_and_traversal(self):
        """Valid refs still resolve even when traversal refs are present."""
        # Create a valid reference
        ref_dir = os.path.join(self.vendor_root, "references")
        os.makedirs(ref_dir, exist_ok=True)
        valid_file = os.path.join(ref_dir, "legit.md")
        with open(valid_file, "w", encoding="utf-8") as f:
            f.write("Legit content")

        content = (
            "Bad: `../../../secret.md`\n"
            "Good: `references/legit.md`\n"
        )
        result = _resolve_skill_references(self.skill_path, content)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "references/legit.md")


class TestSkillReferencesNonExistent(unittest.TestCase):
    """Test 5: Non-existent referenced files are NOT included."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.vendor_root = os.path.join(self.tmpdir, "vendor", "missing-refs")
        self.skill_dir = os.path.join(self.vendor_root, "skills", "broken")
        os.makedirs(self.skill_dir, exist_ok=True)
        self.skill_path = os.path.join(self.skill_dir, "SKILL.md")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_nonexistent_file_excluded(self):
        content = "See `references/does-not-exist.md` for guidance."
        result = _resolve_skill_references(self.skill_path, content)
        self.assertEqual(result, [])

    def test_mix_existing_and_nonexistent(self):
        """Only existing files appear in results."""
        ref_dir = os.path.join(self.vendor_root, "references")
        os.makedirs(ref_dir, exist_ok=True)
        existing = os.path.join(ref_dir, "exists.md")
        with open(existing, "w", encoding="utf-8") as f:
            f.write("I exist")

        content = (
            "Real: `references/exists.md`\n"
            "Fake: `references/ghost.md`\n"
        )
        result = _resolve_skill_references(self.skill_path, content)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "references/exists.md")

    def test_directory_not_treated_as_file(self):
        """A directory matching the path should not be included."""
        ref_dir = os.path.join(self.vendor_root, "references")
        os.makedirs(ref_dir, exist_ok=True)
        # Create a directory (not file) named guide.md
        dir_as_md = os.path.join(ref_dir, "guide.md")
        os.makedirs(dir_as_md, exist_ok=True)

        content = "See `references/guide.md` for info."
        result = _resolve_skill_references(self.skill_path, content)
        self.assertEqual(result, [])


class TestSkillReferencesDeduplicate(unittest.TestCase):
    """Test 6: Duplicate references in content are deduplicated."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.vendor_root = os.path.join(self.tmpdir, "vendor", "dup-skill")
        self.skill_dir = os.path.join(self.vendor_root, "skills", "repeated")
        os.makedirs(self.skill_dir, exist_ok=True)
        self.skill_path = os.path.join(self.skill_dir, "SKILL.md")

        ref_dir = os.path.join(self.vendor_root, "references")
        os.makedirs(ref_dir, exist_ok=True)
        self.ref_file = os.path.join(ref_dir, "patterns.md")
        with open(self.ref_file, "w", encoding="utf-8") as f:
            f.write("# Patterns")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_duplicate_reference_appears_once(self):
        content = (
            "First mention: `references/patterns.md`\n"
            "Second mention: `references/patterns.md`\n"
            "Third mention: `references/patterns.md`\n"
        )
        result = _resolve_skill_references(self.skill_path, content)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "references/patterns.md")

    def test_dedup_preserves_order(self):
        """First occurrence wins when deduplicating."""
        ref_dir = os.path.join(self.vendor_root, "references")
        second_ref = os.path.join(ref_dir, "other.md")
        with open(second_ref, "w", encoding="utf-8") as f:
            f.write("# Other")

        content = (
            "A: `references/patterns.md`\n"
            "B: `references/other.md`\n"
            "C: `references/patterns.md`\n"  # duplicate
            "D: `references/other.md`\n"  # duplicate
        )
        result = _resolve_skill_references(self.skill_path, content)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][0], "references/patterns.md")
        self.assertEqual(result[1][0], "references/other.md")


class TestSkillReferencesEmptyContent(unittest.TestCase):
    """Test 7: Empty content returns empty result."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.vendor_root = os.path.join(self.tmpdir, "vendor", "empty-skill")
        self.skill_dir = os.path.join(self.vendor_root, "skills", "noop")
        os.makedirs(self.skill_dir, exist_ok=True)
        self.skill_path = os.path.join(self.skill_dir, "SKILL.md")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_string_returns_empty(self):
        result = _resolve_skill_references(self.skill_path, "")
        self.assertEqual(result, [])

    def test_none_returns_empty(self):
        result = _resolve_skill_references(self.skill_path, None)
        self.assertEqual(result, [])

    def test_whitespace_only_returns_empty(self):
        result = _resolve_skill_references(self.skill_path, "   \n\t\n  ")
        self.assertEqual(result, [])


class TestSkillReferencesEdgeCases(unittest.TestCase):
    """Additional edge cases for robustness."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.vendor_root = os.path.join(self.tmpdir, "vendor", "edge-skill")
        self.skill_dir = os.path.join(self.vendor_root, "skills", "edge")
        os.makedirs(self.skill_dir, exist_ok=True)
        self.skill_path = os.path.join(self.skill_dir, "SKILL.md")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_nested_subdirectory_reference(self):
        """References can be in nested subdirectories."""
        nested_dir = os.path.join(self.vendor_root, "references", "deep", "nested")
        os.makedirs(nested_dir, exist_ok=True)
        nested_file = os.path.join(nested_dir, "doc.md")
        with open(nested_file, "w", encoding="utf-8") as f:
            f.write("# Deep Doc")

        content = "See `references/deep/nested/doc.md` for details."
        result = _resolve_skill_references(self.skill_path, content)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "references/deep/nested/doc.md")
        self.assertEqual(result[0][1], nested_file)

    def test_non_md_backtick_paths_ignored(self):
        """Only .md files are matched by the pattern."""
        ref_dir = os.path.join(self.vendor_root, "references")
        os.makedirs(ref_dir, exist_ok=True)
        txt_file = os.path.join(ref_dir, "data.txt")
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("data")

        content = "See `references/data.txt` and `references/script.py`."
        result = _resolve_skill_references(self.skill_path, content)
        self.assertEqual(result, [])

    def test_multiline_content_extracts_all(self):
        """References spread across many lines all get found."""
        ref_dir = os.path.join(self.vendor_root, "docs")
        os.makedirs(ref_dir, exist_ok=True)
        for name in ["a.md", "b.md", "c.md"]:
            with open(os.path.join(ref_dir, name), "w", encoding="utf-8") as f:
                f.write(f"# {name}")

        content = (
            "Line 1: `docs/a.md`\n"
            "Line 2: some text\n"
            "Line 3: `docs/b.md`\n"
            "Line 4: more text\n"
            "Line 5: `docs/c.md`\n"
        )
        result = _resolve_skill_references(self.skill_path, content)

        self.assertEqual(len(result), 3)
        ref_texts = [r[0] for r in result]
        self.assertEqual(ref_texts, ["docs/a.md", "docs/b.md", "docs/c.md"])

    def test_backtick_in_code_block_still_matches(self):
        """Pattern matches backtick refs even inside fenced blocks.

        This is expected behavior — the regex doesn't parse markdown structure.
        """
        ref_dir = os.path.join(self.vendor_root, "references")
        os.makedirs(ref_dir, exist_ok=True)
        ref_file = os.path.join(ref_dir, "api.md")
        with open(ref_file, "w", encoding="utf-8") as f:
            f.write("# API")

        content = (
            "```\n"
            "Read `references/api.md` for the API spec.\n"
            "```\n"
        )
        result = _resolve_skill_references(self.skill_path, content)
        # Regex is line-based, will match inside code blocks
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "references/api.md")


if __name__ == "__main__":
    unittest.main()
