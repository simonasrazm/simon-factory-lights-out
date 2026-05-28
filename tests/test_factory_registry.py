"""Tests for named SFLO factory run directories."""

import json
import os

from src.factory_registry import (
    FactoryRegistry,
    final_status_from_pipeline_state,
    format_registry_table,
    slug_from_prompt,
    validate_factory_name,
)


def test_slug_from_prompt_drops_common_verbs():
    assert slug_from_prompt("Build a fancy click counter with neon UI") == (
        "fancy-click-counter-neon-ui"
    )


def test_validate_factory_name_rejects_paths_and_caps():
    assert validate_factory_name("fancy-click-counter")
    assert not validate_factory_name("../escape")
    assert not validate_factory_name("Fancy")


def test_auto_slug_collision_bumps_name(tmp_path):
    reg = FactoryRegistry(str(tmp_path))
    reg.register_start("fancy-click-counter", tmp_path / "fancy-click-counter", "p", 0)

    resolved = reg.resolve_name(
        "fancy-click-counter", is_explicit=False, is_resume=False
    )

    assert resolved == "fancy-click-counter-2"


def test_explicit_existing_factory_is_refused(tmp_path):
    reg = FactoryRegistry(str(tmp_path))
    reg.register_start("named-run", tmp_path / "named-run", "p", 0)

    try:
        reg.resolve_name("named-run", is_explicit=True, is_resume=False)
    except Exception as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("existing explicit factory should be refused")


def test_register_end_and_table(tmp_path):
    reg = FactoryRegistry(str(tmp_path))
    reg.register_start("run-one", tmp_path / "run-one", "Build one", os.getpid())
    reg.register_end("run-one", FactoryRegistry.STATUS_DONE)

    data = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert data["factories"]["run-one"]["status"] == "done"
    assert "run-one" in format_registry_table(reg.list_all())


def test_register_end_records_terminal_exit_details(tmp_path):
    reg = FactoryRegistry(str(tmp_path))
    reg.register_start("run-one", tmp_path / "run-one", "Build one", os.getpid())

    reg.register_end(
        "run-one",
        FactoryRegistry.STATUS_ABORTED,
        exit_kind="signal",
        exit_details={"signal": 15, "signal_name": "SIGTERM"},
    )

    entry = reg.get("run-one")
    assert entry["status"] == "aborted"
    assert entry["last_exit"]["kind"] == "signal"
    assert entry["last_exit"]["signal"] == 15
    assert entry["last_exit"]["signal_name"] == "SIGTERM"
    assert entry["last_exit"]["pid"] == os.getpid()
    assert "at" in entry["last_exit"]


def test_kill_marks_aborted_and_removes_runner_pid(tmp_path):
    reg = FactoryRegistry(str(tmp_path))
    factory = tmp_path / "run-one"
    factory.mkdir()
    (factory / "runner.pid").write_text("123", encoding="utf-8")
    (factory / "pipeline.lock").write_text("{}", encoding="utf-8")
    reg.register_start("run-one", factory, "Build one", os.getpid())

    assert reg.kill("run-one")

    entry = reg.get("run-one")
    assert entry["status"] == "aborted"
    assert entry["last_operator_action"]["kind"] == "kill"
    assert entry["last_operator_action"]["reason"] == "operator_kill"
    assert entry["last_exit"]["kind"] == "operator_kill"
    assert entry["last_exit"]["reason"] == "operator_kill"
    assert entry["last_exit"]["observed"] is False
    assert entry["last_exit"]["pid"] == os.getpid()
    assert not (factory / "runner.pid").exists()
    assert not (factory / "pipeline.lock").exists()


def test_migrate_legacy_moves_only_known_state(tmp_path):
    (tmp_path / "state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "SCOPE.md").write_text("scope", encoding="utf-8")
    (tmp_path / "pipeline.lock").write_text("{}", encoding="utf-8")
    (tmp_path / "user-notes.md").write_text("keep", encoding="utf-8")
    reg = FactoryRegistry(str(tmp_path))

    migrated = reg.migrate_legacy()

    assert migrated == "legacy"
    assert (tmp_path / "legacy" / "state.json").exists()
    assert (tmp_path / "legacy" / "SCOPE.md").exists()
    assert (tmp_path / "legacy" / "pipeline.lock").exists()
    assert (tmp_path / "user-notes.md").exists()


def test_final_status_mapping():
    assert final_status_from_pipeline_state("done") == "done"
    assert final_status_from_pipeline_state("gate-2") == "aborted"
