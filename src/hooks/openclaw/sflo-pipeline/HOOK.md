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
2. Checks for active SFLO state in `.sflo/<factory>/state.json` (or legacy `.sflo/state.json`)
3. If pipeline is active (not done/escalate), runs `scaffold.py prompt`
4. Reinjects the next gate instruction as a message to the agent
5. Includes loop protection — if no state progress between fires, stops to prevent infinite loops

## Requirements

- Python 3.8+ on PATH (as `python3`, or set `SFLO_PYTHON` env var)
- SFLO installed by `setup.sh`. The installer records the SFLO home path for the hook; set `SFLO_HOME` only if you move it later.

## Configuration

No configuration needed. The hook activates automatically when active SFLO state exists.
