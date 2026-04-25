#!/usr/bin/env python3
"""Unit tests for SFLO bindings.yaml parser."""

import os
import shutil
import tempfile
import unittest

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.bindings import (
    parse_bindings,
    resolve_bindings_path,
    load_security_config,
    SECURITY_KEYS,
)


class TestParseBindings(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, content):
        path = os.path.join(self.tmpdir, "bindings.yaml")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_standard_bindings(self):
        roles, err = parse_bindings(
            self.write(
                "roles:\n  pm:\n    model: opus\n    thinking: extended\n"
                "  dev:\n    model: sonnet\n    thinking: off\n"
            )
        )
        self.assertIsNone(err)
        self.assertEqual(roles["pm"]["model"], "opus")
        self.assertEqual(roles["dev"]["thinking"], "off")

    def test_missing_file(self):
        roles, err = parse_bindings("/nonexistent.yaml")
        self.assertIsNone(roles)
        self.assertIn("not found", err)

    def test_tabs_rejected(self):
        roles, err = parse_bindings(self.write("roles:\n\tpm:\n\t\tmodel: opus\n"))
        self.assertIsNone(roles)
        self.assertIn("tabs", err)

    def test_empty_file(self):
        roles, err = parse_bindings(self.write("# just a comment\n"))
        self.assertIsNone(roles)
        self.assertIn("No roles", err)

    def test_comments_skipped(self):
        roles, err = parse_bindings(
            self.write("roles:\n  pm:\n    # this is a comment\n    model: opus\n")
        )
        self.assertIsNone(err)
        self.assertEqual(roles["pm"]["model"], "opus")

    def test_colon_in_value(self):
        roles, err = parse_bindings(
            self.write("roles:\n  scout:\n    agent: ./path/to:agent\n")
        )
        self.assertIsNone(err)
        self.assertEqual(roles["scout"]["agent"], "./path/to:agent")


class TestResolveBindingsPath(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.tmpdir)

    def test_explicit_path(self):
        path = os.path.join(self.tmpdir, "custom.yaml")
        with open(path, "w") as f:
            f.write("roles:\n")
        self.assertEqual(resolve_bindings_path(path), path)

    def test_cwd_path(self):
        path = os.path.join(self.tmpdir, "bindings.yaml")
        with open(path, "w") as f:
            f.write("roles:\n")
        # Use realpath to normalize macOS /var -> /private/var symlink
        self.assertEqual(
            os.path.realpath(resolve_bindings_path()), os.path.realpath(path)
        )

    def test_not_found(self):
        # Ensure no bindings.yaml exists in cwd (tmpdir)
        candidate = os.path.join(self.tmpdir, "bindings.yaml")
        if os.path.isfile(candidate):
            os.remove(candidate)
        result = resolve_bindings_path()
        # Result could be None or a path found in a parent/sflo location;
        # the key assertion is it's not in our empty tmpdir
        if result is not None:
            self.assertNotEqual(os.path.realpath(result), os.path.realpath(candidate))


class TestLoadSecurityConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "bindings.yaml")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write(self, content):
        with open(self.path, "w") as f:
            f.write(content)

    def test_missing_file_returns_all_false(self):
        cfg = load_security_config(os.path.join(self.tmpdir, "nonexistent.yaml"))
        self.assertEqual(set(cfg.keys()), set(SECURITY_KEYS))
        self.assertTrue(all(v is False for v in cfg.values()))

    def test_no_security_block_returns_all_false(self):
        self.write("roles:\n  pm:\n    model: opus\n")
        cfg = load_security_config(self.path)
        self.assertTrue(all(v is False for v in cfg.values()))

    def test_all_true(self):
        self.write(
            "security:\n"
            "  isolate_settings: true\n"
            "  no_session_persistence: true\n"
            "  sandbox_config_dir: true\n"
            "  require_permission: true\n"
            "  wipe_sandbox: true\n"
        )
        cfg = load_security_config(self.path)
        self.assertTrue(all(v is True for v in cfg.values()))

    def test_truthy_aliases(self):
        # yes/on/1 all parse as True
        self.write(
            "security:\n"
            "  isolate_settings: yes\n"
            "  no_session_persistence: on\n"
            "  sandbox_config_dir: 1\n"
        )
        cfg = load_security_config(self.path)
        self.assertTrue(cfg["isolate_settings"])
        self.assertTrue(cfg["no_session_persistence"])
        self.assertTrue(cfg["sandbox_config_dir"])

    def test_falsy_aliases(self):
        # explicit no/off/0 all parse as False (same as default)
        self.write(
            "security:\n"
            "  isolate_settings: no\n"
            "  no_session_persistence: off\n"
            "  sandbox_config_dir: 0\n"
        )
        cfg = load_security_config(self.path)
        self.assertFalse(cfg["isolate_settings"])
        self.assertFalse(cfg["no_session_persistence"])
        self.assertFalse(cfg["sandbox_config_dir"])

    def test_partial_block_unset_keys_default_false(self):
        self.write(
            "security:\n"
            "  isolate_settings: true\n"
        )
        cfg = load_security_config(self.path)
        self.assertTrue(cfg["isolate_settings"])
        self.assertFalse(cfg["no_session_persistence"])
        self.assertFalse(cfg["sandbox_config_dir"])
        self.assertFalse(cfg["require_permission"])
        self.assertFalse(cfg["wipe_sandbox"])

    def test_unknown_key_ignored(self):
        # Forward-compat: a future host may declare a key sflo doesn't yet know.
        self.write(
            "security:\n"
            "  isolate_settings: true\n"
            "  future_unknown_key: true\n"
        )
        cfg = load_security_config(self.path)
        self.assertTrue(cfg["isolate_settings"])
        self.assertNotIn("future_unknown_key", cfg)

    def test_security_after_roles(self):
        # The `security:` block at top level must be recognized regardless of
        # whether it appears before or after the `roles:` block.
        self.write(
            "roles:\n"
            "  pm:\n"
            "    model: opus\n"
            "security:\n"
            "  isolate_settings: true\n"
        )
        cfg = load_security_config(self.path)
        self.assertTrue(cfg["isolate_settings"])

    def test_comment_lines_skipped(self):
        self.write(
            "security:\n"
            "  # commented out\n"
            "  isolate_settings: true\n"
        )
        cfg = load_security_config(self.path)
        self.assertTrue(cfg["isolate_settings"])

    def test_default_sflo_bindings_yaml_all_false(self):
        # The shipped sflo/bindings.yaml must keep all toggles OFF by default
        # (host-trusted single-user posture). Loading the actual file must
        # yield exactly the all-false config.
        repo_yaml = os.path.join(
            os.path.dirname(__file__), "..", "bindings.yaml"
        )
        if not os.path.isfile(repo_yaml):
            self.skipTest("sflo/bindings.yaml not found")
        cfg = load_security_config(repo_yaml)
        for k in SECURITY_KEYS:
            self.assertFalse(
                cfg[k],
                f"Default sflo/bindings.yaml ships {k}=true; "
                f"per host-trusted policy it must be false.",
            )


if __name__ == "__main__":
    unittest.main()
