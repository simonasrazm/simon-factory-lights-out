"""Public SFLO setup cleanup regressions."""

import os
import subprocess
from pathlib import Path


SFLO_ROOT = Path(__file__).resolve().parents[1]


def test_setup_sh_codex_install_dir_writes_agents_block(tmp_path):
    """Codex setup installs AGENTS.md and ready status in install dir."""
    install_dir = tmp_path / "install"

    result = subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "setup.sh"),
            "--runtime",
            "codex",
            "--install-dir",
            str(install_dir),
            "--sflo-path",
            str(SFLO_ROOT),
        ],
        cwd=SFLO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert '"install_dir":"' + str(install_dir) + '"' in result.stdout
    assert '"workspace":' not in result.stdout
    agents = (install_dir / "AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- SFLO-AGENTS-START -->" in agents
    assert "sflo.md" in agents
    assert "pipeline.yaml" in agents
    assert (install_dir / ".sflo" / ".setup-status").read_text(
        encoding="utf-8"
    ) == "ready\n"


def test_setup_sh_rejects_removed_workspace_flag(tmp_path):
    """Setup rejects the removed --workspace flag."""
    install_dir = tmp_path / "install"

    result = subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "setup.sh"),
            "--runtime",
            "codex",
            "--workspace",
            str(install_dir),
            "--sflo-path",
            str(SFLO_ROOT),
        ],
        cwd=SFLO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Unknown option: --workspace" in (result.stderr + result.stdout)
    assert not (install_dir / "AGENTS.md").exists()


def test_hook_installer_openclaw_copies_hook(tmp_path):
    """OpenClaw hook repair copies the hook into the chosen install dir."""
    install_dir = tmp_path / "install"
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")

    result = subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "src/hooks/install.sh"),
            "--runtime",
            "openclaw",
            "--install-dir",
            str(install_dir),
        ],
        cwd=SFLO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    copied = install_dir / "hooks" / "sflo-pipeline"
    assert (copied / "handler.ts").is_file()
    assert (copied / ".sflo-home").read_text(encoding="utf-8") == str(SFLO_ROOT) + "\n"
    assert not copied.is_symlink()
    assert "Copied:" in result.stdout
    assert "Symlinked:" not in result.stdout


def test_hook_installer_rejects_removed_workspace_flag(tmp_path):
    """Hook-only repair also rejects the removed --workspace flag."""
    install_dir = tmp_path / "install"

    result = subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "src/hooks/install.sh"),
            "--runtime",
            "openclaw",
            "--workspace",
            str(install_dir),
        ],
        cwd=SFLO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Unknown option: --workspace" in (result.stderr + result.stdout)
    assert not (install_dir / "hooks").exists()


def test_setup_sh_openclaw_without_install_dir_uses_current_directory(tmp_path):
    """OpenClaw setup does not infer an install dir from runtime config."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    configured_workspace = tmp_path / "configured-openclaw-workspace"
    home = tmp_path / "home"
    openclaw_dir = home / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    (openclaw_dir / "openclaw.json").write_text(
        '{"agents":{"defaults":{"workspace":"'
        + str(configured_workspace)
        + '"}}}\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)

    result = subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "setup.sh"),
            "--runtime",
            "openclaw",
            "--sflo-path",
            str(SFLO_ROOT),
        ],
        cwd=install_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (install_dir / "hooks" / "sflo-pipeline" / "handler.ts").is_file()
    assert not (configured_workspace / "hooks" / "sflo-pipeline").exists()
    assert '"install_dir":"' + str(install_dir) + '"' in result.stdout


def test_hook_installer_openclaw_without_install_dir_uses_current_directory(tmp_path):
    """Hook repair uses cwd as install dir when --install-dir is omitted."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    configured_workspace = tmp_path / "configured-openclaw-workspace"
    home = tmp_path / "home"
    openclaw_dir = home / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    (openclaw_dir / "openclaw.json").write_text(
        '{"agents":{"defaults":{"workspace":"'
        + str(configured_workspace)
        + '"}}}\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)

    result = subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "src/hooks/install.sh"),
            "--runtime",
            "openclaw",
        ],
        cwd=install_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (install_dir / "hooks" / "sflo-pipeline" / "handler.ts").is_file()
    assert not (configured_workspace / "hooks" / "sflo-pipeline").exists()


def test_hook_installer_claude_code_removes_legacy_stop_key(tmp_path):
    """Hook repair does not leave stale lowercase Claude Code stop hooks."""
    install_dir = tmp_path / "install"
    settings_dir = install_dir / ".claude"
    settings_dir.mkdir(parents=True)
    settings_file = settings_dir / "settings.json"
    settings_file.write_text(
        '{"hooks":{"stop":[{"type":"command","command":"legacy"}],"Other":[]}}\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "src/hooks/install.sh"),
            "--runtime",
            "claude-code",
            "--install-dir",
            str(install_dir),
        ],
        cwd=SFLO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    settings = settings_file.read_text(encoding="utf-8")
    assert '"Stop"' in settings
    assert '"stop"' not in settings
    assert '"Other"' in settings


