"""Public SFLO setup cleanup regressions."""

import os
import subprocess
from pathlib import Path

import pytest


SFLO_ROOT = Path(__file__).resolve().parents[1]
requires_posix_shell = pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX installer integration is covered by the Linux CI job",
)


@requires_posix_shell
def test_setup_sh_codex_install_dir_writes_factory_skill(tmp_path):
    """Codex setup installs the factory-triggering skill and ready status."""
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
    skill = (
        install_dir / ".agents" / "skills" / "sflo-factory-triggering" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "name: sflo-factory-triggering" in skill
    assert "--runtime codex" in skill
    assert str(SFLO_ROOT) in skill
    assert "{{SFLO_PATH}}" not in skill
    assert "{{SFLO_RUNNER_SH}}" not in skill
    assert "<<'SFLO_TASK'" in skill
    assert not (install_dir / "AGENTS.md").exists()
    assert (install_dir / ".sflo" / ".setup-status").read_text(
        encoding="utf-8"
    ) == "ready\n"


@requires_posix_shell
def test_setup_sh_codex_removes_old_agents_trigger_block(tmp_path):
    """Codex setup removes old token-trigger AGENTS blocks without clobbering user text."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    agents_file = install_dir / "AGENTS.md"
    agents_file.write_text(
        "Keep this.\n\n"
        "<!-- SFLO-AGENTS-START -->\n"
        "# SFLO\n"
        "When the user says `SFLO:`, run it.\n"
        "<!-- SFLO-AGENTS-END -->\n\n"
        "Keep this too.\n",
        encoding="utf-8",
    )

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
    agents = agents_file.read_text(encoding="utf-8")
    assert "Keep this." in agents
    assert "Keep this too." in agents
    assert "SFLO-AGENTS-START" not in agents
    assert "When the user says `SFLO:`" not in agents


@requires_posix_shell
def test_setup_sh_cursor_installs_global_factory_skill(tmp_path):
    """Cursor setup installs the guarded factory-triggering skill globally."""
    install_dir = tmp_path / "install"
    home_dir = tmp_path / "home"
    rule_file = install_dir / ".cursor" / "rules" / "sflo.mdc"
    rule_file.parent.mkdir(parents=True)
    rule_file.write_text("old token trigger\n", encoding="utf-8")
    duplicate_rule = install_dir / ".cursor" / "rules" / "sflo-factory-triggering.mdc"
    duplicate_rule.write_text("intermediate duplicate\n", encoding="utf-8")
    (home_dir / ".cursor" / "skills-cursor").mkdir(parents=True)
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    old_global_skill = (
        home_dir / ".cursor" / "skills" / "sflo-factory-triggering" / "SKILL.md"
    )
    old_global_skill.parent.mkdir(parents=True)
    old_global_skill.write_text("# SFLO Factory Triggering\n", encoding="utf-8")
    old_skills_cursor_skill = (
        home_dir
        / ".cursor"
        / "skills-cursor"
        / "sflo-factory-triggering"
        / "SKILL.md"
    )
    old_skills_cursor_skill.parent.mkdir(parents=True)
    old_skills_cursor_skill.write_text("# SFLO Factory Triggering\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "setup.sh"),
            "--runtime",
            "cursor",
            "--install-dir",
            str(install_dir),
            "--sflo-path",
            str(SFLO_ROOT),
        ],
        cwd=SFLO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    skill = (
        home_dir
        / ".cursor"
        / "skills"
        / "sflo"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "SFLO Factory Triggering" in skill
    assert "name: sflo" in skill
    assert "disable-model-invocation: true" in skill
    assert "--runtime cursor" in skill
    assert str(SFLO_ROOT) in skill
    assert "{{SFLO_PATH}}" not in skill
    assert "{{SFLO_RUNNER_SH}}" not in skill
    assert "<<'SFLO_TASK'" in skill
    assert (install_dir / "pipeline.yaml").read_text(encoding="utf-8") == (
        SFLO_ROOT / "pipeline-cursor.yaml"
    ).read_text(encoding="utf-8")
    assert (old_global_skill.parents[1] / "sflo" / ".sflo-owned").is_file()
    compat_skill = (
        home_dir
        / ".cursor"
        / "skills-cursor"
        / "sflo"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "name: sflo" in compat_skill
    assert "--runtime cursor" in compat_skill
    assert (old_skills_cursor_skill.parents[1] / "sflo" / ".sflo-owned").is_file()
    assert not rule_file.exists()
    assert not duplicate_rule.exists()
    assert not (install_dir / ".cursor" / "rules").exists()
    assert not old_global_skill.parent.exists()
    assert not old_skills_cursor_skill.parent.exists()
    assert (install_dir / ".cursor" / "hooks.json").is_file()


@requires_posix_shell
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


@requires_posix_shell
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


@requires_posix_shell
def test_hook_installer_cursor_installs_global_factory_skill(tmp_path):
    """Cursor hook repair installs the guarded global factory-triggering skill."""
    install_dir = tmp_path / "install"
    home_dir = tmp_path / "home"
    old_rule = install_dir / ".cursor" / "rules" / "sflo.mdc"
    old_rule.parent.mkdir(parents=True)
    old_rule.write_text("old token trigger\n", encoding="utf-8")
    duplicate_rule = install_dir / ".cursor" / "rules" / "sflo-factory-triggering.mdc"
    duplicate_rule.write_text("intermediate duplicate\n", encoding="utf-8")
    (home_dir / ".cursor" / "skills-cursor").mkdir(parents=True)
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    old_global_skill = (
        home_dir / ".cursor" / "skills" / "sflo-factory-triggering" / "SKILL.md"
    )
    old_global_skill.parent.mkdir(parents=True)
    old_global_skill.write_text("# SFLO Factory Triggering\n", encoding="utf-8")
    old_skills_cursor_skill = (
        home_dir
        / ".cursor"
        / "skills-cursor"
        / "sflo-factory-triggering"
        / "SKILL.md"
    )
    old_skills_cursor_skill.parent.mkdir(parents=True)
    old_skills_cursor_skill.write_text("# SFLO Factory Triggering\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            str(SFLO_ROOT / "src/hooks/install.sh"),
            "--runtime",
            "cursor",
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
    skill = (
        home_dir
        / ".cursor"
        / "skills"
        / "sflo"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "name: sflo" in skill
    assert "disable-model-invocation: true" in skill
    assert "--runtime cursor" in skill
    assert str(SFLO_ROOT) in skill
    assert "{{SFLO_PATH}}" not in skill
    assert "{{SFLO_RUNNER_SH}}" not in skill
    assert "<<'SFLO_TASK'" in skill
    assert (install_dir / "pipeline.yaml").read_text(encoding="utf-8") == (
        SFLO_ROOT / "pipeline-cursor.yaml"
    ).read_text(encoding="utf-8")
    assert (old_global_skill.parents[1] / "sflo" / ".sflo-owned").is_file()
    compat_skill = (
        home_dir
        / ".cursor"
        / "skills-cursor"
        / "sflo"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "name: sflo" in compat_skill
    assert "--runtime cursor" in compat_skill
    assert (old_skills_cursor_skill.parents[1] / "sflo" / ".sflo-owned").is_file()
    assert not old_rule.exists()
    assert not duplicate_rule.exists()
    assert not (install_dir / ".cursor" / "rules").exists()
    assert not old_global_skill.parent.exists()
    assert not old_skills_cursor_skill.parent.exists()
    assert (install_dir / ".cursor" / "hooks.json").is_file()


def test_cursor_hook_template_uses_shell_quoted_placeholder():
    """Manual Cursor hook template does not ship an unquoted SFLO path."""
    text = (SFLO_ROOT / "src/hooks/cursor/hooks.json.template").read_text(
        encoding="utf-8"
    )

    assert "{{SFLO_CURSOR_STOP_HOOK_SH}}" in text
    assert "{{SFLO_PATH}}/src/hooks/cursor/stop_hook.py" not in text


@requires_posix_shell
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


@requires_posix_shell
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


@requires_posix_shell
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


@requires_posix_shell
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


@requires_posix_shell
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
    assert "$status = Write-SetupStatus -InstallDir $InstallDir -Status 'ready'" in text
    assert (
        "Write-SetupResult -Runtime $Runtime -InstallDir $InstallDir "
        "-SfloHome $SfloHome -Status $status -Ok $true"
    ) in text
    assert "-Status 'failed' -Ok $false" in text
    assert "function Assert-SfloVendoredSkills" in text
    assert "required vendored Matt skill is missing" in text
    assert "git not found; skipping" not in text


def test_setup_ps1_cursor_installs_runtime_pipeline():
    """Windows Cursor setup preserves custom pipelines and stages new defaults."""
    text = (SFLO_ROOT / "setup.ps1").read_text(encoding="utf-8")

    assert "function Install-SfloRuntimePipeline" in text
    assert "pipeline-cursor.yaml" in text
    assert "pipeline.yaml.sflo-default" in text
    assert "Existing project pipeline preserved" in text


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
    assert "printf '%s\\n'" not in text
    assert '"[description]"' not in text
    assert "<<'SFLO_TASK'" in text
    assert "| python3" not in text
    assert "Use when user says SFLO" not in text
    assert 'When user says "SFLO:' not in text


def test_trigger_docs_use_heredoc_not_shell_quoted_prompt_placeholders():
    """Runtime-facing docs avoid examples that expand prompt text in the shell."""
    docs = {
        "sflo.md": (SFLO_ROOT / "sflo.md").read_text(encoding="utf-8"),
        "root skill": (SFLO_ROOT / "skill" / "SKILL.md").read_text(
            encoding="utf-8"
        ),
        "openclaw skill": (
            SFLO_ROOT / "src/hooks/openclaw/skill/SKILL.md"
        ).read_text(encoding="utf-8"),
    }

    for name, text in docs.items():
        assert "echo '<description>'" not in text, name
        assert "printf '%s\\n' \"[description]\"" not in text, name
        assert '"[description]"' not in text, name
        assert "<<'SFLO_TASK'" in text, name


def test_codex_and_cursor_factory_skills_have_explicit_trigger_guard():
    """Runtime factory skills reject inert SFLO token mentions."""
    codex = (
        SFLO_ROOT
        / "src"
        / "hooks"
        / "codex"
        / "skills"
        / "sflo-factory-triggering"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    cursor = (
        SFLO_ROOT
        / "src"
        / "hooks"
        / "cursor"
        / "skills"
        / "sflo"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "--runtime codex" in codex
    assert "Run only for an explicit factory action" in codex
    assert "Quoted text, docs, logs" in codex
    assert "printf '%s\\n'" not in codex
    assert '"[task description]"' not in codex
    assert "{{SFLO_RUNNER_SH}}" in codex
    assert "--runtime cursor" in cursor
    assert "name: sflo" in cursor
    assert "disable-model-invocation: true" in cursor
    assert "Run only when explicitly invoked as `/sflo`" in cursor
    assert "Quoted text, docs, logs" in cursor
    assert "printf '%s\\n'" not in cursor
    assert '"[task description]"' not in cursor
    assert "{{SFLO_RUNNER_SH}}" in cursor
    assert "```powershell" in codex
    assert "```powershell" in cursor
    assert "@'" in codex
    assert "@'" in cursor


def test_vendor_provenance_matches_pinned_upstream_release():
    provenance = (
        SFLO_ROOT / "vendor" / "mattpocock-skills" / "SFLO-VENDOR.md"
    ).read_text(encoding="utf-8")

    assert "d574778f94cf620fcc8ce741584093bc650a61d3" in provenance
    assert "d574778f7a8a2fdfe902a4ca60929ef5af946717" not in provenance


def test_readme_describes_sequential_reviews_and_links_evidence():
    readme = (SFLO_ROOT / "README.md").read_text(encoding="utf-8")
    evaluation = SFLO_ROOT / "docs" / "evaluation.md"

    assert "### Sequential QA and security" in readme
    assert "Gate 3 is a parallel fan-out by default" not in readme
    assert "docs/evaluation.md" in readme
    assert evaluation.is_file()
    evidence = evaluation.read_text(encoding="utf-8")
    assert "788" in evidence
    assert "951" in evidence
    assert "End-to-end latency" in evidence


def test_old_token_trigger_templates_are_not_shipped():
    """Obsolete token-trigger templates stay out of the source tree."""
    assert not (SFLO_ROOT / "src/hooks/codex/AGENTS.md").exists()
    assert not (SFLO_ROOT / "src/hooks/cursor/sflo.mdc").exists()
    assert not (
        SFLO_ROOT / "src/hooks/cursor/skills/sflo-factory-triggering.mdc"
    ).exists()
    assert not (
        SFLO_ROOT / "src/hooks/cursor/skills/sflo-factory-triggering"
    ).exists()


def test_installed_openclaw_skill_uses_runner_entrypoint():
    """Copied OpenClaw skill starts SFLO through the public runner."""
    text = (SFLO_ROOT / "src/hooks/openclaw/skill/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "scaffold.py init" not in text
    assert "{{SFLO_RUNNER_SH}}" in text
    assert "printf '%s\\n'" not in text
    assert "<<'SFLO_TASK'" in text
    assert "Use when user says SFLO" not in text
    assert 'When user says "SFLO:' not in text
