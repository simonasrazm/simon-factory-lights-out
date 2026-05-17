# Gate: Security Review

**Produces:** `SECURITY-REPORT.md` with severity summary and grade

## Grading Scale

| Grade | Meaning |
|-------|---------|
| A | No findings, or Info-only — ship it |
| B+ | Low-severity findings only, all documented |
| B | Medium findings, remediation noted |
| C | High findings present, remediation planned |
| D | Multiple High findings, incomplete remediation |
| F | Critical findings present — auto-fail |

## SECURITY-REPORT.md Template

```markdown
## Security Audit Report

### Summary
- Critical: [count]
- High: [count]
- Medium: [count]
- Low: [count]
- Info: [count]

### Findings
| # | Severity | Category | Finding | Remediation |
[One row per finding]

### Grade: [A / B+ / B / B- / C / D / F]
```

The `### Grade:` line is parsed by the validator. It MUST appear as its own heading.

## Gate Check

- [ ] SECURITY-REPORT.md exists
- [ ] Contains `### Summary` with severity counts
- [ ] `### Grade: X` heading present with valid grade letter
- [ ] Grade meets threshold
- [ ] No Critical findings present (auto-fail if any Critical found)

## Pass Criteria

- Zero Critical findings (instant F regardless of other factors)
- High findings acceptable if documented with remediation plan
- Medium/Low/Info findings do not block the gate

