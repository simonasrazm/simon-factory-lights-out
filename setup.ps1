<#
.SYNOPSIS
    Set up SFLO runtime integration files on Windows.

.DESCRIPTION
    Installs the selected SFLO runtime integration into an install directory.
    Re-running is safe.

    This script configures an existing SFLO checkout.
    It does not clone, copy, or update SFLO from git, and it does not install OpenClaw integration.
    OpenClaw setup remains in setup.sh.

.PARAMETER Runtime
    REQUIRED. One of: codex, cursor, claude-code.

.PARAMETER InstallDir
    Directory for runtime files such as AGENTS.md, .cursor, or .claude.
    Defaults to the current directory.

.PARAMETER SfloHome
    Path to the SFLO checkout, the folder containing src\runner.py.
    Defaults to the directory this script lives in. Alias: -SfloPath.

.PARAMETER DefineFunctionsOnly
    Internal/testing switch. Defines functions and returns without installing.

.EXAMPLE
    .\sflo\setup.ps1 -Runtime codex
#>
[CmdletBinding()]
param(
    [ValidateSet('codex', 'cursor', 'claude-code')]
    [string]$Runtime,
    [string]$InstallDir,
    [Alias('SfloPath')]
    [string]$SfloHome,
    [switch]$DefineFunctionsOnly
)

function Resolve-SfloPath {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path)
}

function Assert-SfloCheckout {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$SfloHome)

    $runner = Join-Path $SfloHome 'src\runner.py'
    if (-not (Test-Path $runner)) {
        throw "SFLO runner not found at $runner. Run this script from the SFLO checkout, or pass -SfloPath."
    }
}

function Initialize-SfloSubmodules {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$SfloHome)

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Warning "git not found; skipping submodule initialization."
        return
    }

    Write-Host "==> Initializing git submodules" -ForegroundColor Cyan
    try {
        git -C $SfloHome submodule update --init --recursive
        if ($LASTEXITCODE -ne 0) { throw "git exited $LASTEXITCODE" }
        Write-Host "    Submodules initialized" -ForegroundColor Green
    } catch {
        Write-Warning "git submodule init skipped or failed: $($_.Exception.Message)"
    }
}

function Get-SfloPythonCommand {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return 'python'
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return 'py -3'
    }
    return 'python'
}

function Read-JsonObject {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    if (Test-Path $Path) {
        try {
            $content = Get-Content $Path -Raw
            if (-not [string]::IsNullOrWhiteSpace($content)) {
                return ($content | ConvertFrom-Json)
            }
        } catch {
            Write-Warning "Could not parse $Path; replacing it with SFLO configuration."
        }
    }
    return [pscustomobject]@{}
}

function Ensure-ObjectProperty {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$Object,
        [Parameter(Mandatory)][string]$Name
    )

    if (-not $Object.PSObject.Properties[$Name]) {
        $Object | Add-Member -MemberType NoteProperty -Name $Name -Value ([pscustomobject]@{})
    } elseif ($null -eq $Object.$Name) {
        $Object | Add-Member -Force -MemberType NoteProperty -Name $Name -Value ([pscustomobject]@{})
    }
    return $Object.$Name
}

function Write-JsonObject {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Object
    )

    $dir = Split-Path -Parent $Path
    if ($dir) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $Object | ConvertTo-Json -Depth 20 | Set-Content -Path $Path -Encoding UTF8
}

function Write-SetupStatus {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$InstallDir,
        [Parameter(Mandatory)][string]$Runtime
    )

    $sfloDir = Join-Path $InstallDir '.sflo'
    New-Item -ItemType Directory -Path $sfloDir -Force | Out-Null
    $status = 'ready'
    Set-Content -Path (Join-Path $sfloDir '.setup-status') -Value $status -Encoding UTF8
    return $status
}

function Write-SetupResult {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Runtime,
        [Parameter(Mandatory)][string]$InstallDir,
        [Parameter(Mandatory)][string]$SfloHome,
        [Parameter(Mandatory)][string]$Status
    )

    $result = [ordered]@{
        ok = $true
        runtime = $Runtime
        install_dir = $InstallDir
        sflo_path = $SfloHome
        status = $Status
    }
    Write-Host ('SFLO_SETUP_RESULT:' + ($result | ConvertTo-Json -Compress))
}

function Set-StopHook {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SettingsFile,
        [Parameter(Mandatory)][string]$HookCommand
    )

    $settings = Read-JsonObject -Path $SettingsFile
    $hooks = Ensure-ObjectProperty -Object $settings -Name 'hooks'
    $hooks | Add-Member -Force -MemberType NoteProperty -Name 'Stop' -Value @(
        [pscustomobject]@{
            type = 'command'
            command = $HookCommand
        }
    )
    if ($hooks.PSObject.Properties['stop']) {
        $hooks.PSObject.Properties.Remove('stop')
    }
    Write-JsonObject -Path $SettingsFile -Object $settings
}

function Set-CursorStopHook {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$HooksFile,
        [Parameter(Mandatory)][string]$HookCommand
    )

    $data = Read-JsonObject -Path $HooksFile
    $data | Add-Member -Force -MemberType NoteProperty -Name 'version' -Value 1
    $hooks = Ensure-ObjectProperty -Object $data -Name 'hooks'

    $existingStop = @()
    if ($hooks.PSObject.Properties['stop']) {
        $existingStop = @($hooks.stop) | Where-Object {
            -not (([string]$_.command) -match 'stop_hook\.py')
        }
    }

    $sfloHook = [pscustomobject]@{
        command = $HookCommand
        loop_limit = $null
    }
    $hooks | Add-Member -Force -MemberType NoteProperty -Name 'stop' -Value (@($sfloHook) + @($existingStop))
    Write-JsonObject -Path $HooksFile -Object $data
}

