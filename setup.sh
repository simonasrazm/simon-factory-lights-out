#!/usr/bin/env bash
# SFLO Setup — One-command installation for OpenClaw, Cursor, Claude Code, and Codex
#
# Usage:
#   bash setup.sh --runtime <openclaw|cursor|claude-code|codex> [--install-dir PATH] [--source PATH_OR_URL] [--branch BRANCH]
#
# Install directory:
#   Project directory for runtime integration, pipeline defaults, and state.
#   The complete runtime is installed in the selected runtime's skill root.
#
# What this does:
#   1. Resolves a local source or temporary remote clone
#   2. Atomically installs a complete self-contained runtime skill
#   3. Installs the appropriate hook/config for your runtime
#   4. Preserves project pipeline overrides
#   5. Writes setup status marker

set -euo pipefail

DEFAULT_REPO="https://github.com/simonasrazm/simon-factory-lights-out.git"
BRANCH="main"
INSTALL_DIR=""
SOURCE=""
RUNTIME=""

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
if [[ -z "$SOURCE" && -f "$SCRIPT_DIR/sflo.md" ]]; then
  SOURCE="$SCRIPT_DIR"
  echo "Source: $SOURCE (local — running from SFLO repo)"
elif [[ -z "$SOURCE" ]]; then
  SOURCE="$DEFAULT_REPO"
  echo "Source: $SOURCE"
else
  echo "Source: $SOURCE"
fi

# --- Resolve install directory ---

if [[ -z "$INSTALL_DIR" ]]; then
  INSTALL_DIR="$(pwd)"
fi

echo "Install dir: $INSTALL_DIR"
echo ""

STATUS_DIR="$INSTALL_DIR/.sflo"
STATUS_FILE="$STATUS_DIR/.setup-status"
mkdir -p "$STATUS_DIR"

atomic_status() {
  local status="$1" tmp
  tmp="$(mktemp "$STATUS_DIR/.setup-status.XXXXXX")"
  printf '%s\n' "$status" > "$tmp"
  mv -f "$tmp" "$STATUS_FILE"
}

emit_setup_result() {
  local ok="$1" status="$2" error="${3:-}"
  "$PYTHON_CMD" - "$ok" "$RUNTIME" "$INSTALL_DIR" "${SFLO_PATH:-}" "$status" "$error" <<'PYEOF'
import json, sys
ok, runtime, install_dir, sflo_path, status, error = sys.argv[1:]
result = {
    "ok": ok == "true",
    "runtime": runtime,
    "install_dir": install_dir,
    "sflo_path": sflo_path,
    "status": status,
}
if error:
    result["error"] = error
print("SFLO_SETUP_RESULT:" + json.dumps(result, separators=(",", ":")))
PYEOF
}

SETUP_SUCCEEDED=false
SETUP_ERROR="setup did not complete"
TEMP_SOURCE_ROOT=""
atomic_status "failed"
setup_exit() {
  local rc=$?
  trap - EXIT
  if [[ "$SETUP_SUCCEEDED" != true ]]; then
    atomic_status "failed" || true
    emit_setup_result false failed "$SETUP_ERROR" || true
    [[ $rc -ne 0 ]] || rc=1
  fi
  [[ -z "$TEMP_SOURCE_ROOT" ]] || rm -rf "$TEMP_SOURCE_ROOT"
  exit "$rc"
}
trap setup_exit EXIT

die() {
  SETUP_ERROR="$1"
  echo "ERROR: $1" >&2
  exit 1
}

MATT_REQUIRED_REL="vendor/mattpocock-skills/skills/engineering/tdd/SKILL.md"
ensure_matt_skills() {
  local root="$1"
  [[ -f "$root/$MATT_REQUIRED_REL" ]] || die "required vendored Matt skill is missing: $root/$MATT_REQUIRED_REL"
}

