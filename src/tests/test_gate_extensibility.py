"""Tests for gate extensibility: runner/validator/on_reject_restart_at fields,
list-based parallel gates, custom runner loading, backwards compatibility."""

import os
import sys
import textwrap


# Ensure sflo/src is importable
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


class TestConfigParsing:
    """Config parser handles new gate fields and list-based parallel format."""

    def _write_yaml(self, tmp_path, content):
        p = os.path.join(tmp_path, "pipeline.yaml")
        with open(p, "w") as f:
            f.write(textwrap.dedent(content))
        return p

    def test_runner_validator_fields_parsed(self, tmp_path):
        from src.config import parse_pipeline_yaml

        path = self._write_yaml(
            tmp_path,
            """\
            threshold: B+
            gates:
              2.5:
                artifact: FILTER-REPORT.md
                runner: ext/custom_runner.py
                validator: ext/custom_validator.py
                gate_doc: gates/custom.md
                on_reject_restart_at: 2
        """,
        )
        cfg, err = parse_pipeline_yaml(path)
        assert err is None, f"parse should succeed, got error: {err}"
        gate = cfg["gates"][2.5]
        assert gate["runner"] == "ext/custom_runner.py", (
            f"expected runner path, got {gate['runner']!r}"
        )
        assert gate["validator"] == "ext/custom_validator.py", (
            f"expected validator path, got {gate['validator']!r}"
        )
        assert gate["on_reject_restart_at"] == 2, (
            f"expected on_reject_restart_at=2, got {gate['on_reject_restart_at']}"
        )

    def test_on_reject_restart_at_float(self, tmp_path):
        from src.config import parse_pipeline_yaml

        path = self._write_yaml(
            tmp_path,
            """\
            gates:
              3:
                artifact: QA-REPORT.md
                role: qa
                on_reject_restart_at: 1.5
        """,
        )
        cfg, err = parse_pipeline_yaml(path)
        assert err is None, f"parse should succeed, got error: {err}"
        assert cfg["gates"][3]["on_reject_restart_at"] == 1.5, (
            f"expected on_reject_restart_at=1.5, got {cfg['gates'][3]['on_reject_restart_at']}"
        )

    def test_list_based_parallel_gates(self, tmp_path):
        from src.config import parse_pipeline_yaml

        path = self._write_yaml(
            tmp_path,
            """\
            gates:
              3:
                - artifact: QA-REPORT.md
                  role: qa
                  gate_doc: gates/test.md
                - artifact: SECURITY-REPORT.md
                  role: security
                  gate_doc: gates/security-review.md
        """,
        )
        cfg, err = parse_pipeline_yaml(path)
        assert err is None, f"parse should succeed, got error: {err}"
        gate3 = cfg["gates"][3]
        assert isinstance(gate3, list), (
            f"parallel gate 3 should be a list, got {type(gate3).__name__}"
        )
        assert len(gate3) == 2, f"expected 2 parallel entries, got {len(gate3)}"
        assert gate3[0]["artifact"] == "QA-REPORT.md", (
            f"first parallel artifact mismatch: {gate3[0]['artifact']!r}"
        )
        assert gate3[0]["role"] == "qa", (
            f"first parallel role mismatch: {gate3[0]['role']!r}"
        )
        assert gate3[1]["artifact"] == "SECURITY-REPORT.md", (
            f"second parallel artifact mismatch: {gate3[1]['artifact']!r}"
        )
        assert gate3[1]["role"] == "security", (
            f"second parallel role mismatch: {gate3[1]['role']!r}"
        )

    def test_backwards_compat_no_new_fields(self, tmp_path):
        from src.config import parse_pipeline_yaml

        path = self._write_yaml(
            tmp_path,
            """\
            threshold: B+
            gates:
              1:
                artifact: SCOPE.md
                role: pm
                gate_doc: gates/discovery.md
              2:
                artifact: BUILD-STATUS.md
                role: dev
                gate_doc: gates/build.md
        """,
        )
        cfg, err = parse_pipeline_yaml(path)
        assert err is None, f"parse should succeed, got error: {err}"
        assert cfg["gates"][1] == {
            "artifact": "SCOPE.md",
            "role": "pm",
            "gate_doc": "gates/discovery.md",
        }, f"gate 1 mismatch: {cfg['gates'][1]}"
        assert "runner" not in cfg["gates"][2], (
            "legacy gate should not have runner field"
        )
        assert "validator" not in cfg["gates"][2], (
            "legacy gate should not have validator field"
        )
        assert "on_reject_restart_at" not in cfg["gates"][2], (
            "legacy gate should not have on_reject_restart_at"
        )

    def test_mixed_gates_list_and_dict(self, tmp_path):
        from src.config import parse_pipeline_yaml

        path = self._write_yaml(
            tmp_path,
            """\
            gates:
              1:
                artifact: SCOPE.md
                role: pm
                gate_doc: gates/discovery.md
              2:
                artifact: BUILD-STATUS.md
                role: dev
                gate_doc: gates/build.md
              3:
                - artifact: QA-REPORT.md
                  role: qa
                  gate_doc: gates/test.md
              4:
                artifact: PM-VERIFY.md
                role: pm
                gate_doc: gates/verify.md
        """,
        )
        cfg, err = parse_pipeline_yaml(path)
        assert err is None, f"parse should succeed, got error: {err}"
        assert isinstance(cfg["gates"][1], dict), (
            f"gate 1 should be dict, got {type(cfg['gates'][1]).__name__}"
        )
        assert isinstance(cfg["gates"][3], list), (
            f"gate 3 should be list (parallel), got {type(cfg['gates'][3]).__name__}"
        )
        assert isinstance(cfg["gates"][4], dict), (
            f"gate 4 should be dict, got {type(cfg['gates'][4]).__name__}"
        )


