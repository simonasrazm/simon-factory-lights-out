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
    Directory for runtime files such as .agents, .cursor, or .claude.
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

function Assert-SfloVendoredSkills {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$SfloHome)

    $requiredSkill = Join-Path $SfloHome 'vendor\mattpocock-skills\skills\engineering\tdd\SKILL.md'
    if (-not (Test-Path $requiredSkill -PathType Leaf)) {
        throw "required vendored Matt skill is missing: $requiredSkill"
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

function Get-CursorSkillsRoot {
    $cursorHome = if ($env:CURSOR_HOME) { $env:CURSOR_HOME } else { Join-Path $HOME '.cursor' }
    return (Join-Path $cursorHome 'skills')
}

function ConvertTo-ShSingleQuoted {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Value)

    return "'" + ($Value -replace "'", "'\''") + "'"
}

function ConvertTo-PowerShellSingleQuoted {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Value)

    return "'" + $Value.Replace("'", "''") + "'"
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
        [Parameter(Mandatory)][string]$Status
    )

    $sfloDir = Join-Path $InstallDir '.sflo'
    New-Item -ItemType Directory -Path $sfloDir -Force | Out-Null
    $statusPath = Join-Path $sfloDir '.setup-status'
    $tempPath = Join-Path $sfloDir ('.setup-status.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Set-Content -Path $tempPath -Value $Status -Encoding UTF8
        Move-Item -Path $tempPath -Destination $statusPath -Force
    } finally {
        Remove-Item -Path $tempPath -Force -ErrorAction SilentlyContinue
    }
    return $Status
}

function Write-SetupResult {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Runtime,
        [Parameter(Mandatory)][string]$InstallDir,
        [Parameter(Mandatory)][string]$SfloHome,
        [Parameter(Mandatory)][string]$Status,
        [Parameter(Mandatory)][bool]$Ok,
        [string]$ErrorMessage
    )

    $result = [ordered]@{
        ok = $Ok
        runtime = $Runtime
        install_dir = $InstallDir
        sflo_path = $SfloHome
        status = $Status
    }
    if ($ErrorMessage) { $result['error'] = $ErrorMessage }
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

function Write-RenderedTemplate {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SourceFile,
        [Parameter(Mandatory)][string]$DestinationFile,
        [Parameter(Mandatory)][string]$SfloHome
    )

    if (-not (Test-Path $SourceFile)) {
        throw "SFLO template not found at $SourceFile."
    }

    $dir = Split-Path -Parent $DestinationFile
    if ($dir) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }

    $runner = Join-Path $SfloHome 'src\runner.py'
    $scaffold = Join-Path $SfloHome 'src\scaffold.py'
    $cursorStopHook = Join-Path $SfloHome 'src\hooks\cursor\stop_hook.py'
    $content = (Get-Content $SourceFile -Raw).Replace('{{SFLO_PATH}}', $SfloHome)
    $content = $content.Replace('{{SFLO_RUNNER_SH}}', (ConvertTo-ShSingleQuoted -Value $runner))
    $content = $content.Replace('{{SFLO_SCAFFOLD_SH}}', (ConvertTo-ShSingleQuoted -Value $scaffold))
    $content = $content.Replace('{{SFLO_CURSOR_STOP_HOOK_SH}}', (ConvertTo-ShSingleQuoted -Value $cursorStopHook))
    Set-Content -Path $DestinationFile -Value $content -Encoding UTF8
}

function Install-SfloSkillDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SourceDir,
        [Parameter(Mandatory)][string]$DestinationDir,
        [Parameter(Mandatory)][string]$SfloHome
    )

    if (-not (Test-Path $SourceDir)) {
        throw "SFLO skill source not found at $SourceDir."
    }

    if (Test-Path $DestinationDir) {
        Remove-Item -Path $DestinationDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $DestinationDir -Force | Out-Null
    Copy-Item -Path (Join-Path $SourceDir '*') -Destination $DestinationDir -Recurse -Force

    $skillFile = Join-Path $DestinationDir 'SKILL.md'
    if (Test-Path $skillFile) {
        Write-RenderedTemplate -SourceFile $skillFile -DestinationFile $skillFile -SfloHome $SfloHome
    }
}

function Test-SfloOwnedSkillDirectory {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    if (Test-Path (Join-Path $Path '.sflo-owned')) {
        return $true
    }
    $skillFile = Join-Path $Path 'SKILL.md'
    if (Test-Path $skillFile) {
        return ((Get-Content $skillFile -Raw) -match 'SFLO Factory Triggering')
    }
    return $false
}