# --- Resolve installation source ---
#
# A checkout is an installation source, not the installed product. Local
# sources are consumed in place; remote sources are cloned to disposable
# staging. The durable copy is installed under the selected runtime's skill
# root below.
if [[ -n "${SFLO_PATH_OVERRIDE:-}" ]]; then
  SFLO_PATH="$SFLO_PATH_OVERRIDE"
elif [[ -d "$SOURCE" ]]; then
  SFLO_PATH="$(cd "$SOURCE" && pwd)"
  echo "Using local SFLO source at $SFLO_PATH"
elif [[ "$SOURCE" == http* || "$SOURCE" == git@* || "$SOURCE" == ssh://* || "$SOURCE" == file://* ]]; then
  TEMP_SOURCE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/sflo-install.XXXXXX")"
  SFLO_PATH="$TEMP_SOURCE_ROOT/sflo"
  echo "Cloning SFLO into disposable staging..."
  git clone --branch "$BRANCH" --depth 1 "$SOURCE" "$SFLO_PATH" || die "failed to clone SFLO from $SOURCE"
else
  die "source not found: $SOURCE"
fi
echo "  ✓ SFLO source ready at $SFLO_PATH"

ensure_matt_skills "$SFLO_PATH"
[[ -f "$SFLO_PATH/pipeline.yaml" ]] || die "pipeline.yaml not found at $SFLO_PATH/pipeline.yaml"

# Install one complete, owned SFLO payload in the selected runtime's canonical
# skill directory. All runner, agent, gate, vendor, and integration paths below
# resolve from this durable copy, so the source checkout may be deleted.
SOURCE_SFLO_PATH="$SFLO_PATH"
case "$RUNTIME" in
  codex) SKILL_DST="${AGENTS_HOME:-$HOME/.agents}/skills/sflo" ;;
  cursor) SKILL_DST="${CURSOR_HOME:-$HOME/.cursor}/skills/sflo" ;;
  claude-code) SKILL_DST="${CLAUDE_HOME:-$HOME/.claude}/skills/sflo" ;;
  openclaw) SKILL_DST="$INSTALL_DIR/skills/sflo" ;;
esac
mkdir -p "$(dirname "$SKILL_DST")"
"$PYTHON_CMD" "$SOURCE_SFLO_PATH/src/install_skill.py" \
  --source "$SOURCE_SFLO_PATH" \
  --runtime "$RUNTIME" \
  --destination "$SKILL_DST" \
  || die "failed to install the self-contained SFLO skill at $SKILL_DST"
SFLO_PATH="$SKILL_DST"
echo "  ✓ Self-contained SFLO skill installed at $SFLO_PATH"

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

shell_quote() {
  "$PYTHON_CMD" - "$1" <<'PYEOF'
import shlex
import sys

print(shlex.quote(sys.argv[1]))
PYEOF
}

is_sflo_skill_dir() {
  local dir="$1"

  [[ -f "$dir/.sflo-owned" ]] && return 0
  [[ -f "$dir/.sflo-install.json" ]] && grep -q '"product"[[:space:]]*:[[:space:]]*"sflo"' "$dir/.sflo-install.json" && return 0
  [[ -f "$dir/SKILL.md" ]] && grep -q "SFLO Factory Triggering" "$dir/SKILL.md"
}

remove_owned_skill_dir() {
  local dir="$1"

  [[ -e "$dir" ]] || return 0
  if is_sflo_skill_dir "$dir"; then
    rm -rf "$dir"
  else
    echo "  ⚠ Leaving non-SFLO skill directory untouched: $dir"
  fi
}

