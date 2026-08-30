"""Behavioral tests for updating a self-contained SFLO skill."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from src.install_skill import install_skill


SFLO_ROOT = Path(__file__).resolve().parents[1]


def test_installed_updater_atomically_replaces_skill_from_fresh_source(tmp_path):
    destination = tmp_path / "skills" / "sflo"
    install_skill(SFLO_ROOT, destination, runtime="codex", version="old")
    fresh_source = tmp_path / "fresh-source"
    shutil.copytree(
        SFLO_ROOT,
        fresh_source,
        ignore=shutil.ignore_patterns(".git", ".sflo", "__pycache__"),
    )
    with (fresh_source / "skill" / "SKILL.md").open("a", encoding="utf-8") as skill:
        skill.write("\nUpdated release marker.\n")

    completed = subprocess.run(
        [
            sys.executable,
            str(destination / "src" / "update_skill.py"),
            "--source",
            str(fresh_source),
        ],
        cwd=tmp_path,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Updated release marker." in (destination / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert (destination / "src" / "runner.py").is_file()
    assert not list(destination.parent.glob(".sflo.staging-*"))
    assert not list(destination.parent.glob(".sflo.backup-*"))


def test_installed_updater_downloads_a_disposable_git_source(tmp_path):
    destination = tmp_path / "skills" / "sflo"
    install_skill(SFLO_ROOT, destination, runtime="codex", version="old")
    release = tmp_path / "release"
    shutil.copytree(
        SFLO_ROOT,
        release,
        ignore=shutil.ignore_patterns(".git", ".sflo", "__pycache__"),
    )
    with (release / "skill" / "SKILL.md").open("a", encoding="utf-8") as skill:
        skill.write("\nDownloaded release marker.\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=release, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=release, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=SFLO Test",
            "-c",
            "user.email=sflo@example.invalid",
            "commit",
            "-m",
            "release",
        ],
        cwd=release,
        check=True,
        capture_output=True,
    )
    bare = tmp_path / "release.git"
    subprocess.run(
        ["git", "clone", "--bare", str(release), str(bare)],
        check=True,
        capture_output=True,
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(destination / "src" / "update_skill.py"),
            "--repository",
            bare.as_uri(),
        ],
        cwd=tmp_path,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Downloaded release marker." in (destination / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert not list(tmp_path.glob("sflo-update-*"))
