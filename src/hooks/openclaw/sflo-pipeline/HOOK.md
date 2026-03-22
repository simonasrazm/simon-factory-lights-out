---
name: sflo-pipeline
description: "SFLO pipeline driver — reinjects next gate instruction after each agent turn"
metadata:
  {
    "openclaw":
      {
        "emoji": "🏭",
        "events": ["message:sent"],
        "requires": { "bins": ["python3"] },
      },
  }
---

# SFLO Pipeline Hook

Drives the SFLO pipeline in OpenClaw by intercepting outbound messages and checking pipeline state.

## What It Does

1. Fires after every message sent by the agent
2. Checks if `.sflo/state.json` exists in the workspace
3. If pipeline is active (not done/escalate), runs `scaffold.py prompt`
4. Reinjects the next gate instruction as a message to the agent
5. Includes loop protection — if no state progress between fires, stops to prevent infinite loops

## Requirements

- Python 3.8+ on PATH (as `python3`, or set `SFLO_PYTHON` env var)
- SFLO scaffold (`sflo/src/scaffold.py`) in the workspace

## Configuration

No configuration needed. The hook activates automatically when `.sflo/state.json` exists.
