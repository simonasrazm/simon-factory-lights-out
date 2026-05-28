#!/usr/bin/env bash
# SFLO Setup — One-command installation for OpenClaw, Cursor, Claude Code, and Codex
#
# Usage:
#   bash setup.sh --runtime <openclaw|cursor|claude-code|codex> [--install-dir PATH] [--source PATH_OR_URL] [--branch BRANCH]
#
# Install directory:
#   Directory where SFLO installs runtime integration files and, when needed,
#   an SFLO checkout. Defaults to the current directory.
#
# What this does:
#   1. Copies/clones SFLO into the install directory (or configures in-place)
#   2. Installs the appropriate hook/config for your runtime
#   3. Verifies pipeline.yaml is present
#   4. Installs the skill (OpenClaw only)
#   5. Writes setup status marker

set -euo pipefail

DEFAULT_REPO="https://github.com/simonasrazm/simon-factory-lights-out.git"
BRANCH="main"
INSTALL_DIR=""
SOURCE=""
RUNTIME=""
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
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --sflo-path) SFLO_PATH_OVERRIDE="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --runtime) RUNTIME="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "╔══════════════════════════════════════════╗"
echo "║  SFLO — Simon Factory Lights Out Setup   ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# --- Runtime (explicit, required) ---
#
# The runtime is the agent system that runs the SFLO pipeline. It is a
# deliberate choice, not something to guess: auto-detection silently picked
# the wrong runtime on machines with more than one installed. Pass --runtime.
if [[ -z "$RUNTIME" ]]; then
  echo "ERROR: --runtime is required. Pass one of: openclaw, cursor, claude-code, codex"
  echo "  e.g.  bash setup.sh --runtime codex"
  exit 1
fi
case "$RUNTIME" in
  openclaw|cursor|claude-code|codex) ;;
  *)
    echo "ERROR: unknown --runtime '$RUNTIME'. Valid: openclaw, cursor, claude-code, codex"
    exit 1
    ;;
esac
echo "Runtime: $RUNTIME"

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

# --- Initialize git submodules (vendor/agent-skills) ---
# SFLO resolves pipeline skills from vendor/agent-skills/; a fresh clone
# leaves it empty until initialized. rev-parse skips cleanly (rather than
# aborting setup) when SFLO was extracted from an archive, not git-cloned.
echo ""
echo "Initializing git submodules (vendor/agent-skills)..."
if git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree &>/dev/null; then
  if git -C "$SCRIPT_DIR" submodule update --init --recursive; then
    echo "  ✓ Submodules initialized"
  else
    echo "  ⚠ git submodule update failed — vendor/agent-skills may be incomplete."
  fi
else
  echo "  ⚠ Not a git work tree — skipping. Populate vendor/agent-skills manually."
fi

# --- Resolve install directory ---

if [[ -z "$INSTALL_DIR" ]]; then
  INSTALL_DIR="$(pwd)"
fi

echo "Install dir: $INSTALL_DIR"
echo ""

# --- Install SFLO to install directory ---

# If --sflo-path was provided, sflo is already in place — skip copy entirely
if [[ -n "${SFLO_PATH_OVERRIDE:-}" ]]; then
  SFLO_PATH="$SFLO_PATH_OVERRIDE"
  IN_PLACE=true
  echo "SFLO path provided: $SFLO_PATH — skipping copy"
  echo "  ✓ SFLO at $SFLO_PATH"
else

SFLO_PATH="$INSTALL_DIR/$SFLO_DIR_NAME"
IN_PLACE=false

# Detect in-place install: source IS the target (or its parent is install dir)
resolve_real() { cd "$1" 2>/dev/null && pwd; }

SOURCE_REAL="$(resolve_real "$SOURCE" || echo "")"
SFLO_REAL="$(resolve_real "$SFLO_PATH" || echo "")"
INSTALL_DIR_REAL="$(resolve_real "$INSTALL_DIR" || echo "")"

if [[ -n "$SOURCE_REAL" && -n "$SFLO_REAL" && "$SOURCE_REAL" == "$SFLO_REAL" ]]; then
  # Source and destination are the same directory
  IN_PLACE=true
  echo "Running from SFLO repo inside install directory — configuring in-place (no copy needed)"
elif [[ -n "$SOURCE_REAL" && -n "$INSTALL_DIR_REAL" && "$SOURCE_REAL" == "$INSTALL_DIR_REAL" ]]; then
  # Source is the install directory itself (user ran setup.sh from the SFLO repo root)
  IN_PLACE=true
  SFLO_PATH="$SOURCE"
  echo "Running from SFLO repo root — configuring in-place (no copy needed)"
