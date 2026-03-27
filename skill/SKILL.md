---
name: sflo
description: "Build products using the SFLO pipeline — a gated PM→Dev→QA process with configurable gates, threshold, and guardian safety. Use when user says SFLO or asks to install/download SFLO."
metadata:
  { "openclaw": { "emoji": "🏭", "requires": { "bins": ["python3"] } } }
---

# SFLO — Simon Factory Lights Out

## Check if installed

Look for `sflo/src/runner.py` in the workspace. If it exists, SFLO is installed.

## Installation

When user asks to install or download SFLO:

1. Clone from GitHub:
   ```bash
   git clone https://github.com/simonasrazm/simon-factory-lights-out sflo
   ```

2. Run setup:
   ```bash
   bash sflo/setup.sh
   ```

3. Verify:
   ```bash
   python3 sflo/src/runner.py --help
   ```

## Running the Pipeline

When user says "SFLO: [description]":

```bash
python3 sflo/src/runner.py "[description]"
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
threshold: A          # Grade threshold (default: B+)

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
python3 sflo/src/scaffold.py status    # Show pipeline state
python3 sflo/src/scaffold.py next      # Get next action (validates + transitions)
python3 sflo/src/scaffold.py prompt    # Get reinjectable instruction for hooks
```

Most users never need these — the runner and hooks handle everything.