function Install-SfloCodex {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SfloHome,
        [Parameter(Mandatory)][string]$InstallDir
    )

    Assert-SfloCheckout -SfloHome $SfloHome
    Initialize-SfloSubmodules -SfloHome $SfloHome

    Write-Host "==> Installing SFLO Codex integration" -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

    $template = Join-Path $SfloHome 'src\hooks\codex\AGENTS.md'
    if (-not (Test-Path $template)) {
        throw "SFLO Codex AGENTS template not found at $template."
    }

    $sfloMd = Join-Path $SfloHome 'sflo.md'
    $block = (Get-Content $template -Raw).Replace('{{SFLO_MD}}', $sfloMd).Trim()
    $agentsFile = Join-Path $InstallDir 'AGENTS.md'
    $existing = if (Test-Path $agentsFile) { Get-Content $agentsFile -Raw } else { '' }

    foreach ($pair in @(
        @('<!-- SFLO-AGENTS-START -->', '<!-- SFLO-AGENTS-END -->'),
        @('<!-- SFLO-CODEX-START -->', '<!-- SFLO-CODEX-END -->')
    )) {
        $pattern = '(?s)\s*' + [regex]::Escape($pair[0]) + '.*?' + [regex]::Escape($pair[1]) + '\s*'
        $existing = [regex]::Replace($existing, $pattern, "`n`n")
    }

    $trimmed = ($existing -replace '\s+$', '').Trim()
    $updated = if ([string]::IsNullOrWhiteSpace($trimmed)) {
        $block
    } else {
        "$trimmed`n`n$block"
    }
    Set-Content -Path $agentsFile -Value ($updated.TrimEnd() + "`n") -Encoding UTF8
    Write-Host "    AGENTS.md updated" -ForegroundColor Green

    if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
        Write-Host "    NOTE: codex CLI not on PATH. Install/login before triggering SFLO." -ForegroundColor Yellow
    }

    Write-Host "==> Codex setup complete." -ForegroundColor Green
}

function Install-SfloCursor {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SfloHome,
        [Parameter(Mandatory)][string]$InstallDir
    )

    Assert-SfloCheckout -SfloHome $SfloHome
    Initialize-SfloSubmodules -SfloHome $SfloHome

    $stopHook = Join-Path $SfloHome 'src\hooks\cursor\stop_hook.py'
    $ruleSrc = Join-Path $SfloHome 'src\hooks\cursor\sflo.mdc'
    if (-not (Test-Path $stopHook)) { throw "Cursor stop hook not found at $stopHook." }
    if (-not (Test-Path $ruleSrc)) { throw "Cursor rule not found at $ruleSrc." }

    Write-Host "==> Installing SFLO Cursor integration" -ForegroundColor Cyan
    $cursorDir = Join-Path $InstallDir '.cursor'
    $rulesDir = Join-Path $cursorDir 'rules'
    $hooksFile = Join-Path $cursorDir 'hooks.json'
    New-Item -ItemType Directory -Path $rulesDir -Force | Out-Null

    $python = Get-SfloPythonCommand
    Set-CursorStopHook -HooksFile $hooksFile -HookCommand "$python `"$stopHook`""
    Copy-Item -Path $ruleSrc -Destination (Join-Path $rulesDir 'sflo.mdc') -Force
    Write-Host "    .cursor hooks and rule installed" -ForegroundColor Green

    if (-not (Get-Command cursor-agent -ErrorAction SilentlyContinue)) {
        Write-Host "    NOTE: cursor-agent CLI not on PATH. Install/login before triggering SFLO." -ForegroundColor Yellow
    }
}

function Install-SfloClaudeCode {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SfloHome,
        [Parameter(Mandatory)][string]$InstallDir
    )

    Assert-SfloCheckout -SfloHome $SfloHome
    Initialize-SfloSubmodules -SfloHome $SfloHome

    $stopHook = Join-Path $SfloHome 'src\hooks\claude-code\stop_hook.py'
    if (-not (Test-Path $stopHook)) {
        throw "Claude Code stop hook not found at $stopHook."
    }

    Write-Host "==> Installing SFLO Claude Code integration" -ForegroundColor Cyan
    $settingsFile = Join-Path $InstallDir '.claude\settings.json'
    $python = Get-SfloPythonCommand
    Set-StopHook -SettingsFile $settingsFile -HookCommand "$python `"$stopHook`""
    Write-Host "    .claude/settings.json updated" -ForegroundColor Green
}

if ($DefineFunctionsOnly) {
    return
}

if (-not $Runtime) {
    Write-Error "The -Runtime parameter is required. Example: .\sflo\setup.ps1 -Runtime codex"
    exit 1
}

$ErrorActionPreference = 'Stop'

if (-not $SfloHome) {
    $SfloHome = $PSScriptRoot
}
if (-not $InstallDir) {
    $InstallDir = (Get-Location).Path
}

$SfloHome = Resolve-SfloPath -Path $SfloHome
$InstallDir = Resolve-SfloPath -Path $InstallDir

if ($Runtime -eq 'codex') {
    Install-SfloCodex -SfloHome $SfloHome -InstallDir $InstallDir
} elseif ($Runtime -eq 'cursor') {
    Install-SfloCursor -SfloHome $SfloHome -InstallDir $InstallDir
} else {
    Install-SfloClaudeCode -SfloHome $SfloHome -InstallDir $InstallDir
}

$status = Write-SetupStatus -InstallDir $InstallDir -Runtime $Runtime
Write-SetupResult -Runtime $Runtime -InstallDir $InstallDir -SfloHome $SfloHome -Status $status
