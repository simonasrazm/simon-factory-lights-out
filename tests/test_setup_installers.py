import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


SFLO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX installer integration is covered by the Linux CI job",
)


def _records(output):
    prefix = "SFLO_SETUP_RESULT:"
    return [
        json.loads(line[len(prefix) :])
        for line in output.splitlines()
        if line.startswith(prefix)
    ]


def _run(install_dir, sflo_path, env=None):
    return subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "setup.sh"),
            "--runtime",
            "codex",
            "--install-dir",
            str(install_dir),
            "--sflo-path",
            str(sflo_path),
        ],
        cwd=SFLO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _clean_git_env(env=None):
    clean_env = (env or os.environ).copy()
    # Git hooks export repository-local variables. Fixture repositories must
    # never inherit the parent commit's gitdir, worktree, or index.
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_PREFIX",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
    ):
        clean_env.pop(name, None)
    return clean_env


def _git(*args, cwd=None, env=None):
    subprocess.run(
        [shutil.which("git"), *args],
        cwd=cwd,
        env=_clean_git_env(env),
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo, message):
    _git("add", ".", cwd=repo)
    _git(
        "-c",
        "user.name=SFLO Test",
        "-c",
        "user.email=sflo@example.invalid",
        "commit",
        "-m",
        message,
        cwd=repo,
    )


def test_populated_checkout_succeeds_without_invoking_git(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git = fake_bin / "git"
    git.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    git.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    install = tmp_path / "install"
    archive = tmp_path / "archive"
    matt_skill = (
        archive
        / "vendor"
        / "mattpocock-skills"
        / "skills"
        / "engineering"
        / "tdd"
        / "SKILL.md"
    )
    matt_skill.parent.mkdir(parents=True)
    matt_skill.write_text("# TDD\n")
    (archive / "pipeline.yaml").write_text("gates: {}\n")
    runtime_skill = (
        archive
        / "src"
        / "hooks"
        / "codex"
        / "skills"
        / "sflo-factory-triggering"
        / "SKILL.md"
    )
    runtime_skill.parent.mkdir(parents=True)
    runtime_skill.write_text("name: sflo-factory-triggering\n")

    result = _run(install, archive, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (install / ".sflo" / ".setup-status").read_text() == "ready\n"
    records = _records(result.stdout)
    assert records == [
        {
            "ok": True,
            "runtime": "codex",
            "install_dir": str(install),
            "sflo_path": str(archive),
            "status": "ready",
        }
    ]


def test_missing_matt_skills_fails_before_runtime_mutation(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pipeline.yaml").write_text("gates: {}\n", encoding="utf-8")
    install = tmp_path / "install"
    status = install / ".sflo" / ".setup-status"
    status.parent.mkdir(parents=True)
    status.write_text("ready\n", encoding="utf-8")

    result = _run(install, checkout)

    assert result.returncode != 0
    assert status.read_text() == "failed\n"
    records = _records(result.stdout)
    assert len(records) == 1
    assert records[0]["ok"] is False
    assert records[0]["status"] == "failed"
    assert "mattpocock-skills" in (result.stdout + result.stderr)
    assert not (install / ".agents").exists()


def test_remote_fresh_clone_contains_vendored_matt_skills_before_ready(tmp_path):
    source = tmp_path / "source"
    _git("init", "-b", "main", str(source))
    (source / "sflo.md").write_text("# SFLO\n", encoding="utf-8")
    (source / "pipeline.yaml").write_text("gates: {}\n", encoding="utf-8")
    runtime_skill = (
        source
        / "src"
        / "hooks"
        / "codex"
        / "skills"
        / "sflo-factory-triggering"
        / "SKILL.md"
    )
    runtime_skill.parent.mkdir(parents=True)
    runtime_skill.write_text("name: sflo-factory-triggering\n", encoding="utf-8")
    skill = source / "vendor" / "mattpocock-skills" / "skills" / "engineering" / "tdd" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# TDD\n", encoding="utf-8")
    _commit(source, "add sflo fixture")
    bare = tmp_path / "source.git"
    _git("clone", "--bare", str(source), str(bare))

    launcher = tmp_path / "launcher"
    launcher.mkdir()
    launcher_script = launcher / "setup.sh"
    shutil.copy2(SFLO_ROOT / "setup.sh", launcher_script)
    install = tmp_path / "install"
    result = subprocess.run(
        [
            "bash",
            str(launcher_script),
            "--runtime",
            "codex",
            "--install-dir",
            str(install),
            "--source",
            bare.as_uri(),
            "--branch",
            "main",
        ],
        cwd=launcher,
        env=_clean_git_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (install / "sflo" / "vendor" / "mattpocock-skills" / "skills" / "engineering" / "tdd" / "SKILL.md").is_file()
    assert (install / ".sflo" / ".setup-status").read_text() == "ready\n"
    records = _records(result.stdout)
    assert len(records) == 1 and records[0]["ok"] is True

    skill.write_text("# TDD v2\n", encoding="utf-8")
    _commit(source, "update vendored tdd")
    _git("push", str(bare), "main", cwd=source)

    rerun = subprocess.run(
        [
            "bash",
            str(launcher_script),
            "--runtime",
            "codex",
            "--install-dir",
            str(install),
            "--source",
            bare.as_uri(),
            "--branch",
            "main",
        ],
        cwd=launcher,
        env=_clean_git_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert (install / "sflo" / "vendor" / "mattpocock-skills" / "skills" / "engineering" / "tdd" / "SKILL.md").read_text() == "# TDD v2\n"
