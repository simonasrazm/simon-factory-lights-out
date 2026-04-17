#!/usr/bin/env bash
# SFLO Setup — One-command installation for OpenClaw and Claude Code
#
# Usage:
#   bash setup.sh [--workspace PATH] [--source PATH_OR_URL] [--branch BRANCH]
#
# What this does:
#   1. Copies/clones SFLO into the workspace (or configures in-place)
#   2. Installs the appropriate hook for your runtime
#   3. Creates default bindings.yaml if missing
#   4. Installs the skill (OpenClaw only)
#   5. Writes setup status marker

set -euo pipefail

DEFAULT_REPO="https://github.com/simonasrazm/simon-factory-lights-out.git"
BRANCH="main"
WORKSPACE=""
SOURCE=""
SFLO_DIR_NAME="sflo"

# --- Cross-platform Python detection ---

detect_python() {
  if command -v python3 &>/dev/null; then
    echo "python3"
  elif command -v python &>/dev/null; then
    echo "python"
  else
    echo ""
  fi
}

PYTHON_CMD="$(detect_python)"
if [[ -z "$PYTHON_CMD" ]]; then
  echo "ERROR: Python not found. Install Python 3.8+ and ensure it's on PATH."
  exit 1
fi

# --- Parse args ---

while [[ $# -gt 0 ]]; do
  case $1 in
    --workspace) WORKSPACE="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --sflo-path) SFLO_PATH_OVERRIDE="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "╔══════════════════════════════════════════╗"
echo "║  SFLO — Simon Factory Lights Out Setup   ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# --- Detect runtime ---

RUNTIME="unknown"
# Cursor is checked before claude-code so the native cursor-agent CLI wins
# when both are installed. The user can force a different runtime by
# pre-setting the RUNTIME env var before invoking setup.sh.
if [[ -n "${RUNTIME_OVERRIDE:-}" ]]; then
  RUNTIME="$RUNTIME_OVERRIDE"
elif command -v openclaw &>/dev/null; then
  RUNTIME="openclaw"
elif command -v cursor-agent &>/dev/null || [[ -d ".cursor" ]]; then
  RUNTIME="cursor"
elif command -v claude &>/dev/null || [[ -f ".claude/settings.json" ]]; then
  RUNTIME="claude-code"
fi
echo "Runtime detected: $RUNTIME"

# --- Detect if running from inside SFLO repo ---

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/sflo.md" ]]; then
  SOURCE="$SCRIPT_DIR"
  echo "Source: $SOURCE (local — running from SFLO repo)"
elif [[ -z "$SOURCE" ]]; then
  SOURCE="$DEFAULT_REPO"
  echo "Source: $SOURCE"
else
  echo "Source: $SOURCE"
fi

# --- Resolve workspace ---

if [[ -z "$WORKSPACE" ]]; then
  if [[ "$RUNTIME" == "openclaw" ]]; then
    local_config="$HOME/.openclaw/openclaw.json"
    if [[ -f "$local_config" ]]; then
      WORKSPACE=$("$PYTHON_CMD" -c "
import json
with open('$local_config') as f:
    c = json.load(f)
print(c.get('agents',{}).get('defaults',{}).get('workspace',''))
" 2>/dev/null || true)
    fi
    if [[ -z "$WORKSPACE" ]]; then
      WORKSPACE="$HOME/clawd"
    fi
  else
    WORKSPACE="$(pwd)"
  fi
fi

echo "Workspace: $WORKSPACE"
echo ""

# --- Install SFLO to workspace ---

# If --sflo-path was provided, sflo is already in place — skip copy entirely
if [[ -n "${SFLO_PATH_OVERRIDE:-}" ]]; then
  SFLO_PATH="$SFLO_PATH_OVERRIDE"
  IN_PLACE=true
  echo "SFLO path provided: $SFLO_PATH — skipping copy"
  echo "  ✓ SFLO at $SFLO_PATH"
else

SFLO_PATH="$WORKSPACE/$SFLO_DIR_NAME"
IN_PLACE=false

# Detect in-place install: source IS the target (or its parent is the workspace)
resolve_real() { cd "$1" 2>/dev/null && pwd; }

SOURCE_REAL="$(resolve_real "$SOURCE" || echo "")"
SFLO_REAL="$(resolve_real "$SFLO_PATH" || echo "")"
WORKSPACE_REAL="$(resolve_real "$WORKSPACE" || echo "")"

if [[ -n "$SOURCE_REAL" && -n "$SFLO_REAL" && "$SOURCE_REAL" == "$SFLO_REAL" ]]; then
  # Source and destination are the same directory
  IN_PLACE=true
  echo "Running from SFLO repo inside workspace — configuring in-place (no copy needed)"
elif [[ -n "$SOURCE_REAL" && -n "$WORKSPACE_REAL" && "$SOURCE_REAL" == "$WORKSPACE_REAL" ]]; then
  # Source is the workspace itself (user ran setup.sh from the sflo repo root)
  IN_PLACE=true
  SFLO_PATH="$SOURCE"
  echo "Running from SFLO repo root — configuring in-place (no copy needed)"
elif [[ -n "$SOURCE_REAL" && -n "$SFLO_REAL" && "$SOURCE_REAL" == "$SFLO_REAL"/* ]]; then
  # Source is INSIDE the target (e.g. sflo-dev/sflo/ submodule inside sflo-dev/)
  # Copying would destroy the source. Use source's parent as SFLO_PATH.
  IN_PLACE=true
  SFLO_PATH="$SFLO_REAL"
  echo "Source is inside target directory — configuring in-place (no copy needed)"
elif [[ -n "$SFLO_REAL" && -n "$SOURCE_REAL" && "$SFLO_REAL" == "$SOURCE_REAL"/* ]]; then
  # Target is inside source — same overlap problem in reverse
  IN_PLACE=true
  echo "Target is inside source directory — configuring in-place (no copy needed)"
elif [[ -d "$SOURCE" ]]; then
  # Local source — copy (prefer cp -r for cross-platform, rsync if available)
  if [[ -d "$SFLO_PATH" ]]; then
    echo "Updating SFLO at $SFLO_PATH from local source..."
  else
    echo "Copying SFLO to $SFLO_PATH..."
    mkdir -p "$SFLO_PATH"
  fi

  if command -v rsync &>/dev/null; then
    rsync -a --delete --exclude='.git' --exclude='__pycache__' --exclude='.sflo' "$SOURCE/" "$SFLO_PATH/"
  else
    # Fallback for Windows / systems without rsync
    rm -rf "$SFLO_PATH"
    cp -r "$SOURCE" "$SFLO_PATH"
    rm -rf "$SFLO_PATH/.git" "$SFLO_PATH/__pycache__" "$SFLO_PATH/.sflo"
  fi
elif [[ "$SOURCE" == http* ]]; then
  # Remote source — git clone
  if [[ -d "$SFLO_PATH/.git" ]]; then
    echo "Updating SFLO at $SFLO_PATH from git..."
    git -C "$SFLO_PATH" pull origin "$BRANCH" 2>/dev/null || true
  elif [[ -d "$SFLO_PATH" ]]; then
    echo "SFLO exists at $SFLO_PATH but is not a git repo — skipping clone"
  else
    echo "Cloning SFLO..."
    git clone --branch "$BRANCH" --depth 1 "$SOURCE" "$SFLO_PATH"
  fi
else
  echo "ERROR: Source not found: $SOURCE"
  exit 1
fi
echo "  ✓ SFLO at $SFLO_PATH"

fi  # end of SFLO_PATH_OVERRIDE check

# --- Install hooks ---

echo ""
echo "Installing hooks..."

# Resolve relative hook path from workspace to stop_hook.py
relative_hook_path() {
  local from="$1"
  local to="$2"
  "$PYTHON_CMD" -c "
import os
print(os.path.relpath('$to', '$from'))
" 2>/dev/null
}

if [[ "$RUNTIME" == "openclaw" ]]; then
  HOOK_SRC="$SFLO_PATH/src/hooks/openclaw/sflo-pipeline"
  HOOK_DST="$WORKSPACE/hooks/sflo-pipeline"

  mkdir -p "$WORKSPACE/hooks"

  if [[ -d "$HOOK_SRC" ]]; then
    rm -rf "$HOOK_DST"
    cp -r "$HOOK_SRC" "$HOOK_DST"
    echo "  ✓ Hook copied to $HOOK_DST"
  else
    echo "  ⚠ Hook source not found at $HOOK_SRC"
  fi

  # Enable in config
  CONFIG="$HOME/.openclaw/openclaw.json"
  if [[ -f "$CONFIG" ]]; then
    "$PYTHON_CMD" -c "
import json
with open('$CONFIG') as f:
    config = json.load(f)
hooks = config.setdefault('hooks', {}).setdefault('internal', {}).setdefault('entries', {})
if 'sflo-pipeline' not in hooks:
    hooks['sflo-pipeline'] = {'enabled': True}
    with open('$CONFIG', 'w') as f:
        json.dump(config, f, indent=2)
    print('  ✓ Hook enabled in OpenClaw config')
else:
    print('  ✓ Hook already in config')
" 2>/dev/null || echo "  ⚠ Could not update config — enable sflo-pipeline hook manually"
  fi

elif [[ "$RUNTIME" == "cursor" ]]; then
  # Native Cursor integration: hook + rule. Hooks live in .cursor/hooks.json
  # and Cursor reloads them automatically when the file is saved (no IDE
  # restart needed). Rules live in .cursor/rules/sflo.mdc and apply
  # automatically because we set alwaysApply: true in the front matter.
  CURSOR_DIR="$WORKSPACE/.cursor"
  HOOKS_FILE="$CURSOR_DIR/hooks.json"
  RULES_DIR="$CURSOR_DIR/rules"
  RULE_FILE="$RULES_DIR/sflo.mdc"
  STOP_HOOK_ABS="$SFLO_PATH/src/hooks/cursor/stop_hook.py"
  STOP_HOOK_REL="$(relative_hook_path "$WORKSPACE" "$STOP_HOOK_ABS")"

  mkdir -p "$RULES_DIR"

  HOOK_CMD="$PYTHON_CMD $STOP_HOOK_REL"

  # Cursor hooks.json: merge if exists, create if not. We replace any
  # existing 'stop' entries that point to our stop_hook.py so reruns are
  # idempotent. Other hooks (preToolUse etc.) the user added are preserved.
  "$PYTHON_CMD" - <<PYEOF || echo "  ⚠ Could not write Cursor hooks.json"
import json, os, sys
path = r"$HOOKS_FILE"
hook_cmd = r"$HOOK_CMD"
hook_path = r"$STOP_HOOK_ABS"
data = {"version": 1, "hooks": {}}
if os.path.isfile(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        pass
data.setdefault("version", 1)
hooks = data.setdefault("hooks", {})
stop_list = hooks.get("stop", [])
# Drop any prior SFLO stop entries (match by stop_hook.py substring)
stop_list = [h for h in stop_list if "sflo" not in (h.get("command", "") + "").lower() or "stop_hook" not in h.get("command", "")]
stop_list.insert(0, {"command": hook_cmd, "loop_limit": None})
hooks["stop"] = stop_list
with open(path, "w") as f:
    json.dump(data, f, indent=2)
print("  ✓ .cursor/hooks.json updated (stop hook -> SFLO)")
PYEOF

  RULE_SRC="$SFLO_PATH/src/hooks/cursor/sflo.mdc"
  if [[ -f "$RULE_SRC" ]]; then
    cp "$RULE_SRC" "$RULE_FILE"
    echo "  ✓ .cursor/rules/sflo.mdc installed"
  else
    echo "  ⚠ Cursor rule not found at $RULE_SRC"
  fi

  # Sanity: warn (don't fail) if cursor-agent CLI isn't on PATH. The
  # adapter raises a clear error at first spawn, but installers expect
  # to know now.
  if ! command -v cursor-agent &>/dev/null; then
    echo "  NOTE: cursor-agent CLI not on PATH — install from https://cursor.com/cli"
    echo "        and run 'cursor-agent login' before triggering the pipeline."
  else
    echo "  ✓ cursor-agent CLI detected"
  fi

elif [[ "$RUNTIME" == "claude-code" ]]; then
  SETTINGS_DIR="$WORKSPACE/.claude"
  SETTINGS_FILE="$SETTINGS_DIR/settings.json"
  STOP_HOOK_ABS="$SFLO_PATH/src/hooks/claude-code/stop_hook.py"
  STOP_HOOK_REL="$(relative_hook_path "$WORKSPACE" "$STOP_HOOK_ABS")"

  mkdir -p "$SETTINGS_DIR"

  # Use relative path so settings.json is portable (not machine-specific)
  HOOK_CMD="$PYTHON_CMD $STOP_HOOK_REL"

  if [[ -f "$SETTINGS_FILE" ]]; then
    "$PYTHON_CMD" -c "
import json
with open('$SETTINGS_FILE') as f:
    s = json.load(f)
hooks = s.setdefault('hooks', {})
hooks['Stop'] = [{'type': 'command', 'command': '$HOOK_CMD'}]
# Remove legacy v1 key if present
hooks.pop('stop', None)
with open('$SETTINGS_FILE', 'w') as f:
    json.dump(s, f, indent=2)
print('  ✓ Stop hook configured in .claude/settings.json')
" 2>/dev/null || echo "  ⚠ Could not update settings — configure stop hook manually"
  else
    "$PYTHON_CMD" -c "
import json
settings = {'hooks': {'Stop': [{'type': 'command', 'command': '$HOOK_CMD'}]}}
with open('$SETTINGS_FILE', 'w') as f:
    json.dump(settings, f, indent=2)
print('  ✓ Created .claude/settings.json with Stop hook')
" 2>/dev/null || echo "  ⚠ Could not create settings.json"
  fi
fi

# --- Provision venv for Claude Code (claude-agent-sdk) ---

if [[ "$RUNTIME" == "claude-code" ]]; then
  VENV_DIR="$WORKSPACE/.sflo/.venv"
  if "$PYTHON_CMD" -c "import claude_agent_sdk" 2>/dev/null; then
    echo "  ✓ claude-agent-sdk available"
  else
    echo "  Provisioning venv for claude-agent-sdk..."
    # Find Python 3.10+ (SDK requirement)
    VENV_PYTHON=""
    for candidate in python3.12 python3.11 python3.10 python3 "$PYTHON_CMD"; do
      if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c "import sys; print(sys.version_info >= (3, 10))" 2>/dev/null || echo "False")
        if [[ "$ver" == "True" ]]; then
          VENV_PYTHON="$candidate"
          break
        fi
      fi
    done
    if [[ -n "$VENV_PYTHON" ]]; then
      mkdir -p "$(dirname "$VENV_DIR")"
      "$VENV_PYTHON" -m venv "$VENV_DIR"
      "$VENV_DIR/bin/pip" install -q claude-agent-sdk
      echo "  ✓ venv created at $VENV_DIR with claude-agent-sdk"
      echo "  Note: run the pipeline with $VENV_DIR/bin/python3 sflo/src/runner.py"
    else
      echo "  ⚠ Python 3.10+ not found — claude-agent-sdk requires it. Install manually."
    fi
  fi
fi

# --- Verify bindings exist ---

BINDINGS_FILE="$SFLO_PATH/bindings.yaml"
if [[ ! -f "$BINDINGS_FILE" ]]; then
  echo "  ⚠ bindings.yaml not found at $BINDINGS_FILE — installation may be incomplete"
else
  echo "  ✓ bindings.yaml present"
fi

# --- Install skill (OpenClaw only) ---

if [[ "$RUNTIME" == "openclaw" ]]; then
  SKILL_SRC="$SFLO_PATH/src/hooks/openclaw/skill"
  SKILL_DST="$WORKSPACE/skills/sflo"

  if [[ -d "$SKILL_SRC" ]]; then
    mkdir -p "$WORKSPACE/skills"
    rm -rf "$SKILL_DST"
    mkdir -p "$SKILL_DST"
    cp -r "$SKILL_SRC"/* "$SKILL_DST/"
    # Resolve path placeholders in SKILL.md (cross-platform — no sed -i variance)
    if [[ -f "$SKILL_DST/SKILL.md" ]]; then
      "$PYTHON_CMD" -c "
import sys
p = '$SKILL_DST/SKILL.md'
with open(p) as f:
    content = f.read()
content = content.replace('{{SFLO_PATH}}', '$SFLO_PATH')
with open(p, 'w') as f:
    f.write(content)
"
    fi
    echo "  ✓ Skill installed at $SKILL_DST (paths resolved)"
  fi
fi

# --- Write setup status ---

STATUS_DIR="$WORKSPACE/.sflo"
mkdir -p "$STATUS_DIR"
STATUS_FILE="$STATUS_DIR/.setup-status"
if [[ "$RUNTIME" == "openclaw" ]]; then
  echo "restart_required" > "$STATUS_FILE"
else
  # claude-code hot-reloads settings.json; cursor live-reloads
  # .cursor/hooks.json and .cursor/rules/* — neither needs restart.
  echo "ready" > "$STATUS_FILE"
fi

# --- Final output ---

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  SFLO installed successfully!             ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "SFLO_SETUP_RESULT:{\"ok\":true,\"runtime\":\"$RUNTIME\",\"workspace\":\"$WORKSPACE\",\"sflo_path\":\"$SFLO_PATH\",\"status\":\"$(cat "$STATUS_FILE")\"}"
