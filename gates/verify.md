# Gate 4: PM Verification

**Agent:** Product Manager
**Input:** QA-REPORT.md (passed QA threshold) + original SCOPE.md
**Produces:** `PM-VERIFY.md`

## PM-VERIFY.md Template

```markdown
## PM Verification: [Project Name]

### Acceptance Criteria Check
- [ ] AC1: MET / NOT MET — [evidence]
- [ ] AC2: MET / NOT MET — [evidence]
- [ ] AC3: MET / NOT MET — [evidence]

### Scope Alignment
- Original scope: [summary from SCOPE.md]
- What was built: [summary from BUILD-STATUS.md]
- Alignment: MATCHES / MINOR DEVIATIONS / OFF TRACK

### Deviations
- [Any scope creep, missing features, or unexpected additions]

### Grade: [A / B+ / B / C / D / F]

### Verdict: APPROVED (A) / NEEDS CHANGES (below A)

### If NEEDS CHANGES:
1. [Specific change needed]
2. [Specific change needed]
```

## Gate Check

- [ ] PM-VERIFY.md exists
- [ ] Verdict is APPROVED
- [ ] All acceptance criteria marked MET

The scaffold validates these checks. If verdict is not APPROVED, it loops back to Gate 2 automatically.