def test_hook_installer_claude_code_handles_single_quote_install_dir(tmp_path):
    """Claude hook repair must be argv-safe for paths containing single quotes."""
    install_dir = tmp_path / "install's-dir"
    settings_dir = install_dir / ".claude"
    settings_dir.mkdir(parents=True)
    settings_file = settings_dir / "settings.json"
    settings_file.write_text('{"hooks":{"Other":[]}}\n', encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "src/hooks/install.sh"),
            "--runtime",
            "claude-code",
            "--install-dir",
            str(install_dir),
        ],
        cwd=SFLO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "WARNING: Could not update settings automatically" not in result.stdout
    settings = settings_file.read_text(encoding="utf-8")
    assert '"Stop"' in settings


def test_public_sflo_has_no_hardcoded_local_workspace():
    """Public SFLO must not guess contributor-local workspace folders."""
    for rel in (
        "setup.sh",
        "src/hooks/install.sh",
        "src/hooks/openclaw/sflo-pipeline/handler.ts",
        "src/hooks/README.md",
    ):
        text = (SFLO_ROOT / rel).read_text(encoding="utf-8").lower()
        forbidden = ("claw" + "d",)
        for term in forbidden:
            assert term not in text, rel


def test_setup_ps1_has_no_credential_store_management():
    """Windows setup stays install-focused and avoids token storage flows."""
    text = (SFLO_ROOT / "setup.ps1").read_text(encoding="utf-8")

    forbidden = (
        "SecretStore",
        "SecretManagement",
        "Set-Secret",
        "Reset-SecretStore",
        "SFLO_CLAUDE",
        "CLAUDE_CODE_OAUTH_TOKEN",
    )
    for term in forbidden:
        assert term not in text


def test_setup_ps1_uses_install_dir_without_workspace_alias():
    """Windows setup mirrors bash setup naming without requiring PowerShell."""
    text = (SFLO_ROOT / "setup.ps1").read_text(encoding="utf-8")

    assert "[string]$InstallDir" in text
    assert "[string]$Workspace" not in text
    assert "-Workspace is deprecated" not in text
    assert "PSObject.Properties.Remove('stop')" in text


def test_setup_ps1_declares_reduced_windows_integration_scope():
    """PowerShell setup contract is explicit where it differs from bash setup."""
    text = (SFLO_ROOT / "setup.ps1").read_text(encoding="utf-8")

    assert "configures an existing SFLO checkout" in text
    assert "does not clone, copy, or update SFLO from git" in text
    assert "OpenClaw setup remains in setup.sh" in text


def test_setup_ps1_writes_all_runtime_status_and_result():
    """Windows setup reports the same status contract for supported runtimes."""
    text = (SFLO_ROOT / "setup.ps1").read_text(encoding="utf-8")

    assert "function Write-SetupStatus" in text
    assert "function Write-SetupResult" in text
    assert "SFLO_SETUP_RESULT:" in text
    assert "$status = Write-SetupStatus -InstallDir $InstallDir -Runtime $Runtime" in text
    assert (
        "Write-SetupResult -Runtime $Runtime -InstallDir $InstallDir "
        "-SfloHome $SfloHome -Status $status"
    ) in text


def test_openclaw_hook_resolves_active_factory_not_workspace_state():
    """OpenClaw receives a project workspace and resolves named factory state."""
    text = (
        SFLO_ROOT / "src" / "hooks" / "openclaw" / "sflo-pipeline" / "handler.ts"
    ).read_text(encoding="utf-8")

    assert 'join(workspaceDir, ".sflo")' in text
    assert 'join(sfloParent, "registry.json")' in text
    assert 'entry?.status === "active"' in text
    assert "if (hasState(workspaceDir))" not in text


def test_setup_sh_does_not_provision_python_runtime_venv():
    """Factory execution owns venv provisioning, not setup/install."""
    text = (SFLO_ROOT / "setup.sh").read_text(encoding="utf-8")

    assert "pip\" install" not in text
    assert "claude-agent-sdk" not in text
    assert ".sflo/.venv" not in text


def test_public_skill_uses_required_runtime_and_install_dir():
    """Root OpenClaw skill install instructions match setup.sh contract."""
    text = (SFLO_ROOT / "skill" / "SKILL.md").read_text(encoding="utf-8")

    assert "SFLO_DIR" in text
    assert "sflo/src/runner.py" not in text
    assert "sflo/src/scaffold.py" not in text
    assert "bash sflo/setup.sh\n" not in text
    assert "(default: B+)" not in text
    assert "--runtime" in text
    assert "--install-dir" in text
    assert 'src/runner.py "[description]"' not in text
    assert "| python3" in text


def test_installed_openclaw_skill_uses_runner_entrypoint():
    """Copied OpenClaw skill starts SFLO through the public runner."""
    text = (SFLO_ROOT / "src/hooks/openclaw/skill/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "scaffold.py init" not in text
    assert "src/runner.py" in text
    assert "printf '%s\\n'" in text
