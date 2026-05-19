#Requires -Modules Pester
<#
    setup.Tests.ps1 — Pester tests for setup.ps1 (the SFLO Windows installer).

    =====================================================================
    EXECUTION STATUS — RED/GREEN DEFERRED TO A WINDOWS/PWSH ENVIRONMENT
    =====================================================================
    These tests were authored on a machine WITHOUT pwsh or Pester. They
    have NOT been executed here. They are pure mock-based logic tests, so
    they run on any OS that has PowerShell 7 + Pester 5 installed:

        Install-Module Pester -MinimumVersion 5.0 -Scope CurrentUser
        Invoke-Pester ./setup.Tests.ps1

    They are written FIRST (TDD discipline) and pin the intended behavior
    of setup.ps1 — most importantly the HIGH-severity guard that
    Reset-SecretStore must NEVER run against a SecretStore that is already
    configured or already holds secrets.

    HOW THE TESTS REACH setup.ps1's LOGIC WITHOUT RUNNING THE INSTALLER
    -------------------------------------------------------------------
    setup.ps1 is structured so that when it is dot-sourced with the
    -DefineFunctionsOnly switch it ONLY defines its functions (it does not
    install modules, register vaults, or touch the filesystem). The unit
    under test is the function Test-SecretStoreIsFresh, which encapsulates
    the entire HIGH-fix decision. Tests dot-source the script in that mode
    and exercise that function directly with mocked SecretManagement
    cmdlets — no real vault, no real install, deterministic on any OS.
#>

BeforeAll {
    $script:SetupScript = Join-Path $PSScriptRoot 'setup.ps1'

    # Dot-source in "define functions only" mode so nothing is installed.
    . $script:SetupScript -DefineFunctionsOnly

    # --- AST parse of setup.ps1 ------------------------------------------
    # The file-shape tests below assert on the PowerShell ABSTRACT SYNTAX
    # TREE, not on raw text. A raw `grep` for e.g. "Install-Module" or
    # "Set-Secret" also matches those words inside comments and inside the
    # <#...#> comment-based-help block — which produced false test
    # failures on the Windows Pester run (findings 1a / 1b). The AST
    # contains only genuine code: comments and help blocks are not
    # CommandAst nodes, so this class of false-fail cannot recur.
    $tokens = $null
    $parseErrors = $null
    $script:SetupAst = [System.Management.Automation.Language.Parser]::ParseFile(
        $script:SetupScript, [ref]$tokens, [ref]$parseErrors)
    $script:SetupParseErrors = $parseErrors

    # All command invocations in the script, as CommandAst nodes.
    $script:SetupCommandAsts = $script:SetupAst.FindAll(
        { param($n) $n -is [System.Management.Automation.Language.CommandAst] },
        $true)

    # Helper: resolve the invoked command name for a CommandAst, covering
    # BOTH invocation forms (Security-LOW finding):
    #   * plain    `Set-Secret ...`        — CommandAst.GetCommandName() works.
    #   * call-op  `& 'Set-Secret' ...`    — GetCommandName() returns $null;
    #     the name is the first CommandElement, a string-constant AST.
    # `& $var ...` (a VARIABLE target) has no statically knowable name and
    # returns $null here — those are surfaced separately by
    # $script:DynamicAmpersandInvocations below so a test can flag them.
    function Get-AstCommandName {
        param([Parameter(Mandatory)]$CommandAst)
        $name = $CommandAst.GetCommandName()
        if ($name) { return $name }
        # Call-operator form: InvocationOperator is Ampersand (or Dot) and the
        # name lives in the first command element.
        $op = $CommandAst.InvocationOperator
        if ($op -ne [System.Management.Automation.Language.TokenKind]::Ampersand -and
            $op -ne [System.Management.Automation.Language.TokenKind]::Dot) {
            return $null
        }
        $first = $CommandAst.CommandElements | Select-Object -First 1
        if ($first -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
            return $first.Value
        }
        # Expandable string with no interpolation also exposes a literal.
        if ($first -is [System.Management.Automation.Language.ExpandableStringExpressionAst] -and
            $first.NestedExpressions.Count -eq 0) {
            return $first.Value
        }
        return $null
    }

    # Helper: every CommandAst whose invoked command name equals $Name,
    # matching plain AND static call-operator forms.
    function Get-CommandInvocations {
        param([Parameter(Mandatory)][string]$Name)
        $script:SetupCommandAsts | Where-Object {
            $cmdName = Get-AstCommandName -CommandAst $_
            $cmdName -and $cmdName -ieq $Name
        }
    }

    # Call-operator invocations whose target is NOT a statically resolvable
    # name — i.e. `& $var ...`. A future `& $x` that resolved to Set-Secret
    # or Install-Module at runtime would evade name-based guards, so the
    # 1a/1b tests assert this list contains only the known-safe shell-relaunch
    # (`& $ps51.Source ...`) and nothing that could be a hidden module/secret
    # call.
    $script:DynamicAmpersandInvocations = $script:SetupCommandAsts | Where-Object {
        $_.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Ampersand -and
        -not (Get-AstCommandName -CommandAst $_)
    }

    # Helper: flatten a CommandAst's elements to their textual form, so a
    # test can assert a specific parameter / argument is present on a
    # genuine invocation (still AST-scoped — not whole-file text).
    function Get-CommandAstText {
        param([Parameter(Mandatory)]$CommandAst)
        ($CommandAst.CommandElements | ForEach-Object { $_.Extent.Text }) -join ' '
    }
}

