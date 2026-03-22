#!/usr/bin/env bash
# SFLO Hook Installer
# Detects the runtime environment and installs the appropriate hook.
#
# Usage:
#   bash sflo/src/hooks/install.sh [--workspace PATH]
#
# Supported runtimes:
#   - OpenClaw: symlinks hook to <workspace>/hooks/ and enables in config
#   - Claude Code: configures stop hook in .claude/settings.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SFLO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Parse args
WORKSPACE=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --workspace) WORKSPACE="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# --- Detect runtime ---

detect_runtime() {
  if command -v openclaw &>/dev/null; then
    echo "openclaw"
  elif [[ -f "${SFLO_ROOT}/.claude/settings.json" ]] || command -v claude &>/dev/null; then
    echo "claude-code"
  else
    echo "unknown"
  fi
}

RUNTIME=$(detect_runtime)
echo "SFLO Hook Installer"
echo "  Runtime: $RUNTIME"
echo "  SFLO root: $SFLO_ROOT"

# --- OpenClaw ---

install_openclaw() {
  # Resolve workspace
  if [[ -z "$WORKSPACE" ]]; then
    # Try to read from OpenClaw config
    local config="$HOME/.openclaw/openclaw.json"
    if [[ -f "$config" ]]; then
      WORKSPACE=$(python3 -c "
import json
with open('$config') as f:
    c = json.load(f)
w = c.get('agents',{}).get('defaults',{}).get('workspace','')
print(w)
" 2>/dev/null || true)
    fi
    # Fallback
    if [[ -z "$WORKSPACE" ]]; then
      WORKSPACE="$HOME/clawd"
    fi
  fi

  echo "  Workspace: $WORKSPACE"

  local hook_src="$SCRIPT_DIR/openclaw/sflo-pipeline"
  local hook_dst="$WORKSPACE/hooks/sflo-pipeline"

  if [[ ! -d "$hook_src" ]]; then
    echo "ERROR: Hook source not found at $hook_src"
    exit 1
  fi

  # Create hooks dir if needed
  mkdir -p "$WORKSPACE/hooks"

  # Symlink or copy
  if [[ -L "$hook_dst" || -d "$hook_dst" ]]; then
    echo "  Hook already exists at $hook_dst — updating symlink"
    rm -rf "$hook_dst"
  fi

  cp -r "$hook_src" "$hook_dst"
  echo "  Symlinked: $hook_dst -> $hook_src"

  # Enable in OpenClaw config
  if command -v openclaw &>/dev/null; then
    echo "  Enabling hook in OpenClaw config..."
    # Use openclaw CLI if available, otherwise manual patch
    local config="$HOME/.openclaw/openclaw.json"
    if [[ -f "$config" ]]; then
      python3 -c "
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

# --- Claude Code ---

install_claude_code() {
  local stop_hook="$SCRIPT_DIR/claude-code/stop_hook.py"

  if [[ ! -f "$stop_hook" ]]; then
    echo "ERROR: stop_hook.py not found at $stop_hook"
    exit 1
  fi

  local settings_dir="$SFLO_ROOT/.claude"
  local settings_file="$settings_dir/settings.json"

  mkdir -p "$settings_dir"

  # Build the command string (use python3 on Unix, python on Windows/Git Bash)
  local py_cmd
  if command -v python3 &>/dev/null; then
    py_cmd="python3"
  else
    py_cmd="python"
  fi
  local hook_command="$py_cmd \"$stop_hook\""

  # Create or update settings.json with stop hook (correct Claude Code format)
  if [[ -f "$settings_file" ]]; then
    "$py_cmd" -c "
import json, sys
with open('$settings_file') as f:
    settings = json.load(f)
hooks = settings.setdefault('hooks', {})
hooks['Stop'] = [{'type': 'command', 'command': '$hook_command'}]
with open('$settings_file', 'w') as f:
    json.dump(settings, f, indent=2)
print('  Updated $settings_file with Stop hook')
" 2>/dev/null || echo "  WARNING: Could not update settings automatically."
  else
    cat > "$settings_file" << SETTINGSEOF
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "$hook_command"
      }
    ]
  }
}
SETTINGSEOF
    echo "  Created $settings_file with Stop hook"
  fi

  echo "  Claude Code hook installed successfully."
}

# --- Dispatch ---

case "$RUNTIME" in
  openclaw)
    install_openclaw
    ;;
  claude-code)
    install_claude_code
    ;;
  unknown)
    echo ""
    echo "Could not detect runtime. Install manually:"
    echo ""
    echo "  For OpenClaw:"
    echo "    cp -r $SCRIPT_DIR/openclaw/sflo-pipeline <workspace>/hooks/sflo-pipeline"
    echo "    # Enable in ~/.openclaw/openclaw.json under hooks.internal.entries"
    echo "    # Restart gateway"
    echo ""
    echo "  For Claude Code:"
    echo "    Add to .claude/settings.json:"
    echo '    {"hooks": {"Stop": [{"type": "command", "command": "python3 \"'$SCRIPT_DIR'/claude-code/stop_hook.py\""}]}}'
    ;;
esac
