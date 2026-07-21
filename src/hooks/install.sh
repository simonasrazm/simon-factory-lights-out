#!/usr/bin/env bash
# SFLO Hook Installer
# Installs the hook for the explicitly selected runtime.
#
# Usage:
#   bash sflo/src/hooks/install.sh --runtime <openclaw|cursor|claude-code> [--install-dir PATH]
#
# Supported runtimes:
#   - OpenClaw: copies hook to <install-dir>/hooks/ and enables in config
#   - Cursor: configures .cursor/hooks.json and installs a global Cursor skill
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

render_template_file() {
  local src="$1"
  local dst="$2"

  "$PYTHON_CMD" - "$src" "$dst" "$SFLO_ROOT" <<'PYEOF'
import os
import shlex
import sys

src, dst, sflo_root = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, encoding="utf-8") as f:
    content = f.read()
content = content.replace("{{SFLO_PATH}}", sflo_root)
content = content.replace(
    "{{SFLO_RUNNER_SH}}",
    shlex.quote(os.path.join(sflo_root, "src", "runner.py")),
)
content = content.replace(
    "{{SFLO_SCAFFOLD_SH}}",
    shlex.quote(os.path.join(sflo_root, "src", "scaffold.py")),
)
content = content.replace(
    "{{SFLO_CURSOR_STOP_HOOK_SH}}",
    shlex.quote(os.path.join(sflo_root, "src", "hooks", "cursor", "stop_hook.py")),
)
os.makedirs(os.path.dirname(dst), exist_ok=True)
with open(dst, "w", encoding="utf-8") as f:
    f.write(content)
PYEOF
}

shell_quote() {
  "$PYTHON_CMD" - "$1" <<'PYEOF'
import shlex
import sys

print(shlex.quote(sys.argv[1]))
PYEOF
}

install_skill_dir() {
  local src="$1"
  local dst="$2"

  if [[ ! -d "$src" ]]; then
    echo "ERROR: SFLO skill source not found at $src"
    exit 1
  fi

  rm -rf "$dst"
  mkdir -p "$dst"
  cp -r "$src"/* "$dst/"
  if [[ -f "$dst/SKILL.md" ]]; then
    render_template_file "$dst/SKILL.md" "$dst/SKILL.md"
  fi
}

is_sflo_skill_dir() {
  local dir="$1"

  [[ -f "$dir/.sflo-owned" ]] && return 0
  [[ -f "$dir/SKILL.md" ]] && grep -q "SFLO Factory Triggering" "$dir/SKILL.md"
}

install_owned_skill_dir() {
  local src="$1"
  local dst="$2"

  if [[ -e "$dst" ]]; then
    if ! is_sflo_skill_dir "$dst"; then
      echo "ERROR: Cursor skill already exists at $dst and is not SFLO-owned"
      exit 1
    fi
    rm -rf "$dst"
  fi

  install_skill_dir "$src" "$dst"
  printf '%s\n' "sflo" > "$dst/.sflo-owned"
}

remove_owned_skill_dir() {
  local dir="$1"

  [[ -e "$dir" ]] || return 0
  if is_sflo_skill_dir "$dir"; then
    rm -rf "$dir"
  else
    echo "  Leaving non-SFLO skill directory untouched: $dir"
  fi
}

install_runtime_pipeline() {
  local src="$1"
  local label="$2"
  local dst="$INSTALL_DIR/pipeline.yaml"

  if [[ ! -f "$src" ]]; then
    echo "  ERROR: $label pipeline source not found at $src" >&2
    exit 1
  fi

  mkdir -p "$INSTALL_DIR"
  if [[ -f "$dst" ]] && ! cmp -s "$src" "$dst"; then
    cp "$dst" "$dst.bak"
    echo "  Existing pipeline.yaml backed up to $dst.bak"
  fi
  cp "$src" "$dst"
  echo "  $label pipeline installed at $dst"
}

cursor_skill_roots() {
  if [[ -n "${CURSOR_SKILLS_DIR:-}" ]]; then
    printf '%s\n' "$CURSOR_SKILLS_DIR"
    return
  fi

  local cursor_home="${CURSOR_HOME:-$HOME/.cursor}"
  printf '%s\n' "$cursor_home/skills"
  if [[ -d "$cursor_home/skills-cursor" ]]; then
    printf '%s\n' "$cursor_home/skills-cursor"
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

  cp -r "$hook_src" "$hook_dst"
  printf '%s\n' "$SFLO_ROOT" > "$hook_dst/.sflo-home"
  echo "  Copied: $hook_src -> $hook_dst"

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
  local skill_src="$SCRIPT_DIR/cursor/skills/sflo"
  local cursor_dir="$INSTALL_DIR/.cursor"
  local rules_dir="$cursor_dir/rules"
  local hooks_file="$cursor_dir/hooks.json"
  local hook_command="$PYTHON_CMD $(shell_quote "$stop_hook")"

  if [[ ! -f "$stop_hook" ]]; then
    echo "ERROR: Cursor stop hook not found at $stop_hook"
    exit 1
  fi
  if [[ ! -d "$skill_src" ]]; then
    echo "ERROR: Cursor factory-triggering skill not found at $skill_src"
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
    if "stop_hook.py" not in str(h.get("command", ""))
]
stop_list.insert(0, {"command": hook_cmd, "loop_limit": None})
hooks["stop"] = stop_list
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
PYEOF
  while IFS= read -r skills_root; do
    [[ -n "$skills_root" ]] || continue
    install_owned_skill_dir "$skill_src" "$skills_root/sflo"
    remove_owned_skill_dir "$skills_root/sflo-factory-triggering"
  done < <(cursor_skill_roots)
  install_runtime_pipeline "$SFLO_ROOT/pipeline-cursor.yaml" "Cursor"
  rm -f "$rules_dir/sflo.mdc" "$rules_dir/sflo-factory-triggering.mdc"
  rmdir "$rules_dir" 2>/dev/null || true
  echo "  Cursor hook and global skill installed successfully."
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
hooks["Stop"] = [{"type": "command", "command": hook_command}]
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