Describe 'setup.ps1 — file shape' {

    It 'parses cleanly with no PowerShell syntax errors' {
        # An AST that failed to parse would make every other AST assertion
        # meaningless — check it up front.
        $script:SetupParseErrors | Should -BeNullOrEmpty
    }

    It 'exists at the repo root next to setup.sh' {
        $script:SetupScript                  | Should -Exist
        (Join-Path $PSScriptRoot 'setup.sh') | Should -Exist
    }

    It 'supports a -DefineFunctionsOnly switch (so it is unit-testable)' {
        $cmd = Get-Command $script:SetupScript
        $cmd.Parameters.Keys | Should -Contain 'DefineFunctionsOnly'
    }

    It 'requires an explicit -Runtime parameter (pure-explicit, no detection)' {
        $cmd = Get-Command $script:SetupScript
        $cmd.Parameters.Keys | Should -Contain 'Runtime'
        # ValidateSet pins the accepted runtimes.
        $validateSet = $cmd.Parameters['Runtime'].Attributes |
            Where-Object { $_ -is [System.Management.Automation.ValidateSetAttribute] }
        $validateSet.ValidValues | Should -Contain 'cursor'
        $validateSet.ValidValues | Should -Contain 'claude-code'
    }

    It 'pins every Install-Module invocation with -RequiredVersion (LOW fix)' {
        # AST-based (finding 1a): only genuine Install-Module COMMAND calls
        # are inspected. The word "Install-Module" inside the rationale
        # comment near the top of the script is not a CommandAst and is
        # correctly ignored. Get-CommandInvocations matches plain AND static
        # call-operator (`& 'Install-Module'`) forms.
        $installs = @(Get-CommandInvocations -Name 'Install-Module')
        $installs.Count | Should -BeGreaterThan 0
        foreach ($call in $installs) {
            # -RequiredVersion must be a real parameter on the invocation.
            $paramNames = $call.CommandElements |
                Where-Object { $_ -is [System.Management.Automation.Language.CommandParameterAst] } |
                ForEach-Object { $_.ParameterName }
            $paramNames | Should -Contain 'RequiredVersion'
        }
    }

    It 'does NOT call Set-Secret for the real Claude token (token entry is demoted to a manual step)' {
        # AST-based (finding 1b): only genuine Set-Secret COMMAND calls are
        # inspected. `Set-Secret -Name SFLO_CLAUDE` appearing in the
        # .EXAMPLE comment-based-help block and in the profile here-string's
        # comment lines are NOT CommandAst nodes — they are documentation,
        # and the AST excludes them. The only executable Set-Secret is the
        # throwaway self-test (name _SFLO_SETUP_ROUNDTRIP_TEST); the real
        # token name SFLO_CLAUDE must never appear in an executable call.
        # Get-CommandInvocations matches plain AND static call-operator forms.
        $setSecretCalls = @(Get-CommandInvocations -Name 'Set-Secret')
        foreach ($call in $setSecretCalls) {
            (Get-CommandAstText -CommandAst $call) | Should -Not -Match 'SFLO_CLAUDE'
        }
    }

    It 'has no call-operator (&) invocation of Set-Secret or Install-Module — evasion-proof (Security-LOW)' {
        # GetCommandName() returns $null for `& 'Set-Secret' ...`, so a naive
        # name guard could be bypassed. Get-AstCommandName resolves the
        # static call-operator form too; assert no such hidden invocation of
        # either sensitive command exists.
        $ampSetSecret    = @(Get-CommandInvocations -Name 'Set-Secret') |
            Where-Object { $_.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Ampersand }
        $ampInstallMod   = @(Get-CommandInvocations -Name 'Install-Module') |
            Where-Object { $_.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Ampersand }
        $ampSetSecret  | Should -BeNullOrEmpty
        $ampInstallMod | Should -BeNullOrEmpty
    }

    It 'every dynamic "& $var" invocation is a known-safe external program — nothing hidden (Security-LOW)' {
        # `& $var ...` has no statically knowable name and would evade a
        # name-based guard entirely. setup.ps1 legitimately uses this for
        # exactly one external program: $ps51.Source — relaunching Windows
        # PowerShell 5.1. Assert EVERY dynamic ampersand invocation targets
        # that known variable, so a future hidden `& $x` (which could resolve
        # to Set-Secret or Install-Module at runtime) fails this test.
        $allowed = '\$ps51\.Source'
        foreach ($call in $script:DynamicAmpersandInvocations) {
            $target = ($call.CommandElements | Select-Object -First 1).Extent.Text
            $target | Should -Match $allowed
        }
    }

    It 'runs git submodule update --init to populate vendor/agent-skills' {
        # The Windows installer must initialize the vendor/agent-skills
        # submodule (mirroring `git submodule update --init --recursive` in
        # setup.sh) or SFLO skill resolution fails on a fresh clone. git is a
        # plain command, so Get-CommandInvocations finds it directly.
        $submoduleInit = @(Get-CommandInvocations -Name 'git') | Where-Object {
            $argText = Get-CommandAstText -CommandAst $_
            ($argText -match '\bsubmodule\b') -and
            ($argText -match '\bupdate\b') -and ($argText -match '--init')
        }
        $submoduleInit | Should -Not -BeNullOrEmpty
    }

    # NOTE: the *>&1 / 2>&1 redirection assertions live in the
    # 'Get-InvokeSfloProfileBlock' Describe below. Those operators only
    # ever appear inside the profile here-string TEMPLATE, so they are
    # properties of the generated block (a string), not of setup.ps1's own
    # executable code — asserting them against setup.ps1's AST would be
    # vacuous. They are tested where they actually live.

    It 'preserves the dual-shell strategy (PowerShell 7 + Windows PowerShell 5.1)' {
        # The 5.1 path is keyed off a SkipWindowsPowerShell parameter and a
        # powershell.exe lookup. Assert the parameter exists on the script.
        $cmd = Get-Command $script:SetupScript
        $cmd.Parameters.Keys | Should -Contain 'SkipWindowsPowerShell'
        # And a genuine Get-Command powershell.exe invocation exists.
        $getCmdCalls = @(Get-CommandInvocations -Name 'Get-Command')
        $hasPs51Lookup = $false
        foreach ($call in $getCmdCalls) {
            if ((Get-CommandAstText -CommandAst $call) -match 'powershell\.exe') {
                $hasPs51Lookup = $true
            }
        }
        $hasPs51Lookup | Should -BeTrue
    }
}

