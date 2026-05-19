<#
.SYNOPSIS
    One-time SFLO setup on Windows with subscription auth (no plaintext token on disk).

.DESCRIPTION
    The Windows counterpart of setup.sh. Installs
    Microsoft.PowerShell.SecretManagement + Microsoft.PowerShell.SecretStore,
    configures the SecretStore for unattended access (DPAPI-encrypted at rest,
    no vault password), registers an SfloVault, and installs an Invoke-SFLO
    function into the user's PowerShell profile.

    Handles BOTH PowerShell 7 (pwsh) and Windows PowerShell 5.1
    (powershell.exe): modules and profiles for each shell go into their
    respective paths, but the underlying SecretStore is shared at the user
    level - one secret works from either shell.

    The Claude OAuth token never touches disk in plaintext. It lives encrypted
    in the SecretStore and is materialized in the process environment only for
    the duration of the spawned python runner.

    This script sets up the EMPTY vault only. Storing the actual Claude OAuth
    token is a deliberate manual step you run yourself afterwards - an
    installer should not handle a live auth token. See README.md ("Windows")
    for the one Set-Secret command.

    Re-running is safe (idempotent).

.PARAMETER Runtime
    REQUIRED. The agent runtime SFLO runs under: 'cursor' or 'claude-code'.
    There is no auto-detection -- you state the runtime explicitly.

.PARAMETER SfloHome
    Path to the SFLO checkout (the folder containing src\runner.py). Defaults
    to the directory this script lives in, which is correct when you run
    .\setup.ps1 from the repo root.

.PARAMETER SkipWindowsPowerShell
    Skip installing into Windows PowerShell 5.1 even if powershell.exe exists.

.PARAMETER DefineFunctionsOnly
    Internal/testing switch. When set, the script ONLY defines its functions
    and returns - it installs nothing and touches no state. Used by
    setup.Tests.ps1 so the installer's logic can be unit-tested with mocked
    cmdlets. Not for normal use.

.EXAMPLE
    .\setup.ps1 -Runtime cursor

    Install the SFLO Cursor integration (the stop hook + the project rule).

.EXAMPLE
    .\setup.ps1 -Runtime claude-code

    Run from the SFLO repo root. Then store the token (one-time, manual):
        claude setup-token
        Set-Secret -Name SFLO_CLAUDE -Vault SfloVault
        Invoke-SFLO build a click counter
#>
[CmdletBinding()]
param(
    [ValidateSet('cursor', 'claude-code')]
    [string]$Runtime,
    [string]$SfloHome,
    [switch]$SkipWindowsPowerShell,
    [switch]$DefineFunctionsOnly
)

# Pinned, known-good versions from the PowerShell Gallery. Unpinned
# Install-Module installs whatever PSGallery currently serves, which makes
# the install non-reproducible and vulnerable to a bad upstream release.
# Bump these deliberately after testing a newer release.
$script:SecretManagementVersion = '1.1.2'
$script:SecretStoreVersion      = '1.0.6'

$script:BeginMarker = '# === SFLO BEGIN ==='
$script:EndMarker   = '# === SFLO END ==='


