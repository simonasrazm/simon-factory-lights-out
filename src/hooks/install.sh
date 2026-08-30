#!/usr/bin/env bash
# SFLO Hook Installer
# Installs the hook for the explicitly selected runtime.
#
# Usage:
#   bash sflo/src/hooks/install.sh --runtime <openclaw|cursor|claude-code> [--install-dir PATH]
#
# Supported runtimes:
#   - OpenClaw: links the hook into <install-dir>/hooks/ and enables it
#   - Cursor: repairs the project-local .cursor/hooks.json continuation hook
#   - Claude Code: configures stop hook in .claude/settings.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SFLO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Parse args
RUNTIME=""
INSTALL_DIR=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --runtime) RUNTIME="$2"; shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -z "$RUNTIME" ]]; then
  echo "ERROR: --runtime is required. Pass one of: openclaw, cursor, claude-code"
  exit 1
fi

echo "SFLO Hook Installer"
echo "  Runtime: $RUNTIME"
echo "  SFLO root: $SFLO_ROOT"

detect_python() {
  if command -v python3 &>/dev/null; then
    echo "python3"
  else
    echo "python"
  fi
}

PYTHON_CMD="$(detect_python)"

default_install_dir() {
  if [[ -z "$INSTALL_DIR" ]]; then
    INSTALL_DIR="$(pwd)"
  fi
}

shell_quote() {
  "$PYTHON_CMD" - "$1" <<'PYEOF'
import shlex
import sys

print(shlex.quote(sys.argv[1]))
PYEOF
}

install_runtime_pipeline() {
  local src="$1"
  local label="$2"
  local dst="$INSTALL_DIR/pipeline.yaml"
  local marker="$INSTALL_DIR/.sflo/pipeline.yaml.managed"
  local proposed="$INSTALL_DIR/pipeline.yaml.sflo-default"

  if [[ ! -f "$src" ]]; then
    echo "  ERROR: $label pipeline source not found at $src" >&2
    exit 1
  fi

  mkdir -p "$INSTALL_DIR" "$(dirname "$marker")"
  if [[ ! -f "$dst" ]]; then
    cp "$src" "$dst"
    cp "$src" "$marker"
    rm -f "$proposed"
    echo "  $label pipeline installed at $dst"
  elif cmp -s "$src" "$dst"; then
    cp "$src" "$marker"
    rm -f "$proposed"
    echo "  $label pipeline already current at $dst"
  elif [[ -f "$marker" ]] && cmp -s "$dst" "$marker"; then
    cp "$src" "$dst"
    cp "$src" "$marker"
    rm -f "$proposed"
    echo "  $label managed pipeline updated at $dst"
  else
    cp "$src" "$proposed"
    echo "  Existing project pipeline preserved at $dst; new SFLO defaults written to $proposed"
  fi
}

# --- OpenClaw ---

install_openclaw() {
  default_install_dir

  echo "  Install dir: $INSTALL_DIR"

  local hook_src="$SCRIPT_DIR/openclaw/sflo-pipeline"
  local hook_dst="$INSTALL_DIR/hooks/sflo-pipeline"

  if [[ ! -d "$hook_src" ]]; then
    echo "ERROR: Hook source not found at $hook_src"
    exit 1
  fi

  # Create hooks dir if needed
  mkdir -p "$INSTALL_DIR/hooks"

  # Replace any prior installed hook.
  if [[ -L "$hook_dst" || -d "$hook_dst" ]]; then
    echo "  Hook already exists at $hook_dst — replacing"
    rm -rf "$hook_dst"
  fi

  ln -s "$hook_src" "$hook_dst"
  echo "  Linked: $hook_src -> $hook_dst"

  # Enable in OpenClaw config
  if command -v openclaw &>/dev/null; then
    echo "  Enabling hook in OpenClaw config..."
    # Use openclaw CLI if available, otherwise manual patch
    local config="$HOME/.openclaw/openclaw.json"
    if [[ -f "$config" ]]; then
      "$PYTHON_CMD" -c "
import json

config_path = '$config'
with open(config_path) as f:
    config = json.load(f)

hooks = config.setdefault('hooks', {}).setdefault('internal', {}).setdefault('entries', {})
hooks['sflo-pipeline'] = {'enabled': True}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)

