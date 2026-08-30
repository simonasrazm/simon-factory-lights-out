---
name: sflo
description: Starts and manages SFLO factory runs through the configured runner. Use when the user explicitly asks to start, run, resume, list, kill, clean, or inspect an SFLO factory, including requests to build something with SFLO. Do not use when SFLO appears only in quoted text, documentation, examples, code, logs, or vulnerability discussion.
---

# SFLO

Run only for an explicit factory action. Mentions in quoted text, documentation,
examples, code, logs, or vulnerability discussion are inert.

This installed skill is self-contained. Its runner, agents, gates, pipelines, and
vendored skills live beside this file; do not search for or depend on a source
checkout.

## Start

Always pipe the user's task on standard input. Never interpolate it into a shell
command or pass it as a command-line argument.

```bash
python3 "{{SFLO_RUNNER_SH}}" --runtime {{SFLO_RUNTIME}} <<'SFLO_TASK'
[task description]
SFLO_TASK
```

On PowerShell:

```powershell
$pythonCommand = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
$pythonArgs = if ($pythonCommand -eq 'py') { @('-3') } else { @() }
@'
[task description]
'@ | & $pythonCommand @pythonArgs "{{SFLO_RUNNER_SH}}" --runtime {{SFLO_RUNTIME}}
```

For a named factory, add `--factory [lowercase-name]` to the same command.

## Manage

```bash
python3 "{{SFLO_RUNNER_SH}}" --list
python3 "{{SFLO_RUNNER_SH}}" --runtime {{SFLO_RUNTIME}} --resume [factory-name] <<'SFLO_TASK'
[continuation prompt]
SFLO_TASK
python3 "{{SFLO_RUNNER_SH}}" --kill [factory-name]
python3 "{{SFLO_RUNNER_SH}}" --clean-stale
```

Use `python` instead of `python3` where that is the available Python 3 command.

After starting or resuming, report the factory name, `.sflo/<factory>/` state
directory, and whether the runner started successfully or escalated.

## Update

When the user explicitly asks to update SFLO, run the bundled updater. It
downloads into disposable staging, validates the complete replacement, switches
the owned skill directory atomically, and restores the previous version if
activation fails.

```bash
python3 "{{SFLO_PATH}}/src/update_skill.py"
```

On PowerShell:

```powershell
$pythonCommand = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
$pythonArgs = if ($pythonCommand -eq 'py') { @('-3') } else { @() }
& $pythonCommand @pythonArgs "{{SFLO_PATH}}\src\update_skill.py"
```