function Get-InvokeSfloProfileBlock {
    <#
    .SYNOPSIS
        Builds the marker-delimited profile block that defines Invoke-SFLO.

    .DESCRIPTION
        Returned text is written verbatim into the user's $PROFILE. The block
        is delimited by $BeginMarker / $EndMarker so re-runs replace it
        in-place rather than appending duplicates.

        Escaping note (see changes.md section 6): this here-string is
        double-quoted, so a backtick-escaped `$X emits the LITERAL text $X
        into the profile (a variable reference that expands when Invoke-SFLO
        runs), whereas a bare $X is expanded NOW at build time. $runner and
        the $env: references must survive as literals; $SfloHome must be
        baked in as its build-time value.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SfloHome
    )

    return @"
$script:BeginMarker
# SFLO subscription auth via Microsoft.PowerShell.SecretStore. The OAuth token
# is DPAPI-encrypted at rest in %LOCALAPPDATA%\Microsoft\PowerShell\secretmanagement\
# (NOT in OneDrive), and only materialized in the process environment for the
# duration of the spawned python runner.
#
# Setup (one-time, run these yourself - the installer does NOT store the token):
#   1. claude setup-token                              # generates a long-lived OAuth token
#   2. Set-Secret -Name SFLO_CLAUDE -Vault SfloVault   # paste the token (input hidden)
#
# Usage:
#   Invoke-SFLO build a click counter

`$SFLO_HOME = '$SfloHome'

function Invoke-SFLO {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0, ValueFromRemainingArguments)][string[]]`$Description,
        [string]`$SfloHome = `$script:SFLO_HOME
    )

    # Defensive: explicitly load SecretManagement so the function works in
    # shells where module autoload is disabled, slow, or misconfigured
    # (e.g. a fresh Windows PowerShell 5.1).
    if (-not (Get-Command Get-Secret -ErrorAction SilentlyContinue)) {
        Import-Module Microsoft.PowerShell.SecretManagement -ErrorAction Stop
        Import-Module Microsoft.PowerShell.SecretStore       -ErrorAction Stop
    }

    `$runner = Join-Path `$SfloHome 'src\runner.py'
    if (-not (Test-Path `$runner)) {
        throw "SFLO runner not found at `$runner. Edit the SFLO_HOME path at the top of your PowerShell profile, or pass -SfloHome."
    }

    `$secret = Get-Secret -Name SFLO_CLAUDE -Vault SfloVault -AsPlainText -ErrorAction SilentlyContinue
    if (-not `$secret) {
        throw "SFLO_CLAUDE secret not found in SfloVault. Run: claude setup-token  then  Set-Secret -Name SFLO_CLAUDE -Vault SfloVault"
    }

    `$env:CLAUDE_CODE_OAUTH_TOKEN = `$secret
    try {
        # 2>&1 merges Python's stderr into stdout so tracebacks stay visible
        # in captured output (background tasks, redirection, CI logs).
        #
        # We deliberately do NOT use the merge-ALL-streams redirect, which
        # would also pull in the verbose / debug / information streams. If
        # the python runner ever serialized its process environment into a
        # diagnostic message on one of those streams, the merged capture
        # would contain `$env:CLAUDE_CODE_OAUTH_TOKEN. Restricting to 2>&1
        # keeps the traceback benefit while narrowing what can leak.
        # (Residual risk: the runner could still write the env to its own
        # stdout/stderr - that must be fixed runner-side; it is outside this
        # PowerShell layer's control.)
        (`$Description -join ' ') | python `$runner 2>&1
    } finally {
        Remove-Item Env:\CLAUDE_CODE_OAUTH_TOKEN -ErrorAction SilentlyContinue
    }
}
$script:EndMarker
"@
}


function Test-SecretStoreIsFresh {
    <#
    .SYNOPSIS
        Returns $true ONLY when the local SecretStore is genuinely fresh and
        therefore safe to Reset-SecretStore.

    .DESCRIPTION
        HIGH-severity safety guard. Reset-SecretStore DELETES EVERY SECRET in
        the user's default SecretStore - including unrelated secrets the user
        may already keep there for other tools. The original setup script
        called it with -Force -Confirm:$false, which also suppressed the lone
        safety prompt. That is a destructive operation against data this
        installer does not own.

        FAIL-CLOSED principle: this function returns $true ONLY when it can
        POSITIVELY confirm the store is BOTH unconfigured AND empty. Any
        uncertainty - an unexpected error, a store that cannot be enumerated
        (e.g. password-protected and currently LOCKED) - returns $false.
        Never wipe on doubt.

        "Fresh" means BOTH of:
          * Get-SecretStoreConfiguration reports the store has never been
            configured on this account (the cmdlet throws the specific
            "not configured" error). Any OTHER error -> not fresh.
          * Every registered vault enumerates successfully AND reports zero
            secrets. If enumeration fails for any vault -> not fresh.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param()

    # 1. Already configured? A configured store may belong to the user for
    #    unrelated reasons - never reset it, even if it currently looks empty.
    #    Get-SecretStoreConfiguration throws when (and intends to signal that)
    #    the store has never been configured. But a throw can ALSO mean
    #    something else went wrong - so we must not treat every throw as
    #    "fresh". Only the genuine not-configured error qualifies.
    try {
        $cfg = Get-SecretStoreConfiguration -ErrorAction Stop
        # No throw -> the store IS configured. Not fresh.
        if ($null -ne $cfg) {
            return $false
        }
        # Defensive: cmdlet returned nothing without throwing. We cannot
        # positively confirm "unconfigured", so fail closed.
        return $false
    } catch {
        # Distinguish the expected "never configured" signal from any other
        # failure. SecretStore raises this with a stable identifier; we also
        # accept the localized-message substring as a fallback.
        $isNotConfigured =
            ($_.FullyQualifiedErrorId -match 'SecretStoreNotConfigured') -or
            ($_.Exception.Message -match 'not been configured')
        if (-not $isNotConfigured) {
            # Some other error (locked store, module fault, access denied,
            # ...). We cannot confirm the store is unconfigured -> not fresh.
            return $false
        }
        # Genuine not-configured case - fall through to the emptiness check.
    }

    # 2. Already holds secrets in ANY registered vault? Belt-and-suspenders:
    #    if a store somehow has secrets without a readable configuration,
    #    still refuse to wipe it.
    #
    #    Get-SecretInfo uses -ErrorAction Stop so that an enumeration failure
    #    (e.g. a password-protected store that is currently LOCKED) is THROWN
    #    and CAUGHT here - never silently swallowed into an empty collection.
    #    A store we cannot enumerate is NOT provably empty, so we fail closed.
    try {
        $vaults = @(Get-SecretVault -ErrorAction Stop)
        foreach ($vault in $vaults) {
            $secrets = @(Get-SecretInfo -Vault $vault.Name -ErrorAction Stop)
            if ($secrets.Count -gt 0) {
                return $false
            }
        }
    } catch {
        # Could not prove the store is empty (locked / unreadable / errored).
        # Fail closed: treat as NOT fresh, do not reset.
        return $false
    }

    # Positively confirmed: unconfigured AND every vault enumerated empty.
    return $true
}


