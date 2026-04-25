#!/usr/bin/env python3
"""Integration tests for SFLO scaffold CLI commands."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

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
        result = run_scaffold(
            "init",
            "--bindings",
            "/nonexistent.yaml",
            "--sflo-dir",
            self.sflo_dir,
            cwd=self.tmpdir,
        )
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
        result = run_scaffold(
            "assign",
            "--pm",
            "agents/pm",
            "--dev",
            "agents/dev",
            "--qa",
            "agents/qa",
            "--sflo-dir",
            self.sflo_dir,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["assignments"]["pm"], "agents/pm")
        self.assertEqual(result["next"]["state"], "gate-1")

    def test_assign_with_extras(self):
        result = run_scaffold(
            "assign",
            "--pm",
            "agents/pm",
            "--dev",
            "agents/dev",
            "--qa",
            "agents/qa",
            "--extra",
            "designer=agents/designer",
            "--sflo-dir",
            self.sflo_dir,
        )
        self.assertIn("designer", result["assignments"])

    def test_assign_unknown_arg_rejected(self):
        result = run_scaffold(
            "assign",
            "--pm",
            "agents/pm",
            "--devv",
            "agents/dev",
            "--qa",
            "agents/qa",
            "--sflo-dir",
            self.sflo_dir,
        )
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
        run_scaffold(
            "init", "--bindings", path, "--sflo-dir", self.sflo_dir, cwd=self.tmpdir
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
            "current_state": "done",
            "bindings": {},
            "assignments": {},
            "inner_loops": 0,
            "outer_loops": 0,
            "gates": {
                str(g): {"status": "done", "artifact": a}
                for g, a in {
                    1: "SCOPE.md",
                    2: "BUILD-STATUS.md",
                    3: "QA-REPORT.md",
                    4: "PM-VERIFY.md",
                    5: "SHIP-DECISION.md",
                }.items()
            },
        }
        with open(os.path.join(self.sflo_dir, "state.json"), "w") as f:
            json.dump(state, f)

        for name, content in {
            "QA-REPORT.md": "### Grade: A\n",
            "PM-VERIFY.md": "### Verdict: APPROVED\n",
            "SHIP-DECISION.md": "### Decision: SHIP\n",
        }.items():
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
        run_scaffold(
            "init", "--bindings", path, "--sflo-dir", self.sflo_dir, cwd=self.tmpdir
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


class TestCleanCommand(unittest.TestCase):
    """Q4: cmd_clean archives SFLO-owned files to logs/ for debuggability,
    preserves everything else.

    Move-instead-of-delete: removed files are moved to <sflo_dir>/logs/
    so the most recent state of each artifact is always inspectable.
    Last-write-wins — repeated cleans overwrite logs/<basename>.

    Exercises real cmd_clean via subprocess.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")
        os.makedirs(self.sflo_dir, exist_ok=True)
        # Only SFLO-owned files (guardian.json and PM-REJECTION.md removed)
        for f in (
            "state.json",
            "pipeline.log",
            "SCOPE.md",
            "BUILD-STATUS.md",
            "QA-REPORT.md",
            "PM-VERIFY.md",
            "SHIP-DECISION.md",
            "QA-FEEDBACK.md",
            "PM-FEEDBACK.md",
        ):
            with open(os.path.join(self.sflo_dir, f), "w") as fp:
                fp.write("test content " + f)
        os.makedirs(os.path.join(self.sflo_dir, ".venv"), exist_ok=True)
        with open(os.path.join(self.sflo_dir, ".venv", "marker"), "w") as fp:
            fp.write("dont touch")
        with open(os.path.join(self.sflo_dir, "user-notes.md"), "w") as fp:
            fp.write("user-owned, dont touch")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _ls(self):
        return set(os.listdir(self.sflo_dir))

    def _ls_logs(self):
        logs = os.path.join(self.sflo_dir, "logs")
        if not os.path.isdir(logs):
            return set()
        return set(os.listdir(logs))

    def test_clean_removes_sflo_owned_files_from_top_level(self):
        result = run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        self.assertTrue(result["ok"])
        remaining = self._ls()
        for f in (
            "state.json",
            "pipeline.log",
            "SCOPE.md",
            "BUILD-STATUS.md",
            "QA-REPORT.md",
            "PM-VERIFY.md",
            "SHIP-DECISION.md",
            "QA-FEEDBACK.md",
            "PM-FEEDBACK.md",
        ):
            self.assertNotIn(
                f, remaining, f"{f} should be moved out of top level by clean"
            )

    def test_clean_archives_files_to_logs(self):
        run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        # Every removed file should now exist in logs/
        archived = self._ls_logs()
        for f in (
            "state.json",
            "pipeline.log",
            "SCOPE.md",
            "BUILD-STATUS.md",
            "QA-REPORT.md",
            "PM-VERIFY.md",
            "SHIP-DECISION.md",
            "QA-FEEDBACK.md",
            "PM-FEEDBACK.md",
        ):
            self.assertIn(f, archived, f"{f} should be present in logs/ after clean")

    def test_clean_archived_content_matches_original(self):
        """Round-trip: read content from logs/ after clean — should match
        what we wrote in setUp."""
        run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        scope_in_logs = os.path.join(self.sflo_dir, "logs", "SCOPE.md")
        self.assertTrue(os.path.isfile(scope_in_logs))
        with open(scope_in_logs) as f:
            self.assertEqual(f.read(), "test content SCOPE.md")

    def test_clean_preserves_venv(self):
        run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        remaining = self._ls()
        self.assertIn(".venv", remaining)
        self.assertTrue(os.path.isfile(os.path.join(self.sflo_dir, ".venv", "marker")))

    def test_clean_preserves_user_files(self):
        run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        remaining = self._ls()
        self.assertIn("user-notes.md", remaining)

    def test_clean_idempotent(self):
        run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        result = run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("archived", []), [])

    def test_clean_does_not_archive_logs_dir_into_itself(self):
        """Regression guard: logs/ must never recurse into logs/logs/."""
        run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        # Run again — logs/ exists from first run, must not be moved
        run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        self.assertFalse(os.path.isdir(os.path.join(self.sflo_dir, "logs", "logs")))

    def test_clean_overwrites_logs_on_repeat(self):
        """Last-write wins: second clean overwrites logs/ with new content."""
        run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        # Write a fresh SCOPE.md with different content
        with open(os.path.join(self.sflo_dir, "SCOPE.md"), "w") as fp:
            fp.write("second-run content")
        run_scaffold("clean", "--sflo-dir", self.sflo_dir, cwd=self.tmpdir)
        with open(os.path.join(self.sflo_dir, "logs", "SCOPE.md")) as fp:
            self.assertEqual(fp.read(), "second-run content")

    def test_clean_missing_dir_returns_error(self):
        result = run_scaffold(
            "clean", "--sflo-dir", "/tmp/nonexistent-sflo-dir-xyz", cwd=self.tmpdir
        )
        self.assertFalse(result["ok"])
        self.assertIn("does not exist", result["error"])

    def test_clean_unknown_arg_rejected(self):
        result = run_scaffold(
            "clean", "--sflo-dir", self.sflo_dir, "--bogus-flag", cwd=self.tmpdir
        )
        self.assertFalse(result["ok"])
        self.assertIn("Unknown arguments", result["error"])
        self.assertIn("state.json", self._ls())


if __name__ == "__main__":
    unittest.main()
