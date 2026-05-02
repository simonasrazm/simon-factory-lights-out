# Gate 3: QA Testing

**Agent:** QA Tester
**Input:** Working app from Gate 2
**Produces:** `QA-REPORT.md` with grade

## QA-REPORT.md Template

```markdown
## QA Report: [Project Name]

### Data Verification
- Records loaded: [N] from [source]
- Data freshness: [current/stale]

### Test Results
| Test | Result | Notes |
|------|--------|-------|
| Real data loads | PASS/FAIL | |
| Core journey works | PASS/FAIL | |
| Acceptance criteria | PASS/FAIL | Tested each AC from SCOPE.md |
| Edge cases | PASS/FAIL | |
| Performance | PASS/FAIL | |
| Error states | PASS/FAIL | |

### Issues Found
1. [CRITICAL/MAJOR/MINOR] — [description] → [suggested fix]
2. ...

### Grade: [A / B+ / B / C / D / F]

### Stranger Test
Would a random person find this useful? [Yes/No/Maybe — why]
```

## Grading Scale

| Grade | Meaning | Criteria |
|-------|---------|----------|
| **A** | Ship it | Real data works, UX polished, clear value |
| **B+** | Almost | Minor issues, still useful |
| **B** | Decent | Works but needs polish |
| **C** | Mediocre | Works but ugly/confusing/slow |
| **D** | Broken | Major issues, not useful |
| **F** | Fail | Doesn't work or no real data |

## Minimum to proceed to Gate 4: configured in `src/constants.py` (GRADE_THRESHOLD)

## Auto-Fail Triggers

These automatically score F regardless of other results:

- Mock/sample data instead of real data
- Product doesn't start or run
- Purpose is unclear ("what is this for?")
- Core use case from SCOPE.md doesn't work
- Any unmet **"Dev MUST follow"** decision from SCOPE.md (e.g. a `D2 / D3 / D6` constraint marked as Dev-binding). MUST-follow decisions are non-negotiable architectural commitments the PM scoped — leaving any of them violated is a contract breach, not a quality nit. Catch them at QA (Gate 3) so they don't survive to PM-VERIFY (Gate 4) and trigger an outer-loop iteration.

## Gate Check

- [ ] QA-REPORT.md exists
- [ ] Grade meets threshold (see `src/constants.py`)
- [ ] No auto-fail triggers present

The scaffold validates these checks. If grade is below threshold, it loops back to Gate 2 automatically (max 10 cycles).
