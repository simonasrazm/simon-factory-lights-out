# Agent Roles

## Orchestrator (Team Lead)

The agent running the SFLO pipeline. Usually the main/primary agent in a conversation.

**Responsibilities:**

- Own the project end-to-end
- Spawn specialist agents for each gate
- Verify gate artifacts before proceeding
- Track iteration count and post status
- Escalate after 10 failed cycles

**Does NOT:** Write code, test features, or verify specs — delegates to specialists.

## Product Manager (PM)

Runs Gate 1 (Discovery) and Gate 4 (Verification).

**Responsibilities:**

- Find and verify data sources
- Define scope and acceptance criteria
- Verify built product matches spec

**Selection:** Use a PM-flavored agent if available. Otherwise, any capable agent with explicit PM instructions.

## Developer

Runs Gate 2 (Build).

**Responsibilities:**

- Build the application from SCOPE.md
- Connect to real data sources
- Run self-checks before handoff
- Fix issues from QA feedback loops

**Selection:** Use a coding agent (Codex, Claude Code, etc.) with access to the project directory.

## QA Tester

Runs Gate 3 (Test).

**Responsibilities:**

- Test with real data, not mocks
- Grade the product honestly
- Fix minor bugs directly (don't just report)
- Provide evidence for every finding

**Selection:** Use any capable agent. QA agents should NOT be the same instance that built the feature

## Agent Selection Heuristic

```
Simple tool call?           → Don't spawn, just do it
Noisy/verbose execution?    → Lightweight agent, minimal context
Needs workspace context?    → Mid-tier agent, full workspace
Complex reasoning/judgment? → Top-tier agent, extended thinking
```

## Cost Optimization

Not every gate needs your most expensive model:

- **Gate 1 (PM Discovery):** Mid-tier — needs web access and judgment
- **Gate 2 (Build):** Coding agent — needs tool use and file access
- **Gate 3 (QA):** Mid-tier — needs to test and reason about quality
- **Gate 4 (Verify):** Lightweight — comparing two documents
- **Gate 5 (Ship):** Orchestrator already running — no extra spawn
