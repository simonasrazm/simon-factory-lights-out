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
#   4. Installs runtime skills where supported
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
atomic_status "failed"
setup_exit() {
  local rc=$?
  trap - EXIT
  if [[ "$SETUP_SUCCEEDED" != true ]]; then
    atomic_status "failed" || true
    emit_setup_result false failed "$SETUP_ERROR" || true
    [[ $rc -ne 0 ]] || rc=1
  fi
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
  ensure_matt_skills "$SOURCE"
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
elif [[ "$SOURCE" == http* || "$SOURCE" == git@* || "$SOURCE" == ssh://* || "$SOURCE" == file://* ]]; then
  # Remote source — git clone
  if [[ -d "$SFLO_PATH/.git" ]]; then
    echo "Updating SFLO at $SFLO_PATH from git..."
    git -C "$SFLO_PATH" pull --ff-only origin "$BRANCH" || die "failed to update SFLO checkout at $SFLO_PATH"
  elif [[ -d "$SFLO_PATH" ]]; then
    die "SFLO exists at $SFLO_PATH but is not a git checkout"
  else
    echo "Cloning SFLO..."
    git clone --branch "$BRANCH" --depth 1 "$SOURCE" "$SFLO_PATH" || die "failed to clone SFLO from $SOURCE"
  fi
else
  echo "ERROR: Source not found: $SOURCE"
  exit 1
fi
echo "  ✓ SFLO at $SFLO_PATH"

fi  # end of SFLO_PATH_OVERRIDE check

ensure_matt_skills "$SFLO_PATH"
[[ -f "$SFLO_PATH/pipeline.yaml" ]] || die "pipeline.yaml not found at $SFLO_PATH/pipeline.yaml"

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

render_template_file() {
  local src="$1"
  local dst="$2"
  local label="$3"

  if [[ ! -f "$src" ]]; then
    echo "  ⚠ $label source not found at $src"
    return 1
  fi

  mkdir -p "$(dirname "$dst")"
  "$PYTHON_CMD" - "$src" "$dst" "$SFLO_PATH" <<'PYEOF'
import os
import shlex
import sys

src, dst, sflo_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, encoding="utf-8") as f:
    content = f.read()
content = content.replace("{{SFLO_PATH}}", sflo_path)
content = content.replace(
    "{{SFLO_RUNNER_SH}}",
    shlex.quote(os.path.join(sflo_path, "src", "runner.py")),
)
content = content.replace(
    "{{SFLO_SCAFFOLD_SH}}",
    shlex.quote(os.path.join(sflo_path, "src", "scaffold.py")),
)
content = content.replace(
    "{{SFLO_CURSOR_STOP_HOOK_SH}}",
    shlex.quote(os.path.join(sflo_path, "src", "hooks", "cursor", "stop_hook.py")),
)
with open(dst, "w", encoding="utf-8") as f:
    f.write(content)
PYEOF
  echo "  ✓ $label installed at $dst"
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
  local label="$3"

  if [[ ! -d "$src" ]]; then
    echo "  ⚠ $label skill source not found at $src"
    return 1
  fi

  rm -rf "$dst"
  mkdir -p "$dst"
  cp -r "$src"/* "$dst/"
  if [[ -f "$dst/SKILL.md" ]]; then
    render_template_file "$dst/SKILL.md" "$dst/SKILL.md" "$label skill"
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
  local label="$3"

  if [[ -e "$dst" ]]; then
    if ! is_sflo_skill_dir "$dst"; then
      echo "  ⚠ $label skill already exists at $dst and is not SFLO-owned"
      return 1
    fi
    rm -rf "$dst"
  fi

  install_skill_dir "$src" "$dst" "$label" || return 1
  printf '%s\n' "sflo" > "$dst/.sflo-owned"
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
  # Cursor integration: project stop hook plus global Agent Skill roots. The
  # primary custom-skill root is ~/.cursor/skills. Some Cursor builds expose
  # ~/.cursor/skills-cursor as the active root, so install there too when it
  # already exists. The stop hook remains project-local because Cursor reads
  # hooks from the workspace.
  CURSOR_DIR="$INSTALL_DIR/.cursor"
  HOOKS_FILE="$CURSOR_DIR/hooks.json"
  RULES_DIR="$CURSOR_DIR/rules"
  CURSOR_SKILL_SRC="$SFLO_PATH/src/hooks/cursor/skills/sflo"
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
stop_list = [h for h in hooks.get("stop", []) if "stop_hook" not in h.get("command", "")]
stop_list.insert(0, {"command": hook_cmd, "loop_limit": None})
hooks["stop"] = stop_list
with open(path, "w") as f:
    json.dump(data, f, indent=2)
print("  ✓ .cursor/hooks.json updated (stop hook -> SFLO)")
PYEOF

  while IFS= read -r CURSOR_SKILL_ROOT; do
    [[ -n "$CURSOR_SKILL_ROOT" ]] || continue
    install_owned_skill_dir "$CURSOR_SKILL_SRC" "$CURSOR_SKILL_ROOT/sflo" "Cursor factory-triggering" || exit 1
    remove_owned_skill_dir "$CURSOR_SKILL_ROOT/sflo-factory-triggering"
  done < <(cursor_skill_roots)
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
  CODEX_SKILL_SRC="$SFLO_PATH/src/hooks/codex/skills/sflo-factory-triggering"
  CODEX_SKILL_DST="$INSTALL_DIR/.agents/skills/sflo-factory-triggering"
  mkdir -p "$INSTALL_DIR/.agents/skills"
  install_skill_dir "$CODEX_SKILL_SRC" "$CODEX_SKILL_DST" "Codex factory-triggering" || exit 1
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

# --- Install skill (OpenClaw only) ---

if [[ "$RUNTIME" == "openclaw" ]]; then
  SKILL_SRC="$SFLO_PATH/src/hooks/openclaw/skill"
  SKILL_DST="$INSTALL_DIR/skills/sflo"

  mkdir -p "$INSTALL_DIR/skills"
  install_skill_dir "$SKILL_SRC" "$SKILL_DST" "OpenClaw" || true
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