function Invoke-SecretStoreSetup {
    <#
    .SYNOPSIS
        Configures the SecretStore for unattended access - but only when it
        is safe to do so.

    .DESCRIPTION
        Calls Test-SecretStoreIsFresh. If the store is fresh, runs
        Reset-SecretStore once to put it into unattended (Authentication=None)
        mode. If the store is already in use, ABORTS with an actionable
        message instead of wiping the user's existing secrets.
    #>
    [CmdletBinding()]
    param()

    Write-Host "==> Configuring SecretStore for unattended access" -ForegroundColor Cyan

    if (-not (Test-SecretStoreIsFresh)) {
        throw @'
SecretStore is already configured and/or already holds secrets.

This installer will NOT reset it, because Reset-SecretStore deletes EVERY
secret in your default SecretStore - including secrets unrelated to SFLO.

If your existing SecretStore is ALREADY set up for unattended access
(Authentication=None), you are done: just register the vault and store the
token. Check with:

    Get-SecretStoreConfiguration

If it requires a password and you want SFLO to run unattended, decide
deliberately - either:
  * keep the password and run SFLO interactively, or
  * back up your existing secrets, then reset the store yourself:
        Get-SecretInfo            # review what you have first
        Reset-SecretStore         # you will be prompted to confirm

Then re-run .\setup.ps1.
'@
    }

    # Store is genuinely fresh - safe to configure for unattended access.
    # Security here is delegated to the Windows login + DPAPI key binding.
    Reset-SecretStore -Authentication None -Interaction None -Force -Confirm:$false
    Write-Host "    SecretStore configured for unattended access (fresh store)" -ForegroundColor Green
}


function Install-IntoShell {
    <#
    .SYNOPSIS
        Writes (or idempotently updates) the Invoke-SFLO block into one
        shell's $PROFILE.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ShellName,
        [Parameter(Mandatory)][string]$ProfilePath,
        [Parameter(Mandatory)][string]$Block
    )

    Write-Host ""
    Write-Host "==> Installing into $ShellName" -ForegroundColor Cyan
    Write-Host "    Profile: $ProfilePath"

    $profileDir = Split-Path -Parent $ProfilePath
    if (-not (Test-Path $profileDir)) {
        New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
    }

    if (Test-Path $ProfilePath) {
        $existing = Get-Content $ProfilePath -Raw
        if ($null -eq $existing) { $existing = '' }
        $updated = Update-SfloProfileContent -ExistingContent $existing -Block $Block
        Set-Content -Path $ProfilePath -Value $updated -Encoding UTF8
    } else {
        Set-Content -Path $ProfilePath -Value $Block -Encoding UTF8
    }
}