install_runtime_pipeline() {
  local src="$1"
  local label="$2"
  local dst="$INSTALL_DIR/pipeline.yaml"
  local marker="$INSTALL_DIR/.sflo/pipeline.yaml.managed"
  local proposed="$INSTALL_DIR/pipeline.yaml.sflo-default"

  if [[ ! -f "$src" ]]; then
    echo "  ⚠ $label pipeline source not found at $src"
    return 1
  fi

  mkdir -p "$INSTALL_DIR" "$(dirname "$marker")"
  if [[ ! -f "$dst" ]]; then
    cp "$src" "$dst"
    cp "$src" "$marker"
    rm -f "$proposed"
    echo "  ✓ $label pipeline installed at $dst"
  elif cmp -s "$src" "$dst"; then
    cp "$src" "$marker"
    rm -f "$proposed"
    echo "  ✓ $label pipeline already current at $dst"
  elif [[ -f "$marker" ]] && cmp -s "$dst" "$marker"; then
    cp "$src" "$dst"
    cp "$src" "$marker"
    rm -f "$proposed"
    echo "  ✓ $label managed pipeline updated at $dst"
  else
    cp "$src" "$proposed"
    echo "  ✓ Existing project pipeline preserved at $dst; new SFLO defaults written to $proposed"
  fi
}

cursor_skill_root() {
  local cursor_home="${CURSOR_HOME:-$HOME/.cursor}"
  printf '%s\n' "$cursor_home/skills"
}

remove_old_agents_block() {
  local agents_file="$1"

  [[ -f "$agents_file" ]] || return 0

  "$PYTHON_CMD" - "$agents_file" <<'PYEOF'
import os
import re
import sys

agents_file = sys.argv[1]
with open(agents_file, encoding="utf-8") as f:
    existing = f.read()

cleaned = existing
for start, end in (
    ("<!-- SFLO-AGENTS-START -->", "<!-- SFLO-AGENTS-END -->"),
    ("<!-- SFLO-CODEX-START -->", "<!-- SFLO-CODEX-END -->"),
):
    cleaned = re.sub(
        r"\n*" + re.escape(start) + r".*?" + re.escape(end) + r"\n*",
        "\n\n",
        cleaned,
        flags=re.S,
    )

cleaned = cleaned.strip()
if cleaned:
    with open(agents_file, "w", encoding="utf-8") as f:
        f.write(cleaned + "\n")
else:
    os.remove(agents_file)
PYEOF
}

if [[ "$RUNTIME" == "openclaw" ]]; then
  HOOK_SRC="$SFLO_PATH/src/hooks/openclaw/sflo-pipeline"
  HOOK_DST="$INSTALL_DIR/hooks/sflo-pipeline"

  mkdir -p "$INSTALL_DIR/hooks"

  if [[ -d "$HOOK_SRC" ]]; then
    rm -rf "$HOOK_DST"
    ln -s "$HOOK_SRC" "$HOOK_DST"
    echo "  ✓ Hook linked to the self-contained SFLO skill at $HOOK_DST"
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
  # Cursor integration: project stop hook plus the global Agent Skill root at
  # ~/.cursor/skills. The stop hook remains project-local because Cursor reads
  # hooks from the workspace.
  CURSOR_DIR="$INSTALL_DIR/.cursor"
  HOOKS_FILE="$CURSOR_DIR/hooks.json"
  RULES_DIR="$CURSOR_DIR/rules"
  CURSOR_SKILL_ROOT="$(cursor_skill_root)"
  CURSOR_COMPAT_SKILL_ROOT="${CURSOR_HOME:-$HOME/.cursor}/skills-cursor"
  STOP_HOOK_ABS="$SFLO_PATH/src/hooks/cursor/stop_hook.py"
  STOP_HOOK_REL="$(relative_hook_path "$INSTALL_DIR" "$STOP_HOOK_ABS")"

  mkdir -p "$CURSOR_DIR"

  HOOK_CMD="$PYTHON_CMD $(shell_quote "$STOP_HOOK_REL")"

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
def is_sflo_stop(entry):
    command = str(entry.get("command", "")).replace("\\", "/")
    return "src/hooks/cursor/stop_hook.py" in command
stop_list = [h for h in hooks.get("stop", []) if not is_sflo_stop(h)]
stop_list.insert(0, {"command": hook_cmd, "loop_limit": None})
hooks["stop"] = stop_list
with open(path, "w") as f:
    json.dump(data, f, indent=2)