function Install-SfloOwnedSkillDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SourceDir,
        [Parameter(Mandatory)][string]$DestinationDir,
        [Parameter(Mandatory)][string]$SfloHome
    )

    if (Test-Path $DestinationDir) {
        if (-not (Test-SfloOwnedSkillDirectory -Path $DestinationDir)) {
            throw "Skill already exists at $DestinationDir and is not SFLO-owned."
        }
        Remove-Item -Path $DestinationDir -Recurse -Force
    }
    Install-SfloSkillDirectory -SourceDir $SourceDir -DestinationDir $DestinationDir -SfloHome $SfloHome
    Set-Content -Path (Join-Path $DestinationDir '.sflo-owned') -Value 'sflo' -Encoding UTF8
}

function Remove-SfloOwnedSkillDirectory {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }
    if (Test-SfloOwnedSkillDirectory -Path $Path) {
        Remove-Item -Path $Path -Recurse -Force
    } else {
        Write-Host "    Leaving non-SFLO skill directory untouched: $Path" -ForegroundColor Yellow
    }
}

function Install-SfloRuntimePipeline {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SourceFile,
        [Parameter(Mandatory)][string]$InstallDir,
        [Parameter(Mandatory)][string]$Label
    )

    if (-not (Test-Path $SourceFile)) {
        throw "$Label pipeline source not found at $SourceFile."
    }

    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    $destination = Join-Path $InstallDir 'pipeline.yaml'
    $markerDirectory = Join-Path $InstallDir '.sflo'
    $marker = Join-Path $markerDirectory 'pipeline.yaml.managed'
    $proposed = Join-Path $InstallDir 'pipeline.yaml.sflo-default'
    New-Item -ItemType Directory -Path $markerDirectory -Force | Out-Null
    $incoming = Get-Content -Path $SourceFile -Raw

    if (-not (Test-Path $destination)) {
        Copy-Item -Path $SourceFile -Destination $destination -Force
        Copy-Item -Path $SourceFile -Destination $marker -Force
        Remove-Item -Path $proposed -Force -ErrorAction SilentlyContinue
        Write-Host "    $Label pipeline installed at $destination" -ForegroundColor Green
    } elseif ((Get-Content -Path $destination -Raw) -eq $incoming) {
        Copy-Item -Path $SourceFile -Destination $marker -Force
        Remove-Item -Path $proposed -Force -ErrorAction SilentlyContinue
        Write-Host "    $Label pipeline already current at $destination" -ForegroundColor Green
    } elseif ((Test-Path $marker) -and
              ((Get-Content -Path $destination -Raw) -eq (Get-Content -Path $marker -Raw))) {
        Copy-Item -Path $SourceFile -Destination $destination -Force
        Copy-Item -Path $SourceFile -Destination $marker -Force
        Remove-Item -Path $proposed -Force -ErrorAction SilentlyContinue
        Write-Host "    $Label managed pipeline updated at $destination" -ForegroundColor Green
    } else {
        Copy-Item -Path $SourceFile -Destination $proposed -Force
        Write-Host "    Existing project pipeline preserved at $destination; new SFLO defaults written to $proposed" -ForegroundColor Yellow
    }
}

function Remove-SfloOldAgentsBlock {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$AgentsFile)

    if (-not (Test-Path $AgentsFile)) {
        return
    }

    $existing = Get-Content $AgentsFile -Raw
    foreach ($pair in @(
        @('<!-- SFLO-AGENTS-START -->', '<!-- SFLO-AGENTS-END -->'),
        @('<!-- SFLO-CODEX-START -->', '<!-- SFLO-CODEX-END -->')
    )) {
        $pattern = '(?s)\s*' + [regex]::Escape($pair[0]) + '.*?' + [regex]::Escape($pair[1]) + '\s*'
        $existing = [regex]::Replace($existing, $pattern, "`n`n")
    }

    $trimmed = ($existing -replace '\s+$', '').Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed)) {
        Remove-Item -Path $AgentsFile -Force
    } else {
        Set-Content -Path $AgentsFile -Value ($trimmed + "`n") -Encoding UTF8
    }
}