function Update-SfloProfileContent {
    <#
    .SYNOPSIS
        Pure string transform: takes a profile's existing content and returns
        new content containing EXACTLY ONE SFLO block. No filesystem I/O - so
        it is directly unit-testable.

    .DESCRIPTION
        Idempotent and robust for ANY prior profile state:

        * 0 SFLO blocks  -> the fresh block is appended.
        * 1 SFLO block   -> the old block is removed, the fresh one appended
                           (net: replaced; still exactly one).
        * N SFLO blocks  -> ALL old blocks are removed (a global match, not
                           just the first), the fresh one appended. A
                           doubly-appended or hand-edited profile collapses
                           back to a single block.

        Removal is done with a GREEDY-SAFE strategy: the begin/end markers are
        matched as whole anchored lines, and the span between each BEGIN and
        the NEXT END after it is removed, iterating left to right. This
        cannot be fooled into truncating early.

        MALFORMED MARKERS (unbalanced - a BEGIN with no following END, or an
        orphaned END with no preceding BEGIN):
        A regex span-removal cannot safely decide what to delete when the
        markers do not pair up, and guessing risks eating the user's own
        content. The deliberate, documented choice is FAIL-SAFE: do NOT
        delete the user's malformed/stray marker text - preserve it verbatim,
        emit a warning, and ensure exactly one fresh block is present at the
        end. The user is left with a visible stale fragment they can remove
        by hand - which is strictly safer than silently corrupting or
        truncating their profile.

        IDEMPOTENCY in the fail-safe path: appending a fresh block adds its
        own BEGIN+END, so a stray-END profile (0 BEGIN / 1 END) becomes
        1 BEGIN / 2 END - still unbalanced. A naive re-run would append
        AGAIN and bloat the profile without bound. To prevent that, the
        fail-safe path checks whether the content ALREADY ends with exactly
        the fresh $Block (a prior fail-safe append). If so it strips that
        trailing block before re-appending - a plain string-suffix
        comparison, not a regex, so it is unambiguous, can never span into
        or consume the user's own text, and is not confused by a stray
        marker elsewhere in the profile. Result: update(update(x)) ==
        update(x) holds for unbalanced input too.

        The user's non-SFLO content is ALWAYS preserved in every path.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$ExistingContent,
        [Parameter(Mandatory)][string]$Block
    )

    $beginEsc = [regex]::Escape($script:BeginMarker)
    $endEsc   = [regex]::Escape($script:EndMarker)

    # Regex fragments are built by concatenating SINGLE-quoted literals with
    # the (already regex-escaped) marker text. Single quotes mean PowerShell
    # performs no string interpolation, so the regex anchors '^' / '$' and
    # the group '(\r?\n)?' reach the regex engine verbatim - a double-quoted
    # pattern would let PowerShell mis-read '$(' as a subexpression.
    #
    # Multiline / Singleline are passed as explicit RegexOptions to the
    # [regex] calls rather than as inline '(?m)' / '(?s)' flags, so the
    # fragments stay pure pattern text and compose cleanly.
    $lineStart = '^[ \t]*'      # allow leading horizontal whitespace on the line
    $lineEnd   = '[ \t]*\r?$'   # trailing horizontal whitespace + optional CR,
                                # then EOL. The '\r?' makes the marker-line
                                # match CRLF-robust: with Multiline, '$' anchors
                                # before '\n', so a CRLF line leaves a stray
                                # '\r' that '[ \t]*' alone would not absorb.
    $beginLineRe = $lineStart + $beginEsc + $lineEnd
    $endLineRe   = $lineStart + $endEsc   + $lineEnd

    $mlOpt  = [System.Text.RegularExpressions.RegexOptions]::Multiline
    $mlSlOpt = $mlOpt -bor [System.Text.RegularExpressions.RegexOptions]::Singleline

    # Count markers as whole anchored lines (Multiline so ^/$ match per line).
    $beginCount = [regex]::Matches($ExistingContent, $beginLineRe, $mlOpt).Count
    $endCount   = [regex]::Matches($ExistingContent, $endLineRe,   $mlOpt).Count

    # --- No markers at all -> simple append ---------------------------------
    if ($beginCount -eq 0 -and $endCount -eq 0) {
        return (Join-SfloBlock -Body $ExistingContent -Block $Block)
    }

    # --- Unbalanced markers -> FAIL-SAFE: preserve everything, append -------
    if ($beginCount -ne $endCount) {
        Write-Warning ("Existing PowerShell profile has unbalanced SFLO markers " +
            "($beginCount BEGIN / $endCount END). To avoid corrupting your " +
            "profile, your existing content is left untouched and a fresh SFLO " +
            "block is ensured at the end. Please review the profile and delete " +
            "any stale SFLO block by hand.")

        # Idempotency: if the content ALREADY ENDS WITH a complete SFLO
        # block (e.g. one a prior fail-safe append produced), strip exactly
        # that marker-delimited block before re-appending - otherwise
        # repeated runs stack duplicate blocks without bound.
        #
        # Detection is MARKER-BASED, not an exact-string suffix test. An
        # earlier version compared $rstripped.EndsWith($Block); that only
        # matched a BYTE-IDENTICAL trailing block and so failed two real
        # ways on Windows:
        #   (a) CRLF vs LF - the block written into the user's $PROFILE
        #       file may have different line endings than the freshly
        #       generated in-memory $Block; EndsWith returned false, the
        #       strip was skipped, a second block was appended -> stacking.
        #   (b) Block content drift - a setup.ps1 update changes the
        #       block's body between runs, so the old trailing block no
        #       longer equals the new $Block -> strip skipped -> stacking.
        #
        # Instead, match a trailing complete block defined precisely as:
        #   the LAST BEGIN marker line, through the FIRST END marker line
        #   after it, followed by only optional whitespace to end-of-string.
        # The inner '.*?' carries a negative lookahead for BOTH marker
        # lines, which forces:
        #   * the matched BEGIN to be the LAST one (no BEGIN line inside),
        #   * the matched END to be the FIRST after it (no END line inside).
        # '\s*\z' anchors the END flush to end-of-string (modulo whitespace).
        #
        # This is line-ending-agnostic ($lineEnd already tolerates '\r')
        # and content-agnostic (only the markers matter, not the body), so
        # update(update(x)) == update(x) holds for the unbalanced case
        # regardless of CRLF/LF or block-content drift.
        #
        # Critical edge: a profile ending '...[BEGIN..END][stray END]' is
        # NOT mis-stripped. The stray trailing END has no BEGIN delimiting
        # it as a tail block, and the negative lookahead stops the inner
        # span at the FIRST END - so '\s*\z' must then match, but the stray
        # END after it is not whitespace, the match fails, and the
        # preceding complete block is left intact. A stray/partial marker
        # is the user's content and is never deleted.
        $endAnchoredInner = '(?:(?!' + $beginLineRe + ')(?!' + $endLineRe + ').)*?'
        $trailingBlockRe  = $beginLineRe + $endAnchoredInner + $endLineRe + '\s*\z'

        $body  = $ExistingContent
        $match = [regex]::Match($ExistingContent, $trailingBlockRe, $mlSlOpt)
        if ($match.Success) {
            # Strip exactly the matched marker-delimited block. Join-SfloBlock
            # re-normalizes the separating whitespace, so dropping any blank
            # line that preceded the stripped block is intentional and tidy.
            $body = $ExistingContent.Substring(0, $match.Index)
        }

        return (Join-SfloBlock -Body $body -Block $Block)
    }

    # --- Balanced markers -> remove ALL blocks, then append one fresh -------
    # Match each BEGIN line through the NEXT END line. With the Singleline
    # option '.' spans newlines; the lazy '.*?' is bounded by the very next
    # END line, so a block can never swallow a following block or stray text.
    # [regex]::Replace is global, so ALL blocks are removed, not just the
    # first. The trailing '(\r?\n)?' consumes the block's own line break so
    # removal does not leave a blank gap.
    $blockPattern = $beginLineRe + '.*?' + $endLineRe + '(\r?\n)?'
    $stripped = [regex]::Replace($ExistingContent, $blockPattern, '', $mlSlOpt)

    return (Join-SfloBlock -Body $stripped -Block $Block)
}