print("  ✓ .cursor/hooks.json updated (stop hook -> SFLO)")
PYEOF

  remove_owned_skill_dir "$CURSOR_SKILL_ROOT/sflo-factory-triggering"
  remove_owned_skill_dir "$CURSOR_COMPAT_SKILL_ROOT/sflo"
  remove_owned_skill_dir "$CURSOR_COMPAT_SKILL_ROOT/sflo-factory-triggering"
  install_runtime_pipeline "$SFLO_PATH/pipeline-cursor.yaml" "Cursor" || exit 1
  rm -f "$RULES_DIR/sflo.mdc" "$RULES_DIR/sflo-factory-triggering.mdc"
  rmdir "$RULES_DIR" 2>/dev/null || true

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
  HOOK_CMD="$PYTHON_CMD $(shell_quote "$STOP_HOOK_REL")"

  if [[ -f "$SETTINGS_FILE" ]]; then
    "$PYTHON_CMD" -c '
import json, sys
settings_file, hook_cmd = sys.argv[1], sys.argv[2]
with open(settings_file) as f:
    s = json.load(f)
hooks = s.setdefault("hooks", {})
existing = list(hooks.get("Stop", [])) + list(hooks.get("stop", []))
def is_sflo_stop(entry):
    command = str(entry.get("command", "")).replace("\\", "/")
    return "src/hooks/claude-code/stop_hook.py" in command
hooks["Stop"] = [{"type": "command", "command": hook_cmd}] + [
    entry for entry in existing if not is_sflo_stop(entry)
]
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
  CODEX_SKILL_DST="$SKILL_DST"
  CODEX_OLD_PROJECT_SKILL_DST="$INSTALL_DIR/.agents/skills/sflo"
  CODEX_LEGACY_SKILL_DST="$INSTALL_DIR/.agents/skills/sflo-factory-triggering"
  CODEX_GLOBAL_LEGACY_SKILL_DST="${AGENTS_HOME:-$HOME/.agents}/skills/sflo-factory-triggering"
  remove_owned_skill_dir "$CODEX_LEGACY_SKILL_DST"
  remove_owned_skill_dir "$CODEX_GLOBAL_LEGACY_SKILL_DST"
  CODEX_OLD_PROJECT_SKILL_REAL="$($PYTHON_CMD -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$CODEX_OLD_PROJECT_SKILL_DST")"
  CODEX_SKILL_REAL="$($PYTHON_CMD -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$CODEX_SKILL_DST")"
  if [[ "$CODEX_OLD_PROJECT_SKILL_REAL" != "$CODEX_SKILL_REAL" ]]; then
    remove_owned_skill_dir "$CODEX_OLD_PROJECT_SKILL_DST"
  fi
  remove_old_agents_block "$INSTALL_DIR/AGENTS.md"
  if ! command -v codex &>/dev/null; then
    echo "  NOTE: codex CLI not on PATH — install/login before triggering the pipeline."
  else
    echo "  ✓ codex CLI detected"
  fi
fi

# --- Verify pipeline config exists ---

PIPELINE_FILE="$SFLO_PATH/pipeline.yaml"
if [[ ! -f "$PIPELINE_FILE" ]]; then
  die "pipeline.yaml not found at $PIPELINE_FILE"
else
  echo "  ✓ pipeline.yaml present"
fi

# --- Write setup status ---

if [[ "$RUNTIME" == "openclaw" ]]; then
  FINAL_STATUS="restart_required"
else
  # claude-code hot-reloads settings.json; cursor live-reloads
  # .cursor/hooks.json and discovers the global skill; codex has no hook.
  FINAL_STATUS="ready"
fi
atomic_status "$FINAL_STATUS"

# --- Final output ---

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  SFLO installed successfully!             ║"
echo "╚══════════════════════════════════════════╝"
echo ""
emit_setup_result true "$FINAL_STATUS"
SETUP_SUCCEEDED=true
trap - EXIT
[[ -z "$TEMP_SOURCE_ROOT" ]] || rm -rf "$TEMP_SOURCE_ROOT"
