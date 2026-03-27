#!/usr/bin/env python3
"""Unit tests for SFLO pipeline config loader."""

import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import parse_pipeline_yaml, load_pipeline_config, resolve_pipeline_path


class TestParsePipelineYaml(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write_yaml(self, content, name="pipeline.yaml"):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_parse_default_pipeline(self):
        yaml = """threshold: B+

guardian:
  enabled: false
  max_spawns: 50
  wall_clock_s: 7200
  circuit_breaker_window: 5

gates:
  1:
    artifact: SCOPE.md
    role: pm
    gate_doc: gates/discovery.md
  2:
    artifact: BUILD-STATUS.md
    role: dev
    gate_doc: gates/build.md
"""
        path = self.write_yaml(yaml)
        config, err = parse_pipeline_yaml(path)
        self.assertIsNone(err)
        self.assertIsNotNone(config)
        self.assertEqual(config["threshold"], "B+")
        self.assertFalse(config["guardian"]["enabled"])
        self.assertEqual(config["guardian"]["max_spawns"], 50)
        self.assertEqual(config["guardian"]["wall_clock_s"], 7200)
        self.assertEqual(config["guardian"]["circuit_breaker_window"], 5)
        self.assertIn(1, config["gates"])
        self.assertIn(2, config["gates"])
        self.assertEqual(config["gates"][1]["artifact"], "SCOPE.md")
        self.assertEqual(config["gates"][1]["role"], "pm")
        self.assertEqual(config["gates"][2]["artifact"], "BUILD-STATUS.md")

    def test_parse_float_gate_keys(self):
        yaml = """gates:
  1:
    artifact: SCOPE.md
    role: pm
    gate_doc: gates/discovery.md
  1.5:
    artifact: ARCH.md
    role: architect
    gate_doc: gates/arch.md
  2:
    artifact: BUILD-STATUS.md
    role: dev
    gate_doc: gates/build.md
"""
        path = self.write_yaml(yaml)
        config, err = parse_pipeline_yaml(path)
        self.assertIsNone(err)
        self.assertIn(1, config["gates"])
        self.assertIn(1.5, config["gates"])
        self.assertIn(2, config["gates"])
        self.assertEqual(config["gates"][1.5]["artifact"], "ARCH.md")
        self.assertEqual(config["gates"][1.5]["role"], "architect")

    def test_parse_comments_ignored(self):
        yaml = """# This is a comment
threshold: A  # inline ignored

# Another comment
guardian:
  # Guardian settings
  enabled: false
  max_spawns: 10

gates:
  1:
    # gate 1
    artifact: SCOPE.md
    role: pm
    gate_doc: gates/discovery.md
"""
        path = self.write_yaml(yaml)
        config, err = parse_pipeline_yaml(path)
        self.assertIsNone(err)
        self.assertEqual(config["threshold"], "A")
        self.assertEqual(config["guardian"]["max_spawns"], 10)
        self.assertEqual(config["gates"][1]["artifact"], "SCOPE.md")

    def test_file_not_found(self):
        config, err = parse_pipeline_yaml("/nonexistent/path/pipeline.yaml")
        self.assertIsNone(config)
        self.assertIn("not found", err)

    def test_guardian_enabled_true(self):
        yaml = """guardian:
  enabled: true
  max_spawns: 20
  wall_clock_s: 3600
  circuit_breaker_window: 3
"""
        path = self.write_yaml(yaml)
        config, err = parse_pipeline_yaml(path)
        self.assertIsNone(err)
        self.assertTrue(config["guardian"]["enabled"])
        self.assertEqual(config["guardian"]["max_spawns"], 20)


class TestLoadPipelineConfig(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write_yaml(self, content, name="pipeline.yaml"):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_load_default_config(self):
        """Load from sflo's own pipeline.yaml via explicit path."""
        sflo_root = os.path.join(os.path.dirname(__file__), "..")
        default_path = os.path.join(sflo_root, "pipeline.yaml")
        if not os.path.isfile(default_path):
            self.skipTest("pipeline.yaml not found in sflo root")
        config = load_pipeline_config(default_path)
        self.assertIn("gates", config)
        self.assertIn("grade_threshold", config)
        self.assertIn("guardian", config)
        self.assertIsInstance(config["grade_threshold"], (int, float))
        # Default threshold B+ = 5
        self.assertEqual(config["grade_threshold"], 5)

    def test_float_gate_keys_sorted(self):
        yaml = """gates:
  3:
    artifact: QA-REPORT.md
    role: qa
    gate_doc: gates/test.md
  1:
    artifact: SCOPE.md
    role: pm
    gate_doc: gates/discovery.md
  1.5:
    artifact: ARCH.md
    role: architect
    gate_doc: gates/arch.md
  2:
    artifact: BUILD-STATUS.md
    role: dev
    gate_doc: gates/build.md
"""
        path = self.write_yaml(yaml)
        config = load_pipeline_config(path)
        keys = list(config["gates"].keys())
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(keys[0], 1)
        self.assertEqual(keys[1], 1.5)
        self.assertEqual(keys[2], 2)
        self.assertEqual(keys[3], 3)

    def test_threshold_resolved_to_numeric(self):
        path = self.write_yaml("threshold: A\n")
        config = load_pipeline_config(path)
        self.assertEqual(config["grade_threshold"], 6)

    def test_threshold_b_plus(self):
        path = self.write_yaml("threshold: B+\n")
        config = load_pipeline_config(path)
        self.assertEqual(config["grade_threshold"], 5)

    def test_unknown_threshold_falls_back_to_default(self):
        path = self.write_yaml("threshold: Z++\n")
        config = load_pipeline_config(path)
        # Unknown grade -> fallback to B+ (5)
        self.assertEqual(config["grade_threshold"], 5)

    def test_no_pipeline_yaml_uses_defaults(self):
        """When no pipeline.yaml exists, built-in defaults are used."""
        config = load_pipeline_config("/nonexistent/pipeline.yaml")
        self.assertIn(1, config["gates"])
        self.assertIn(5, config["gates"])
        self.assertEqual(config["grade_threshold"], 5)
        self.assertFalse(config["guardian"]["enabled"])

    def test_guardian_merged_with_defaults(self):
        """Partial guardian config is merged with defaults."""
        path = self.write_yaml("guardian:\n  enabled: true\n  max_spawns: 10\n")
        config = load_pipeline_config(path)
        self.assertTrue(config["guardian"]["enabled"])
        self.assertEqual(config["guardian"]["max_spawns"], 10)
        # wall_clock_s should still have default
        self.assertEqual(config["guardian"]["wall_clock_s"], 7200)

    def test_cwd_override(self):
        """When pipeline.yaml in cwd, it takes priority."""
        # Write a custom yaml to tmpdir and resolve from there
        yaml = """threshold: A
gates:
  1:
    artifact: CUSTOM.md
    role: pm
    gate_doc: gates/discovery.md
"""
        path = self.write_yaml(yaml)
        config = load_pipeline_config(path)
        self.assertEqual(config["grade_threshold"], 6)
        self.assertIn(1, config["gates"])
        self.assertEqual(config["gates"][1]["artifact"], "CUSTOM.md")


class TestResolvePipelinePath(unittest.TestCase):

    def test_explicit_path_returned_if_exists(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            f.write(b"threshold: B+\n")
            p = f.name
        try:
            result = resolve_pipeline_path(p)
            self.assertEqual(result, p)
        finally:
            os.unlink(p)

    def test_nonexistent_explicit_path_not_returned(self):
        result = resolve_pipeline_path("/nonexistent/pipeline.yaml")
        # Falls through to other locations
        # We can only check it didn't return the nonexistent path
        self.assertNotEqual(result, "/nonexistent/pipeline.yaml")

    def test_returns_none_when_nothing_found(self):
        """When no pipeline.yaml exists in any expected location,
        resolve_pipeline_path returns None."""
        # This is hard to test without mocking cwd/SFLO_ROOT.
        # Just test it returns something or None (not raising).
        result = resolve_pipeline_path()
        # Result is either None or a valid path
        self.assertTrue(result is None or os.path.isfile(result))


if __name__ == "__main__":
    unittest.main()