function Join-SfloBlock {
    <#
    .SYNOPSIS
        Appends $Block to $Body with exactly one blank line of separation,
        trimming any trailing whitespace already on $Body so re-runs do not
        accumulate blank lines.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Body,
        [Parameter(Mandatory)][string]$Block
    )

    $trimmed = $Body -replace '\s+$', ''
    if ([string]::IsNullOrEmpty($trimmed)) {
        return $Block
    }
    return "$trimmed`n`n$Block"
}


function Get-WindowsPowerShell51InstallScript {
    <#
    .SYNOPSIS
        Builds the script that the child Windows PowerShell 5.1 process runs
        to install the two SecretManagement modules unattended.

    .DESCRIPTION
        Stock Windows PowerShell 5.1 cannot reach the PowerShell Gallery
        cleanly out of the box (Bundle-C finding m3):

          * TLS 1.2 is not in the default SecurityProtocol, so the HTTPS
            request to PSGallery fails outright.
          * The NuGet package provider is often absent; Install-Module then
            prompts to download it - fatal under -NonInteractive.
          * PSGallery is registered Untrusted, so Install-Module prompts
            "Are you sure you want to install from an untrusted repository?"
            - also fatal unattended.

        This script fixes all three BEFORE calling Install-Module, so the
        5.1 leg genuinely runs without prompts:

          1. Add Tls12 to [Net.ServicePointManager]::SecurityProtocol.
          2. Bootstrap the NuGet provider with -Force (no prompt).
          3. Set PSGallery's InstallationPolicy to Trusted (no prompt).

        The whole body is wrapped in try/catch: any failure writes the error
        and `exit 1`s, so the PARENT process can read $LASTEXITCODE and
        refuse to install the 5.1 profile (Bundle-C finding m2). Success
        ends with `exit 0`.

    .PARAMETER SecretManagementVersion
        Pinned version of Microsoft.PowerShell.SecretManagement.

    .PARAMETER SecretStoreVersion
        Pinned version of Microsoft.PowerShell.SecretStore.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [string]$SecretManagementVersion = $script:SecretManagementVersion,
        [string]$SecretStoreVersion      = $script:SecretStoreVersion
    )

    # Single-quoted here-string: this is the child shell's source verbatim.
    # The only values interpolated are the two pinned version strings,
    # spliced via the -f format operator - so literal braces are doubled
    # ({{ }}) and nothing else can be mis-expanded. Each statement is on one
    # line; no backtick line-continuations (a backtick is literal text in a
    # single-quoted here-string, which makes continuations error-prone).
    $template = @'
