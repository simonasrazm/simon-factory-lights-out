"""Tests for multi-vendor skill resolution in machine.py.

Verifies _discover_vendor_dirs() and resolve_skill_paths() after refactor
from hardcoded 'agent-skills' vendor to dynamic multi-vendor scanner.
"""

import os
import tempfile
import shutil
import unittest
from unittest.mock import patch

# Repo root added to sys.path by tests/conftest.py — no hardcoded absolute
# path here so the suite stays portable across machines.
from src.machine import _discover_vendor_dirs, resolve_skill_paths, SkillResolutionError

# The sflo/ repo root, derived from this test file's location.
SFLO_REPO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))


def _symlinks_supported():
    """Return True if os.symlink() works here.

    Standard Windows accounts lack the SeCreateSymbolicLinkPrivilege, so
    os.symlink() raises OSError. Tests that depend on symlinks are skipped
    rather than failed on such machines.
    """
    probe = tempfile.mkdtemp()
    try:
        target = os.path.join(probe, "target")
        link = os.path.join(probe, "link")
        os.mkdir(target)
        try:
            os.symlink(target, link)
            return True
        except (OSError, NotImplementedError):
            return False
    finally:
        shutil.rmtree(probe, ignore_errors=True)


SYMLINKS_SUPPORTED = _symlinks_supported()


