#!/usr/bin/env python3
"""Integration tests for SFLO stop hook."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import run_scaffold, run_hook, BINDINGS_YAML, PASSING_ARTIFACTS


class TestHookDecisions(unittest.TestCase):
    """Test stop hook block/pass decisions with isolated state."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.state_dir)

    def write_state(self, current):
        state = {
            "current_state": current,
            "bindings": {
                "pm": {"model": "opus"},
                "dev": {"model": "sonnet"},
                "qa": {"model": "sonnet"},
            },
            "assignments": {"pm": "agents/pm", "dev": "agents/dev", "qa": "agents/qa"},
            "inner_loops": 0,
            "outer_loops": 0,
            "gates": {
                str(g): {"status": "waiting", "artifact": a}
                for g, a in {
                    1: "SCOPE.md",
                    2: "BUILD-STATUS.md",
                    3: "QA-REPORT.md",
                    4: "PM-VERIFY.md",
                    5: "SHIP-DECISION.md",
                }.items()
            },
        }
        with open(os.path.join(self.state_dir, "state.json"), "w") as f:
            json.dump(state, f)

    def test_no_pipeline_allows_stop(self):
        result, rc = run_hook(self.state_dir)
        self.assertIsNone(result)
        self.assertEqual(rc, 0)

    def test_done_allows_stop(self):
        self.write_state("done")
        result, rc = run_hook(self.state_dir)
        self.assertIsNone(result)

    def test_escalate_allows_stop(self):
        self.write_state("escalate")
        result, rc = run_hook(self.state_dir)
        self.assertIsNone(result)

    def test_active_pipeline_blocks(self):
        self.write_state("gate-1")
        result, rc = run_hook(self.state_dir)
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "block")
        self.assertIn("SFLO PIPELINE", result["reason"])

    def test_loop_protection_same_state_allows_stop(self):
        self.write_state("gate-1")
        run_hook(self.state_dir, stop_active=False)
        result, rc = run_hook(self.state_dir, stop_active=True)
        self.assertIsNone(result)

    def test_loop_protection_changed_state_blocks(self):
        self.write_state("gate-1")
        run_hook(self.state_dir, stop_active=False)
        self.write_state("gate-2")
        result, rc = run_hook(self.state_dir, stop_active=True)
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "block")


class TestHookPipelineTraversal(unittest.TestCase):
    """Test prompt command drives through all gates correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")
        os.makedirs(os.path.join(self.tmpdir, "gates"), exist_ok=True)
        bindings = os.path.join(self.tmpdir, "bindings.yaml")
        with open(bindings, "w") as f:
            f.write(BINDINGS_YAML)
        run_scaffold(
            "init", "--bindings", bindings, "--sflo-dir", self.sflo_dir, cwd=self.tmpdir
        )
        run_scaffold(
            "assign",
            "--pm",
            "agents/pm",
            "--dev",
            "agents/dev",
            "--qa",
            "agents/qa",
            "--sflo-dir",
            self.sflo_dir,
            cwd=self.tmpdir,
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def artifact(self, name, content):
        with open(os.path.join(self.sflo_dir, name), "w") as f:
            f.write(content)

    def prompt(self):
        return run_scaffold("prompt", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)

    def advance(self):
        return run_scaffold("next", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)

    def test_full_traversal(self):
        """Prompt returns correct agent for each gate in sequence."""
        gate_sequence = {
            "gate-1": ("SCOPE.md", "PM"),
            "gate-2": ("BUILD-STATUS.md", "DEV"),
            "gate-3": ("QA-REPORT.md", "QA"),
            "gate-4": ("PM-VERIFY.md", "PM-VERIFY"),
            "gate-5": ("SHIP-DECISION.md", "SHIP-DECISION"),
        }
        for gate, (artifact_name, expected_keyword) in gate_sequence.items():
            r = self.prompt()
            self.assertTrue(r["ok"], f"Prompt failed at {gate}")
            self.assertIn(
                expected_keyword, r["prompt"], f"Missing {expected_keyword} at {gate}"
            )
            self.artifact(artifact_name, PASSING_ARTIFACTS[artifact_name])
            self.advance()

        # Final prompt should indicate terminal
        r = self.prompt()
        self.assertFalse(r["ok"])

    def test_loop_back_prompts_dev(self):
        """After QA failure, prompt should re-target DEV."""
        self.artifact("SCOPE.md", PASSING_ARTIFACTS["SCOPE.md"])
        self.advance()
        self.artifact("BUILD-STATUS.md", PASSING_ARTIFACTS["BUILD-STATUS.md"])
        self.advance()
        self.artifact("QA-REPORT.md", "### Grade: F\n### Issues Found\n1. CRITICAL\n")
        result = self.advance()
        self.assertEqual(result["state"], "loop-inner")

        r = self.prompt()
        self.assertTrue(r["ok"])
        self.assertIn("DEV", r["prompt"])


if __name__ == "__main__":
    unittest.main()
