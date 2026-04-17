# SFLO Cursor Hooks

Native Cursor support for the SFLO gated pipeline. Cursor's hooks API is a
better fit than Claude Code's: the `stop` hook can return a structured
`followup_message` that Cursor auto-submits, so SFLO drives the agent
through every gate without any "decision: block" workaround.

## What's installed

| File | Purpose |
|------|---------|
| `stop_hook.py` | Reads `.sflo/state.json`, asks `scaffold.py` for the next instruction, returns it as `followup_message` so Cursor auto-submits the next gate. |
| `hooks.json.template` | The `.cursor/hooks.json` snippet `setup.sh` writes into your workspace. |

## How it fires

1. The user types `SFLO: build a click counter` in Cursor's agent chat.
2. The `.cursor/rules/sflo.mdc` rule tells Cursor to invoke `python sflo/src/runner.py` (or directly run gate 1, depending on configuration) which writes `.sflo/state.json`.
3. When Cursor's response ends, the `stop` hook fires.
4. The hook reads state, asks `scaffold.py prompt` for the next gate's instruction, and returns `{"followup_message": "<that instruction>"}`.
5. Cursor auto-submits the message — gate 2 runs.
6. Repeat until `state.current_state == "done"` (hook returns `{}`, Cursor stops).

## Loop protection

Two safety layers, in order:

1. **State-progress check** (primary) — `.sflo/.last_hook_state` records the pipeline state after each fire. If the next fire sees the same state, the hook stops returning a follow-up. This catches gates that fail validation repeatedly.
2. **Cursor's `loop_limit`** (secondary) — we set `"loop_limit": null` in the template so Cursor doesn't cap us at the default 5. SFLO can need 15–30 follow-ups across all gates.

## Aborts and errors

If Cursor reports `status: "aborted"` or `status: "error"`, the hook returns `{}` and clears the marker. The user can inspect `.sflo/`, edit artifacts, and re-issue the prompt to resume.

## Manual install (if `setup.sh` didn't run)

Add to `.cursor/hooks.json` in your workspace (replace the path):

```json
{
  "version": 1,
  "hooks": {
    "stop": [
      {
        "command": "python /absolute/path/to/sflo/src/hooks/cursor/stop_hook.py",
        "loop_limit": null
      }
    ]
  }
}
```

Cursor watches this file and reloads it automatically — no restart needed.
