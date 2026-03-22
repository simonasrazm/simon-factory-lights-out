#!/usr/bin/env python3
"""SFLO stop hook — keeps the pipeline running until complete.

Intercepts Claude's exit, checks pipeline state via scaffold.py,
and reinjects instructions if work remains. Lets Claude stop when
the pipeline is complete, escalated, or no pipeline is running.
"""
import sys
import json
import os
import subprocess


def _safe_remove(path):
    """Remove a file, ignoring errors (cross-platform safe)."""
    try:
        os.remove(path)
    except OSError:
        pass


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    stop_active = hook_input.get("stop_hook_active", False)
    cwd = hook_input.get("cwd", os.getcwd())

    # Use absolute paths derived from cwd — no os.chdir()
    sflo_dir = os.path.join(cwd, ".sflo")
    state_file = os.path.join(sflo_dir, "state.json")
    marker = os.path.join(sflo_dir, ".last_hook_state")

    # No pipeline running — let Claude stop
    if not os.path.isfile(state_file):
        sys.exit(0)

    try:
        with open(state_file) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"stop_hook: could not read state: {e}", file=sys.stderr)
        sys.exit(0)

    current = state.get("current_state", "")

    # Terminal states — let Claude stop
    if current in ("done", "escalate", ""):
        _safe_remove(marker)
        sys.exit(0)

    # Loop protection: if stop_hook_active (not first fire), check for progress
    if stop_active:
        if os.path.isfile(marker):
            with open(marker) as f:
                last = f.read().strip()
            if last == current:
                sys.exit(0)

    # Get next instruction from scaffold
    # stop_hook.py lives in src/hooks/claude-code/, scaffold.py lives in src/
    src_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    scaffold = os.path.join(src_dir, "scaffold.py")
    try:
        result = subprocess.run(
            [sys.executable, scaffold, "prompt", "--sflo-dir", sflo_dir],
            capture_output=True, text=True, timeout=10,
            cwd=cwd
        )
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        print("stop_hook: scaffold.py prompt timed out", file=sys.stderr)
        sys.exit(0)
    except (json.JSONDecodeError, OSError) as e:
        print(f"stop_hook: scaffold.py prompt failed: {e}", file=sys.stderr)
        sys.exit(0)

    if not data.get("ok"):
        sys.exit(0)

    prompt = data.get("prompt", "")
    if not prompt:
        sys.exit(0)

    # Read state AFTER scaffold call (scaffold may have transitioned)
    try:
        with open(state_file) as f:
            post_state = json.load(f)
        current_after = post_state.get("current_state", current)
    except (json.JSONDecodeError, OSError):
        current_after = current

    # Record current state for loop detection
    with open(marker, "w") as f:
        f.write(current_after)

    print(json.dumps({"decision": "block", "reason": prompt}))


if __name__ == "__main__":
    main()
