#!/usr/bin/env python3
"""Unit tests for allow_task gate restriction — config parsing, prompt injection, machine passthrough."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.config import parse_pipeline_yaml, _parse_bool
from src.runner import build_agent_prompt


class TestParseBool(unittest.TestCase):
    """_parse_bool handles YAML-style booleans."""

    def test_true_variants(self):
        for val in ("true", "True", "TRUE", "yes", "Yes", "on", "ON"):
            self.assertIs(_parse_bool(val), True, f"Expected True for {val!r}")

    def test_false_variants(self):
        for val in ("false", "False", "FALSE", "no", "No", "off", "OFF"):
            self.assertIs(_parse_bool(val), False, f"Expected False for {val!r}")

    def test_ambiguous_returns_none(self):
        for val in ("maybe", "1", "0", ""):
            self.assertIsNone(_parse_bool(val), f"Expected None for {val!r}")


class TestAllowTaskConfig(unittest.TestCase):
    """pipeline.yaml parser converts allow_task to Python bool."""

    def _write_yaml(self, content):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.write(fd, content.encode())
        os.close(fd)
        self.addCleanup(os.unlink, path)
        return path

    def test_single_gate_allow_task_false(self):
        path = self._write_yaml(
            "threshold: B+\n"
            "gates:\n"
            "  1:\n"
            "    role: pm\n"
            "    artifact: SCOPE.md\n"
            "    allow_task: false\n"
        )
        config, err = parse_pipeline_yaml(path)
        self.assertIsNone(err)
        self.assertIs(config["gates"][1]["allow_task"], False)

    def test_single_gate_allow_task_true(self):
        path = self._write_yaml(
            "threshold: B+\n"
            "gates:\n"
            "  2:\n"
            "    role: dev\n"
            "    artifact: BUILD-STATUS.md\n"
            "    allow_task: true\n"
        )
        config, err = parse_pipeline_yaml(path)
        self.assertIsNone(err)
        self.assertIs(config["gates"][2]["allow_task"], True)

    def test_parallel_gate_allow_task_false(self):
        path = self._write_yaml(
            "threshold: B+\n"
            "gates:\n"
            "  3:\n"
            "    - role: qa\n"
            "      artifact: QA-REPORT.md\n"
            "      allow_task: false\n"
            "    - role: security\n"
            "      artifact: SECURITY-REPORT.md\n"
            "      allow_task: false\n"
        )
        config, err = parse_pipeline_yaml(path)
        self.assertIsNone(err)
        entries = config["gates"][3]
        self.assertIs(entries[0]["allow_task"], False)
        self.assertIs(entries[1]["allow_task"], False)

    def test_absent_allow_task_not_in_dict(self):
        path = self._write_yaml(
            "threshold: B+\n"
            "gates:\n"
            "  1:\n"
            "    role: pm\n"
            "    artifact: SCOPE.md\n"
        )
        config, err = parse_pipeline_yaml(path)
        self.assertIsNone(err)
        self.assertNotIn("allow_task", config["gates"][1])


class TestAllowTaskPromptInjection(unittest.TestCase):
    """build_agent_prompt injects Task restriction when allow_task is False."""

    def _make_agent_info(self, allow_task=None):
        """Minimal agent_info with no real files (tests prompt assembly logic)."""
        info = {
            "role": "qa",
            "reads": ["", ""],  # empty paths — read_file returns ""
            "gate_num": 3,
            "produces": "/tmp/test/QA-REPORT.md",
            "skills": [],
            "agents": [],
        }
        if allow_task is not None:
            info["allow_task"] = allow_task
        return info

    def test_allow_task_false_injects_restriction(self):
        """allow_task=False → system prompt contains Task restriction."""
        agent_info = self._make_agent_info(allow_task=False)
        system_prompt, _ = build_agent_prompt(
            agent_info, "build a widget", "/tmp/test"
        )
        self.assertIn("DO NOT use the Task tool", system_prompt)

    def test_allow_task_true_no_restriction(self):
        """allow_task=True → system prompt does NOT contain Task restriction."""
        agent_info = self._make_agent_info(allow_task=True)
        system_prompt, _ = build_agent_prompt(
            agent_info, "build a widget", "/tmp/test"
        )
        self.assertNotIn("DO NOT use the Task tool", system_prompt)

    def test_allow_task_absent_no_restriction(self):
        """allow_task absent → system prompt does NOT contain Task restriction."""
        agent_info = self._make_agent_info(allow_task=None)
        system_prompt, _ = build_agent_prompt(
            agent_info, "build a widget", "/tmp/test"
        )
        self.assertNotIn("DO NOT use the Task tool", system_prompt)


class TestAllowTaskMachinePassthrough(unittest.TestCase):
    """compute_next passes allow_task from GATES config to agent dicts."""

    def test_passthrough_single_gate(self):
        from src.machine import compute_next
        from src.constants import GATES

        # Find first single-agent gate
        for key, val in GATES.items():
            if isinstance(val, dict) and val.get("role"):
                # Check if allow_task would pass through
                # (current pipeline.yaml doesn't set it, so it's None)
                state = {"current_state": f"gate-{key}"}
                result = compute_next(state, "/tmp/nonexistent")
                if result.get("action") == "spawn_agent":
                    agent = result["agent"]
                    # allow_task key should be present (even if None)
                    self.assertIn("allow_task", agent)
                    break


if __name__ == "__main__":
    unittest.main()