Describe 'Test-SecretStoreIsFresh — the HIGH-severity guard' {

    Context 'when the SecretStore has never been configured' {

        BeforeEach {
            # Genuine "never configured": Get-SecretStoreConfiguration throws
            # the specific SecretStore "not been configured" error. No vaults,
            # no secrets.
            Mock Get-SecretStoreConfiguration { throw 'The SecretStore has not been configured.' }
            Mock Get-SecretVault             { @() }
            Mock Get-SecretInfo              { @() }
        }

        It 'reports the store as FRESH (safe to reset)' {
            Test-SecretStoreIsFresh | Should -BeTrue
        }
    }

    Context 'when the SecretStore configuration enumerates successfully (positive configured branch)' {

        BeforeEach {
            # Get-SecretStoreConfiguration returns a non-null object WITHOUT
            # throwing — the store is configured. This is the branch the
            # earlier test suite never exercised (QA finding M1).
            Mock Get-SecretStoreConfiguration { [pscustomobject]@{ Authentication = 'None'; Interaction = 'None' } }
            Mock Get-SecretVault             { @([pscustomobject]@{ Name = 'SfloVault' }) }
            Mock Get-SecretInfo              { @() }
        }

        It 'reports the store as NOT fresh — a configured store is left alone' {
            # A configured store may belong to the user for other reasons;
            # we do not reset it even when it currently looks empty.
            Test-SecretStoreIsFresh | Should -BeFalse
        }

        It 'returns before ever reaching the secret-enumeration step' {
            # The configured check is an early return — Get-SecretInfo must
            # not be consulted once the store is known to be configured.
            Test-SecretStoreIsFresh | Out-Null
            Should -Invoke Get-SecretInfo -Times 0 -Exactly
        }
    }

    Context 'when the SecretStore already holds secrets' {

        BeforeEach {
            # Unconfigured config (so step 1 passes through), but a vault
            # reports a secret — step 2 must classify it NOT fresh.
            Mock Get-SecretStoreConfiguration { throw 'The SecretStore has not been configured.' }
            Mock Get-SecretVault {
                @([pscustomobject]@{ Name = 'SfloVault' }, [pscustomobject]@{ Name = 'UnrelatedVault' })
            }
            Mock Get-SecretInfo {
                @([pscustomobject]@{ Name = 'SomeUnrelatedSecret'; VaultName = 'UnrelatedVault' })
            }
        }

        It 'reports the store as NOT fresh — must never wipe a populated store' {
            Test-SecretStoreIsFresh | Should -BeFalse
        }
    }

    Context 'when the SecretStore is password-protected and LOCKED (HIGH-1 fail-closed)' {

        BeforeEach {
            # The store has never been "configured for unattended access", so
            # Get-SecretStoreConfiguration throws the not-configured error and
            # step 1 passes through. But the store DOES exist and is LOCKED,
            # so enumerating it fails. The bug: with -ErrorAction
            # SilentlyContinue this failure was swallowed into an empty
            # collection and the populated store was mis-classified as fresh.
            Mock Get-SecretStoreConfiguration { throw 'The SecretStore has not been configured.' }
            Mock Get-SecretVault             { @([pscustomobject]@{ Name = 'SfloVault' }) }
            Mock Get-SecretInfo              { throw 'The SecretStore is locked. Run Unlock-SecretStore.' }
        }

        It 'reports the store as NOT fresh — an un-enumerable store is never assumed empty' {
            # This is the headline HIGH-1 regression test: a store whose
            # secrets cannot be listed must NOT be reset.
            Test-SecretStoreIsFresh | Should -BeFalse
        }
    }

    Context 'when Get-SecretStoreConfiguration throws an UNEXPECTED error (HIGH-1 fail-closed)' {

        BeforeEach {
            # A throw that is NOT the "not been configured" signal — e.g. an
            # access-denied or module fault. The guard must not interpret
            # every exception as "fresh".
            Mock Get-SecretStoreConfiguration { throw 'Access to the path is denied.' }
            Mock Get-SecretVault             { @() }
            Mock Get-SecretInfo              { @() }
        }

        It 'reports the store as NOT fresh — only the genuine not-configured error means fresh' {
            Test-SecretStoreIsFresh | Should -BeFalse
        }
    }

    Context 'when Get-SecretVault itself fails (fail-closed)' {

        BeforeEach {
            # Unconfigured config, but the vault registry cannot be read.
            # We cannot prove emptiness → not fresh.
            Mock Get-SecretStoreConfiguration { throw 'The SecretStore has not been configured.' }
            Mock Get-SecretVault             { throw 'Vault registry unavailable.' }
            Mock Get-SecretInfo              { @() }
        }

        It 'reports the store as NOT fresh — an unreadable vault registry is doubt' {
            Test-SecretStoreIsFresh | Should -BeFalse
        }
    }
}