class TestMachineComputeNext:
    """compute_next dispatches correctly for custom runner and parallel gates."""

    def _make_state(self, current, gates_config):
        return {
            "current_state": current,
            "assignments": {},
            "roles": {},
            "inner_loops": 0,
            "outer_loops": 0,
            "gates": {str(k): {"status": "pending"} for k in gates_config},
            "gate_retries": {},
        }

    def test_custom_runner_dispatch(self, tmp_path):
        from src.machine import compute_next

        test_gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/discovery.md"},
            2: {
                "artifact": "BUILD-STATUS.md",
                "role": "dev",
                "gate_doc": "gates/build.md",
            },
            2.5: {
                "artifact": "FILTER-REPORT.md",
                "runner": "ext/custom_runner.py",
                "validator": "ext/custom_validator.py",
                "gate_doc": "gates/custom.md",
                "on_reject_restart_at": 2,
            },
            3: {"artifact": "QA-REPORT.md", "role": "qa", "gate_doc": "gates/test.md"},
        }

        state = self._make_state("gate-2.5", test_gates)
        result = compute_next(state, str(tmp_path), gates=test_gates)
        assert result["action"] == "run_custom_gate", (
            f"expected run_custom_gate, got {result['action']!r}"
        )
        assert result["runner"] == "ext/custom_runner.py", (
            f"expected custom runner path, got {result['runner']!r}"
        )
        assert result["on_reject_restart_at"] == 2, (
            f"expected on_reject_restart_at=2, got {result['on_reject_restart_at']}"
        )

    def test_parallel_gate_dispatch(self, tmp_path):
        from src.machine import compute_next

        test_gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/discovery.md"},
            2: {
                "artifact": "BUILD-STATUS.md",
                "role": "dev",
                "gate_doc": "gates/build.md",
            },
            3: [
                {"artifact": "QA-REPORT.md", "role": "qa", "gate_doc": "gates/test.md"},
                {
                    "artifact": "SECURITY-REPORT.md",
                    "role": "security",
                    "gate_doc": "gates/security-review.md",
                },
            ],
            4: {
                "artifact": "PM-VERIFY.md",
                "role": "pm",
                "gate_doc": "gates/verify.md",
            },
            5: {
                "artifact": "SHIP-DECISION.md",
                "role": "sflo",
                "gate_doc": "gates/ship.md",
            },
        }

        state = self._make_state("gate-3", test_gates)
        result = compute_next(state, str(tmp_path), gates=test_gates)
        assert result["action"] == "spawn_parallel", (
            f"expected spawn_parallel, got {result['action']!r}"
        )
        assert len(result["agents"]) == 2, (
            f"expected 2 parallel agents, got {len(result['agents'])}"
        )
        assert result["agents"][0]["role"] == "qa", (
            f"first agent role should be qa, got {result['agents'][0]['role']!r}"
        )
        assert result["agents"][1]["role"] == "security", (
            f"second agent role should be security, got {result['agents'][1]['role']!r}"
        )

    def test_default_agent_gate_unchanged(self, tmp_path):
        from src.machine import compute_next

        test_gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/discovery.md"},
            2: {
                "artifact": "BUILD-STATUS.md",
                "role": "dev",
                "gate_doc": "gates/build.md",
            },
            3: {"artifact": "QA-REPORT.md", "role": "qa", "gate_doc": "gates/test.md"},
        }

        state = self._make_state("gate-2", test_gates)
        result = compute_next(state, str(tmp_path), gates=test_gates)
        assert result["action"] == "spawn_agent", (
            f"expected spawn_agent, got {result['action']!r}"
        )
        assert result["agent"]["role"] == "dev", (
            f"expected dev role, got {result['agent']['role']!r}"
        )


