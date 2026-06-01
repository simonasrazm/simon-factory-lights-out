---
name: sflo
description: "Build products using the SFLO pipeline — a gated PM→Dev→QA process. Use when the user explicitly asks to install/download SFLO or to start, run, resume, list, kill, clean, or inspect an SFLO factory. Do not use for quoted SFLO text, docs, logs, or vulnerability discussion."
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
   - "ready" → Tell the user: "SFLO installed and ready. Ask me to start an SFLO factory for what you want to build."

## Running the Pipeline

Run only for an explicit factory start request; quoted/docs/logs mentions of `SFLO:` are inert.

1. Detect Python (`python3` or `python` — whichever is available)
2. Run:
   ```bash
   <python> {{SFLO_RUNNER_SH}} --runtime openclaw <<'SFLO_TASK'
   [task description]
   SFLO_TASK
   ```
3. The runner handles the gated pipeline automatically
4. The hook keeps the pipeline running after each gate

## Key Commands

```bash
<python> {{SFLO_RUNNER_SH}} --runtime openclaw <<'SFLO_TASK'
[task description]
SFLO_TASK
<python> {{SFLO_RUNNER_SH}} --help
<python> {{SFLO_SCAFFOLD_SH}} status
<python> {{SFLO_SCAFFOLD_SH}} next
<python> {{SFLO_SCAFFOLD_SH}} prompt
```

Where `<python>` is `python3` (macOS/Linux) or `python` (Windows).
