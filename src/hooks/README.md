# SFLO Hooks

Hooks keep the SFLO pipeline running automatically in host UIs that emit one
assistant turn at a time. Without a host continuation hook, Claude Code, Cursor,
and OpenClaw can finish the current assistant message while an active factory
state file still points at the next gate. The hook reads the active state
directory, usually `.sflo/<factory>/state.json`, and injects the next prompt.
This is not a long-running-process watchdog; the only hook timeout is a short
guard around `scaffold.py prompt`.

## Supported Runtimes

| Runtime | Hook | How it works |
|---------|------|-------------|
| **OpenClaw** | `openclaw/sflo-pipeline/` | Fires on `message:sent` events. Checks pipeline state, reinjects next instruction. |
| **Claude Code** | `claude-code/stop_hook.py` | Intercepts exit. Checks pipeline state, blocks exit with next instruction. |
| **Cursor** | `cursor/stop_hook.py` | Returns `followup_message` from the `stop` hook so Cursor auto-submits the next gate. See `cursor/README.md`. |
| **Codex** | none | Runner-driven through `codex exec`; no host continuation hook is installed. |
| **Ollama** | none | Adapter/API-driven; no host continuation hook is installed. The hook installer does not manage Ollama. |

## Quick Install

```bash
bash sflo/src/hooks/install.sh --runtime cursor
```

Pass the runtime explicitly to the hook installer. Normal installs should use `setup.sh`; this helper exists for hook-only repair or manual integration.

### Manual Install: OpenClaw

1. Copy the hook into your install directory:
   ```bash
   cp -r /path/to/sflo/src/hooks/openclaw/sflo-pipeline /path/to/install-dir/hooks/sflo-pipeline
   ```

2. Enable in `~/.openclaw/openclaw.json`:
   ```json
   {
     "hooks": {
       "internal": {
         "entries": {
           "sflo-pipeline": { "enabled": true }
         }
       }
     }
   }
   ```

3. Restart the gateway (full restart required — SIGUSR1 does not load new hooks):
   ```bash
   openclaw gateway stop && openclaw gateway start
   ```

### Manual Install: Claude Code

Add to `.claude/settings.json` in your project root:
```json
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "python3 \"/path/to/sflo/src/hooks/claude-code/stop_hook.py\""
      }
    ]
  }
}
```

> **Windows:** Use `python` instead of `python3` and backslash paths:
> `"command": "python \"C:\\path\\to\\stop_hook.py\""`

## How It Works

The Claude Code and Cursor hooks follow the same logic:

1. Find active state: `.sflo/<factory>/state.json` via `registry.json`, or legacy `.sflo/state.json`
2. Check if pipeline is in a terminal state (`done` or `escalate`) — let the agent stop
3. Run `scaffold.py prompt` to get the next instruction
4. Reinject the instruction so the agent continues

### Loop Protection

The Claude Code and Cursor hooks track the last pipeline state they acted on inside the active state directory (`.sflo/<factory>/.last_hook_state`). If the state hasn't changed between fires, the hook stops — preventing infinite reinjection loops.

**Claude Code specifics:** The hook receives `stop_hook_active` in its input — this is `true` when the hook has already blocked at least once in the current turn. When `true`, the hook checks `.last_hook_state` for progress. When `false` (first fire), loop protection is skipped to allow the initial block.

### Cross-Platform

The Python hooks work on macOS, Linux, and Windows where their host runtime runs:
- `stop_hook.py` uses `sys.executable` to call scaffold.py (no hardcoded `python3`)
- `os.path.join` handles path separators
- File locking uses `msvcrt` on Windows, `fcntl` on Unix (in scaffold modules)

The OpenClaw hook (`handler.ts`) uses `python3` in its exec call — on Windows, ensure `python3` is on PATH or set `SFLO_PYTHON` env var.

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `SFLO_WORKSPACE` | Explicit run workspace for the OpenClaw hook when the event has no workspace path | unset |

## Troubleshooting

**Hook doesn't fire (OpenClaw):**
- Check `openclaw hooks list` — is `sflo-pipeline` registered?
- Check config — is it enabled?
- Full restart required after first install (`openclaw gateway stop && openclaw gateway start`)

**Pipeline stops mid-way:**
- Check `.sflo/<factory>/.last_hook_state` — loop protection may have triggered
- Delete `.sflo/<factory>/.last_hook_state` and send any message to re-trigger

**Hook fires but pipeline doesn't advance:**
- Check `python3 sflo/src/scaffold.py status` — what state is the pipeline in?
- Check if the expected artifact exists in `.sflo/<factory>/`