class TestMachineApplyTransition:
    """apply_transition uses on_reject_restart_at for config-driven loopback."""

    def test_on_reject_restart_at(self, tmp_path):
        from src.state import write_state
        from src.machine import apply_transition

        test_gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/discovery.md"},
            2: {
                "artifact": "BUILD-STATUS.md",
                "role": "dev",
                "gate_doc": "gates/build.md",
            },
            2.5: {
                "artifact": "FILTER-REPORT.md",
                "runner": "ext/custom_runner.py",
                "gate_doc": "gates/custom.md",
                "on_reject_restart_at": 2,
            },
            3: {"artifact": "QA-REPORT.md", "role": "qa", "gate_doc": "gates/test.md"},
        }

        sflo_dir = str(tmp_path / ".sflo")
        os.makedirs(sflo_dir, exist_ok=True)

        state = {
            "current_state": "check-2.5",
            "assignments": {},
            "roles": {},
            "inner_loops": 0,
            "outer_loops": 0,
            "gates": {str(k): {"status": "pending"} for k in test_gates},
            "gate_retries": {},
        }
        write_state(sflo_dir, state)

        result = {
            "action": "check_failed",
            "gate": 2.5,
            "pass": False,
            "checks": [{"name": "verdict_is_pass", "pass": False}],
        }
        out = apply_transition(state, result, sflo_dir, gates=test_gates)
        assert out["action"] == "loop_back", (
            f"expected loop_back on reject, got {out['action']!r}"
        )
        assert state["current_state"] == "gate-2", (
            f"expected restart at gate-2, got {state['current_state']!r}"
        )
        assert state["gate_retries"]["2.5"] == 1, (
            f"expected 1 retry for gate 2.5, got {state['gate_retries'].get('2.5')}"
        )

    def test_parallel_gate_check_failed_loops_back(self, tmp_path):
        """Parallel gate at check-3 failing loops back to gate-2 (default)."""
        from src.state import write_state
        from src.machine import apply_transition

        test_gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/discovery.md"},
            2: {
                "artifact": "BUILD-STATUS.md",
                "role": "dev",
                "gate_doc": "gates/build.md",
            },
            3: [
                {"artifact": "QA-REPORT.md", "role": "qa", "gate_doc": "gates/test.md"},
                {
                    "artifact": "SEC-REPORT.md",
                    "role": "security",
                    "gate_doc": "gates/sec.md",
                },
            ],
            4: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }

        sflo_dir = str(tmp_path / ".sflo")
        os.makedirs(sflo_dir, exist_ok=True)

        state = {
            "current_state": "check-3",
            "assignments": {},
            "roles": {},
            "inner_loops": 0,
            "outer_loops": 0,
            "gates": {str(k): {"status": "pending"} for k in test_gates},
            "gate_retries": {},
        }
        write_state(sflo_dir, state)

        result = {
            "action": "check_failed",
            "gate": 3,
            "pass": False,
            "checks": [{"name": "grade_check", "pass": False}],
        }
        out = apply_transition(state, result, sflo_dir, gates=test_gates)
        assert out["action"] == "loop_back", (
            f"expected loop_back for parallel gate failure, got {out['action']!r}"
        )
        # Default loopback = previous gate (gate 2)
        assert state["current_state"] == "gate-2", (
            f"expected loopback to gate-2, got {state['current_state']!r}"
        )