elif [[ -n "$SOURCE_REAL" && -n "$SFLO_REAL" && "$SOURCE_REAL" == "$SFLO_REAL"/* ]]; then
  # Source is INSIDE the target (e.g. an in-repo SFLO checkout)
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

# Resolve relative hook path from install directory to stop_hook.py
relative_hook_path() {
  local from="$1"
  local to="$2"
  "$PYTHON_CMD" -c '
import os, sys
print(os.path.relpath(sys.argv[1], sys.argv[2]))
' "$to" "$from" 2>/dev/null
}

if [[ "$RUNTIME" == "openclaw" ]]; then
  HOOK_SRC="$SFLO_PATH/src/hooks/openclaw/sflo-pipeline"
  HOOK_DST="$INSTALL_DIR/hooks/sflo-pipeline"

  mkdir -p "$INSTALL_DIR/hooks"

  if [[ -d "$HOOK_SRC" ]]; then
    rm -rf "$HOOK_DST"
    cp -r "$HOOK_SRC" "$HOOK_DST"
    printf '%s\n' "$SFLO_PATH" > "$HOOK_DST/.sflo-home"
    echo "  ✓ Hook copied to $HOOK_DST"
  else
    echo "  ⚠ Hook source not found at $HOOK_SRC"
  fi

  # Enable in config
  CONFIG="$HOME/.openclaw/openclaw.json"
  if [[ -f "$CONFIG" ]]; then
    "$PYTHON_CMD" -c '
import json, sys
with open(sys.argv[1]) as f:
    config = json.load(f)
hooks = config.setdefault("hooks", {}).setdefault("internal", {}).setdefault("entries", {})
if "sflo-pipeline" not in hooks:
    hooks["sflo-pipeline"] = {"enabled": True}
    with open(sys.argv[1], "w") as f:
        json.dump(config, f, indent=2)
    print("  ✓ Hook enabled in OpenClaw config")
else:
    print("  ✓ Hook already in config")
' "$CONFIG" 2>/dev/null || echo "  ⚠ Could not update config — enable sflo-pipeline hook manually"
  fi

elif [[ "$RUNTIME" == "cursor" ]]; then
  # Native Cursor integration: hook + rule. Hooks live in .cursor/hooks.json
  # and Cursor reloads them automatically when the file is saved (no IDE
  # restart needed). Rules live in .cursor/rules/sflo.mdc and apply
  # automatically because we set alwaysApply: true in the front matter.
  CURSOR_DIR="$INSTALL_DIR/.cursor"
  HOOKS_FILE="$CURSOR_DIR/hooks.json"
  RULES_DIR="$CURSOR_DIR/rules"
  RULE_FILE="$RULES_DIR/sflo.mdc"
  STOP_HOOK_ABS="$SFLO_PATH/src/hooks/cursor/stop_hook.py"
  STOP_HOOK_REL="$(relative_hook_path "$INSTALL_DIR" "$STOP_HOOK_ABS")"

  mkdir -p "$RULES_DIR"

  HOOK_CMD="$PYTHON_CMD $STOP_HOOK_REL"

  # Cursor hooks.json: merge if exists, create if not. We replace any
  # existing 'stop' entries that point to our stop_hook.py so reruns are
  # idempotent. Other hooks (preToolUse etc.) the user added are preserved.
  "$PYTHON_CMD" - "$HOOKS_FILE" "$HOOK_CMD" <<'PYEOF' || echo "  ⚠ Could not write Cursor hooks.json"
import json, os, sys
path, hook_cmd = sys.argv[1], sys.argv[2]
data = {"version": 1, "hooks": {}}
if os.path.isfile(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        pass
data.setdefault("version", 1)
hooks = data.setdefault("hooks", {})
# Drop any prior SFLO stop entry (matched by the stop_hook.py command).
stop_list = [h for h in hooks.get("stop", []) if "stop_hook" not in h.get("command", "")]
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
  SETTINGS_DIR="$INSTALL_DIR/.claude"
  SETTINGS_FILE="$SETTINGS_DIR/settings.json"
  STOP_HOOK_ABS="$SFLO_PATH/src/hooks/claude-code/stop_hook.py"
  STOP_HOOK_REL="$(relative_hook_path "$INSTALL_DIR" "$STOP_HOOK_ABS")"

  mkdir -p "$SETTINGS_DIR"

  # Use relative path so settings.json is portable (not machine-specific)
  HOOK_CMD="$PYTHON_CMD $STOP_HOOK_REL"

  if [[ -f "$SETTINGS_FILE" ]]; then
    "$PYTHON_CMD" -c '
import json, sys
settings_file, hook_cmd = sys.argv[1], sys.argv[2]
with open(settings_file) as f:
    s = json.load(f)
hooks = s.setdefault("hooks", {})
hooks["Stop"] = [{"type": "command", "command": hook_cmd}]
# Remove legacy v1 key if present
hooks.pop("stop", None)
with open(settings_file, "w") as f:
    json.dump(s, f, indent=2)
print("  ✓ Stop hook configured in .claude/settings.json")
' "$SETTINGS_FILE" "$HOOK_CMD" 2>/dev/null || echo "  ⚠ Could not update settings — configure stop hook manually"
  else
    "$PYTHON_CMD" -c '
import json, sys
settings_file, hook_cmd = sys.argv[1], sys.argv[2]
settings = {"hooks": {"Stop": [{"type": "command", "command": hook_cmd}]}}
with open(settings_file, "w") as f:
    json.dump(settings, f, indent=2)
print("  ✓ Created .claude/settings.json with Stop hook")
' "$SETTINGS_FILE" "$HOOK_CMD" 2>/dev/null || echo "  ⚠ Could not create settings.json"
  fi

elif [[ "$RUNTIME" == "codex" ]]; then
  echo "  ✓ Codex runtime selected — no stop hook installation required"
  AGENTS_FILE="$INSTALL_DIR/AGENTS.md"
  AGENTS_TEMPLATE="$SFLO_PATH/src/hooks/codex/AGENTS.md"
  SFLO_MD_REL="$(relative_hook_path "$INSTALL_DIR" "$SFLO_PATH/sflo.md")"
  "$PYTHON_CMD" - "$AGENTS_FILE" "$SFLO_MD_REL" "$AGENTS_TEMPLATE" <<'PYEOF' || echo "  ⚠ Could not update AGENTS.md"
import os
import sys

agents_file, sflo_md_rel, template_path = sys.argv[1], sys.argv[2], sys.argv[3]
start = "<!-- SFLO-AGENTS-START -->"
end = "<!-- SFLO-AGENTS-END -->"
legacy_start = "<!-- SFLO-CODEX-START -->"
legacy_end = "<!-- SFLO-CODEX-END -->"
with open(template_path, encoding="utf-8") as f:
    block = f.read().replace("{{SFLO_MD}}", sflo_md_rel).strip()

existing = ""
if os.path.isfile(agents_file):
    with open(agents_file, encoding="utf-8") as f:
        existing = f.read().rstrip()

content = None
for old_start, old_end in ((start, end), (legacy_start, legacy_end)):
    if old_start in existing and old_end in existing:
        before, rest = existing.split(old_start, 1)
        _, after = rest.split(old_end, 1)
        content = before.rstrip() + "\n\n" + block + after.lstrip("\n")
        break
if content is None and existing:
    content = existing + "\n\n" + block
elif content is None:
    content = block

os.makedirs(os.path.dirname(agents_file) or ".", exist_ok=True)
with open(agents_file, "w", encoding="utf-8") as f:
    f.write(content.rstrip() + "\n")
print("  ✓ AGENTS.md updated with SFLO trigger")
PYEOF
  if ! command -v codex &>/dev/null; then
    echo "  NOTE: codex CLI not on PATH — install/login before triggering the pipeline."
  else
    echo "  ✓ codex CLI detected"
  fi
fi

# --- Verify pipeline config exists ---

PIPELINE_FILE="$SFLO_PATH/pipeline.yaml"
if [[ ! -f "$PIPELINE_FILE" ]]; then
  echo "  ⚠ pipeline.yaml not found at $PIPELINE_FILE — installation may be incomplete"
else
  echo "  ✓ pipeline.yaml present"
fi

# --- Install skill (OpenClaw only) ---

if [[ "$RUNTIME" == "openclaw" ]]; then
  SKILL_SRC="$SFLO_PATH/src/hooks/openclaw/skill"
  SKILL_DST="$INSTALL_DIR/skills/sflo"

  if [[ -d "$SKILL_SRC" ]]; then
    mkdir -p "$INSTALL_DIR/skills"
    rm -rf "$SKILL_DST"
    mkdir -p "$SKILL_DST"
    cp -r "$SKILL_SRC"/* "$SKILL_DST/"
    # Resolve path placeholders in SKILL.md (cross-platform — no sed -i variance)
    if [[ -f "$SKILL_DST/SKILL.md" ]]; then
      "$PYTHON_CMD" -c '
import sys
p, sflo_path = sys.argv[1], sys.argv[2]
with open(p) as f:
    content = f.read()
content = content.replace("{{SFLO_PATH}}", sflo_path)
with open(p, "w") as f:
    f.write(content)
' "$SKILL_DST/SKILL.md" "$SFLO_PATH"
    fi
    echo "  ✓ Skill installed at $SKILL_DST (paths resolved)"
  fi
fi

# --- Write setup status ---

STATUS_DIR="$INSTALL_DIR/.sflo"
mkdir -p "$STATUS_DIR"
STATUS_FILE="$STATUS_DIR/.setup-status"
if [[ "$RUNTIME" == "openclaw" ]]; then
  echo "restart_required" > "$STATUS_FILE"
else
  # claude-code hot-reloads settings.json; cursor live-reloads
  # .cursor/hooks.json and .cursor/rules/*; codex has no hook.
  echo "ready" > "$STATUS_FILE"
fi

# --- Final output ---

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  SFLO installed successfully!             ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "SFLO_SETUP_RESULT:{\"ok\":true,\"runtime\":\"$RUNTIME\",\"install_dir\":\"$INSTALL_DIR\",\"sflo_path\":\"$SFLO_PATH\",\"status\":\"$(cat "$STATUS_FILE")\"}"
