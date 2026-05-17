# Gate: QA Review

**Produces:** `QA-REPORT.md` with grade

## Auto-Fail Triggers (instant F)

- Mock/sample data instead of real data
- Product doesn't start or run
- Purpose is unclear ("what is this for?")
- Core use case from SCOPE.md doesn't work
- Any MUST-follow decision from SCOPE.md violated

## Grading Scale

| Grade | Meaning |
|-------|---------|
| A | Ship it — all ACs pass, polished, clear value |
| B+ | Almost — minor issues, still useful |
| B | Decent — works but needs polish |
| C | Mediocre — works but ugly/confusing/slow |
| D | Broken — major issues, not useful |
| F | Fail — doesn't work or auto-fail triggered |

## QA-REPORT.md Template

```markdown
GATE_RESULT: PASS or FAIL

## Test Summary
| Test | Result | Notes |
|------|--------|-------|
[One row per AC from SCOPE.md + edge cases + core journey]

## Five-Axis Review

**Correctness** — Logic right? Edge cases? Matches SCOPE.md intent?
**Design** — Right boundaries, clean structure, minimal coupling, clear naming? Can future devs understand and modify?
**Robustness** — Error handling graceful? Inputs validated at boundary? Failure modes diagnosable? No leaked secrets?
**Performance** — Acceptable for expected data/load? No wasted resources? No O(n²) surprises?
**Tests** — Core paths covered? Edge cases tested? Bug fixes have regression tests? If no tests visible: list which paths need coverage.

### Dependency Governance (skip if no new deps)
- New deps added? Justified? Risky or unmaintained? Flag it.

### Architecture Principles
- **OCP**: Must modify existing code to add new variants? (registries, plugin patterns, handler maps)
- **DIP**: Concrete dependencies blocking testability or swapability?
- **SRP**: Single module doing 3+ unrelated jobs? (partially covered by Design — flag if egregious)
- Skip LSP/ISP — they rarely produce actionable findings.
- Severity: Minor on medium+ scope. Nit/Consider on legacy code (include remediation path).

## Dead Code Hygiene
[Explicit check — list orphaned items or confirm clean]

## Findings
| # | Severity | Finding |
[All issues: Critical, Major, Minor, Nit, Consider, FYI]

## Grading
- Starting grade: A
- Auto-fail check: [see triggers above]
- Deductions: [Critical = D/F. Major = drop 1 level. Minor/Nit = report only]
- Bonuses noted: [nice-to-haves delivered, beyond-spec quality]

### Grade: [A / B+ / B / B- / C / D / F]

## Stranger Test
Would a random person find this useful? [Yes/No — why]
```

The `### Grade:` line is parsed by the validator. It MUST appear as its own heading — not inside a bullet, not as bold text, not as part of another section title.

## Gate Check

- [ ] QA-REPORT.md exists
- [ ] First line is `GATE_RESULT: PASS` or `GATE_RESULT: FAIL`
- [ ] `### Grade: X` heading present with valid grade letter
- [ ] Grade meets threshold
- [ ] No auto-fail triggers present
