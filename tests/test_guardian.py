#!/usr/bin/env python3
"""Unit tests for SFLO Guardian safety layer."""

import json
import os
import sys
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helper to patch GUARDIAN_CONFIG
# ---------------------------------------------------------------------------

def _make_guardian_config(enabled=True, max_spawns=50, wall_clock_s=7200,
                           circuit_breaker_window=5):
    return {
        "enabled": enabled,
        "max_spawns": max_spawns,
        "wall_clock_s": wall_clock_s,
        "circuit_breaker_window": circuit_breaker_window,
    }


class TestGuardianDisabled(unittest.TestCase):
    """All guardian functions should be no-ops when disabled."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")
        os.makedirs(self.sflo_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _patch_disabled(self):
        return patch("src.guardian.GUARDIAN_CONFIG", _make_guardian_config(enabled=False))

    def test_init_guardian_noop(self):
        with self._patch_disabled():
            from src.guardian import init_guardian
            result = init_guardian(self.sflo_dir)
        self.assertEqual(result, {})
        # No file should be created
        guardian_file = os.path.join(self.sflo_dir, "guardian.json")
        self.assertFalse(os.path.isfile(guardian_file))

    def test_record_spawn_noop(self):
        with self._patch_disabled():
            from src.guardian import record_spawn
            result = record_spawn(self.sflo_dir)
        self.assertIsNone(result)
        # No file created
        guardian_file = os.path.join(self.sflo_dir, "guardian.json")
        self.assertFalse(os.path.isfile(guardian_file))

    def test_record_gate_failure_noop(self):
        with self._patch_disabled():
            from src.guardian import record_gate_failure
            result = record_gate_failure(self.sflo_dir, 3)
        self.assertIsNone(result)

    def test_check_time_budget_noop(self):
        with self._patch_disabled():
            from src.guardian import check_time_budget
            result = check_time_budget(self.sflo_dir)
        self.assertIsNone(result)

    def test_guardian_check_noop(self):
        with self._patch_disabled():
            from src.guardian import guardian_check
            result = guardian_check(self.sflo_dir)
        self.assertIsNone(result)

    def test_guardian_status_disabled(self):
        with self._patch_disabled():
            from src.guardian import guardian_status
            result = guardian_status(self.sflo_dir)
        self.assertEqual(result, {"enabled": False})


class TestGuardianEnabled(unittest.TestCase):
    """Test guardian functions when enabled."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")
        os.makedirs(self.sflo_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _patch_enabled(self, **kwargs):
        return patch("src.guardian.GUARDIAN_CONFIG", _make_guardian_config(enabled=True, **kwargs))

    def test_init_guardian_creates_file(self):
        with self._patch_enabled():
            from src.guardian import init_guardian, guardian_path
            state = init_guardian(self.sflo_dir)
        self.assertTrue(state.get("enabled"))
        self.assertIn("started_at", state)
        self.assertEqual(state["spawn_count"], 0)
        self.assertEqual(state["gate_failures"], {})
        self.assertTrue(os.path.isfile(guardian_path(self.sflo_dir)))

    def test_record_spawn_increments(self):
        with self._patch_enabled(max_spawns=10):
            from src.guardian import init_guardian, record_spawn, read_guardian
            init_guardian(self.sflo_dir)
            result1 = record_spawn(self.sflo_dir)
            result2 = record_spawn(self.sflo_dir)
            state = read_guardian(self.sflo_dir)
        self.assertIsNone(result1)
        self.assertIsNone(result2)
        self.assertEqual(state["spawn_count"], 2)

    def test_record_spawn_trips_at_limit(self):
        with self._patch_enabled(max_spawns=3):
            from src.guardian import init_guardian, record_spawn
            init_guardian(self.sflo_dir)
            record_spawn(self.sflo_dir)  # 1
            record_spawn(self.sflo_dir)  # 2
            record_spawn(self.sflo_dir)  # 3
            result = record_spawn(self.sflo_dir)  # 4 — exceeds max
        self.assertIsNotNone(result)
        self.assertIn("spawn budget exceeded", result)
        self.assertIn("4", result)
        self.assertIn("3", result)

    def test_record_gate_failure_increments(self):
        with self._patch_enabled(circuit_breaker_window=5):
            from src.guardian import init_guardian, record_gate_failure, read_guardian
            init_guardian(self.sflo_dir)
            result1 = record_gate_failure(self.sflo_dir, 3)
            result2 = record_gate_failure(self.sflo_dir, 3)
            state = read_guardian(self.sflo_dir)
        self.assertIsNone(result1)
        self.assertIsNone(result2)
        self.assertEqual(state["gate_failures"]["3"], 2)

    def test_circuit_breaker_trips_at_window(self):
        with self._patch_enabled(circuit_breaker_window=3):
            from src.guardian import init_guardian, record_gate_failure
            init_guardian(self.sflo_dir)
            record_gate_failure(self.sflo_dir, 4)  # 1
            record_gate_failure(self.sflo_dir, 4)  # 2
            result = record_gate_failure(self.sflo_dir, 4)  # 3 — trips
        self.assertIsNotNone(result)
        self.assertIn("circuit breaker", result)
        self.assertIn("gate 4", result)
        self.assertIn("3", result)

    def test_circuit_breaker_per_gate(self):
        """Circuit breaker tracks failures per gate independently."""
        with self._patch_enabled(circuit_breaker_window=3):
            from src.guardian import init_guardian, record_gate_failure
            init_guardian(self.sflo_dir)
            # Gate 3 failures
            record_gate_failure(self.sflo_dir, 3)
            record_gate_failure(self.sflo_dir, 3)
            # Gate 4 failures — separate counter
            result = record_gate_failure(self.sflo_dir, 4)  # 1 for gate 4
        self.assertIsNone(result)  # Gate 4 hasn't hit window yet

    def test_check_time_budget_not_exceeded(self):
        with self._patch_enabled(wall_clock_s=7200):
            from src.guardian import init_guardian, check_time_budget
            init_guardian(self.sflo_dir)
            result = check_time_budget(self.sflo_dir)
        self.assertIsNone(result)

    def test_check_time_budget_exceeded(self):
        with self._patch_enabled(wall_clock_s=1):
            from src.guardian import init_guardian, check_time_budget, read_guardian, write_guardian
            init_guardian(self.sflo_dir)
            # Manually set started_at to far in the past
            state = read_guardian(self.sflo_dir)
            state["started_at"] = time.time() - 100  # 100 seconds ago
            write_guardian(self.sflo_dir, state)
            result = check_time_budget(self.sflo_dir)
        self.assertIsNotNone(result)
        self.assertIn("wall-clock budget exceeded", result)

    def test_guardian_check_all_ok(self):
        with self._patch_enabled():
            from src.guardian import init_guardian, guardian_check
            init_guardian(self.sflo_dir)
            result = guardian_check(self.sflo_dir)
        self.assertIsNone(result)

    def test_guardian_check_trips_on_time(self):
        with self._patch_enabled(wall_clock_s=1):
            from src.guardian import init_guardian, guardian_check, read_guardian, write_guardian
            init_guardian(self.sflo_dir)
            state = read_guardian(self.sflo_dir)
            state["started_at"] = time.time() - 100
            write_guardian(self.sflo_dir, state)
            result = guardian_check(self.sflo_dir)
        self.assertIsNotNone(result)
        self.assertIn("wall-clock", result)

    def test_guardian_status_structure(self):
        with self._patch_enabled(max_spawns=20, wall_clock_s=3600,
                                  circuit_breaker_window=3):
            from src.guardian import init_guardian, record_spawn, guardian_status
            init_guardian(self.sflo_dir)
            record_spawn(self.sflo_dir)
            status = guardian_status(self.sflo_dir)
        self.assertTrue(status["enabled"])
        self.assertTrue(status["initialized"])
        self.assertEqual(status["spawn_count"], 1)
        self.assertEqual(status["max_spawns"], 20)
        self.assertEqual(status["wall_clock_s"], 3600)
        self.assertEqual(status["circuit_breaker_window"], 3)
        self.assertIn("elapsed_s", status)
        self.assertIn("gate_failures", status)

    def test_guardian_status_not_initialized(self):
        with self._patch_enabled():
            from src.guardian import guardian_status
            status = guardian_status(self.sflo_dir)
        self.assertTrue(status["enabled"])
        self.assertFalse(status.get("initialized", True))

    def test_read_write_guardian(self):
        with self._patch_enabled():
            from src.guardian import read_guardian, write_guardian
            data = {"enabled": True, "spawn_count": 5, "started_at": 12345.0}
            write_guardian(self.sflo_dir, data)
            read_back = read_guardian(self.sflo_dir)
        self.assertEqual(read_back["spawn_count"], 5)
        self.assertEqual(read_back["started_at"], 12345.0)

    def test_read_guardian_missing_returns_empty(self):
        from src.guardian import read_guardian
        result = read_guardian(self.sflo_dir)
        self.assertEqual(result, {})

    def test_guardian_path(self):
        from src.guardian import guardian_path
        p = guardian_path(self.sflo_dir)
        self.assertTrue(p.endswith("guardian.json"))
        self.assertTrue(p.startswith(self.sflo_dir))


if __name__ == "__main__":
    unittest.main()