class TestRunnerHappyPath:
    """_load_custom_runner loads a valid runner with run_gate function."""

    def test_load_valid_runner(self):
        from src.runner import _load_custom_runner

        # Write a valid runner in cwd
        runner_rel = "test_runner_tmp.py"
        runner_abs = os.path.join(os.getcwd(), runner_rel)
        with open(runner_abs, "w") as f:
            f.write(
                textwrap.dedent("""\
                def run_gate(gate_config, sflo_dir, output_dir):
                    return "OK"
            """)
            )
        try:
            mod, err = _load_custom_runner(runner_rel)
            assert err is None, f"valid runner should load without error, got: {err}"
            assert mod is not None, "loaded runner module should not be None"
            assert hasattr(mod, "run_gate"), (
                "runner module should have run_gate function"
            )
        finally:
            if os.path.exists(runner_abs):
                os.unlink(runner_abs)

    def test_load_runner_with_run_function(self):
        from src.runner import _load_custom_runner

        runner_rel = "test_runner_run_tmp.py"
        runner_abs = os.path.join(os.getcwd(), runner_rel)
        with open(runner_abs, "w") as f:
            f.write(
                textwrap.dedent("""\
                def run(gate_config, sflo_dir, output_dir):
                    return "OK"
            """)
            )
        try:
            mod, err = _load_custom_runner(runner_rel)
            assert err is None, (
                f"runner with run() should load without error, got: {err}"
            )
            assert hasattr(mod, "run"), "runner module should have run function"
        finally:
            if os.path.exists(runner_abs):
                os.unlink(runner_abs)

    def test_reject_runner_missing_interface(self):
        from src.runner import _load_custom_runner

        runner_rel = "test_runner_bad_tmp.py"
        runner_abs = os.path.join(os.getcwd(), runner_rel)
        with open(runner_abs, "w") as f:
            f.write("x = 1\n")
        try:
            mod, err = _load_custom_runner(runner_rel)
            assert mod is None, "runner missing interface should return None module"
            assert "no run_gate() or run()" in err, (
                f"error should mention missing interface, got: {err!r}"
            )
        finally:
            if os.path.exists(runner_abs):
                os.unlink(runner_abs)

    def test_reject_absolute_path(self):
        from src.runner import _load_custom_runner

        mod, err = _load_custom_runner("/tmp/evil.py")
        assert mod is None, "absolute path runner should return None module"
        assert "must be relative" in err, (
            f"error should mention 'must be relative', got: {err!r}"
        )

    def test_reject_dotdot_traversal(self):
        from src.runner import _load_custom_runner

        mod, err = _load_custom_runner("../escape/runner.py")
        assert mod is None, "dotdot traversal runner should return None module"
        assert ".." in err, f"error should mention '..', got: {err!r}"


class TestValidatorPathAlignment:
    """_load_validator_module rejects absolute paths (aligned with runner)."""

    def test_reject_absolute_path(self):
        from src.validate import _load_validator_module

        mod, err = _load_validator_module("/tmp/evil_validator.py")
        assert mod is None, "absolute path validator should return None module"
        assert "must be relative" in err, (
            f"error should mention 'must be relative', got: {err!r}"
        )

    def test_reject_dotdot_traversal(self):
        from src.validate import _load_validator_module

        mod, err = _load_validator_module("../escape/validator.py")
        assert mod is None, "dotdot traversal validator should return None module"
        assert ".." in err, f"error should mention '..', got: {err!r}"

    def test_reject_empty_path(self):
        from src.validate import _load_validator_module

        mod, err = _load_validator_module("")
        assert mod is None, "empty path validator should return None module"
        assert "empty" in err, f"error should mention 'empty', got: {err!r}"


