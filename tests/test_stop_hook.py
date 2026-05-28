#!/usr/bin/env python3
"""Integration tests for SFLO stop hook."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import run_scaffold, run_hook, PASSING_ARTIFACTS


CLAUDE_HOOK = os.path.join(
    os.path.dirname(__file__), "..", "src", "hooks", "claude-code", "stop_hook.py"
)
CURSOR_HOOK = os.path.join(
    os.path.dirname(__file__), "..", "src", "hooks", "cursor", "stop_hook.py"
)


class TestHookDecisions(unittest.TestCase):
    """Test stop hook block/pass decisions with isolated state."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.state_dir)

    def write_state(self, current):
        state = {
            "current_state": current,
            "roles": {
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
        with open(
            os.path.join(self.state_dir, "state.json"), "w", encoding="utf-8"
        ) as f:
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

    def test_active_factory_state_blocks(self):
        """Hook finds active named factory state under .sflo/<factory>/."""
        project_dir = tempfile.mkdtemp()
        try:
            factory_dir = os.path.join(project_dir, ".sflo", "click-counter")
            os.makedirs(factory_dir, exist_ok=True)
            self.write_state("gate-1")
            shutil.copy2(
                os.path.join(self.state_dir, "state.json"),
                os.path.join(factory_dir, "state.json"),
            )
            registry = {
                "version": 1,
                "factories": {
                    "click-counter": {
                        "status": "active",
                        "pid": os.getpid(),
                        "last_active": "2026-05-27T00:00:00+00:00",
                        "sflo_dir": factory_dir,
                    }
                },
            }
            with open(
                os.path.join(project_dir, ".sflo", "registry.json"),
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(registry, f)

            hook_input = json.dumps(
                {
                    "stop_hook_active": False,
                    "cwd": project_dir,
                    "session_id": "test",
                    "last_assistant_message": "test",
                }
            )
            result = subprocess.run(
                [sys.executable, CLAUDE_HOOK],
                input=hook_input,
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip(), result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["decision"], "block")
            self.assertIn("SFLO PIPELINE", data["reason"])
            self.assertTrue(os.path.isfile(os.path.join(factory_dir, ".last_hook_state")))
        finally:
            shutil.rmtree(project_dir)

    def test_cursor_hook_active_factory_returns_followup(self):
        """Cursor hook also finds active named factory state."""
        project_dir = tempfile.mkdtemp()
        try:
            factory_dir = os.path.join(project_dir, ".sflo", "click-counter")
            os.makedirs(factory_dir, exist_ok=True)
            self.write_state("gate-1")
            shutil.copy2(
                os.path.join(self.state_dir, "state.json"),
                os.path.join(factory_dir, "state.json"),
            )
            registry = {
                "version": 1,
                "factories": {
                    "click-counter": {
                        "status": "active",
                        "pid": os.getpid(),
                        "last_active": "2026-05-27T00:00:00+00:00",
                        "sflo_dir": factory_dir,
                    }
                },
            }
            with open(
                os.path.join(project_dir, ".sflo", "registry.json"),
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(registry, f)

            hook_input = json.dumps(
                {
                    "status": "completed",
                    "loop_count": 0,
                    "cwd": project_dir,
                    "hook_event_name": "stop",
                }
            )
            result = subprocess.run(
                [sys.executable, CURSOR_HOOK],
                input=hook_input,
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip(), result.stderr)
            data = json.loads(result.stdout)
            self.assertIn("followup_message", data)
            self.assertIn("SFLO PIPELINE", data["followup_message"])
            self.assertTrue(os.path.isfile(os.path.join(factory_dir, ".last_hook_state")))
        finally:
            shutil.rmtree(project_dir)

    def test_active_factory_wins_over_legacy_flat_state(self):
        """Named active factory state is canonical when legacy state also exists."""
        project_dir = tempfile.mkdtemp()
        try:
            legacy_dir = os.path.join(project_dir, ".sflo")
            factory_dir = os.path.join(legacy_dir, "click-counter")
            os.makedirs(factory_dir, exist_ok=True)

            self.write_state("done")
            shutil.copy2(
                os.path.join(self.state_dir, "state.json"),
                os.path.join(legacy_dir, "state.json"),
            )
            self.write_state("gate-1")
            shutil.copy2(
                os.path.join(self.state_dir, "state.json"),
                os.path.join(factory_dir, "state.json"),
            )
            registry = {
                "version": 1,
                "factories": {
                    "click-counter": {
                        "status": "active",
                        "pid": os.getpid(),
                        "last_active": "2026-05-27T00:00:00+00:00",
                        "sflo_dir": factory_dir,
                    }
                },
            }
            with open(
                os.path.join(legacy_dir, "registry.json"), "w", encoding="utf-8"
            ) as f:
                json.dump(registry, f)

            hook_input = json.dumps(
                {
                    "stop_hook_active": False,
                    "cwd": project_dir,
                    "session_id": "test",
                    "last_assistant_message": "test",
                }
            )
            result = subprocess.run(
                [sys.executable, CLAUDE_HOOK],
                input=hook_input,
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip(), result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["decision"], "block")
            self.assertTrue(os.path.isfile(os.path.join(factory_dir, ".last_hook_state")))
            self.assertFalse(os.path.isfile(os.path.join(legacy_dir, ".last_hook_state")))
        finally:
            shutil.rmtree(project_dir)

    def test_cursor_active_factory_wins_over_legacy_flat_state(self):
        """Cursor follows named active factory state before legacy flat state."""
        project_dir = tempfile.mkdtemp()
        try:
            legacy_dir = os.path.join(project_dir, ".sflo")
            factory_dir = os.path.join(legacy_dir, "click-counter")
            os.makedirs(factory_dir, exist_ok=True)

            self.write_state("done")
            shutil.copy2(
                os.path.join(self.state_dir, "state.json"),
                os.path.join(legacy_dir, "state.json"),
            )
            self.write_state("gate-1")
            shutil.copy2(
                os.path.join(self.state_dir, "state.json"),
                os.path.join(factory_dir, "state.json"),
            )
            registry = {
                "version": 1,
                "factories": {
                    "click-counter": {
                        "status": "active",
                        "pid": os.getpid(),
                        "last_active": "2026-05-27T00:00:00+00:00",
                        "sflo_dir": factory_dir,
                    }
                },
            }
            with open(
                os.path.join(legacy_dir, "registry.json"), "w", encoding="utf-8"
            ) as f:
                json.dump(registry, f)

            hook_input = json.dumps(
                {
                    "status": "completed",
                    "loop_count": 0,
                    "cwd": project_dir,
                    "hook_event_name": "stop",
                }
            )
            result = subprocess.run(
                [sys.executable, CURSOR_HOOK],
                input=hook_input,
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip(), result.stderr)
            data = json.loads(result.stdout)
            self.assertIn("followup_message", data)
            self.assertTrue(os.path.isfile(os.path.join(factory_dir, ".last_hook_state")))
            self.assertFalse(os.path.isfile(os.path.join(legacy_dir, ".last_hook_state")))
        finally:
            shutil.rmtree(project_dir)


class TestHookPipelineTraversal(unittest.TestCase):
    """Test prompt command drives through all gates correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")
        os.makedirs(os.path.join(self.tmpdir, "gates"), exist_ok=True)
        run_scaffold("init", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
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
        with open(
            os.path.join(self.sflo_dir, name), "w", encoding="utf-8"
        ) as f:
            f.write(content)

    def prompt(self):
        return run_scaffold("prompt", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)

    def advance(self):
        return run_scaffold("next", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)

    def test_full_traversal(self):
        """Prompt returns correct agent for each gate in sequence."""
        from src.constants import GATES

        gate_sequence = {
            "gate-1": (["SCOPE.md"], "PM"),
            "gate-2": (["BUILD-STATUS.md"], "DEV"),
            "gate-3": (
                [e["artifact"] for e in GATES[3]]
                if isinstance(GATES[3], list)
                else [GATES[3]["artifact"]],
                "QA",
            ),
            "gate-4": (["PM-VERIFY.md"], "PM-VERIFY"),
            "gate-5": (["SHIP-DECISION.md"], "SHIP-DECISION"),
        }
        for gate, (artifact_names, expected_keyword) in gate_sequence.items():
            r = self.prompt()
            self.assertTrue(r["ok"], f"Prompt failed at {gate}")
            self.assertIn(
                expected_keyword, r["prompt"], f"Missing {expected_keyword} at {gate}"
            )
            for name in artifact_names:
                self.artifact(name, PASSING_ARTIFACTS[name])
            self.advance()

        # Final prompt should indicate terminal
        r = self.prompt()
        self.assertFalse(r["ok"])

    def test_loop_back_prompts_dev(self):
        """After QA failure, prompt should re-target DEV."""
        from src.constants import GATES

        self.artifact("SCOPE.md", PASSING_ARTIFACTS["SCOPE.md"])
        self.advance()
        self.artifact("BUILD-STATUS.md", PASSING_ARTIFACTS["BUILD-STATUS.md"])
        self.advance()
        # Write failing QA report + passing security report for parallel gate 3
        self.artifact("QA-REPORT.md", "### Grade: F\n### Issues Found\n1. CRITICAL\n")
        if isinstance(GATES.get(3), list):
            for entry in GATES[3]:
                a = entry.get("artifact")
                if a and a != "QA-REPORT.md" and a in PASSING_ARTIFACTS:
                    self.artifact(a, PASSING_ARTIFACTS[a])
        result = self.advance()
        self.assertEqual(result["state"], "loop-inner")

        r = self.prompt()
        self.assertTrue(r["ok"])
        self.assertIn("DEV", r["prompt"])


if __name__ == "__main__":
    unittest.main()
