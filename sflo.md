# SFLO — Core Pipeline

## Configuration

SFLO loads pipeline configuration from `pipeline.yaml`. Resolution order:
1. `./pipeline.yaml` (project root / cwd)
2. `sflo/pipeline.yaml` (sflo subdir of project root)
3. `sflo/pipeline.yaml` (built-in defaults, bundled with SFLO)

Override the pipeline by placing your own `pipeline.yaml` in the project root. Private projects can extend the pipeline (add custom gates, change the grade threshold, enable the Guardian) without modifying the submodule.

## Trigger

When the user says `SFLO: <description>`, run:

```
echo '<description>' | python3 src/runner.py
```

Always pipe the prompt via stdin — never pass it as a CLI argument. User prompts contain special characters that break shell escaping.

If `python3` is not found, try `python`. The runner handles everything else.

## Overview

SFLO is a five-gate pipeline for building software with AI agents. The runner (`src/runner.py`) executes the pipeline. The scaffold (`src/scaffold.py`) is the state machine — it manages state, validates artifacts, enforces gate sequence, and controls loop limits. No agent can skip, override, or shortcut the pipeline.

## Roles

- **PM:** Gates 1 (Discovery) and 4 (Verification)
- **Developer:** Gate 2 (Build)
- **QA:** Gate 3 (Test)
- **SFLO:** Gate 5 (Ship) + pipeline coordination

Custom agents can extend any role. Core gate checks are always enforced by the scaffold regardless of which agent runs.

## Gates

| Gate | Artifact | Validated by scaffold |
|------|----------|----------------------|
| 1. Discovery | `SCOPE.md` | Data sources section, acceptance criteria |
| 2. Build | `BUILD-STATUS.md` | Build success marker, all checks marked |
| 3. Test | `QA-REPORT.md` | Grade present, grade meets threshold (configured in `pipeline.yaml`), auto-fail patterns |
| 4. Verify | `PM-VERIFY.md` | Verdict present, verdict = APPROVED |
| 5. Ship | `SHIP-DECISION.md` | Decision present, decision ∈ {SHIP, HOLD, KILL} |

All artifacts are produced in `.sflo/` — runtime outputs, not source code.

## Fail Loops

Enforced by the scaffold state machine:

- **QA grade below threshold:** Inner loop — Dev rebuilds, QA retests. Max 10 cycles. (Threshold configured in `pipeline.yaml`, default B+.)
- **PM rejects:** Outer loop — back to Dev→QA with PM's deviation list. Inner counter resets. Max 10 outer loops.
- **Limits exhausted:** Scaffold escalates to human. No agent can continue.

## Emergency Override

Only the human owner can override. The scaffold supports this via the `SHIP-DECISION.md` override field — the human says "ship it anyway," the decision is logged with reason.