function Install-SfloCodex {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SfloHome,
        [Parameter(Mandatory)][string]$InstallDir
    )

    Assert-SfloCheckout -SfloHome $SfloHome

    Write-Host "==> Installing SFLO Codex integration" -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

    $skillSrc = Join-Path $SfloHome 'src\hooks\codex\skills\sflo'
    $skillDst = Join-Path $InstallDir '.agents\skills\sflo'
    Install-SfloOwnedSkillDirectory -SourceDir $skillSrc -DestinationDir $skillDst -SfloHome $SfloHome
    Remove-SfloOwnedSkillDirectory -Path (Join-Path $InstallDir '.agents\skills\sflo-factory-triggering')
    Remove-SfloOldAgentsBlock -AgentsFile (Join-Path $InstallDir 'AGENTS.md')
    Write-Host "    Codex sflo skill installed" -ForegroundColor Green

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

    $stopHook = Join-Path $SfloHome 'src\hooks\cursor\stop_hook.py'
    $skillSrc = Join-Path $SfloHome 'src\hooks\cursor\skills\sflo'
    if (-not (Test-Path $stopHook)) { throw "Cursor stop hook not found at $stopHook." }
    if (-not (Test-Path $skillSrc)) { throw "Cursor factory-triggering skill not found at $skillSrc." }

    Write-Host "==> Installing SFLO Cursor integration" -ForegroundColor Cyan
    $cursorDir = Join-Path $InstallDir '.cursor'
    $hooksFile = Join-Path $cursorDir 'hooks.json'
    $rulesDir = Join-Path $cursorDir 'rules'
    $skillsRoot = Get-CursorSkillsRoot
    $cursorHome = if ($env:CURSOR_HOME) { $env:CURSOR_HOME } else { Join-Path $HOME '.cursor' }
    $compatRoot = Join-Path $cursorHome 'skills-cursor'

    $python = Get-SfloPythonCommand
    Set-CursorStopHook -HooksFile $hooksFile -HookCommand "$python $(ConvertTo-PowerShellSingleQuoted -Value $stopHook)"
    $skillDst = Join-Path $skillsRoot 'sflo'
    Install-SfloOwnedSkillDirectory -SourceDir $skillSrc -DestinationDir $skillDst -SfloHome $SfloHome
    $oldSkillDst = Join-Path $skillsRoot 'sflo-factory-triggering'
    Remove-SfloOwnedSkillDirectory -Path $oldSkillDst
    Remove-SfloOwnedSkillDirectory -Path (Join-Path $compatRoot 'sflo')
    Remove-SfloOwnedSkillDirectory -Path (Join-Path $compatRoot 'sflo-factory-triggering')
    Install-SfloRuntimePipeline `
        -SourceFile (Join-Path $SfloHome 'pipeline-cursor.yaml') `
        -InstallDir $InstallDir `
        -Label 'Cursor'
    foreach ($staleRule in @(
        (Join-Path $rulesDir 'sflo.mdc'),
        (Join-Path $rulesDir 'sflo-factory-triggering.mdc')
    )) {
        if (Test-Path $staleRule) {
            Remove-Item -Path $staleRule -Force
        }
    }
    if ((Test-Path $rulesDir) -and -not (Get-ChildItem -Path $rulesDir -Force)) {
        Remove-Item -Path $rulesDir -Force
    }
    Write-Host "    .cursor hook and global factory-triggering skill installed" -ForegroundColor Green

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

    $stopHook = Join-Path $SfloHome 'src\hooks\claude-code\stop_hook.py'
    if (-not (Test-Path $stopHook)) {
        throw "Claude Code stop hook not found at $stopHook."
    }

    Write-Host "==> Installing SFLO Claude Code integration" -ForegroundColor Cyan
    $settingsFile = Join-Path $InstallDir '.claude\settings.json'
    $python = Get-SfloPythonCommand
    Set-StopHook -SettingsFile $settingsFile -HookCommand "$python $(ConvertTo-PowerShellSingleQuoted -Value $stopHook)"
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

try {
    Write-SetupStatus -InstallDir $InstallDir -Status 'failed' | Out-Null
    Assert-SfloCheckout -SfloHome $SfloHome
    Assert-SfloVendoredSkills -SfloHome $SfloHome

    if ($Runtime -eq 'codex') {
        Install-SfloCodex -SfloHome $SfloHome -InstallDir $InstallDir
    } elseif ($Runtime -eq 'cursor') {
        Install-SfloCursor -SfloHome $SfloHome -InstallDir $InstallDir
    } else {
        Install-SfloClaudeCode -SfloHome $SfloHome -InstallDir $InstallDir
    }

    $status = Write-SetupStatus -InstallDir $InstallDir -Status 'ready'
    Write-SetupResult -Runtime $Runtime -InstallDir $InstallDir -SfloHome $SfloHome -Status $status -Ok $true
} catch {
    $message = $_.Exception.Message
    try { Write-SetupStatus -InstallDir $InstallDir -Status 'failed' | Out-Null } catch {}
    Write-SetupResult -Runtime $Runtime -InstallDir $InstallDir -SfloHome $SfloHome -Status 'failed' -Ok $false -ErrorMessage $message
    [Console]::Error.WriteLine("ERROR: $message")
    exit 1
}