class TestDiscoverVendorDirs(unittest.TestCase):
    """Tests for _discover_vendor_dirs(sflo_base)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create a vendor/ with two vendor dirs
        self.vendor_root = os.path.join(self.tmpdir, "vendor")
        os.makedirs(os.path.join(self.vendor_root, "alpha", "skills", "tdd"))
        os.makedirs(os.path.join(self.vendor_root, "beta", "skills", "lint"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_finds_existing_vendor_dirs(self):
        """_discover_vendor_dirs finds all subdirectories under vendor/."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            dirs = _discover_vendor_dirs(self.tmpdir)
        basenames = [os.path.basename(d) for d in dirs]
        self.assertIn("alpha", basenames)
        self.assertIn("beta", basenames)
        self.assertEqual(len(dirs), 2)

    def test_deduplicates_when_sflo_root_equals_sflo_base(self):
        """When SFLO_ROOT == sflo_base, vendors are not duplicated."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            dirs = _discover_vendor_dirs(self.tmpdir)
        # Should still be 2, not 4
        self.assertEqual(len(dirs), 2)

    @unittest.skipUnless(
        SYMLINKS_SUPPORTED, "symlink creation not available (e.g. unprivileged Windows)"
    )
    def test_deduplicates_via_realpath(self):
        """Symlinked sflo_base pointing to same dir is deduplicated."""
        symlink = os.path.join(self.tmpdir, "symlinked_base")
        os.symlink(self.tmpdir, symlink)
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            dirs = _discover_vendor_dirs(symlink)
        self.assertEqual(len(dirs), 2)

    def test_no_vendor_dir_returns_empty(self):
        """If no vendor/ directory exists, returns empty list."""
        empty_dir = tempfile.mkdtemp()
        try:
            with patch("src.machine.SFLO_ROOT", empty_dir):
                dirs = _discover_vendor_dirs(empty_dir)
            self.assertEqual(dirs, [])
        finally:
            shutil.rmtree(empty_dir)

    def test_multiple_vendors_from_temp_second_dir(self):
        """Create a second base with different vendors, verify both scanned."""
        second_base = tempfile.mkdtemp()
        os.makedirs(os.path.join(second_base, "vendor", "gamma", "skills", "fmt"))
        try:
            with patch("src.machine.SFLO_ROOT", self.tmpdir):
                dirs = _discover_vendor_dirs(second_base)
            basenames = [os.path.basename(d) for d in dirs]
            # SFLO_ROOT has alpha, beta; second_base has gamma
            self.assertIn("alpha", basenames)
            self.assertIn("beta", basenames)
            self.assertIn("gamma", basenames)
            self.assertEqual(len(dirs), 3)
        finally:
            shutil.rmtree(second_base)

    def test_files_in_vendor_dir_ignored(self):
        """Regular files (non-dirs) in vendor/ are not returned."""
        # Create a file alongside the vendor subdirs
        with open(
            os.path.join(self.vendor_root, "README.md"), "w", encoding="utf-8"
        ) as f:
            f.write("ignore me")
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            dirs = _discover_vendor_dirs(self.tmpdir)
        basenames = [os.path.basename(d) for d in dirs]
        self.assertNotIn("README.md", basenames)
        self.assertEqual(len(dirs), 2)

    def test_sflo_root_entries_come_first(self):
        """SFLO_ROOT vendor dirs appear before sflo_base vendor dirs."""
        second_base = tempfile.mkdtemp()
        os.makedirs(os.path.join(second_base, "vendor", "zzz-last", "skills"))
        try:
            with patch("src.machine.SFLO_ROOT", self.tmpdir):
                dirs = _discover_vendor_dirs(second_base)
            # First entries from SFLO_ROOT (alpha, beta sorted), then second_base
            basenames = [os.path.basename(d) for d in dirs]
            alpha_idx = basenames.index("alpha")
            zzz_idx = basenames.index("zzz-last")
            self.assertLess(alpha_idx, zzz_idx)
        finally:
            shutil.rmtree(second_base)


class TestResolveSkillPaths(unittest.TestCase):
    """Tests for resolve_skill_paths(skill_names, sflo_base)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.vendor_root = os.path.join(self.tmpdir, "vendor")
        # Vendor "agent-skills" with skill "tdd"
        self.tdd_dir = os.path.join(self.vendor_root, "agent-skills", "skills", "tdd")
        os.makedirs(self.tdd_dir)
        self.tdd_skill = os.path.join(self.tdd_dir, "SKILL.md")
        with open(self.tdd_skill, "w", encoding="utf-8") as f:
            f.write("# TDD Skill\n")
        # Vendor "agent-skills" with skill "lint"
        self.lint_dir = os.path.join(self.vendor_root, "agent-skills", "skills", "lint")
        os.makedirs(self.lint_dir)
        self.lint_skill = os.path.join(self.lint_dir, "SKILL.md")
        with open(self.lint_skill, "w", encoding="utf-8") as f:
            f.write("# Lint Skill\n")
        # Second vendor "custom-vendor" with skill "fmt"
        self.fmt_dir = os.path.join(self.vendor_root, "custom-vendor", "skills", "fmt")
        os.makedirs(self.fmt_dir)
        self.fmt_skill = os.path.join(self.fmt_dir, "SKILL.md")
        with open(self.fmt_skill, "w", encoding="utf-8") as f:
            f.write("# Fmt Skill\n")
        # Second vendor "custom-vendor" also has "tdd" (for priority test)
        self.custom_tdd_dir = os.path.join(
            self.vendor_root, "custom-vendor", "skills", "tdd"
        )
        os.makedirs(self.custom_tdd_dir)
        self.custom_tdd_skill = os.path.join(self.custom_tdd_dir, "SKILL.md")
        with open(self.custom_tdd_skill, "w", encoding="utf-8") as f:
            f.write("# Custom TDD\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_unqualified_ambiguous_fails_with_qualification_choices(self):
        """Unqualified duplicate leaves fail instead of silently choosing."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            with self.assertRaisesRegex(SkillResolutionError, "ambiguous"):
                resolve_skill_paths(["tdd"], self.tmpdir)

    def test_unqualified_multiple_skills(self):
        """Multiple unqualified skills all resolve."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            paths = resolve_skill_paths(["agent-skills/tdd", "lint", "fmt"], self.tmpdir)
        self.assertEqual(len(paths), 3)

    def test_qualified_resolves_specific_vendor(self):
        """Qualified 'agent-skills/tdd' resolves to that specific vendor."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            paths = resolve_skill_paths(["agent-skills/tdd"], self.tmpdir)
        self.assertEqual(len(paths), 1)
        self.assertIn("agent-skills", paths[0])
        self.assertIn("tdd", paths[0])

    def test_qualified_different_vendor(self):
        """Qualified 'custom-vendor/fmt' resolves correctly."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            paths = resolve_skill_paths(["custom-vendor/fmt"], self.tmpdir)
        self.assertEqual(len(paths), 1)
        self.assertIn("custom-vendor", paths[0])
        self.assertIn("fmt", paths[0])

    def test_qualified_wrong_vendor_no_resolve(self):
        """Qualified name with wrong vendor raises SkillResolutionError."""
        # "custom-vendor/lint" — lint exists only in agent-skills, not custom-vendor
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            with self.assertRaises(SkillResolutionError):
                resolve_skill_paths(["custom-vendor/lint"], self.tmpdir)

    def test_unknown_vendor_qualified_empty(self):
        """Unknown vendor in qualified name raises SkillResolutionError."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            with self.assertRaises(SkillResolutionError):
                resolve_skill_paths(["nonexistent-vendor/tdd"], self.tmpdir)

    def test_unknown_skill_name_empty(self):
        """Unknown skill name raises SkillResolutionError."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            with self.assertRaises(SkillResolutionError):
                resolve_skill_paths(["does-not-exist"], self.tmpdir)

    def test_path_traversal_dotdot_rejected(self):
        """Path traversal with '..' raises SkillResolutionError."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            with self.assertRaises(SkillResolutionError):
                resolve_skill_paths(["../etc/passwd"], self.tmpdir)

    def test_path_traversal_dotdot_in_qualified_rejected(self):
        """Qualified name with '..' traversal raises SkillResolutionError."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            with self.assertRaises(SkillResolutionError):
                resolve_skill_paths(["agent-skills/../../../etc"], self.tmpdir)

    def test_path_traversal_backslash_rejected(self):
        r"""Backslash in skill name raises SkillResolutionError."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            with self.assertRaises(SkillResolutionError):
                resolve_skill_paths([r"agent-skills\tdd"], self.tmpdir)

    def test_empty_skill_names_returns_empty(self):
        """Empty skill_names list returns empty result."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            paths = resolve_skill_paths([], self.tmpdir)
        self.assertEqual(paths, [])

    def test_none_like_empty(self):
        """None-ish (falsy) skill_names returns empty result."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            paths = resolve_skill_paths(None, self.tmpdir)
        self.assertEqual(paths, [])

    def test_qualified_empty_parts_rejected(self):
        """Qualified name with empty vendor or skill part raises SkillResolutionError."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            with self.assertRaises(SkillResolutionError):
                resolve_skill_paths(["/tdd"], self.tmpdir)
            with self.assertRaises(SkillResolutionError):
                resolve_skill_paths(["agent-skills/"], self.tmpdir)

    def test_resolved_paths_are_absolute(self):
        """All returned paths are absolute paths."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            paths = resolve_skill_paths(["agent-skills/tdd", "custom-vendor/fmt"], self.tmpdir)
        for p in paths:
            self.assertTrue(os.path.isabs(p), f"Path not absolute: {p}")

    def test_resolved_paths_exist_on_disk(self):
        """All returned paths actually exist as files."""
        with patch("src.machine.SFLO_ROOT", self.tmpdir):
            paths = resolve_skill_paths(["agent-skills/tdd", "lint", "fmt"], self.tmpdir)
        for p in paths:
            self.assertTrue(os.path.isfile(p), f"File does not exist: {p}")


class TestResolveSkillPathsWithRealVendor(unittest.TestCase):
    """Integration test against the real vendor/ directory structure."""

    # Derived from the test file location so this is portable — the old
    # hardcoded maintainer Mac path did not exist on any other machine.
    SFLO_BASE = SFLO_REPO_ROOT

    def test_real_vendor_discovered(self):
        """_discover_vendor_dirs finds real Matt Pocock vendor."""
        with patch("src.machine.SFLO_ROOT", self.SFLO_BASE):
            dirs = _discover_vendor_dirs(self.SFLO_BASE)
        basenames = [os.path.basename(d) for d in dirs]
        self.assertIn("mattpocock-skills", basenames)

    def test_real_skill_resolves(self):
        """A known real skill resolves from the actual vendor dir."""
        with patch("src.machine.SFLO_ROOT", self.SFLO_BASE):
            paths = resolve_skill_paths(["tdd"], self.SFLO_BASE)
        self.assertEqual(len(paths), 1)
        self.assertTrue(os.path.isfile(paths[0]))
        self.assertIn("mattpocock-skills", paths[0])

    def test_real_qualified_resolves(self):
        """Qualified name resolves against real vendor."""
        with patch("src.machine.SFLO_ROOT", self.SFLO_BASE):
            paths = resolve_skill_paths(
                ["mattpocock-skills/engineering/tdd"], self.SFLO_BASE
            )
        self.assertEqual(len(paths), 1)
        self.assertTrue(os.path.isfile(paths[0]))


if __name__ == "__main__":
    unittest.main()