print('  Config updated: hooks.internal.entries.sflo-pipeline.enabled = true')
" 2>/dev/null || echo "  WARNING: Could not update config automatically. Enable manually."
    fi

    echo ""
    echo "  IMPORTANT: Restart the gateway to load the hook:"
    echo "    openclaw gateway restart"
    echo ""
    echo "  Note: SIGUSR1 restart does NOT load new hooks."
    echo "  If 'openclaw gateway restart' doesn't work, do:"
    echo "    openclaw gateway stop && openclaw gateway start"
  fi

  echo "  OpenClaw hook installed successfully."
}

# --- Cursor ---

install_cursor() {
  default_install_dir

  local stop_hook="$SCRIPT_DIR/cursor/stop_hook.py"
  local cursor_dir="$INSTALL_DIR/.cursor"
  local rules_dir="$cursor_dir/rules"
  local hooks_file="$cursor_dir/hooks.json"
  local hook_command="$PYTHON_CMD $(shell_quote "$stop_hook")"

  if [[ ! -f "$stop_hook" ]]; then
    echo "ERROR: Cursor stop hook not found at $stop_hook"
    exit 1
  fi

  mkdir -p "$cursor_dir"
  "$PYTHON_CMD" - "$hooks_file" "$hook_command" <<'PYEOF'
import json, os, sys

path, hook_cmd = sys.argv[1], sys.argv[2]
data = {"version": 1, "hooks": {}}
if os.path.isfile(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass
data.setdefault("version", 1)
hooks = data.setdefault("hooks", {})
stop_list = [
    h for h in hooks.get("stop", [])
    if "src/hooks/cursor/stop_hook.py"
    not in str(h.get("command", "")).replace("\\", "/")
]
stop_list.insert(0, {"command": hook_cmd, "loop_limit": None})
hooks["stop"] = stop_list
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
PYEOF
  install_runtime_pipeline "$SFLO_ROOT/pipeline-cursor.yaml" "Cursor"
  rm -f "$rules_dir/sflo.mdc" "$rules_dir/sflo-factory-triggering.mdc"
  rmdir "$rules_dir" 2>/dev/null || true
  echo "  Cursor continuation hook repaired successfully."
}

# --- Claude Code ---

install_claude_code() {
  default_install_dir

  local stop_hook="$SCRIPT_DIR/claude-code/stop_hook.py"

  if [[ ! -f "$stop_hook" ]]; then
    echo "ERROR: stop_hook.py not found at $stop_hook"
    exit 1
  fi

  local settings_dir="$INSTALL_DIR/.claude"
  local settings_file="$settings_dir/settings.json"

  mkdir -p "$settings_dir"

  # Build the command string (use python3 on Unix, python on Windows/Git Bash)
  local py_cmd
  if command -v python3 &>/dev/null; then
    py_cmd="python3"
  else
    py_cmd="python"
  fi
  local hook_command="$py_cmd $(shell_quote "$stop_hook")"

  "$py_cmd" - "$settings_file" "$hook_command" <<'PYEOF' || {
import json
import os
import sys

settings_file, hook_command = sys.argv[1], sys.argv[2]
settings = {}
if os.path.isfile(settings_file):
    with open(settings_file, encoding="utf-8") as f:
        existing = f.read().strip()
    if existing:
        settings = json.loads(existing)
hooks = settings.setdefault("hooks", {})
existing = list(hooks.get("Stop", [])) + list(hooks.get("stop", []))
hooks["Stop"] = [{"type": "command", "command": hook_command}] + [
    entry
    for entry in existing
    if "src/hooks/claude-code/stop_hook.py"
    not in str(entry.get("command", "")).replace("\\", "/")
]
hooks.pop("stop", None)
os.makedirs(os.path.dirname(settings_file), exist_ok=True)
with open(settings_file, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
print(f"  Updated {settings_file} with Stop hook")
PYEOF
    echo "ERROR: Could not update $settings_file" >&2
    exit 1
  }

  echo "  Claude Code hook installed successfully."
}

# --- Dispatch ---

case "$RUNTIME" in
  openclaw)
    install_openclaw
    ;;
  cursor)
    install_cursor
    ;;
  claude-code)
    install_claude_code
    ;;
  *)
    echo "ERROR: unknown --runtime '$RUNTIME'. Valid: openclaw, cursor, claude-code"
    exit 1
    ;;
esac
