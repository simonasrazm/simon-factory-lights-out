# Gate 1: PM Discovery

**Agent:** Product Manager
**Produces:** `SCOPE.md`

## SCOPE.md Template

```markdown
## Data Sources
- Endpoint: [URL] — Verified (tested with curl, returned [N] records)
- Endpoint: [URL] — Verified

## What We're Building
[One paragraph — what problem this solves for a real human]

## Features (prioritized)
1. [Must-have] ...
2. [Must-have] ...
3. [Nice-to-have] ...

## Challenge Analysis
- [Risk/constraint]: [impact] — [mitigation]
- [Risk/constraint]: [impact] — [mitigation]

## Acceptance Criteria
- [ ] AC1: [specific, testable]
- [ ] AC2: [specific, testable]
- [ ] AC3: [specific, testable]
```

## Gate Check

- [ ] SCOPE.md exists
- [ ] At least 1 data endpoint verified with real curl output
- [ ] Acceptance criteria are specific and testable
The scaffold validates these checks before advancing to Gate 2.