$ErrorActionPreference = 'Stop'
try {{
    # 1. TLS 1.2 - stock WinPS 5.1 defaults to SSL3/TLS1.0; PSGallery
    #    refuses those, so the download fails before it starts.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    # 2. NuGet provider - Install-Module needs it; often absent on a stock
    #    box. -Force installs it without the interactive download prompt.
    Get-PackageProvider -Name NuGet -ForceBootstrap -Force | Out-Null

    # 3. Trust PSGallery so Install-Module does not prompt about an
    #    untrusted repository under -NonInteractive.
    if (Get-PSRepository -Name PSGallery -ErrorAction SilentlyContinue) {{
        Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
    }}

    # Pinned, unattended installs.
    Install-Module Microsoft.PowerShell.SecretManagement -RequiredVersion '{0}' -Scope CurrentUser -Force -AllowClobber
    Install-Module Microsoft.PowerShell.SecretStore -RequiredVersion '{1}' -Scope CurrentUser -Force -AllowClobber

    exit 0
}} catch {{
    Write-Error ("Windows PowerShell 5.1 SecretManagement install failed: " + $_.Exception.Message)
    exit 1
}}
'@

    return ($template -f $SecretManagementVersion, $SecretStoreVersion)
}


function Invoke-SfloWindowsSetup {
    <#
    .SYNOPSIS
        Full installer entry point. Orchestrates submodule init, module
        install, the guarded SecretStore configuration, vault registration,
        profile install for both shells, and the self-test.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SfloHome,
        [switch]$SkipWindowsPowerShell
    )

    if (-not (Test-Path (Join-Path $SfloHome 'src\runner.py'))) {
        throw "SFLO runner not found at $SfloHome\src\runner.py. Run this script from the SFLO repo root, or pass -SfloHome."
    }

    # Populate vendor/agent-skills (SFLO resolves pipeline skills from it).
    # Skip cleanly - don't abort setup - if git is absent or SFLO was unzipped.
    Write-Host "==> Initializing git submodules (vendor/agent-skills)" -ForegroundColor Cyan
    try {
        git -C $SfloHome submodule update --init --recursive
        if ($LASTEXITCODE -ne 0) { throw "git exited $LASTEXITCODE" }
        Write-Host "    Submodules initialized" -ForegroundColor Green
    } catch {
        Write-Warning ("git submodule init skipped or failed - populate " +
            "vendor/agent-skills manually: git submodule update --init --recursive")
    }

    $block = Get-InvokeSfloProfileBlock -SfloHome $SfloHome

    # --- PowerShell 7 (current host) ----------------------------------------
    Write-Host "==> Installing SecretManagement modules in current shell ($($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion))" -ForegroundColor Cyan
    # Versions pinned (-RequiredVersion) for reproducible installs.
    Install-Module Microsoft.PowerShell.SecretManagement -RequiredVersion $script:SecretManagementVersion -Scope CurrentUser -Force -AllowClobber
    Install-Module Microsoft.PowerShell.SecretStore      -RequiredVersion $script:SecretStoreVersion      -Scope CurrentUser -Force -AllowClobber
    Import-Module  Microsoft.PowerShell.SecretManagement
    Import-Module  Microsoft.PowerShell.SecretStore

    # HIGH-fix: guarded - only resets a genuinely fresh store, otherwise aborts.
    Invoke-SecretStoreSetup

    Write-Host "==> Registering SfloVault" -ForegroundColor Cyan
    if (-not (Get-SecretVault -Name SfloVault -ErrorAction SilentlyContinue)) {
        Register-SecretVault -Name SfloVault -ModuleName Microsoft.PowerShell.SecretStore -DefaultVault
    }

    Install-IntoShell -ShellName "$($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion)" -ProfilePath $PROFILE -Block $block

    # --- Windows PowerShell 5.1 (separate module + profile path) ------------
    $ps51 = $null
    if (-not $SkipWindowsPowerShell) {
        $ps51 = Get-Command powershell.exe -ErrorAction SilentlyContinue
    }

    if ($ps51) {
        Write-Host ""
        Write-Host "==> Detected Windows PowerShell 5.1 at $($ps51.Source) - installing for it too" -ForegroundColor Cyan

        # The 5.1 install runs in a child powershell.exe. Build its script
        # with Get-WindowsPowerShell51InstallScript so the same logic can be
        # unit-tested. It bootstraps the unattended prerequisites that stock
        # WinPS 5.1 lacks (TLS 1.2, the NuGet provider, PSGallery trust),
        # then installs the two pinned modules, and `exit`s non-zero on any
        # failure so the parent can detect it.
        $ps51Script = Get-WindowsPowerShell51InstallScript

        & $ps51.Source -NoProfile -ExecutionPolicy Bypass -Command $ps51Script | Out-Host
        $ps51InstallExit = $LASTEXITCODE

        if ($ps51InstallExit -ne 0) {
            # m2 fix: a failed 5.1 install must NOT be swallowed. If we wrote
            # the 5.1 profile anyway, Invoke-SFLO would fail at runtime in
            # that shell. Warn unmistakably and SKIP the 5.1 profile install.
            # The PowerShell 7 install above already succeeded, so SFLO still
            # works from pwsh - only the 5.1 convenience is lost.
            Write-Warning ("Windows PowerShell 5.1 module install FAILED " +
                "(child process exit code $ps51InstallExit). Skipping the " +
                "5.1 profile install - its Invoke-SFLO would fail at runtime " +
                "without the modules. SFLO still works from PowerShell 7. To " +
                "retry the 5.1 leg, fix the cause (often PSGallery/TLS/NuGet) " +
                "and re-run setup.ps1, or pass -SkipWindowsPowerShell to " +
                "silence this.")
        } else {
            $ps51Profile = & $ps51.Source -NoProfile -Command '$PROFILE'
            if ($ps51Profile) {
                Install-IntoShell -ShellName 'Windows PowerShell 5.1' -ProfilePath $ps51Profile -Block $block
            }
        }
    } else {
        Write-Host "==> Windows PowerShell 5.1 not found, skipping" -ForegroundColor DarkGray
    }

    # --- Self-test: round-trip a THROWAWAY secret ---------------------------
    # This proves set/get/remove work. It deliberately uses a disposable
    # GUID value under the name _SFLO_SETUP_ROUNDTRIP_TEST and removes it
    # immediately. It NEVER touches the real SFLO_CLAUDE token - storing that
    # is a separate manual step the user performs (see README "Windows").
    Write-Host ""
    Write-Host "==> Self-test: round-tripping a throwaway secret" -ForegroundColor Cyan
    $testName  = '_SFLO_SETUP_ROUNDTRIP_TEST'
    $testValue = 'roundtrip-' + [Guid]::NewGuid().Guid
    try {
        Set-Secret    -Name $testName -Vault SfloVault -Secret $testValue
        $got = Get-Secret -Name $testName -Vault SfloVault -AsPlainText
        Remove-Secret -Name $testName -Vault SfloVault
        if ($got -eq $testValue) {
            Write-Host "    PASS - set/get/remove all work" -ForegroundColor Green
        } else {
            Write-Host "    FAIL - round trip mismatch" -ForegroundColor Red
        }
    } catch {
        Write-Host "    FAIL - $($_.Exception.Message)" -ForegroundColor Red
    }

    Write-Host ""
    Write-Host "==> Setup complete." -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps (interactive - store your token yourself, the installer never does):" -ForegroundColor Yellow
    Write-Host "  1. claude setup-token                              # generates a long-lived OAuth token"
    Write-Host "  2. Set-Secret -Name SFLO_CLAUDE -Vault SfloVault   # paste the token (input is hidden)"
    Write-Host "  3. . `$PROFILE                                      # reload profile in current shell"
    Write-Host "  4. Invoke-SFLO build a click counter               # run the factory"
    Write-Host ""
}


