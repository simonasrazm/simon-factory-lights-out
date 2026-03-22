#!/usr/bin/env python3
"""Integration tests for SFLO scaffold CLI commands."""

import json
import os
import shutil
import tempfile
import unittest

from conftest import run_scaffold, BINDINGS_YAML, PASSING_ARTIFACTS


class TestInitCommand(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write_bindings(self, content):
        path = os.path.join(self.tmpdir, "bindings.yaml")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_init_creates_state(self):
        path = self.write_bindings(BINDINGS_YAML)
        result = run_scaffold("init", "--bindings", path, "--sflo-dir", self.sflo_dir)
        self.assertTrue(result["ok"])
        with open(os.path.join(self.sflo_dir, "state.json")) as f:
            state = json.load(f)
        self.assertEqual(state["current_state"], "scout")

    def test_init_returns_roles(self):
        path = self.write_bindings(BINDINGS_YAML)
        result = run_scaffold("init", "--bindings", path, "--sflo-dir", self.sflo_dir)
        self.assertEqual(result["roles"]["pm"]["model"], "opus")
        self.assertEqual(result["roles"]["dev"]["model"], "sonnet")

    def test_init_missing_bindings(self):
        result = run_scaffold("init", "--bindings", "/nonexistent.yaml",
                              "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        self.assertFalse(result["ok"])


class TestAssignCommand(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")
        path = os.path.join(self.tmpdir, "bindings.yaml")
        with open(path, "w") as f:
            f.write(BINDINGS_YAML)
        run_scaffold("init", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_assign_sets_state(self):
        result = run_scaffold("assign", "--pm", "agents/pm", "--dev", "agents/dev",
                              "--qa", "agents/qa", "--sflo-dir", self.sflo_dir)
        self.assertTrue(result["ok"])
        self.assertEqual(result["assignments"]["pm"], "agents/pm")
        self.assertEqual(result["next"]["state"], "gate-1")

    def test_assign_with_extras(self):
        result = run_scaffold("assign", "--pm", "agents/pm", "--dev", "agents/dev",
                              "--qa", "agents/qa", "--extra", "designer=agents/designer",
                              "--sflo-dir", self.sflo_dir)
        self.assertIn("designer", result["assignments"])

    def test_assign_unknown_arg_rejected(self):
        result = run_scaffold("assign", "--pm", "agents/pm", "--devv", "agents/dev",
                              "--qa", "agents/qa", "--sflo-dir", self.sflo_dir)
        self.assertFalse(result["ok"])
        self.assertIn("Unknown", result["error"])


class TestNextCommand(unittest.TestCase):
    """Integration test: full gate validation via CLI."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")
        os.makedirs(os.path.join(self.tmpdir, "gates"), exist_ok=True)
        path = os.path.join(self.tmpdir, "bindings.yaml")
        with open(path, "w") as f:
            f.write(BINDINGS_YAML)
        run_scaffold("init", "--bindings", path, "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        run_scaffold("assign", "--pm", "agents/pm", "--dev", "agents/dev",
                     "--qa", "agents/qa", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def artifact(self, name, content):
        with open(os.path.join(self.sflo_dir, name), "w") as f:
            f.write(content)

    def test_full_pipeline_traversal(self):
        """Walk through all 5 gates via next, verify pipeline completes."""
        for name, content in PASSING_ARTIFACTS.items():
            self.artifact(name, content)
            result = run_scaffold("next", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
            self.assertTrue(result["ok"])
            self.assertTrue(result["pass"])

        # Final state should be done
        with open(os.path.join(self.sflo_dir, "state.json")) as f:
            state = json.load(f)
        self.assertEqual(state["current_state"], "done")


class TestStatusCommand(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")
        os.makedirs(self.sflo_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_status_shows_grades(self):
        state = {
            "current_state": "done", "bindings": {}, "assignments": {},
            "inner_loops": 0, "outer_loops": 0,
            "gates": {str(g): {"status": "done", "artifact": a}
                      for g, a in {1: "SCOPE.md", 2: "BUILD-STATUS.md", 3: "QA-REPORT.md",
                                    4: "PM-VERIFY.md", 5: "SHIP-DECISION.md"}.items()},
        }
        with open(os.path.join(self.sflo_dir, "state.json"), "w") as f:
            json.dump(state, f)

        for name, content in {"QA-REPORT.md": "### Grade: A\n",
                               "PM-VERIFY.md": "### Verdict: APPROVED\n",
                               "SHIP-DECISION.md": "### Decision: SHIP\n"}.items():
            with open(os.path.join(self.sflo_dir, name), "w") as f:
                f.write(content)

        result = run_scaffold("status", "--sflo-dir", self.sflo_dir)
        self.assertTrue(result["ok"])
        self.assertEqual(result["gates"]["3"]["grade"], "A")
        self.assertEqual(result["gates"]["4"]["verdict"], "APPROVED")
        self.assertEqual(result["gates"]["5"]["decision"], "SHIP")


class TestPromptCommand(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")
        os.makedirs(os.path.join(self.tmpdir, "gates"), exist_ok=True)
        path = os.path.join(self.tmpdir, "bindings.yaml")
        with open(path, "w") as f:
            f.write(BINDINGS_YAML)
        run_scaffold("init", "--bindings", path, "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        run_scaffold("assign", "--pm", "agents/pm", "--dev", "agents/dev",
                     "--qa", "agents/qa", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_prompt_at_gate1(self):
        result = run_scaffold("prompt", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        self.assertTrue(result["ok"])
        self.assertIn("PM", result["prompt"])

    def test_prompt_at_done(self):
        with open(os.path.join(self.sflo_dir, "state.json")) as f:
            state = json.load(f)
        state["current_state"] = "done"
        with open(os.path.join(self.sflo_dir, "state.json"), "w") as f:
            json.dump(state, f)
        result = run_scaffold("prompt", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        self.assertFalse(result["ok"])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