Describe 'Invoke-SecretStoreSetup — Reset-SecretStore call discipline (HIGH fix)' {

    Context 'fresh store' {

        BeforeEach {
            Mock Test-SecretStoreIsFresh { $true }
            Mock Reset-SecretStore       { }
            Mock Write-Host             { }
        }

        It 'DOES call Reset-SecretStore exactly once when the store is fresh' {
            Invoke-SecretStoreSetup
            Should -Invoke Reset-SecretStore -Times 1 -Exactly
        }
    }

    Context 'store already in use (populated)' {

        BeforeEach {
            Mock Test-SecretStoreIsFresh { $false }
            Mock Reset-SecretStore       { }
            Mock Write-Host             { }
        }

        It 'NEVER calls Reset-SecretStore when the store is already in use' {
            # The headline safety property: no destructive wipe of a store
            # that may hold the user's unrelated secrets.
            { Invoke-SecretStoreSetup } | Should -Throw
            Should -Invoke Reset-SecretStore -Times 0 -Exactly
        }

        It 'ABORTS with an actionable message instead of silently continuing' {
            { Invoke-SecretStoreSetup } |
                Should -Throw -ExpectedMessage '*already*configured*'
        }
    }
}

Describe 'Get-InvokeSfloProfileBlock — the profile block written into $PROFILE' {

    BeforeAll {
        $script:Block = Get-InvokeSfloProfileBlock -SfloHome 'C:\Projects\SFLO'
    }

    It 'is delimited by the SFLO BEGIN/END markers (idempotent re-runs)' {
        $script:Block | Should -Match '# === SFLO BEGIN ==='
        $script:Block | Should -Match '# === SFLO END ==='
    }

    It 'defines the Invoke-SFLO function' {
        $script:Block | Should -Match 'function Invoke-SFLO'
    }

    It 'removes the OAuth token from the environment in a finally block' {
        $script:Block | Should -Match 'finally'
        $script:Block | Should -Match 'Remove-Item Env:\\CLAUDE_CODE_OAUTH_TOKEN'
    }

    It 'does NOT use *>&1 to merge every stream (MEDIUM fix — token-in-logs)' {
        $script:Block | Should -Not -Match '\*>&1'
    }

    It 'still surfaces stderr so Python tracebacks remain visible (2>&1 only)' {
        # The bundle author added stream-merge so runner tracebacks are not
        # lost. We keep stderr->stdout (2>&1) but drop the blanket *>&1 that
        # also pulled verbose/debug/information streams (which is where an
        # env-dump traceback would carry the token).
        $script:Block | Should -Match '2>&1'
    }

    It 'emits $runner as a literal (function-scope var), not the build-time value' {
        # Here-string escaping correctness (changes.md section 6): $runner
        # must survive into the profile as a variable reference.
        $script:Block | Should -Match '\$runner'
    }

    It 'bakes the SfloHome path in as a literal string value' {
        $script:Block | Should -Match "C:\\Projects\\SFLO"
    }

    It 'does not auto-wipe pipeline.log (changes.md section 12 — reverted)' {
        $script:Block | Should -Not -Match 'pipeline\.log'
    }
}