function Invoke-SfloCursorSetup {
    <#
    .SYNOPSIS
        Install SFLO's Cursor integration: the stop hook and the project rule.

    .DESCRIPTION
        The Cursor runtime drives the SFLO pipeline through Cursor's stop-hook
        protocol. This installs, into the current directory:
          * .cursor/hooks.json     -- a stop hook pointing at the SFLO cursor
            stop_hook.py (merged with any 'stop' hooks already present).
          * .cursor/rules/sflo.mdc -- the always-applied SFLO rule.
        No SecretStore vault and no Claude token: cursor-agent carries its own
        auth. Run 'cursor-agent login' once before triggering the pipeline.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$SfloHome
    )

    $stopHook = Join-Path $SfloHome 'src\hooks\cursor\stop_hook.py'
    $ruleSrc  = Join-Path $SfloHome 'src\hooks\cursor\sflo.mdc'
    if (-not (Test-Path $stopHook)) {
        throw "SFLO cursor stop hook not found at $stopHook. Run this script from the SFLO repo root, or pass -SfloHome."
    }

    Write-Host "==> Installing SFLO Cursor integration" -ForegroundColor Cyan

    $cursorDir = Join-Path (Get-Location) '.cursor'
    $rulesDir  = Join-Path $cursorDir 'rules'
    $hooksFile = Join-Path $cursorDir 'hooks.json'
    New-Item -ItemType Directory -Path $rulesDir -Force | Out-Null

    # Merge the SFLO stop hook into .cursor/hooks.json. The JSON merge runs in
    # Python (paths passed as argv, so no PowerShell quoting hazards) and keeps
    # any other 'stop' hooks the user already configured.
    $mergeScript = @'
