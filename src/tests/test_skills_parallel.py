"""Tests for skills system, parallel gate execution, and related fixes.

Run with: pytest sflo/src/tests/test_skills_parallel.py -v

Tests cover:
  config.py   — per-gate skills/agents YAML parsing, gate field parsing
  machine.py  — resolve_skill_paths, resolve_agent_paths, agent_reads,
                compute_next with list-based parallel gates,
                role-based loop detection, parallel sub-gate skip,
                parallel gate status update (preserves artifact key)
  validate.py — parallel gate merge validation
  validate_ext.py — security report validator (implicit pass)
  constants.py — KNOWN_ROLES
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest import mock


# Ensure src/ is importable
_SFLO_DIR = Path(__file__).parent.parent.parent  # sflo/ root
if str(_SFLO_DIR) not in sys.path:
    sys.path.insert(0, str(_SFLO_DIR))


# =========================================================================
# config.py — per-gate skills parsing (co-located in gate entries)
# =========================================================================


class TestPerGateSkillsParsing:
    """TC-S1 through TC-S5: per-gate skills list parsing in pipeline.yaml."""

    def test_per_gate_skills_parsed(self, tmp_path):
        """TC-S1: Skills list inside a gate entry parses correctly."""
        yaml = tmp_path / "pipeline.yaml"
        yaml.write_text(
            textwrap.dedent("""\
            gates:
              1:
                artifact: SCOPE.md
                role: pm
                gate_doc: gates/discovery.md
                skills:
                  - spec-driven-development
                  - debugging-and-error-recovery
              2:
                artifact: BUILD-STATUS.md
                role: dev
                gate_doc: gates/build.md
                skills:
                  - test-driven-development
        """)
        )
        from src.config import parse_pipeline_yaml

        result, err = parse_pipeline_yaml(str(yaml))
        assert err is None, f"Parse error: {err}"
        assert result["gates"][1]["skills"] == [
            "spec-driven-development",
            "debugging-and-error-recovery",
        ]
        assert result["gates"][2]["skills"] == ["test-driven-development"]

    def test_gate_without_skills_has_no_key(self, tmp_path):
        """TC-S2: Gate with no skills: field has no skills key."""
        yaml = tmp_path / "pipeline.yaml"
        yaml.write_text(
            textwrap.dedent("""\
            gates:
              1:
                artifact: SCOPE.md
                role: pm
                gate_doc: gates/discovery.md
        """)
        )
        from src.config import parse_pipeline_yaml

        result, err = parse_pipeline_yaml(str(yaml))
        assert err is None
        assert "skills" not in result["gates"][1]

    def test_skill_inline_comment_stripped(self, tmp_path):
        """TC-S3: Inline comments on skill names are stripped."""
        yaml = tmp_path / "pipeline.yaml"
        yaml.write_text(
            textwrap.dedent("""\
            gates:
              2:
                artifact: BUILD-STATUS.md
                role: dev
                gate_doc: gates/build.md
                skills:
                  - test-driven-development  # H28a winner
        """)
        )
        from src.config import parse_pipeline_yaml

        result, err = parse_pipeline_yaml(str(yaml))
        assert err is None
        assert result["gates"][2]["skills"] == ["test-driven-development"]

    def test_parallel_gate_entry_skills(self, tmp_path):
        """TC-S4: Skills inside list-based parallel gate entries parse correctly."""
        yaml = tmp_path / "pipeline.yaml"
        yaml.write_text(
            textwrap.dedent("""\
            gates:
              3:
                - artifact: QA-REPORT.md
                  role: qa
                  gate_doc: gates/test.md
                  skills:
                    - code-review-and-quality
                    - performance-optimization
                - artifact: SECURITY-REPORT.md
                  role: security
                  gate_doc: gates/security-review.md
        """)
        )
        from src.config import parse_pipeline_yaml

        result, err = parse_pipeline_yaml(str(yaml))
        assert err is None
        gate3 = result["gates"][3]
        assert isinstance(gate3, list)
        assert gate3[0]["skills"] == [
            "code-review-and-quality",
            "performance-optimization",
        ]
        assert "skills" not in gate3[1]

    def test_per_gate_agents_parsed(self, tmp_path):
        """TC-S5: agents: list inside gate entry parses correctly."""
        yaml = tmp_path / "pipeline.yaml"
        yaml.write_text(
            textwrap.dedent("""\
            gates:
              3:
                - artifact: QA-REPORT.md
                  role: qa
                  gate_doc: gates/test.md
                  agents:
                    - agents/qa-w-agent-skills
                    - vendor/agent-skills/agents/code-reviewer
        """)
        )
        from src.config import parse_pipeline_yaml

        result, err = parse_pipeline_yaml(str(yaml))
        assert err is None
        gate3 = result["gates"][3]
        assert isinstance(gate3, list)
        assert gate3[0]["agents"] == [
            "agents/qa-w-agent-skills",
            "vendor/agent-skills/agents/code-reviewer",
        ]


# =========================================================================
# machine.py — resolve_skill_paths
# =========================================================================


class TestSkillPathResolution:
    """TC-SP1 through TC-SP4: resolve_skill_paths from machine.py."""

    def test_resolve_found_in_sflo_root(self, tmp_path):
        """TC-SP1: Skill found in SFLO_ROOT/vendor/agent-skills/skills/<name>/SKILL.md."""
        skill_dir = tmp_path / "vendor" / "agent-skills" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test Skill")

        from src.machine import resolve_skill_paths

        with mock.patch("src.machine.SFLO_ROOT", str(tmp_path)):
            result = resolve_skill_paths(["test-skill"], str(tmp_path))
        assert len(result) == 1
        assert result[0].endswith("SKILL.md")
        assert "test-skill" in result[0]

    def _assert_unresolved_skill_raises(self, skill_name, tmp_path, expected_detail):
        from src.machine import SkillResolutionError, resolve_skill_paths

        with mock.patch("src.machine.SFLO_ROOT", str(tmp_path)):
            try:
                resolve_skill_paths([skill_name], str(tmp_path))
            except SkillResolutionError as err:
                msg = str(err)
                assert skill_name in msg
                assert expected_detail in msg
            else:
                raise AssertionError(f"{skill_name!r} should raise SkillResolutionError")

    def test_resolve_not_found(self, tmp_path):
        """TC-SP2: Missing skill raises instead of silently degrading."""
        self._assert_unresolved_skill_raises(
            "nonexistent-skill", tmp_path, "not found in any vendor"
        )

    def test_resolve_empty_input(self):
        """TC-SP3: Empty or None input returns empty list."""
        from src.machine import resolve_skill_paths

        assert resolve_skill_paths([], "/tmp") == []
        assert resolve_skill_paths(None, "/tmp") == []

    def test_resolve_rejects_path_traversal(self, tmp_path):
        """TC-SP4: Invalid/unresolved skill names raise fail-fast errors."""
        # Create a skill that WOULD resolve if traversal was allowed
        evil_dir = tmp_path / "vendor" / "agent-skills" / "skills" / "legit"
        evil_dir.mkdir(parents=True)
        (evil_dir / "SKILL.md").write_text("# Legit")

        self._assert_unresolved_skill_raises(
            "../etc/passwd", tmp_path, "rejected: traversal sequence"
        )
        self._assert_unresolved_skill_raises(
            "foo/bar", tmp_path, "vendor or skill not found"
        )
        self._assert_unresolved_skill_raises(
            "foo\\bar", tmp_path, "rejected: traversal sequence"
        )
        self._assert_unresolved_skill_raises(
            "..", tmp_path, "rejected: traversal sequence"
        )


# =========================================================================
# machine.py — resolve_agent_paths
# =========================================================================


class TestAgentPathResolution:
    """TC-AP1 through TC-AP4: resolve_agent_paths from machine.py."""

    def test_resolve_directory_soul(self, tmp_path):
        """TC-AP1: Directory ref resolves to dir/SOUL.md."""
        agent_dir = tmp_path / "agents" / "qa-custom"
        agent_dir.mkdir(parents=True)
        (agent_dir / "SOUL.md").write_text("# QA Agent")

        from src.machine import resolve_agent_paths

        result = resolve_agent_paths(["agents/qa-custom"], str(tmp_path))
        assert len(result) == 1
        assert result[0].endswith("SOUL.md")

    def test_resolve_md_file(self, tmp_path):
        """TC-AP2: .md file ref resolves directly."""
        agent_file = tmp_path / "agents" / "reviewer.md"
        agent_file.parent.mkdir(parents=True)
        agent_file.write_text("# Reviewer Agent")

        from src.machine import resolve_agent_paths

        result = resolve_agent_paths(["agents/reviewer.md"], str(tmp_path))
        assert len(result) == 1
        assert result[0].endswith("reviewer.md")

    def test_resolve_rejects_traversal(self, tmp_path):
        """TC-AP3: Refs with .. are rejected."""
        from src.machine import resolve_agent_paths

        result = resolve_agent_paths(["../etc/passwd"], str(tmp_path))
        assert result == []

    def test_resolve_rejects_outside_boundary(self, tmp_path):
        """TC-AP4: Absolute paths outside sflo_base and cwd are rejected."""
        from src.machine import resolve_agent_paths

        result = resolve_agent_paths(["/etc/passwd"], str(tmp_path))
        assert result == []


# =========================================================================
# machine.py — agent_reads
# =========================================================================


class TestAgentReads:
    """TC-AR1, TC-AR2: agent_reads returns minimal reads (gate_doc + SOUL)."""

    def test_agent_reads_minimal(self, tmp_path):
        """TC-AR1: agent_reads returns [gate_doc, SOUL.md] only."""
        gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/discovery.md"},
        }
        from src.machine import agent_reads

        reads = agent_reads(1, str(tmp_path), str(tmp_path), str(tmp_path), gates=gates)
        assert len(reads) == 2
        assert reads[0].endswith("gates/discovery.md")
        assert reads[1].endswith("SOUL.md")

    def test_agent_reads_no_gate_doc(self, tmp_path):
        """TC-AR2: Gate without gate_doc returns [SOUL.md] only."""
        gates = {
            1: {"artifact": "SCOPE.md", "role": "pm"},
        }
        from src.machine import agent_reads

        reads = agent_reads(1, str(tmp_path), str(tmp_path), str(tmp_path), gates=gates)
        assert len(reads) == 1
        assert reads[0].endswith("SOUL.md")


# =========================================================================
# machine.py — compute_next with list-based parallel gates
# =========================================================================


class TestParallelGateCompute:
    """TC-M4 through TC-M6: compute_next with list-based parallel gates."""

    def _make_gates(self):
        return {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/d.md"},
            2: {"artifact": "BUILD.md", "role": "dev", "gate_doc": "gates/b.md"},
            3: [
                {"artifact": "QA.md", "role": "qa", "gate_doc": "gates/t.md"},
                {"artifact": "SEC.md", "role": "security", "gate_doc": "gates/s.md"},
            ],
            4: {"artifact": "PM-V.md", "role": "pm", "gate_doc": "gates/v.md"},
            5: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }

    def test_compute_next_spawn_parallel(self, tmp_path):
        """TC-M4: List-based gate 3 returns spawn_parallel action."""
        gates = self._make_gates()
        state = {
            "current_state": "gate-3",
            "assignments": {},
            "roles": {},
        }
        with mock.patch("src.machine.GATES", gates):
            from src.machine import compute_next

            result = compute_next(state, str(tmp_path), gates=gates)

        assert result["action"] == "spawn_parallel"
        assert len(result["agents"]) == 2
        roles = [a["role"] for a in result["agents"]]
        assert "qa" in roles
        assert "security" in roles

    def test_parallel_skills_resolved(self, tmp_path):
        """TC-M5: Skills in parallel gate entries are resolved."""
        skill_dir = tmp_path / "vendor" / "agent-skills" / "skills" / "code-review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Code Review")

        gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/d.md"},
            3: [
                {
                    "artifact": "QA.md",
                    "role": "qa",
                    "gate_doc": "gates/t.md",
                    "skills": ["code-review"],
                },
                {"artifact": "SEC.md", "role": "security", "gate_doc": "gates/s.md"},
            ],
            5: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }
        state = {
            "current_state": "gate-3",
            "assignments": {},
            "roles": {},
        }
        with (
            mock.patch("src.machine.GATES", gates),
            mock.patch("src.machine.SFLO_ROOT", str(tmp_path)),
        ):
            from src.machine import compute_next

            result = compute_next(state, str(tmp_path), gates=gates)

        qa_agent = next(a for a in result["agents"] if a["role"] == "qa")
        assert len(qa_agent["skills"]) == 1
        assert qa_agent["skills"][0].endswith("SKILL.md")

    def test_parallel_status_preserves_artifact(self, tmp_path):
        """TC-M6: Marking parallel sub-gate done preserves artifact key."""
        gates = self._make_gates()
        state = {
            "current_state": "check-3",
            "gates": {
                "1": {"status": "done", "artifact": "SCOPE.md"},
                "2": {"status": "done", "artifact": "BUILD.md"},
                "3": {"status": "waiting", "artifact": "QA.md"},
                "4": {"status": "waiting", "artifact": "PM-V.md"},
                "5": {"status": "waiting", "artifact": "SHIP.md"},
            },
            "inner_loops": 0,
            "outer_loops": 0,
            "assignments": {},
            "roles": {},
        }
        (tmp_path / "QA.md").write_text("### Grade: A\n\nAll pass\n")
        (tmp_path / "SEC.md").write_text(
            "## Findings\n\nNo critical issues.\n\n## Verdict\n\nPASS\n"
        )

        with mock.patch("src.machine.GATES", gates):
            from src.machine import compute_next, apply_transition

            result = compute_next(state, str(tmp_path), gates=gates)

            if result.get("action") == "validated":
                apply_transition(state, result, str(tmp_path))
                assert state["gates"]["3"]["status"] == "done"


class TestPerGateConfig:
    """TC-PG1 through TC-PG3: per-gate model/thinking/effort in compute_next."""

    def test_per_gate_model_in_spawn(self, tmp_path):
        """TC-PG1: Per-gate model field propagates to spawn_agent result."""
        gates = {
            1: {
                "artifact": "SCOPE.md",
                "role": "pm",
                "gate_doc": "gates/d.md",
                "model": "claude-opus-4-6",
            },
            5: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }
        state = {
            "current_state": "gate-1",
            "assignments": {},
            "roles": {},
        }
        with mock.patch("src.machine.GATES", gates):
            from src.machine import compute_next

            result = compute_next(state, str(tmp_path), gates=gates)
        assert result["agent"]["model"] == "claude-opus-4-6"

    def test_per_gate_thinking_effort(self, tmp_path):
        """TC-PG2: Per-gate thinking and effort propagate to spawn_agent."""
        gates = {
            1: {
                "artifact": "SCOPE.md",
                "role": "pm",
                "gate_doc": "gates/d.md",
                "thinking": "extended",
                "effort": "max",
            },
            5: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }
        state = {
            "current_state": "gate-1",
            "assignments": {},
            "roles": {},
        }
        with mock.patch("src.machine.GATES", gates):
            from src.machine import compute_next

            result = compute_next(state, str(tmp_path), gates=gates)
        assert result["agent"]["thinking"] == "extended"
        assert result["agent"]["effort"] == "max"

    def test_roles_fallback_when_no_gate_config(self, tmp_path):
        """TC-PG3: Missing per-gate model falls back to roles."""
        gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/d.md"},
            5: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }
        state = {
            "current_state": "gate-1",
            "assignments": {},
            "roles": {"pm": {"model": "haiku"}},
        }
        with mock.patch("src.machine.GATES", gates):
            from src.machine import compute_next

            result = compute_next(state, str(tmp_path), gates=gates)
        assert result["agent"]["model"] == "haiku"


# =========================================================================
# machine.py — role-based loop detection
# =========================================================================


class TestRoleBasedLoopDetection:
    """TC-M7, TC-M8: Role-based inner/outer loop detection."""

    def _make_state(self, gates_dict, current, inner=0, outer=0):
        return {
            "current_state": current,
            "gates": {
                str(k): {
                    "status": "waiting",
                    "artifact": (
                        v[0]["artifact"] if isinstance(v, list) else v["artifact"]
                    ),
                }
                for k, v in gates_dict.items()
            },
            "inner_loops": inner,
            "outer_loops": outer,
            "gate_retries": {},
            "assignments": {},
            "roles": {},
        }

    def test_inner_loop_on_qa_failure(self, tmp_path):
        """TC-M7: QA failure loops back to dev gate."""
        gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/d.md"},
            2: {"artifact": "BUILD.md", "role": "dev", "gate_doc": "gates/b.md"},
            3: [
                {"artifact": "QA.md", "role": "qa", "gate_doc": "gates/t.md"},
                {"artifact": "SEC.md", "role": "security", "gate_doc": "gates/s.md"},
            ],
            4: {"artifact": "PM-V.md", "role": "pm", "gate_doc": "gates/v.md"},
            5: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }
        state = self._make_state(gates, "check-3")
        state["gates"]["1"]["status"] = "done"
        state["gates"]["2"]["status"] = "done"
        (tmp_path / "QA.md").write_text("### Grade: D\n\nFailed\n")

        with mock.patch("src.machine.GATES", gates):
            from src.machine import compute_next, apply_transition

            result = compute_next(state, str(tmp_path), gates=gates)
            if result.get("action") == "check_failed":
                result = apply_transition(state, result, str(tmp_path))
                assert result["state"] == "loop-inner", (
                    f"Expected loop-inner but got {result['state']}"
                )
                assert state["current_state"] == "gate-2"

    def test_inner_exhaustion_skips_to_pm(self, tmp_path):
        """TC-M8: Exhausted inner loop goes to PM verify (gate 4)."""
        gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/d.md"},
            2: {"artifact": "BUILD.md", "role": "dev", "gate_doc": "gates/b.md"},
            3: [
                {"artifact": "QA.md", "role": "qa", "gate_doc": "gates/t.md"},
            ],
            4: {"artifact": "PM-V.md", "role": "pm", "gate_doc": "gates/v.md"},
            5: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }
        state = self._make_state(gates, "check-3", inner=10)
        (tmp_path / "QA.md").write_text("### Grade: D\n\nFailed\n")

        with (
            mock.patch("src.machine.GATES", gates),
            mock.patch("src.machine.INNER_LOOP_MAX", 10),
        ):
            from src.machine import compute_next, apply_transition

            result = compute_next(state, str(tmp_path), gates=gates)
            if result.get("action") == "check_failed":
                result = apply_transition(state, result, str(tmp_path))
                assert result["state"] == "loop-inner-exhausted"
                assert state["current_state"] == "gate-4", (
                    f"Expected gate-4 but got {state['current_state']}"
                )


# =========================================================================
# validate_ext.py — custom validator registration
# =========================================================================


class TestValidatorRegistry:
    """TC-V1 through TC-V3: validate_ext register/get/unregister."""

    def test_register_and_retrieve(self):
        """TC-V1: Registered validator is retrievable."""
        from src.validate_ext import (
            register_validator,
            get_validator,
            unregister_validator,
        )

        def dummy(g, c, d, ch):
            return (True, ch)
        register_validator(99, dummy)
        try:
            assert get_validator(99) is dummy
        finally:
            unregister_validator(99)

    def test_builtin_gate_returns_none(self):
        """TC-V2: Built-in gates (1-5) return None from get_validator."""
        from src.validate_ext import get_validator

        for g in (1, 2, 3, 4, 5):
            assert get_validator(g) is None

    def test_unregistered_custom_gate_returns_none(self):
        """TC-V3: Unregistered gate not in GATES returns None."""
        from src.validate_ext import get_validator

        assert get_validator(99.9) is None


# =========================================================================
# validate.py — list-based parallel gate validation
# =========================================================================


class TestParallelGateMerge:
    """TC-V6, TC-V7: List-based parallel gate validation."""

    def test_list_gate_passes_all_artifacts(self, tmp_path):
        """TC-V6: List-based gate 3 passes when all artifacts exist and validate."""
        gates = {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/d.md"},
            2: {"artifact": "BUILD.md", "role": "dev", "gate_doc": "gates/b.md"},
            3: [
                {"artifact": "QA-REPORT.md", "role": "qa", "gate_doc": "gates/t.md"},
                {
                    "artifact": "SECURITY-REPORT.md",
                    "role": "security",
                    "gate_doc": "gates/s.md",
                },
            ],
            4: {"artifact": "PM-V.md", "role": "pm", "gate_doc": "gates/v.md"},
            5: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }
        qa = textwrap.dedent("""\
            ## QA Report

            ### Test Results
            | Test | Result |
            |------|--------|
            | Core | PASS |

            ### Grade: A

            ### Stranger Test
            Yes, useful.
        """)
        sec = textwrap.dedent("""\
            ## Summary

            Critical: 0
            Low: 1

            ## Findings

            #### [LOW] Missing CSP
            Add a CSP meta tag.
        """)
        (tmp_path / "QA-REPORT.md").write_text(qa)
        (tmp_path / "SECURITY-REPORT.md").write_text(sec)

        with (
            mock.patch("src.validate.GATES", gates),
            mock.patch("src.validate.GRADE_THRESHOLD", 6),
        ):
            from src.validate import validate_gate

            passed, checks = validate_gate(3, str(tmp_path))

        # File existence checks for both artifacts
        file_checks = [c for c in checks if c["name"].startswith("file_exists:")]
        assert len(file_checks) == 2
        assert all(c["pass"] for c in file_checks)

    def test_list_gate_fails_missing_artifact(self, tmp_path):
        """TC-V7: List-based gate fails when required artifact missing."""
        gates = {
            3: [
                {"artifact": "QA-REPORT.md", "role": "qa", "gate_doc": "gates/t.md"},
                {
                    "artifact": "SECURITY-REPORT.md",
                    "role": "security",
                    "gate_doc": "gates/s.md",
                },
            ],
        }
        qa = textwrap.dedent("""\
            ## QA Report
            ### Grade: A
        """)
        (tmp_path / "QA-REPORT.md").write_text(qa)
        # No SECURITY-REPORT.md

        with (
            mock.patch("src.validate.GATES", gates),
            mock.patch("src.validate.GRADE_THRESHOLD", 6),
        ):
            from src.validate import validate_gate

            passed, checks = validate_gate(3, str(tmp_path))

        assert passed is False
        sec_check = next(c for c in checks if "SECURITY-REPORT.md" in c["name"])
        assert sec_check["pass"] is False


# =========================================================================
# constants.py — KNOWN_ROLES
# =========================================================================


class TestConstants:
    """TC-C1: KNOWN_ROLES includes expected roles."""

    def test_known_roles_baseline(self):
        """TC-C1: KNOWN_ROLES has at minimum pm, dev, qa."""
        from src.constants import KNOWN_ROLES

        for role in ("pm", "dev", "qa"):
            assert role in KNOWN_ROLES


# =========================================================================
# config.py — gate key parsing for float gates
# =========================================================================


class TestFloatGateParsing:
    """TC-G1, TC-G2: Float gate keys in pipeline.yaml."""

    def test_float_gate_key(self, tmp_path):
        """TC-G1: Gate 3.5 parses as float key."""
        yaml = tmp_path / "pipeline.yaml"
        yaml.write_text(
            textwrap.dedent("""\
            gates:
              3.5:
                artifact: SEC.md
                role: security
                gate_doc: gates/s.md
        """)
        )
        from src.config import parse_pipeline_yaml

        result, err = parse_pipeline_yaml(str(yaml))
        assert err is None
        assert 3.5 in result["gates"]
        assert result["gates"][3.5]["role"] == "security"

    def test_gate_2_5_stst(self, tmp_path):
        """TC-G2: Gate 2.5 with role stst parses correctly."""
        yaml = tmp_path / "pipeline.yaml"
        yaml.write_text(
            textwrap.dedent("""\
            gates:
              2.5:
                artifact: STST-REPORT.md
                role: stst
                gate_doc: gates/stst.md
        """)
        )
        from src.config import parse_pipeline_yaml

        result, err = parse_pipeline_yaml(str(yaml))
        assert err is None
        assert 2.5 in result["gates"]
        assert result["gates"][2.5]["role"] == "stst"


# (Validator registration tests merged into TestValidatorRegistry above)


# =========================================================================
# Integration: full pipeline config with per-gate co-located config
# =========================================================================


class TestFullPipelineConfig:
    """TC-I1: Full pipeline.yaml with all per-gate features."""

    def test_full_config_parses(self, tmp_path):
        """TC-I1: Complete pipeline.yaml with per-gate skills, agents, model, thinking."""
        yaml = tmp_path / "pipeline.yaml"
        yaml.write_text(
            textwrap.dedent("""\
            threshold: A
            gates:
              1:
                artifact: SCOPE.md
                role: pm
                model: claude-opus-4-6
                thinking: adaptive
                effort: max
                skills:
                  - spec-driven-development
                  - debugging-and-error-recovery
                gate_doc: gates/discovery.md
              2:
                artifact: BUILD-STATUS.md
                role: dev
                model: sonnet
                thinking: adaptive
                effort: low
                skills:
                  - test-driven-development
                gate_doc: gates/build.md
              3:
                - artifact: QA-REPORT.md
                  role: qa
                  model: sonnet
                  thinking: adaptive
                  effort: max
                  threshold: A
                  skills:
                    - code-review-and-quality
                  agents:
                    - agents/qa-w-agent-skills
                  gate_doc: gates/test.md
                - artifact: SECURITY-REPORT.md
                  role: security
                  model: claude-opus-4-6
                  thinking: adaptive
                  effort: max
                  gate_doc: gates/security-review.md
              4:
                artifact: PM-VERIFY.md
                role: pm-verify
                model: claude-opus-4-6
                gate_doc: gates/verify.md
              5:
                artifact: SHIP-DECISION.md
                role: sflo
                gate_doc: gates/ship.md
        """)
        )
        from src.config import parse_pipeline_yaml

        result, err = parse_pipeline_yaml(str(yaml))
        assert err is None

        # Gates
        assert len(result["gates"]) == 5
        assert isinstance(result["gates"][3], list)
        assert len(result["gates"][3]) == 2

        # Per-gate scalar fields
        assert result["gates"][1]["model"] == "claude-opus-4-6"
        assert result["gates"][1]["thinking"] == "adaptive"
        assert result["gates"][1]["effort"] == "max"
        assert result["gates"][2]["model"] == "sonnet"

        # Per-gate skills
        assert result["gates"][1]["skills"] == [
            "spec-driven-development",
            "debugging-and-error-recovery",
        ]
        assert result["gates"][2]["skills"] == ["test-driven-development"]

        # Parallel gate entry fields
        qa_entry = result["gates"][3][0]
        assert qa_entry["role"] == "qa"
        assert qa_entry["model"] == "sonnet"
        assert qa_entry["threshold"] == "A"
        assert qa_entry["skills"] == ["code-review-and-quality"]
        assert qa_entry["agents"] == ["agents/qa-w-agent-skills"]

        sec_entry = result["gates"][3][1]
        assert sec_entry["role"] == "security"
        assert sec_entry["model"] == "claude-opus-4-6"

        # Threshold
        assert result["threshold"] == "A"


# =========================================================================
# Bug fixes: apply_transition defensive gate key
# =========================================================================


class TestApplyTransitionDefensiveGate:
    """TC-D1, TC-D2: apply_transition behavior with gate key presence."""

    def _make_gates(self):
        return {
            1: {"artifact": "SCOPE.md", "role": "pm", "gate_doc": "gates/d.md"},
            2: {"artifact": "BUILD.md", "role": "dev", "gate_doc": "gates/b.md"},
            3: {"artifact": "QA.md", "role": "qa", "gate_doc": "gates/t.md"},
            4: {"artifact": "PM-V.md", "role": "pm", "gate_doc": "gates/v.md"},
            5: {"artifact": "SHIP.md", "role": "sflo", "gate_doc": "gates/ship.md"},
        }

    def test_validated_transition_advances(self, tmp_path):
        """TC-D1: apply_transition marks gate done and advances."""
        gates = self._make_gates()
        state = {
            "current_state": "check-3",
            "gates": {
                "1": {"status": "done", "artifact": "SCOPE.md"},
                "2": {"status": "done", "artifact": "BUILD.md"},
                "3": {"status": "waiting", "artifact": "QA.md"},
                "4": {"status": "waiting", "artifact": "PM-V.md"},
                "5": {"status": "waiting", "artifact": "SHIP.md"},
            },
            "inner_loops": 0,
            "outer_loops": 0,
            "assignments": {},
            "roles": {},
        }
        result = {
            "state": "check-3",
            "action": "validated",
            "gate": 3,
            "pass": True,
            "checks": [],
        }
        with mock.patch("src.machine.GATES", gates):
            from src.machine import apply_transition

            apply_transition(state, result, str(tmp_path))
            assert state["gates"]["3"]["status"] == "done"
            assert state["current_state"] == "gate-4"

    def test_ext_gate_transition(self, tmp_path):
        """TC-D2: Float gate (2.1) transition works when state entry exists."""
        gates = self._make_gates()
        gates[2.1] = {
            "artifact": "DEVLOOP.md",
            "role": "dev",
            "gate_doc": "gates/dl.md",
        }
        state = {
            "current_state": "check-2.1",
            "gates": {
                "1": {"status": "done", "artifact": "SCOPE.md"},
                "2": {"status": "done", "artifact": "BUILD.md"},
                "2.1": {"status": "waiting", "artifact": "DEVLOOP.md"},
                "3": {"status": "waiting", "artifact": "QA.md"},
                "4": {"status": "waiting", "artifact": "PM-V.md"},
                "5": {"status": "waiting", "artifact": "SHIP.md"},
            },
            "inner_loops": 0,
            "outer_loops": 0,
            "assignments": {},
            "roles": {},
        }
        result = {
            "state": "check-2.1",
            "action": "validated",
            "gate": 2.1,
            "pass": True,
            "checks": [],
        }
        with mock.patch("src.machine.GATES", gates):
            from src.machine import apply_transition

            apply_transition(state, result, str(tmp_path))
            assert state["gates"]["2.1"]["status"] == "done"
