"""Windows setup contract for self-contained runtime skills.

These tests intentionally inspect the PowerShell entry point on hosts where
PowerShell is unavailable.  The shared ``install_skill.py`` implementation has
its own behavioral tests; this file verifies that Windows routes every runtime
through it and never renders a skill back to the downloaded checkout.
"""

from pathlib import Path


SFLO_ROOT = Path(__file__).resolve().parents[1]
SETUP = (SFLO_ROOT / "setup.ps1").read_text(encoding="utf-8")


def test_windows_setup_installs_every_runtime_with_shared_packager():
    assert "function Install-SfloSelfContainedSkill" in SETUP
    assert "src\\install_skill.py" in SETUP
    assert "--source" in SETUP
    assert "--runtime" in SETUP
    assert "--destination" in SETUP

    for function in (
        "Install-SfloCodex",
        "Install-SfloCursor",
        "Install-SfloClaudeCode",
    ):
        body = SETUP.split(f"function {function}", 1)[1].split("\nfunction ", 1)[0]
        assert "Install-SfloSelfContainedSkill" in body


def test_windows_setup_uses_conventional_global_skill_roots():
    assert "function Get-CodexSkillsRoot" in SETUP
    assert "Join-Path $HOME '.agents'" in SETUP
    assert "Join-Path $agentsHome 'skills'" in SETUP
    assert "function Get-CursorSkillsRoot" in SETUP
    assert "Join-Path $cursorHome 'skills'" in SETUP
    assert "function Get-ClaudeSkillsRoot" in SETUP
    assert "Join-Path $claudeHome 'skills'" in SETUP

    codex = SETUP.split("function Install-SfloCodex", 1)[1].split(
        "\nfunction ", 1
    )[0]
    cursor = SETUP.split("function Install-SfloCursor", 1)[1].split(
        "\nfunction ", 1
    )[0]
    claude = SETUP.split("function Install-SfloClaudeCode", 1)[1].split(
        "\nif ($DefineFunctionsOnly)", 1
    )[0]
    assert "Get-SfloSkillDestination -Runtime 'codex'" in codex
    assert "Get-SfloSkillDestination -Runtime 'cursor'" in cursor
    assert "Get-SfloSkillDestination -Runtime 'claude-code'" in claude


def test_windows_setup_runtime_hooks_reference_installed_payload():
    cursor = SETUP.split("function Install-SfloCursor", 1)[1].split(
        "\nfunction ", 1
    )[0]
    claude = SETUP.split("function Install-SfloClaudeCode", 1)[1].split(
        "\nif ($DefineFunctionsOnly)", 1
    )[0]

    assert "$installedSfloHome = $skillDst" in cursor
    assert "Join-Path $installedSfloHome 'src\\hooks\\cursor\\stop_hook.py'" in cursor
    assert "$installedSfloHome = $skillDst" in claude
    assert (
        "Join-Path $installedSfloHome 'src\\hooks\\claude-code\\stop_hook.py'"
        in claude
    )


def test_windows_setup_does_not_copy_thin_checkout_bound_skills():
    assert "src\\hooks\\codex\\skills\\sflo'" not in SETUP
    assert "src\\hooks\\cursor\\skills\\sflo'" not in SETUP
    assert "Install-SfloOwnedSkillDirectory -SourceDir" not in SETUP


def test_windows_setup_keeps_project_pipeline_outside_global_skill():
    cursor = SETUP.split("function Install-SfloCursor", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "Install-SfloRuntimePipeline" in cursor
    assert "-InstallDir $InstallDir" in cursor
    assert "-InstallDir $skillDst" not in cursor


def test_windows_setup_writes_json_atomically_without_utf8_bom():
    writer = SETUP.split("function Write-JsonObject", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "UTF8Encoding($false)" in SETUP
    assert "[System.IO.File]::Replace" in writer
    assert "[Guid]::NewGuid()" in writer
    assert "Set-Content -Path $Path" not in writer


def test_windows_setup_preserves_unrelated_stop_hooks():
    claude = SETUP.split("function Set-StopHook", 1)[1].split(
        "\nfunction ", 1
    )[0]
    cursor = SETUP.split("function Set-CursorStopHook", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "$existingStop" in claude
    assert r"src[\\/]hooks[\\/]claude-code[\\/]stop_hook" in claude
    assert r"src[\\/]hooks[\\/]cursor[\\/]stop_hook" in cursor
    assert "-match 'stop_hook\\.py'" not in cursor


def test_windows_legacy_cleanup_recognizes_only_owned_skill_markers():
    ownership = SETUP.split("function Test-SfloOwnedSkillDirectory", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert ".sflo-install.json" in ownership
    assert ".sflo-owned" in ownership
    assert "$manifest.product -eq 'sflo'" in ownership
