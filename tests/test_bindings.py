#!/usr/bin/env python3
"""Unit tests for SFLO bindings.yaml parser."""

import os
import shutil
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.bindings import parse_bindings, resolve_bindings_path


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
        roles, err = parse_bindings(self.write(
            "roles:\n  pm:\n    model: opus\n    thinking: extended\n"
            "  dev:\n    model: sonnet\n    thinking: off\n"))
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
        roles, err = parse_bindings(self.write(
            "roles:\n  pm:\n    # this is a comment\n    model: opus\n"))
        self.assertIsNone(err)
        self.assertEqual(roles["pm"]["model"], "opus")

    def test_colon_in_value(self):
        roles, err = parse_bindings(self.write(
            "roles:\n  scout:\n    agent: ./path/to:agent\n"))
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
        self.assertEqual(resolve_bindings_path(), path)

    def test_not_found(self):
        self.assertIsNone(resolve_bindings_path())


if __name__ == "__main__":
    unittest.main()
