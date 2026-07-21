#!/usr/bin/env python3
"""Integration tests for skill resolution + reference injection in prompt assembly.

Verifies the end-to-end flow:
  pipeline.yaml skills: [...] → machine.resolve_skill_paths() → runner.build_agent_prompt()

Tests:
  1. Gate with skill having references → methodology section + reference subsection
  2. Gate with no skills → no methodology section
  3. Gate with skill having no references → methodology section, no reference subsection
  4. Multiple skills → each gets own methodology section
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.machine import resolve_skill_paths, SkillResolutionError
from src.runner import build_agent_prompt, _resolve_skill_references


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SFLO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR_SKILLS_DIR = os.path.join(SFLO_ROOT, "vendor", "mattpocock-skills", "skills")


class TestSkillIntegrationEndToEnd(unittest.TestCase):
    """End-to-end: resolve_skill_paths → build_agent_prompt with real vendor skills."""

    def setUp(self):
        """Create minimal fake gate/soul files for prompt assembly."""
        self.tmpdir = tempfile.mkdtemp(prefix="sflo_skill_test_")
        self.fake_gate = os.path.join(self.tmpdir, "gate.md")
        self.fake_soul = os.path.join(self.tmpdir, "soul.md")
        self.fake_sflo_dir = os.path.join(self.tmpdir, "sflo_state")
        os.makedirs(self.fake_sflo_dir, exist_ok=True)

        with open(self.fake_gate, "w", encoding="utf-8") as f:
            f.write("# Gate 2: Build\n\nImplement the feature.\n")
        with open(self.fake_soul, "w", encoding="utf-8") as f:
            f.write("# Dev Agent\n\nYou are a developer agent.\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_skill_with_references_produces_methodology_and_refs(self):
        """Given skills: ['test-driven-development'], prompt contains methodology + refs."""
        # Resolve skill path via machine.py
        resolved = resolve_skill_paths(["mattpocock-skills/engineering/tdd"], SFLO_ROOT)
        self.assertEqual(len(resolved), 1, "Should resolve exactly one skill path")

        expected_skill = os.path.join(
            VENDOR_SKILLS_DIR, "engineering", "tdd", "SKILL.md"
        )
        self.assertEqual(resolved[0], expected_skill)
        self.assertTrue(os.path.isfile(resolved[0]))

        # Build prompt with resolved skill
        agent_info = {
            "reads": [self.fake_gate, self.fake_soul],
            "gate_num": 2,
            "skills": resolved,
            "agents": [],
            "role": "dev",
        }

        system_prompt, _ = build_agent_prompt(
            agent_info, "Build a counter app", self.fake_sflo_dir
        )

        # Verify methodology section header
        self.assertIn(
            "## Methodology: tdd",
            system_prompt,
            "Prompt must contain methodology section for TDD skill",
        )

        # Verify reference subsection
        self.assertIn(
            "### Reference files (read on demand)",
            system_prompt,
            "Prompt must contain reference subsection when skill has refs",
        )

        testing_patterns_path = os.path.join(VENDOR_SKILLS_DIR, "engineering", "tdd", "tests.md")
        self.assertIn(
            testing_patterns_path,
            system_prompt,
            "Prompt must contain absolute path to testing-patterns.md",
        )

    def test_no_skills_no_methodology_section(self):
        """Gate with no skills → no methodology section in prompt."""
        agent_info = {
            "reads": [self.fake_gate, self.fake_soul],
            "gate_num": 2,
            "skills": [],
            "agents": [],
            "role": "dev",
        }

        system_prompt, _ = build_agent_prompt(
            agent_info, "Build a counter app", self.fake_sflo_dir
        )

        self.assertNotIn(
            "## Methodology:",
            system_prompt,
            "Prompt must NOT contain methodology section when no skills declared",
        )
        self.assertNotIn(
            "### Reference files (read on demand)",
            system_prompt,
            "Prompt must NOT contain reference subsection when no skills declared",
        )

    def test_skill_without_references_has_methodology_no_refs(self):
        """Skill that has no backtick-wrapped .md refs → methodology present, no ref subsection."""
        # Create a temporary skill with no references
        skill_dir = os.path.join(self.tmpdir, "vendor", "test-vendor", "skills", "no-refs-skill")
        os.makedirs(skill_dir, exist_ok=True)
        skill_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_path, "w", encoding="utf-8") as f:
            f.write("---\nname: no-refs-skill\ndescription: A skill with no refs.\n---\n\n")
            f.write("# No Refs Skill\n\nThis skill has no reference files mentioned.\n")

        agent_info = {
            "reads": [self.fake_gate, self.fake_soul],
            "gate_num": 2,
            "skills": [skill_path],
            "agents": [],
            "role": "dev",
        }

        system_prompt, _ = build_agent_prompt(
            agent_info, "Build a counter app", self.fake_sflo_dir
        )

        # Methodology section IS present
        self.assertIn(
            "## Methodology: no-refs-skill",
            system_prompt,
            "Prompt must contain methodology section for skill without refs",
        )

        # Reference subsection is NOT present
        self.assertNotIn(
            "### Reference files (read on demand)",
            system_prompt,
            "Prompt must NOT contain reference subsection for skill without refs",
        )

    def test_multiple_skills_each_gets_own_methodology(self):
        """Multiple skills → each gets its own ## Methodology: section."""
        # Use two real vendor skills
        resolved = resolve_skill_paths(
            ["mattpocock-skills/engineering/tdd", "mattpocock-skills/engineering/codebase-design"], SFLO_ROOT
        )
        self.assertEqual(len(resolved), 2, "Should resolve two skill paths")

        agent_info = {
            "reads": [self.fake_gate, self.fake_soul],
            "gate_num": 2,
            "skills": resolved,
            "agents": [],
            "role": "dev",
        }

        system_prompt, _ = build_agent_prompt(
            agent_info, "Build a counter app", self.fake_sflo_dir
        )

        self.assertIn(
            "## Methodology: tdd",
            system_prompt,
            "Prompt must contain TDD methodology section",
        )
        self.assertIn(
            "## Methodology: codebase-design",
            system_prompt,
            "Prompt must contain incremental-implementation methodology section",
        )


