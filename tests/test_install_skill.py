"""Behavioral tests for the self-contained SFLO skill installer."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.install_skill import InstallError, install_skill


SFLO_ROOT = Path(__file__).resolve().parents[1]


def test_install_creates_complete_skill_that_survives_source_deletion(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(SFLO_ROOT, source, ignore=shutil.ignore_patterns(".git", ".sflo"))
    destination = tmp_path / "skills" / "sflo"

    result = install_skill(source, destination, runtime="codex", version="test-1")
    shutil.rmtree(source)

    assert result.destination == destination.resolve()
    assert (destination / "SKILL.md").is_file()
    assert (destination / "src" / "runner.py").is_file()
    assert (destination / "agents" / "dev" / "SOUL.md").is_file()
    assert (destination / "gates" / "build.md").is_file()
    assert (destination / "pipeline.yaml").is_file()
    assert (destination / "pipeline-cursor.yaml").is_file()
    assert (
        destination
        / "vendor"
        / "mattpocock-skills"
        / "skills"
        / "engineering"
        / "tdd"
        / "SKILL.md"
    ).is_file()
    assert not list((destination / "src" / "hooks").rglob("SKILL.md"))
    assert not (destination / "src" / "hooks" / "codex" / "skills").exists()
    assert not (destination / "tests").exists()
    assert not (destination / "src" / "tests").exists()
    assert not (destination / "vendor" / "mattpocock-skills" / "package.json").exists()
    assert not list(destination.rglob("__pycache__"))
    completed = subprocess.run(
        [sys.executable, str(destination / "src" / "runner.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    manifest = json.loads((destination / ".sflo-install.json").read_text())
    assert manifest["product"] == "sflo"
    assert manifest["runtime"] == "codex"
    assert manifest["version"] == "test-1"
    assert "src/runner.py" in manifest["files"]


def test_install_renders_paths_and_runtime_in_root_skill(tmp_path):
    source = _minimal_source(tmp_path / "source")
    (source / "skill" / "SKILL.md").write_text(
        "runner={{SFLO_RUNNER_SH}}\nscaffold={{SFLO_SCAFFOLD_SH}}\nruntime={{SFLO_RUNTIME}}\n",
        encoding="utf-8",
    )
    destination = tmp_path / "skills with spaces" / "sflo"

    install_skill(source, destination, runtime="cursor")

    skill = (destination / "SKILL.md").read_text(encoding="utf-8")
    assert "{{SFLO_" not in skill
    assert str(destination.resolve() / "src" / "runner.py") in skill
    assert str(destination.resolve() / "src" / "scaffold.py") in skill
    assert "runtime=cursor" in skill


@pytest.mark.parametrize("runtime", ("codex", "cursor", "claude-code", "openclaw"))
def test_real_skill_invokes_the_selected_runtime(tmp_path, runtime):
    destination = tmp_path / runtime / "sflo"

    install_skill(SFLO_ROOT, destination, runtime=runtime)

    skill = (destination / "SKILL.md").read_text(encoding="utf-8")
    assert f"--runtime {runtime}" in skill
    assert "{{SFLO_RUNTIME}}" not in skill
    assert "Get-Command py" in skill
    assert "@('-3')" in skill
    if runtime != "openclaw":
        assert "--runtime openclaw" not in skill


def test_install_refuses_existing_unowned_destination(tmp_path):
    source = _minimal_source(tmp_path / "source")
    destination = tmp_path / "skills" / "sflo"
    destination.mkdir(parents=True)
    sentinel = destination / "user-owned.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(InstallError, match="not SFLO-owned"):
        install_skill(source, destination, runtime="codex")

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_failed_activated_install_restores_previous_owned_skill(tmp_path):
    destination = tmp_path / "skills" / "sflo"
    previous = _minimal_source(tmp_path / "previous")
    install_skill(previous, destination, runtime="codex", version="old")
    old_runner = (destination / "src" / "runner.py").read_text(encoding="utf-8")

    broken = _minimal_source(tmp_path / "broken", fail_at_destination_name="sflo")
    with pytest.raises(InstallError, match="verification failed"):
        install_skill(broken, destination, runtime="codex", version="broken")

    assert (destination / "src" / "runner.py").read_text(encoding="utf-8") == old_runner
    assert json.loads((destination / ".sflo-install.json").read_text())["version"] == "old"


def test_cli_installs_requested_runtime_and_emits_machine_result(tmp_path):
    source = _minimal_source(tmp_path / "source")
    destination = tmp_path / "skills" / "sflo"

    completed = subprocess.run(
        [
            sys.executable,
            str(SFLO_ROOT / "src" / "install_skill.py"),
            "--source",
            str(source),
            "--runtime",
            "codex",
            "--destination",
            str(destination),
            "--version",
            "cli-test",
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {
        "ok": True,
        "destination": str(destination.resolve()),
        "runtime": "codex",
        "version": "cli-test",
        "manifest": str(destination.resolve() / ".sflo-install.json"),
    }


def _minimal_source(root: Path, fail_at_destination_name: str | None = None) -> Path:
    for directory in (
        root / "skill",
        root / "src" / "hooks" / "codex",
        root / "agents" / "dev",
        root / "gates",
        root / "vendor" / "mattpocock-skills" / "skills" / "engineering" / "tdd",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (root / "skill" / "SKILL.md").write_text("# SFLO\n", encoding="utf-8")
    condition = repr(fail_at_destination_name) if fail_at_destination_name else "None"
    (root / "src" / "runner.py").write_text(
        "from pathlib import Path\nimport sys\n"
        f"bad = {condition}\n"
        "if bad and Path(__file__).resolve().parents[1].name == bad: sys.exit(9)\n"
        "if '--help' in sys.argv: print('usage: sflo')\n",
        encoding="utf-8",
    )
    (root / "src" / "scaffold.py").write_text("# scaffold\n", encoding="utf-8")
    (root / "src" / "hooks" / "codex" / "hook.py").write_text("# hook\n", encoding="utf-8")
    (root / "agents" / "dev" / "SOUL.md").write_text("# developer\n", encoding="utf-8")
    (root / "gates" / "build.md").write_text("# build\n", encoding="utf-8")
    (root / "pipeline.yaml").write_text("threshold: A\n", encoding="utf-8")
    (
        root
        / "vendor"
        / "mattpocock-skills"
        / "skills"
        / "engineering"
        / "tdd"
        / "SKILL.md"
    ).write_text("# TDD\n", encoding="utf-8")
    (root / "LICENSE").write_text("test license\n", encoding="utf-8")
    return root
