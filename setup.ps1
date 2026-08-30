<#
.SYNOPSIS
    Set up SFLO runtime integration files on Windows.

.DESCRIPTION
    Installs a self-contained SFLO skill for the selected runtime and writes
    project-local integration files into an install directory. Re-running is safe.

    The source checkout is used only as installation input. The installed
    skill remains usable after that checkout is moved or deleted. This script
    does not clone or update SFLO from git, and it does not install OpenClaw
    integration. OpenClaw setup remains in setup.sh.

.PARAMETER Runtime
    REQUIRED. One of: codex, cursor, claude-code.

.PARAMETER InstallDir
    Project directory for pipeline, hook settings, and setup status.
    Runtime skills are installed into their conventional user-level roots.
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
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return 'py -3'
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return 'python'
    }
    return 'python'
}

function Get-CursorSkillsRoot {
    $cursorHome = if ($env:CURSOR_HOME) { $env:CURSOR_HOME } else { Join-Path $HOME '.cursor' }
    return (Join-Path $cursorHome 'skills')
}

function Get-CodexSkillsRoot {
    $agentsHome = if ($env:AGENTS_HOME) { $env:AGENTS_HOME } else { Join-Path $HOME '.agents' }
    return (Join-Path $agentsHome 'skills')
}

function Get-ClaudeSkillsRoot {
    $claudeHome = if ($env:CLAUDE_HOME) {
        $env:CLAUDE_HOME
    } elseif ($env:CLAUDE_CONFIG_DIR) {
        $env:CLAUDE_CONFIG_DIR
    } else {
        Join-Path $HOME '.claude'
    }
    return (Join-Path $claudeHome 'skills')
}

function Get-SfloSkillDestination {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Runtime)

    if ($Runtime -eq 'codex') {
        return (Join-Path (Get-CodexSkillsRoot) 'sflo')
    }
    if ($Runtime -eq 'cursor') {
        return (Join-Path (Get-CursorSkillsRoot) 'sflo')
    }
    if ($Runtime -eq 'claude-code') {
        return (Join-Path (Get-ClaudeSkillsRoot) 'sflo')
    }
    throw "Unsupported Windows runtime: $Runtime"
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

function Write-Utf8NoBom {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Content
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Write-JsonObject {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Object
    )

    $dir = Split-Path -Parent $Path
    $writeDir = if ($dir) { $dir } else { (Get-Location).Path }
    New-Item -ItemType Directory -Path $writeDir -Force | Out-Null
    $tempPath = Join-Path $writeDir ((Split-Path -Leaf $Path) + '.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $content = ($Object | ConvertTo-Json -Depth 20) + "`n"
    try {
        Write-Utf8NoBom -Path $tempPath -Content $content
        if (Test-Path $Path -PathType Leaf) {
            try {
                [System.IO.File]::Replace($tempPath, $Path, $null)
            } catch [System.PlatformNotSupportedException] {
                Move-Item -Path $tempPath -Destination $Path -Force
            }
        } else {
            Move-Item -Path $tempPath -Destination $Path
        }
    } finally {
        Remove-Item -Path $tempPath -Force -ErrorAction SilentlyContinue
    }
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
        Write-Utf8NoBom -Path $tempPath -Content ($Status + "`n")
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
    $sfloHookPattern = 'src[\\/]hooks[\\/]claude-code[\\/]stop_hook\.py'
    $existingStop = @()
    foreach ($propertyName in @('Stop', 'stop')) {
        if ($hooks.PSObject.Properties[$propertyName]) {
            $existingStop += @($hooks.$propertyName) | Where-Object {
                -not (([string]$_.command) -match $sfloHookPattern)
            }
        }
    }
    $sfloHook = [pscustomobject]@{
        type = 'command'
        command = $HookCommand
    }
    $hooks | Add-Member -Force -MemberType NoteProperty -Name 'Stop' -Value (@($sfloHook) + @($existingStop))
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

    $sfloHookPattern = 'src[\\/]hooks[\\/]cursor[\\/]stop_hook\.py'
    $existingStop = @()
    if ($hooks.PSObject.Properties['stop']) {
        $existingStop = @($hooks.stop) | Where-Object {
            -not (([string]$_.command) -match $sfloHookPattern)
        }
    }

    $sfloHook = [pscustomobject]@{
        command = $HookCommand
        loop_limit = $null
    }
    $hooks | Add-Member -Force -MemberType NoteProperty -Name 'stop' -Value (@($sfloHook) + @($existingStop))
    Write-JsonObject -Path $HooksFile -Object $data
}

function Install-SfloSelfContainedSkill {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SfloHome,
        [Parameter(Mandatory)][string]$Runtime,
        [Parameter(Mandatory)][string]$DestinationDir
    )

    $installer = Join-Path $SfloHome 'src\install_skill.py'
    if (-not (Test-Path $installer -PathType Leaf)) {
        throw "SFLO skill installer not found at $installer."
    }

    $arguments = @(
        $installer,
        '--source', $SfloHome,
        '--runtime', $Runtime,
        '--destination', $DestinationDir
    )
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @arguments
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python @arguments
    } else {
        throw 'Python 3 is required to install the SFLO skill.'
    }
    if ($LASTEXITCODE -ne 0) {
        throw "SFLO skill installer failed for $Runtime (exit $LASTEXITCODE)."
    }

    $installedRunner = Join-Path $DestinationDir 'src\runner.py'
    if (-not (Test-Path $installedRunner -PathType Leaf)) {
        throw "SFLO installation did not produce $installedRunner."
    }
}

