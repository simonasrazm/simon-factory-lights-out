# SFLO Hooks

Hooks keep the SFLO pipeline running automatically. Without hooks, the pipeline stops after each gate and you must manually trigger the next step. With hooks, the pipeline drives itself until completion or escalation.

## Supported Runtimes

| Runtime | Hook | How it works |
|---------|------|-------------|
| **OpenClaw** | `openclaw/sflo-pipeline/` | Fires on `message:sent` events. Checks pipeline state, reinjects next instruction. |
| **Claude Code** | `claude-code/stop_hook.py` | Intercepts exit. Checks pipeline state, blocks exit with next instruction. |
| **Cursor** | `cursor/stop_hook.py` | Returns `followup_message` from the `stop` hook so Cursor auto-submits the next gate. See `cursor/README.md`. |

## Quick Install

```bash
bash sflo/src/hooks/install.sh
```

The installer auto-detects your runtime (OpenClaw or Claude Code) and configures the hook.

### Manual Install: OpenClaw

1. Symlink the hook into your workspace:
   ```bash
   cp -r /path/to/sflo/src/hooks/openclaw/sflo-pipeline ~/clawd/hooks/sflo-pipeline
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

Both hooks follow the same logic:

1. Check if `.sflo/state.json` exists — no file means no active pipeline, skip
2. Check if pipeline is in a terminal state (`done` or `escalate`) — let the agent stop
3. Run `scaffold.py prompt` to get the next instruction
4. Reinject the instruction so the agent continues

### Loop Protection

Both hooks track the last pipeline state they acted on (`.sflo/.last_hook_state`). If the state hasn't changed between fires, the hook stops — preventing infinite reinjection loops.

**Claude Code specifics:** The hook receives `stop_hook_active` in its input — this is `true` when the hook has already blocked at least once in the current turn. When `true`, the hook checks `.last_hook_state` for progress. When `false` (first fire), loop protection is skipped to allow the initial block.

### Cross-Platform

Both hooks work on macOS, Linux, and Windows — anywhere Claude Code runs:
- `stop_hook.py` uses `sys.executable` to call scaffold.py (no hardcoded `python3`)
- `os.path.join` handles path separators
- File locking uses `msvcrt` on Windows, `fcntl` on Unix (in scaffold modules)

The OpenClaw hook (`handler.ts`) uses `python3` in its exec call — on Windows, ensure `python3` is on PATH or set `SFLO_PYTHON` env var.

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `SFLO_WORKSPACE` | Override workspace path for the OpenClaw hook | Auto-detected from HOME |

## Troubleshooting

**Hook doesn't fire (OpenClaw):**
- Check `openclaw hooks list` — is `sflo-pipeline` registered?
- Check config — is it enabled?
- Full restart required after first install (`openclaw gateway stop && openclaw gateway start`)

**Pipeline stops mid-way:**
- Check `.sflo/.last_hook_state` — loop protection may have triggered
- Delete `.sflo/.last_hook_state` and send any message to re-trigger

**Hook fires but pipeline doesn't advance:**
- Check `python3 sflo/src/scaffold.py status` — what state is the pipeline in?
- Check if the expected artifact exists in `.sflo/`
