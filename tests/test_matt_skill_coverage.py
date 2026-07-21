from pathlib import Path

from src.config import derive_roles_from_pipeline, load_pipeline_config
from src.machine import compute_next, resolve_skill_paths
from src.runner import render_skill_methodologies


ROOT = Path(__file__).resolve().parents[1]
PIPELINES = ("pipeline.yaml", "pipeline-claude.yaml", "pipeline-cursor.yaml")


def _role_skill_names(config):
    result = {"scout": config.get("scout", {}).get("skills", [])}
    for raw in config["gates"].values():
        for entry in raw if isinstance(raw, list) else [raw]:
            result[entry["role"]] = entry.get("skills", [])
    return result


def test_defaults_attach_only_evidence_supported_skills():
    for pipeline in PIPELINES:
        roles = _role_skill_names(load_pipeline_config(str(ROOT / pipeline)))
        assert roles["dev"] == [
            "mattpocock-skills/engineering/tdd",
            "mattpocock-skills/engineering/code-review",
        ]
        assert roles["qa"] == ["mattpocock-skills/engineering/code-review"]
        assert all(
            not skills
            for role, skills in roles.items()
            if role not in {"dev", "qa"}
        )


def test_opt_in_matt_skills_still_resolve_and_render_multiple():
    paths = resolve_skill_paths(
        [
            "mattpocock-skills/engineering/tdd",
            "mattpocock-skills/engineering/codebase-design",
        ],
        str(ROOT),
    )
    sections = render_skill_methodologies(paths)
    assert len(sections) == 2
    assert sum(section.count("## Methodology:") for section in sections) == 2


def test_scout_and_ship_action_contracts_allow_empty_skills():
    config = load_pipeline_config(str(ROOT / "pipeline.yaml"))
    roles = derive_roles_from_pipeline(config)
    scout = compute_next(
        {"current_state": "scout", "roles": roles, "assignments": {}},
        str(ROOT / ".sflo-test"),
        gates=config["gates"],
    )
    assert scout["agent"]["skills"] == []

    ship = compute_next(
        {"current_state": "gate-5", "roles": roles, "assignments": {}},
        str(ROOT / ".sflo-test"),
        gates=config["gates"],
    )
    assert ship["action"] == "produce_artifact"
    assert ship["skills"] == []