function Test-SfloOwnedSkillDirectory {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    $manifestPath = Join-Path $Path '.sflo-install.json'
    if (Test-Path $manifestPath -PathType Leaf) {
        try {
            $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
            return ($manifest.product -eq 'sflo')
        } catch {
            # A corrupt marker is not proof of ownership. Preserve the directory.
            return $false
        }
    }
    if (Test-Path (Join-Path $Path '.sflo-owned')) {
        return $true
    }
    $skillFile = Join-Path $Path 'SKILL.md'
    if (Test-Path $skillFile) {
        return ((Get-Content $skillFile -Raw) -match 'SFLO Factory Triggering')
    }
    return $false
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
        Write-Utf8NoBom -Path $AgentsFile -Content ($trimmed + "`n")
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

    $skillDst = Get-SfloSkillDestination -Runtime 'codex'
    Install-SfloSelfContainedSkill -SfloHome $SfloHome -Runtime 'codex' -DestinationDir $skillDst
    Remove-SfloOwnedSkillDirectory -Path (Join-Path (Get-CodexSkillsRoot) 'sflo-factory-triggering')

    # Remove prior project-local SFLO wrappers only after the global,
    # self-contained installation has succeeded. Never remove unowned skills.
    $oldProjectSkill = Join-Path $InstallDir '.agents\skills\sflo'
    if ((Resolve-SfloPath -Path $oldProjectSkill) -ne (Resolve-SfloPath -Path $skillDst)) {
        Remove-SfloOwnedSkillDirectory -Path $oldProjectSkill
    }
    Remove-SfloOwnedSkillDirectory -Path (Join-Path $InstallDir '.agents\skills\sflo-factory-triggering')
    Remove-SfloOldAgentsBlock -AgentsFile (Join-Path $InstallDir 'AGENTS.md')
    Write-Host "    Codex self-contained sflo skill installed at $skillDst" -ForegroundColor Green

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

    Write-Host "==> Installing SFLO Cursor integration" -ForegroundColor Cyan
    $cursorDir = Join-Path $InstallDir '.cursor'
    $hooksFile = Join-Path $cursorDir 'hooks.json'
    $rulesDir = Join-Path $cursorDir 'rules'
    $cursorHome = if ($env:CURSOR_HOME) { $env:CURSOR_HOME } else { Join-Path $HOME '.cursor' }
    $compatRoot = Join-Path $cursorHome 'skills-cursor'

    $skillDst = Get-SfloSkillDestination -Runtime 'cursor'
    Install-SfloSelfContainedSkill -SfloHome $SfloHome -Runtime 'cursor' -DestinationDir $skillDst
    $installedSfloHome = $skillDst
    $stopHook = Join-Path $installedSfloHome 'src\hooks\cursor\stop_hook.py'
    if (-not (Test-Path $stopHook -PathType Leaf)) { throw "Installed Cursor stop hook not found at $stopHook." }
    $python = Get-SfloPythonCommand
    Set-CursorStopHook -HooksFile $hooksFile -HookCommand "$python $(ConvertTo-PowerShellSingleQuoted -Value $stopHook)"
    $oldSkillDst = Join-Path (Get-CursorSkillsRoot) 'sflo-factory-triggering'
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
    Write-Host "    .cursor hook and self-contained global sflo skill installed" -ForegroundColor Green

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

    Write-Host "==> Installing SFLO Claude Code integration" -ForegroundColor Cyan
    $skillDst = Get-SfloSkillDestination -Runtime 'claude-code'
    Install-SfloSelfContainedSkill -SfloHome $SfloHome -Runtime 'claude-code' -DestinationDir $skillDst
    $installedSfloHome = $skillDst
    $stopHook = Join-Path $installedSfloHome 'src\hooks\claude-code\stop_hook.py'
    if (-not (Test-Path $stopHook -PathType Leaf)) {
        throw "Installed Claude Code stop hook not found at $stopHook."
    }
    $settingsFile = Join-Path $InstallDir '.claude\settings.json'
    $python = Get-SfloPythonCommand
    Set-StopHook -SettingsFile $settingsFile -HookCommand "$python $(ConvertTo-PowerShellSingleQuoted -Value $stopHook)"
    Write-Host "    .claude/settings.json and self-contained global sflo skill installed" -ForegroundColor Green
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
    $installedSfloHome = Get-SfloSkillDestination -Runtime $Runtime
    Write-SetupResult -Runtime $Runtime -InstallDir $InstallDir -SfloHome $installedSfloHome -Status $status -Ok $true
} catch {
    $message = $_.Exception.Message
    try { Write-SetupStatus -InstallDir $InstallDir -Status 'failed' | Out-Null } catch {}
    Write-SetupResult -Runtime $Runtime -InstallDir $InstallDir -SfloHome $SfloHome -Status 'failed' -Ok $false -ErrorMessage $message
    [Console]::Error.WriteLine("ERROR: $message")
    exit 1
}