Describe 'Update-SfloProfileContent — $PROFILE block rewrite (QA fix M2)' {

    BeforeAll {
        # A synthetic SFLO block carrying the real BEGIN/END markers. The
        # rewrite logic keys off the markers, so a minimal body is enough.
        $script:Begin = '# === SFLO BEGIN ==='
        $script:End   = '# === SFLO END ==='
        $script:FreshBlock = @"
$script:Begin
function Invoke-SFLO { 'v-new' }
$script:End
"@
        # A representative user-authored block to wrap the SFLO content in,
        # so every test can assert the user's own content survived.
        $script:UserHead = "# my profile`nSet-Alias ll Get-ChildItem`n"
        $script:UserTail = "`nfunction Prompt { 'PS> ' }`n# end of profile"

        function Assert-ExactlyOneBlock {
            param([string]$Content)
            ([regex]::Matches($Content, [regex]::Escape($script:Begin))).Count |
                Should -Be 1
            ([regex]::Matches($Content, [regex]::Escape($script:End))).Count |
                Should -Be 1
        }
    }

    Context 'prior state: empty profile' {
        It 'returns exactly the fresh block' {
            $result = Update-SfloProfileContent -ExistingContent '' -Block $script:FreshBlock
            $result | Should -Be $script:FreshBlock
            Assert-ExactlyOneBlock $result
        }
    }

    Context 'prior state: profile with NO SFLO markers' {
        It 'appends one block and preserves all user content' {
            $existing = $script:UserHead + $script:UserTail
            $result = Update-SfloProfileContent -ExistingContent $existing -Block $script:FreshBlock

            Assert-ExactlyOneBlock $result
            $result | Should -Match 'Set-Alias ll Get-ChildItem'
            $result | Should -Match "function Prompt"
            $result | Should -Match "function Invoke-SFLO \{ 'v-new' \}"
        }
    }

    Context 'prior state: ONE existing SFLO block' {
        It 'replaces it in place — still exactly one block, user content intact' {
            $oldBlock = "$script:Begin`nfunction Invoke-SFLO { 'v-OLD' }`n$script:End"
            $existing = $script:UserHead + $oldBlock + $script:UserTail
            $result = Update-SfloProfileContent -ExistingContent $existing -Block $script:FreshBlock

            Assert-ExactlyOneBlock $result
            # Old body gone, new body present.
            $result | Should -Not -Match "v-OLD"
            $result | Should -Match "v-new"
            # User content survives on both sides of where the block was.
            $result | Should -Match 'Set-Alias ll Get-ChildItem'
            $result | Should -Match "function Prompt"
        }
    }

    Context 'prior state: TWO existing SFLO blocks (doubly-appended profile)' {
        It 'collapses both into a single fresh block' {
            $blockA = "$script:Begin`nfunction Invoke-SFLO { 'v-A' }`n$script:End"
            $blockB = "$script:Begin`nfunction Invoke-SFLO { 'v-B' }`n$script:End"
            # User content head, block A, more user content, block B, tail.
            $existing = $script:UserHead + $blockA +
                        "`n# midsection user note`n" + $blockB + $script:UserTail
            $result = Update-SfloProfileContent -ExistingContent $existing -Block $script:FreshBlock

            # The headline M2 assertion: 0/1/many -> exactly one.
            Assert-ExactlyOneBlock $result
            $result | Should -Not -Match "v-A"
            $result | Should -Not -Match "v-B"
            $result | Should -Match "v-new"
            # Every piece of user content — including the midsection note —
            # survives.
            $result | Should -Match 'Set-Alias ll Get-ChildItem'
            $result | Should -Match '# midsection user note'
            $result | Should -Match "function Prompt"
        }
    }

    Context 'prior state: a stray END marker in unrelated user text' {
        It 'does not truncate — user content with the stray marker is preserved' {
            # The user happens to have a line that looks like the END marker
            # sitting in their own text, with NO matching BEGIN. Markers are
            # unbalanced (0 BEGIN / 1 END) -> fail-safe path: nothing is
            # deleted, content preserved verbatim, one fresh block appended.
            $existing = "# notes`n$script:End`n# this line and the marker above are mine`n"
            $result = Update-SfloProfileContent -ExistingContent $existing -Block $script:FreshBlock

            # User content fully intact — not truncated at the stray marker.
            $result | Should -Match '# notes'
            $result | Should -Match '# this line and the marker above are mine'
            # A fresh block was still appended.
            $result | Should -Match "function Invoke-SFLO \{ 'v-new' \}"
            # The fresh block contributes one BEGIN; the stray END plus the
            # block's own END means END count is not asserted to 1 here —
            # the point is the user's content survived. BEGIN is exactly one.
            ([regex]::Matches($result, [regex]::Escape($script:Begin))).Count |
                Should -Be 1
        }
    }

    Context 'prior state: a BEGIN marker with no matching END (unbalanced)' {
        It 'fail-safe — preserves user content, never deletes to end of file' {
            # 1 BEGIN / 0 END. A naive span-removal would delete from the
            # BEGIN to end-of-file, eating the user's trailing content. The
            # fail-safe path must keep everything.
            $existing = "# top`n$script:Begin`n# user text after a lone BEGIN`nSet-Alias gg git`n"
            $result = Update-SfloProfileContent -ExistingContent $existing -Block $script:FreshBlock

            $result | Should -Match '# top'
            $result | Should -Match '# user text after a lone BEGIN'
            $result | Should -Match 'Set-Alias gg git'
            $result | Should -Match "function Invoke-SFLO \{ 'v-new' \}"
        }
    }

    Context 'idempotency — fail-safe branch must not stack blocks (round 6)' {

        # Helper: run the rewrite three times and assert every pass after
        # the first produces output identical to the first. This is the
        # update(update(x)) == update(x) invariant, checked to x3 depth.
        function Assert-Idempotent {
            param([Parameter(Mandatory)][AllowEmptyString()][string]$Existing)
            $o1 = Update-SfloProfileContent -ExistingContent $Existing -Block $script:FreshBlock
            $o2 = Update-SfloProfileContent -ExistingContent $o1       -Block $script:FreshBlock
            $o3 = Update-SfloProfileContent -ExistingContent $o2       -Block $script:FreshBlock
            $o2 | Should -Be $o1
            $o3 | Should -Be $o1
            return $o1
        }

        It 'balanced-path: stable on a normal profile (unchanged behaviour)' {
            $result = Assert-Idempotent -Existing ($script:UserHead + $script:UserTail)
            Assert-ExactlyOneBlock $result
        }

        It 'STRAY-END profile, re-run x3 -> exactly one block' {
            # 0 BEGIN / 1 END -> fail-safe append. The appended block adds
            # its own BEGIN+END, so the result is still unbalanced; without
            # the marker-based trailing-block strip a re-run appends AGAIN
            # and stacks. The user's stray END must survive.
            $existing = "# notes`n$script:End`n# the marker above is the user's`n"
            $result = Assert-Idempotent -Existing $existing
            ([regex]::Matches($result, [regex]::Escape($script:Begin))).Count | Should -Be 1
            $result | Should -Match '# notes'
        }

        It 'LONE-BEGIN profile, re-run x3 -> exactly one block, user text intact' {
            # 1 BEGIN / 0 END -> fail-safe. An earlier lazy-regex strip ate
            # user text here; the marker-based strip (anchored on the LAST
            # BEGIN) must be stable AND preserve all user content.
            $existing = "# top`n$script:Begin`n# user text`nSet-Alias gg git`n"
            $result = Assert-Idempotent -Existing $existing
            ([regex]::Matches($result, [regex]::Escape($script:Begin))).Count | Should -Be 2
            $result | Should -Match '# user text'
            $result | Should -Match 'Set-Alias gg git'
        }

        It 'CRLF trailing block while $Block is LF -> still detected and stripped' {
            # The block previously written into the user $PROFILE file may
            # carry CRLF line endings while the freshly generated in-memory
            # $Block is LF. An exact-string EndsWith test would miss it and
            # stack a second block. Marker-based detection ($lineEnd tolerates
            # an optional \r) must still see and strip the CRLF block.
            #
            # Construct: a stray END (forces fail-safe) + a CRLF copy of the
            # block flush against end-of-string.
            $crlfBlock = $script:FreshBlock -replace "`n", "`r`n"
            $existing  = "# notes`r`n$script:End`r`n" + $crlfBlock
            $result = Update-SfloProfileContent -ExistingContent $existing -Block $script:FreshBlock
            # The pre-existing CRLF block was recognised and removed; exactly
            # one block remains.
            ([regex]::Matches($result, [regex]::Escape($script:Begin))).Count | Should -Be 1
            # And the rewrite is idempotent from there.
            $again = Update-SfloProfileContent -ExistingContent $result -Block $script:FreshBlock
            $again | Should -Be $result
        }

        It 'block-content drift between runs -> old trailing block still stripped' {
            # A setup.ps1 update changes the block body, so a prior trailing
            # block is no longer byte-identical to the new $Block. Exact-string
            # detection would skip the strip and stack. Marker-based detection
            # is content-agnostic — only the markers matter — so the drifted
            # block is still recognised and replaced.
            $oldBlock = "$script:Begin`nfunction Invoke-SFLO { 'v-OLD-DRIFTED' }`n$script:End"
            $existing = "# notes`n$script:End`n`n$oldBlock"
            $result = Update-SfloProfileContent -ExistingContent $existing -Block $script:FreshBlock
            ([regex]::Matches($result, [regex]::Escape($script:Begin))).Count | Should -Be 1
            $result | Should -Not -Match 'v-OLD-DRIFTED'
            $result | Should -Match "function Invoke-SFLO \{ 'v-new' \}"
            # Idempotent thereafter.
            $again = Update-SfloProfileContent -ExistingContent $result -Block $script:FreshBlock
            $again | Should -Be $result
        }

        It '[complete block][stray END] -> stray END preserved, NOT eaten' {
            # Critical edge: the trailing stray END has no BEGIN delimiting
            # it as a tail block. The marker-based detector stops the inner
            # span at the FIRST END after the BEGIN, then requires only
            # whitespace to end-of-string — the stray END after the complete
            # block breaks that anchor, so NO trailing block is detected and
            # a fresh block is appended. The user's stray END must remain.
            $completeBlock = "$script:Begin`nbody-line`n$script:End"
            $existing = "# user head`n$completeBlock`n$script:End`n"
            $result = Update-SfloProfileContent -ExistingContent $existing -Block $script:FreshBlock

            $result | Should -Match '# user head'
            $result | Should -Match 'body-line'
            # The stray END is user content — it survives. END count is at
            # least 3 (original complete block's END + stray END + fresh
            # block's END).
            ([regex]::Matches($result, [regex]::Escape($script:End))).Count |
                Should -BeGreaterOrEqual 3
            $result | Should -Match "function Invoke-SFLO \{ 'v-new' \}"
        }
    }
}

