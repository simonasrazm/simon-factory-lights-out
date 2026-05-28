---
name: sflo
description: "Build products using the SFLO pipeline — a gated PM→Dev→QA process. Use when user says SFLO or asks to install/download SFLO."
metadata:
  { "openclaw": { "emoji": "🏭", "requires": { "bins": ["python3"] } } }
---

# SFLO — Simon Factory Lights Out

## Installation

When user asks to install or download SFLO:

1. Determine the source:
   - If user provides a GitHub URL → use --source URL
   - If user provides a local path → use --source PATH
   - If no source given → script uses default GitHub repo

2. Run setup:
   ```bash
   bash setup.sh --runtime openclaw --install-dir <install_dir> --source <source>
   ```

3. Parse the last line of output for SFLO_SETUP_RESULT JSON

4. Check the status field:
   - "restart_required" → Tell the user: "SFLO installed. The gateway needs a restart to activate the pipeline hook. Shall I restart?"
   - "ready" → Tell the user: "SFLO installed and ready. Say SFLO: followed by what you want to build."

## Running the Pipeline

When user says "SFLO: [description]":

1. Detect Python (`python3` or `python` — whichever is available)
2. Run:
   ```bash
   printf '%s\n' "[description]" | <python> {{SFLO_PATH}}/src/runner.py --runtime openclaw
   ```
3. The runner handles the gated pipeline automatically
4. The hook keeps the pipeline running after each gate

## Key Commands

```bash
printf '%s\n' "[description]" | <python> {{SFLO_PATH}}/src/runner.py --runtime openclaw  # Start pipeline
<python> {{SFLO_PATH}}/src/runner.py --help                                               # Show runner help
<python> {{SFLO_PATH}}/src/scaffold.py status                                              # Advanced: show pipeline state
<python> {{SFLO_PATH}}/src/scaffold.py next                                                # Advanced: get next action
<python> {{SFLO_PATH}}/src/scaffold.py prompt                                              # Advanced: get reinjectable instruction
```

Where `<python>` is `python3` (macOS/Linux) or `python` (Windows).