class TestValidateGateWithCustomValidator:
    """validate_gate loads config-driven validators from gate's validator field."""

    def test_custom_validator_loaded(self, tmp_path):
        from src.validate import validate_gate

        # Write a custom validator script inside cwd so relative path works
        validator_rel = "test_validator_tmp.py"
        validator_abs = os.path.join(os.getcwd(), validator_rel)
        with open(validator_abs, "w") as f:
            f.write(
                textwrap.dedent("""\
                def validate(gate_num, content, sflo_dir, checks):
                    checks.append({"name": "custom_check", "pass": True, "detail": "OK"})
                    return True, checks
            """)
            )

        try:
            test_gates = {
                7: {
                    "artifact": "CUSTOM.md",
                    "role": "custom",
                    "gate_doc": "gates/x.md",
                    "validator": validator_rel,
                },
            }

            sflo_dir = str(tmp_path / ".sflo")
            os.makedirs(sflo_dir, exist_ok=True)
            with open(os.path.join(sflo_dir, "CUSTOM.md"), "w") as f:
                f.write("# Custom artifact\n\nContent here.\n")

            passed, checks = validate_gate(7, sflo_dir, gates=test_gates)
            assert passed is True, f"custom validator should pass, checks: {checks}"
            custom_checks = [c for c in checks if c["name"] == "custom_check"]
            assert len(custom_checks) == 1, (
                f"expected 1 custom_check, got {len(custom_checks)} in {checks}"
            )
        finally:
            if os.path.exists(validator_abs):
                os.unlink(validator_abs)

    def test_no_validator_uses_ext_registry(self, tmp_path, monkeypatch):
        from src.validate import validate_gate
        from src import constants as _constants

        test_gates = {
            7: {"artifact": "CUSTOM.md", "role": "custom", "gate_doc": "gates/x.md"},
        }
        # validate_ext.get_validator reads constants.GATES to decide
        # if a gate is custom — must still monkeypatch that path
        monkeypatch.setattr(_constants, "GATES", test_gates)

        sflo_dir = str(tmp_path / ".sflo")
        os.makedirs(sflo_dir, exist_ok=True)
        with open(os.path.join(sflo_dir, "CUSTOM.md"), "w") as f:
            f.write("# Custom artifact content\n")

        passed, checks = validate_gate(7, sflo_dir, gates=test_gates)
        assert passed is True, (
            f"gate with no validator should pass via ext registry, checks: {checks}"
        )
        assert any(c["name"] == "custom_gate_no_checks" for c in checks), (
            f"expected custom_gate_no_checks in checks: {[c['name'] for c in checks]}"
        )


class TestValidateExtRegistry:
    """validate_ext registry works with no ext validators by default."""

    def test_no_ext_validator_registered_by_default(self):
        from src.validate_ext import list_validators

        assert 2.5 not in list_validators(), (
            "no ext validator should be registered for gate 2.5 by default"
        )

    def test_register_and_get(self):
        from src.validate_ext import (
            register_validator,
            get_validator,
            unregister_validator,
        )

        def dummy(gn, c, sd, ch):
            return True, ch

        register_validator(99, dummy)
        try:
            assert get_validator(99) is dummy, (
                "registered validator should be retrievable"
            )
        finally:
            unregister_validator(99)

    def test_builtin_gates_return_none(self):
        from src.validate_ext import get_validator

        for k in [1, 2, 3, 4, 5]:
            assert get_validator(k) is None, (
                f"builtin gate {k} should not have ext validator"
            )