import json, os, sys
path, stop_hook = sys.argv[1], sys.argv[2]
hook_cmd = 'python "%s"' % stop_hook
data = {"version": 1, "hooks": {}}
if os.path.isfile(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        pass
data.setdefault("version", 1)
hooks = data.setdefault("hooks", {})
stop_list = [h for h in hooks.get("stop", []) if "stop_hook" not in h.get("command", "")]
stop_list.insert(0, {"command": hook_cmd, "loop_limit": None})
hooks["stop"] = stop_list
with open(path, "w") as f:
    json.dump(data, f, indent=2)
'@
    # Random temp name - a fixed name in world-readable %TEMP% is a
    # pre-create / symlink-swap vector for a write-then-exec temp script.
    $mergeTmp = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $mergeTmp -Value $mergeScript -Encoding ascii
    try {
        python $mergeTmp $hooksFile $stopHook
        if ($LASTEXITCODE -ne 0) { throw "python exit $LASTEXITCODE" }
        Write-Host "    .cursor/hooks.json updated (stop hook -> SFLO)" -ForegroundColor Green
    } catch {
        Write-Warning "Could not update .cursor/hooks.json automatically: $_"
    } finally {
        Remove-Item $mergeTmp -ErrorAction SilentlyContinue
    }

    Copy-Item -Path $ruleSrc -Destination (Join-Path $rulesDir 'sflo.mdc') -Force
    Write-Host "    .cursor/rules/sflo.mdc installed" -ForegroundColor Green

    if (-not (Get-Command cursor-agent -ErrorAction SilentlyContinue)) {
        Write-Host "    NOTE: cursor-agent CLI not on PATH. Install it from" -ForegroundColor Yellow
        Write-Host "          https://cursor.com/cli and run 'cursor-agent login'." -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "==> Cursor setup complete." -ForegroundColor Green
    Write-Host "    Open this folder in Cursor; SFLO drives the pipeline via the stop hook."
    Write-Host ""
}


# ---------------------------------------------------------------------------
# Entry point. With -DefineFunctionsOnly the script stops here: every function
# above is now defined but nothing has been installed. setup.Tests.ps1 relies
# on this to unit-test the logic with mocked cmdlets.
# ---------------------------------------------------------------------------
if ($DefineFunctionsOnly) {
    return
}

# Runtime is an explicit choice, never guessed. A wrong guess silently
# installs the wrong hooks / wrong auth on a machine with more than one
# runtime present.
if (-not $Runtime) {
    Write-Error ("The -Runtime parameter is required. Pass one of: " +
        "cursor, claude-code.  Example:  .\setup.ps1 -Runtime cursor")
    exit 1
}

$ErrorActionPreference = 'Stop'

if (-not $SfloHome) {
    # Default to the directory this script lives in - correct when the user
    # runs .\setup.ps1 from the SFLO repo root.
    $SfloHome = $PSScriptRoot
}

if ($Runtime -eq 'cursor') {
    Invoke-SfloCursorSetup -SfloHome $SfloHome
} else {
    Invoke-SfloWindowsSetup -SfloHome $SfloHome -SkipWindowsPowerShell:$SkipWindowsPowerShell
}
