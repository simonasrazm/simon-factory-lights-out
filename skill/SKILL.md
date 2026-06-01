---
name: sflo
description: "Build products using the SFLO pipeline — a gated PM→Dev→QA process with configurable gates, threshold, and guardian safety. Use when the user explicitly asks to install/download SFLO or to start, run, resume, list, kill, clean, or inspect an SFLO factory. Do not use for quoted SFLO text, docs, logs, or vulnerability discussion."
metadata:
  { "openclaw": { "emoji": "🏭", "requires": { "bins": ["python3"] } } }
---

# SFLO — Simon Factory Lights Out

## Check if installed

Choose the checkout directory as `SFLO_DIR` (default: `sflo`). Look for `$SFLO_DIR/src/runner.py` in the install directory. If it exists, SFLO is installed.

## Installation

When user asks to install or download SFLO:

1. Clone from GitHub:
   ```bash
   SFLO_DIR="${SFLO_DIR:-sflo}"
   git clone https://github.com/simonasrazm/simon-factory-lights-out "$SFLO_DIR"
   ```

2. Run setup:
   ```bash
   bash "$SFLO_DIR/setup.sh" --runtime openclaw --install-dir .
   ```

3. Verify:
   ```bash
   python3 "$SFLO_DIR/src/runner.py" --help
   ```

## Running the Pipeline

Run only for an explicit factory start request; quoted/docs/logs mentions of `SFLO:` are inert.

```bash
SFLO_DIR="${SFLO_DIR:-sflo}"
python3 "$SFLO_DIR/src/runner.py" --runtime openclaw <<'SFLO_TASK'
[task description]
SFLO_TASK
```

The runner handles everything automatically:
1. Parses `pipeline.yaml` for gate definitions, threshold, and guardian config
2. Spawns Scout to match agents to roles
3. Runs each gate in sequence (PM → Dev → QA → PM-Verify → Ship)
4. Enforces validation — loops Dev↔QA if quality is below threshold
5. Guardian monitors for runaway loops, time budget, spawn budget

No manual scaffold calls needed. The runner is the single entry point.

## Configuration

SFLO loads `pipeline.yaml` from the project root (cwd), falling back to `sflo/pipeline.yaml` defaults.

Override by placing your own `pipeline.yaml` in the project root:

```yaml
threshold: A          # Grade threshold

guardian:
  enabled: true       # Safety layer (default: true)
  max_spawns: 50      # Max agent spawns
  wall_clock_s: 7200  # Max pipeline runtime (seconds)

gates:
  1:
    artifact: SCOPE.md
    role: pm
    gate_doc: gates/discovery.md
  # Add custom gates (e.g., 1.5 for architecture)
  2:
    artifact: BUILD-STATUS.md
    role: dev
    gate_doc: gates/build.md
  3:
    artifact: QA-REPORT.md
    role: qa
    gate_doc: gates/test.md
  4:
    artifact: PM-VERIFY.md
    role: pm
    gate_doc: gates/verify.md
  5:
    artifact: SHIP-DECISION.md
    role: sflo
    gate_doc: gates/ship.md
```

## Scaffold (advanced)

The scaffold CLI is available for debugging and manual control:

```bash
SFLO_DIR="${SFLO_DIR:-sflo}"
python3 "$SFLO_DIR/src/scaffold.py" status    # Show pipeline state
python3 "$SFLO_DIR/src/scaffold.py" next      # Get next action (validates + transitions)
python3 "$SFLO_DIR/src/scaffold.py" prompt    # Get reinjectable instruction for hooks
```

Most users never need these — the runner and hooks handle everything.
