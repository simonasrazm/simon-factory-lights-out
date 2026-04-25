#!/usr/bin/env python3
"""SFLO Cursor stop hook — drives the gated pipeline to completion.

Cursor's stop hook protocol (cursor.com/docs/agent/hooks):
    Input  (stdin JSON):
        {
          "status": "completed" | "aborted" | "error",
          "loop_count": <int>,
          "conversation_id": "<uuid>",
          "generation_id": "<uuid>",
          "model": "<name>",
          "cwd": "<absolute path>",        # supplied via env in some versions
          "hook_event_name": "stop",
          "cursor_version": "..."
        }
    Output (stdout JSON):
        {"followup_message": "<next instruction>"}    -> Cursor auto-submits
        {}                                            -> Cursor stops

We use this to keep the SFLO pipeline self-driving inside Cursor:
  - If `.sflo/state.json` exists and the pipeline is mid-flight, ask
    `scaffold.py prompt` for the next reinjectable instruction and return
    it as `followup_message` so Cursor auto-submits the next gate prompt.
  - Terminal states (`done`, `escalate`) emit `{}` so Cursor stops cleanly.
  - Loop protection: track last state in `.sflo/.last_hook_state` and bail
    out if two consecutive fires saw the same state — prevents the same
    failing gate from spinning forever inside Cursor's loop_limit budget.

Cursor's own `loop_limit` (default 5) is a secondary safety net; the
state-progress check is the primary one because the SFLO pipeline can
legitimately need >5 follow-ups across all gates.
"""

import json
import os
import subprocess
import sys


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _emit(obj):
    """Write a JSON response and exit 0."""
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()
    sys.exit(0)


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        _emit({})

    status = hook_input.get("status", "completed")
    try:
        loop_count = int(hook_input.get("loop_count", 0) or 0)
    except (TypeError, ValueError):
        loop_count = 0
    # Cursor doesn't always pass cwd in hook input — fall back to the
    # process cwd which is set to the workspace root for project hooks.
    cwd = hook_input.get("cwd") or os.environ.get("CURSOR_WORKSPACE") or os.getcwd()

    sflo_dir = os.path.join(cwd, ".sflo")
    state_file = os.path.join(sflo_dir, "state.json")
    marker = os.path.join(sflo_dir, ".last_hook_state")

    # No active pipeline — let Cursor stop normally.
    if not os.path.isfile(state_file):
        _emit({})

    # If the agent crashed or the user aborted, don't try to drive forward.
    # The user may want to inspect state, edit artifacts, or re-issue the
    # prompt manually. Respect their stop.
    if status in ("aborted", "error"):
        _safe_remove(marker)
        _emit({})

    try:
        with open(state_file, encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"sflo cursor stop_hook: cannot read state: {e}", file=sys.stderr)
        _emit({})

    current = state.get("current_state", "") or ""

    # Terminal — pipeline is done or escalated. Clear marker and stop.
    if current in ("done", "escalate", ""):
        _safe_remove(marker)
        _emit({})

    # Loop protection — if state hasn't advanced since the last fire AND
    # we're past the first follow-up, stop. The first fire (loop_count==0)
    # always proceeds so the pipeline can kick off after the user's prompt.
    if loop_count > 0 and os.path.isfile(marker):
        try:
            with open(marker, encoding="utf-8") as f:
                last = f.read().strip()
        except OSError:
            last = ""
        if last == current:
            print(
                f"sflo cursor stop_hook: state unchanged ({current!r}) "
                f"across loops, halting reinjection",
                file=sys.stderr,
            )
            _emit({})

    # Resolve scaffold.py — this hook lives at sflo/src/hooks/cursor/, so
    # scaffold.py is two parents up from __file__'s parent.
    src_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    scaffold = os.path.join(src_dir, "scaffold.py")

    try:
        result = subprocess.run(
            [sys.executable, scaffold, "prompt", "--sflo-dir", sflo_dir],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"sflo cursor stop_hook: scaffold call failed: {e}", file=sys.stderr)
        _emit({})

    if result.returncode != 0:
        print(
            f"sflo cursor stop_hook: scaffold exit {result.returncode}: "
            f"{result.stderr.strip()[:400]}",
            file=sys.stderr,
        )
        _emit({})

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"sflo cursor stop_hook: scaffold output not JSON: {e}", file=sys.stderr)
        _emit({})

    if not data.get("ok"):
        _emit({})

    prompt = data.get("prompt") or ""
    if not prompt.strip():
        _emit({})

    # Re-read state after scaffold (it may have transitioned) and persist
    # the post-scaffold state for next-fire loop detection.
    try:
        with open(state_file, encoding="utf-8") as f:
            post_state = json.load(f)
        current_after = post_state.get("current_state", current) or current
    except (json.JSONDecodeError, OSError):
        current_after = current

    try:
        os.makedirs(sflo_dir, exist_ok=True)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(current_after)
    except OSError:
        pass  # marker is best-effort; loop protection degrades gracefully

    _emit({"followup_message": prompt})


if __name__ == "__main__":
    main()