Describe 'Get-WindowsPowerShell51InstallScript — unattended PS 5.1 install (Bundle-C m3)' {

    BeforeAll {
        $script:Ps51Script = Get-WindowsPowerShell51InstallScript `
            -SecretManagementVersion '1.1.2' -SecretStoreVersion '1.0.6'
    }

    It 'returns a non-empty script' {
        $script:Ps51Script | Should -Not -BeNullOrEmpty
    }

    It 'is itself syntactically valid PowerShell' {
        # The script is executed by a child powershell.exe; a parse error
        # would fail unattended with a cryptic message. Parse it here.
        $errs = $null
        [System.Management.Automation.Language.Parser]::ParseInput(
            $script:Ps51Script, [ref]$null, [ref]$errs) | Out-Null
        $errs | Should -BeNullOrEmpty
    }

    It 'enables TLS 1.2 before contacting PSGallery (m3)' {
        # Stock WinPS 5.1 negotiates SSL3/TLS1.0; PSGallery rejects those.
        $script:Ps51Script | Should -Match 'Tls12'
        $script:Ps51Script | Should -Match 'SecurityProtocol'
    }

    It 'bootstraps the NuGet package provider non-interactively (m3)' {
        $script:Ps51Script | Should -Match 'Get-PackageProvider'
        $script:Ps51Script | Should -Match 'NuGet'
        # -Force / -ForceBootstrap suppress the interactive download prompt.
        $script:Ps51Script | Should -Match 'ForceBootstrap'
    }

    It 'marks PSGallery trusted so Install-Module does not prompt (m3)' {
        $script:Ps51Script | Should -Match 'Set-PSRepository'
        $script:Ps51Script | Should -Match 'Trusted'
    }

    It 'installs both modules pinned to the supplied versions' {
        $script:Ps51Script | Should -Match 'Microsoft\.PowerShell\.SecretManagement'
        $script:Ps51Script | Should -Match 'Microsoft\.PowerShell\.SecretStore'
        $script:Ps51Script | Should -Match "-RequiredVersion '1\.1\.2'"
        $script:Ps51Script | Should -Match "-RequiredVersion '1\.0\.6'"
    }

    It 'exits non-zero on failure so the parent can detect it (m2)' {
        # try/catch wrapper: failure path must `exit 1`, success `exit 0`.
        $script:Ps51Script | Should -Match 'exit 1'
        $script:Ps51Script | Should -Match 'exit 0'
        $script:Ps51Script | Should -Match 'catch'
    }
}
