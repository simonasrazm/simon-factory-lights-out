"""Regression checks for the default sequential QA then Security pipeline."""

from pathlib import Path

from src.config import load_pipeline_config
from src.machine import compute_next, resolve_skill_paths
from src.validate import validate_gate


ROOT = Path(__file__).resolve().parents[1]
PIPELINES = ("pipeline.yaml", "pipeline-claude.yaml", "pipeline-cursor.yaml")


def _state_at(gate, gates):
    return {
        "current_state": f"gate-{gate}",
        "assignments": {},
        "roles": {},
        "inner_loops": 0,
        "outer_loops": 0,
        "gates": {str(key): {"status": "pending"} for key in gates},
        "gate_retries": {},
    }


def test_default_pipeline_uses_selected_role_configurations():
    config = load_pipeline_config(str(ROOT / "pipeline.yaml"))
    gates = config["gates"]

    assert gates[2]["model"] == "gpt-5.6-terra"
    assert gates[2]["effort"] == "medium"
    assert gates[2]["skills"] == [
        "mattpocock-skills/engineering/tdd",
        "mattpocock-skills/engineering/code-review",
    ]
    assert gates[3]["role"] == "qa"
    assert gates[3]["skills"] == ["mattpocock-skills/engineering/code-review"]
    assert gates[3]["on_reject_restart_at"] == 2
    assert gates[3.5]["role"] == "security"
    assert "skills" not in gates[3.5]
    assert gates[3.5]["on_reject_restart_at"] == 2


def test_all_pipeline_variants_run_security_after_qa(tmp_path):
    for pipeline in PIPELINES:
        gates = load_pipeline_config(str(ROOT / pipeline))["gates"]
        sflo_dir = tmp_path / pipeline
        sflo_dir.mkdir()

        qa = compute_next(_state_at(3, gates), str(sflo_dir), gates=gates)
        assert qa["action"] == "spawn_agent"
        assert qa["agent"]["role"] == "qa"

        state = _state_at(3, gates)
        state["current_state"] = "check-3"
        transitioned = {
            "action": "validated",
            "gate": 3,
            "pass": True,
            "checks": [],
        }
        from src.machine import apply_transition

        out = apply_transition(state, transitioned, str(sflo_dir), gates=gates)
        assert state["current_state"] == "gate-3.5"
        assert out["next"]["agent"]["role"] == "security"


def test_single_float_security_gate_enforces_grade_and_critical_findings(tmp_path):
    gates = {
        3.5: {
            "artifact": "SECURITY-REPORT.md",
            "role": "security",
            "threshold": "A",
        }
    }
    report = tmp_path / "SECURITY-REPORT.md"

    report.write_text("# Security\n\nCritical: 0\n\n### Grade: A\n")
    passed, checks = validate_gate(3.5, str(tmp_path), gates=gates)
    assert passed
    assert any(c["name"] == "grade_sufficient" and c["pass"] for c in checks)

    report.write_text("# Security\n\nCritical: 1\n\n### Grade: A\n")
    passed, checks = validate_gate(3.5, str(tmp_path), gates=gates)
    assert not passed
    assert any(c["name"] == "no_critical_findings" and not c["pass"] for c in checks)

    report.write_text("# Security\n\nCritical: 0\n\n### Grade: B\n")
    passed, checks = validate_gate(3.5, str(tmp_path), gates=gates)
    assert not passed
    assert any(c["name"] == "grade_sufficient" and not c["pass"] for c in checks)


def test_all_default_matt_skills_resolve():
    config = load_pipeline_config(str(ROOT / "pipeline.yaml"))
    configured = [
        skill
        for gate in config["gates"].values()
        for entry in (gate if isinstance(gate, list) else [gate])
        for skill in entry.get("skills", [])
    ]
    resolved = resolve_skill_paths(configured, str(ROOT))
    assert len(resolved) == len(configured)
    assert all(Path(path).is_file() for path in resolved)


def test_security_rejection_preserves_feedback_before_restart(tmp_path):
    from src.machine import apply_transition

    gates = load_pipeline_config(str(ROOT / "pipeline.yaml"))["gates"]
    sflo_dir = tmp_path / ".sflo"
    sflo_dir.mkdir()
    (sflo_dir / "SECURITY-REPORT.md").write_text(
        "# Security\n\nCritical: 1\n\n### Findings\nUnsafe input.\n\n### Grade: C\n"
    )
    state = _state_at(3.5, gates)
    result = {
        "action": "check_failed",
        "gate": 3.5,
        "pass": False,
        "checks": [{"name": "no_critical_findings", "pass": False}],
    }

    out = apply_transition(state, result, str(sflo_dir), gates=gates)

    assert out["action"] == "loop_back"
    assert state["current_state"] == "gate-2"
    feedback = (sflo_dir / "SECURITY-REPORT-FEEDBACK.md").read_text()
    assert "Unsafe input" in feedback
    assert "Security Grade: C" in feedback
