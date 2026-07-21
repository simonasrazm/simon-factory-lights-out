# Gate 1: PM Discovery

**Agent:** Product Manager
**Produces:** `SCOPE.md`

## SCOPE.md Template

```markdown
## Data Sources
Use exactly one of these forms:

- None — No external data sources required for this scope.

Or, for every required external source:

- Endpoint: [URL] — Verified
  - Probe: `curl ...` (or equivalent real request)
  - Result: HTTP [status], returned [N] records, [response time]

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
- [ ] Every required external source includes real probe evidence, or the scope explicitly states that no external source is required
- [ ] Acceptance criteria are specific and testable
The scaffold validates these checks before advancing to Gate 2.