class TestSkillsConfigParsing:
    """Config parser handles skills: section — per-role skill name lists."""

    def _write_yaml(self, tmp_path, content):
        p = os.path.join(tmp_path, "pipeline.yaml")
        with open(p, "w") as f:
            f.write(textwrap.dedent(content))
        return p

    def test_per_gate_skills_parsed(self, tmp_path):
        from src.config import parse_pipeline_yaml

        path = self._write_yaml(
            tmp_path,
            """\
            threshold: B+
            gates:
              1:
                artifact: SCOPE.md
                role: pm
                skills:
                  - spec-driven-development
                  - debugging-and-error-recovery
                gate_doc: gates/discovery.md
        """,
        )
        cfg, err = parse_pipeline_yaml(path)
        assert err is None, f"parse should succeed, got error: {err}"
        assert "skills" in cfg["gates"][1], "gate 1 should contain skills list"
        assert cfg["gates"][1]["skills"] == [
            "spec-driven-development",
            "debugging-and-error-recovery",
        ], f"gate 1 skills mismatch: {cfg['gates'][1].get('skills')}"

    def test_no_skills_in_gate_returns_no_key(self, tmp_path):
        from src.config import parse_pipeline_yaml

        path = self._write_yaml(
            tmp_path,
            """\
            threshold: B+
            gates:
              1:
                artifact: SCOPE.md
                role: pm
                gate_doc: gates/discovery.md
        """,
        )
        cfg, err = parse_pipeline_yaml(path)
        assert err is None, f"parse should succeed, got error: {err}"
        assert "skills" not in cfg["gates"][1], (
            "gate without skills should not have skills key"
        )

    def test_per_gate_skills_in_loaded_config(self, tmp_path):
        from src.config import load_pipeline_config

        path = self._write_yaml(
            tmp_path,
            """\
            gates:
              1:
                artifact: SCOPE.md
                role: pm
                skills:
                  - test-driven-development
                gate_doc: gates/discovery.md
        """,
        )
        cfg = load_pipeline_config(path)
        assert cfg["gates"][1]["skills"] == ["test-driven-development"], (
            f"loaded gate skills mismatch: {cfg['gates'][1].get('skills')}"
        )


class TestSkillResolution:
    """machine.resolve_skill_paths resolves skill names to SKILL.md paths."""

    def test_resolve_skill_paths_finds_files(self, tmp_path):
        from src.machine import resolve_skill_paths

        # Create vendor skill structure
        skill_dir = os.path.join(
            tmp_path, "vendor", "agent-skills", "skills", "test-skill"
        )
        os.makedirs(skill_dir)
        skill_file = os.path.join(skill_dir, "SKILL.md")
        with open(skill_file, "w") as f:
            f.write("# Test Skill\n")

        paths = resolve_skill_paths(["test-skill"], str(tmp_path))
        assert len(paths) == 1, (
            f"expected 1 resolved skill path, got {len(paths)}: {paths}"
        )
        assert paths[0] == skill_file, (
            f"resolved path should match skill file, got {paths[0]!r}"
        )

    def test_resolve_skill_paths_empty_list(self, tmp_path):
        from src.machine import resolve_skill_paths

        paths = resolve_skill_paths([], str(tmp_path))
        assert paths == [], f"empty skill list should return empty list, got {paths}"

    def test_resolve_skill_paths_missing_file_skipped(self, tmp_path):
        from src.machine import resolve_skill_paths

        paths = resolve_skill_paths(["nonexistent-skill"], str(tmp_path))
        assert paths == [], f"missing skill file should be skipped, got {paths}"