class TestResolveSkillReferences(unittest.TestCase):
    """Unit tests for _resolve_skill_references alone."""

    def test_resolves_backtick_md_references(self):
        """Backtick-wrapped .md paths in skill content resolve to absolute paths."""
        skill_path = os.path.join(
            VENDOR_SKILLS_DIR, "engineering", "tdd", "SKILL.md"
        )
        with open(skill_path, encoding="utf-8") as f:
            content = f.read()

        refs = _resolve_skill_references(skill_path, content)

        # testing-patterns.md should be among resolved refs
        ref_names = [name for name, _ in refs]
        self.assertIn(
            "tests.md",
            ref_names,
            "Should resolve references/testing-patterns.md from TDD skill",
        )

        # Each ref should be an absolute path to an existing file
        for name, abs_path in refs:
            self.assertTrue(
                os.path.isabs(abs_path),
                f"Reference path must be absolute: {abs_path}",
            )
            self.assertTrue(
                os.path.isfile(abs_path),
                f"Reference must point to existing file: {abs_path}",
            )

    def test_empty_content_returns_no_refs(self):
        """Empty or None skill content → empty refs list."""
        self.assertEqual(_resolve_skill_references("/fake/SKILL.md", ""), [])
        self.assertEqual(_resolve_skill_references("/fake/SKILL.md", None), [])

    def test_traversal_rejected(self):
        """Refs containing '..' are rejected for security."""
        content = "See `../../../etc/passwd.md` for details."
        refs = _resolve_skill_references("/tmp/vendor/x/skills/y/SKILL.md", content)
        self.assertEqual(refs, [], "Traversal paths must be rejected")


class TestResolveSkillPaths(unittest.TestCase):
    """Unit tests for machine.resolve_skill_paths."""

    def test_unqualified_name_resolves(self):
        """Unqualified skill name scans vendor dirs and resolves."""
        paths = resolve_skill_paths(["tdd"], SFLO_ROOT)
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].endswith("SKILL.md"))
        self.assertIn("tdd", paths[0])

    def test_qualified_name_resolves(self):
        """Qualified 'vendor/skill' format resolves correctly."""
        paths = resolve_skill_paths(["mattpocock-skills/engineering/tdd"], SFLO_ROOT)
        self.assertEqual(len(paths), 1)
        self.assertIn("mattpocock-skills", paths[0])
        self.assertIn("tdd", paths[0])

    def test_nonexistent_skill_raises(self):
        """Non-existent skill name → SkillResolutionError (pipeline fails)."""
        with self.assertRaises(SkillResolutionError):
            resolve_skill_paths(["does-not-exist-xyz"], SFLO_ROOT)

    def test_traversal_in_name_rejected(self):
        """Names with '..' raise SkillResolutionError."""
        with self.assertRaises(SkillResolutionError):
            resolve_skill_paths(["../../etc/passwd"], SFLO_ROOT)

    def test_empty_skill_names(self):
        """Empty list or None → empty result."""
        self.assertEqual(resolve_skill_paths([], SFLO_ROOT), [])
        self.assertEqual(resolve_skill_paths(None, SFLO_ROOT), [])


if __name__ == "__main__":
    unittest.main()