class TestSkillInjectionInComputeNext:
    """compute_next passes resolved skill paths in agent_info."""

    def test_spawn_agent_includes_skills(self, tmp_path, monkeypatch):
        # Create a vendor skill
        skill_dir = os.path.join(
            tmp_path, "vendor", "agent-skills", "skills", "code-review"
        )
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("# Code Review\n")
        # Create agents dir + gate doc
        os.makedirs(os.path.join(tmp_path, "agents", "qa"), exist_ok=True)
        with open(os.path.join(tmp_path, "agents", "qa", "SOUL.md"), "w") as f:
            f.write("# QA\n")
        os.makedirs(os.path.join(tmp_path, "gates"), exist_ok=True)
        with open(os.path.join(tmp_path, "gates", "test.md"), "w") as f:
            f.write("# Test gate\n")

        test_gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/discovery.md"},
            2: {
                "artifact": "QA-REPORT.md",
                "role": "qa",
                "skills": ["code-review"],
                "gate_doc": "gates/test.md",
            },
            3: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }
        # Still need monkeypatch for SFLO_ROOT (different constant, not gates/skills)
        from src import constants, machine

        monkeypatch.setattr(constants, "SFLO_ROOT", str(tmp_path))
        monkeypatch.setattr(machine, "SFLO_ROOT", str(tmp_path))
        from src.machine import compute_next

        sflo_dir = os.path.join(tmp_path, ".sflo")
        os.makedirs(sflo_dir, exist_ok=True)
        # Prior gate artifact must exist
        with open(os.path.join(sflo_dir, "SCOPE.md"), "w") as f:
            f.write("# Scope\n")
        state = {
            "current_state": "gate-2",
            "assignments": {},
            "roles": {},
            "inner_loops": 0,
            "outer_loops": 0,
            "gates": {
                "1": {"status": "passed"},
                "2": {"status": "pending"},
                "3": {"status": "pending"},
            },
            "gate_retries": {},
        }
        result = compute_next(state, sflo_dir, gates=test_gates)
        assert result["action"] == "spawn_agent", (
            f"expected spawn_agent, got {result['action']!r}"
        )
        assert len(result["agent"]["skills"]) == 1, (
            f"expected 1 skill, got {len(result['agent']['skills'])}"
        )
        assert "code-review" in result["agent"]["skills"][0], (
            f"expected code-review in skill path, got {result['agent']['skills'][0]!r}"
        )

    def test_parallel_agents_include_skills(self, tmp_path, monkeypatch):
        # Create vendor skills
        for skill_name in ["code-review", "sec-audit"]:
            skill_dir = os.path.join(
                tmp_path, "vendor", "agent-skills", "skills", skill_name
            )
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
                f.write(f"# {skill_name}\n")
        # Create agents dirs + gate docs
        for role in ["qa", "security"]:
            os.makedirs(os.path.join(tmp_path, "agents", role), exist_ok=True)
            with open(os.path.join(tmp_path, "agents", role, "SOUL.md"), "w") as f:
                f.write(f"# {role}\n")
        os.makedirs(os.path.join(tmp_path, "gates"), exist_ok=True)
        for gd in ["test.md", "sec.md"]:
            with open(os.path.join(tmp_path, "gates", gd), "w") as f:
                f.write(f"# {gd}\n")

        test_gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/discovery.md"},
            2: [
                {
                    "artifact": "QA-REPORT.md",
                    "role": "qa",
                    "skills": ["code-review"],
                    "gate_doc": "gates/test.md",
                },
                {
                    "artifact": "SEC-REPORT.md",
                    "role": "security",
                    "skills": ["sec-audit"],
                    "gate_doc": "gates/sec.md",
                },
            ],
            3: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }
        # Still need monkeypatch for SFLO_ROOT (different constant, not gates/skills)
        from src import constants, machine

        monkeypatch.setattr(constants, "SFLO_ROOT", str(tmp_path))
        monkeypatch.setattr(machine, "SFLO_ROOT", str(tmp_path))
        from src.machine import compute_next

        sflo_dir = os.path.join(tmp_path, ".sflo")
        os.makedirs(sflo_dir, exist_ok=True)
        # Prior gate artifact must exist
        with open(os.path.join(sflo_dir, "SCOPE.md"), "w") as f:
            f.write("# Scope\n")
        state = {
            "current_state": "gate-2",
            "assignments": {},
            "roles": {},
            "inner_loops": 0,
            "outer_loops": 0,
            "gates": {
                "1": {"status": "passed"},
                "2": {"status": "pending"},
                "3": {"status": "pending"},
            },
            "gate_retries": {},
        }
        result = compute_next(state, sflo_dir, gates=test_gates)
        assert result["action"] == "spawn_parallel", (
            f"expected spawn_parallel, got {result['action']!r}"
        )
        assert len(result["agents"]) == 2, (
            f"expected 2 agents, got {len(result['agents'])}"
        )
        assert len(result["agents"][0]["skills"]) == 1, (
            f"qa agent should have 1 skill, got {len(result['agents'][0]['skills'])}"
        )
        assert "code-review" in result["agents"][0]["skills"][0], (
            f"qa skill should contain code-review, got {result['agents'][0]['skills'][0]!r}"
        )
        assert len(result["agents"][1]["skills"]) == 1, (
            f"security agent should have 1 skill, got {len(result['agents'][1]['skills'])}"
        )
        assert "sec-audit" in result["agents"][1]["skills"][0], (
            f"security skill should contain sec-audit, got {result['agents'][1]['skills'][0]!r}"
        )
