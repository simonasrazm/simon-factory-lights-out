"""Tool execution handlers for OllamaAdapter.

Each handler takes (fn_args: dict) and returns output: str.
Registry pattern — add new tools by adding a handler function
and registering it in TOOL_HANDLERS dict.
"""

import os
import pathlib
import re
import shlex
import subprocess


_TOOL_TRUNCATE_LIMIT = 200

# ---------------------------------------------------------------------------
# Bash safety policy.
#
# Philosophy: workhorse agents need to actually do work — running tests,
# git operations, package managers, build tools, file moves, mkdir, etc.
# An overly restrictive allowlist (cat/ls/grep only) is theatre — the agent
# will fail the task instead of producing useful output. Real security
# concerns are shell-injection patterns (;, &, |, $( ), backticks, redirects,
# eval/exec/source, sudo escalation), NOT the leading executable name.
#
# This module rejects commands that contain shell-injection or escalation
# patterns. The leading executable is NOT filtered — an operator who wants
# command-level filtering can opt in by setting BASH_ALLOWED_COMMANDS to a
# non-empty set in their host integration.
#
# Set SFLO_BASH_ALLOWED_COMMANDS env var (comma-separated) to enforce a
# command allowlist for paranoid environments. Empty / unset = no allowlist.
# ---------------------------------------------------------------------------

BASH_ALLOWED_COMMANDS = {
    cmd.strip()
    for cmd in os.environ.get("SFLO_BASH_ALLOWED_COMMANDS", "").split(",")
    if cmd.strip()
}  # empty set by default = no command-level filter (only injection check)

# Patterns that indicate shell injection / command chaining / privilege
# escalation. These are rejected regardless of the leading command.
_DANGEROUS_PATTERNS = re.compile(
    r"(?:"
    r"[;&|]"  # shell operators ; & |
    r"|`[^`]"  # backtick subshell
    r"|\$\("  # $( subshell
    r"|>[>]?"  # redirect (write to file)
    r"|<\("  # process substitution
    r"|\beval\b"  # eval
    r"|\bexec\b"  # exec
    r"|\bsource\b"  # source
    r"|\bsudo\b"  # sudo escalation
    r")"
)


def _check_bash_safety(command: str):
    """Return (is_safe: bool, reason: str)."""
    if not command or not command.strip():
        return False, "empty command"

    # Check for dangerous shell patterns
    if _DANGEROUS_PATTERNS.search(command):
        return False, f"command contains dangerous shell pattern: {command!r}"

    # Parse to a list — if shlex fails the command is malformed
    try:
        parts = shlex.split(command)
    except ValueError as e:
        return False, f"command parse error: {e}"

    if not parts:
        return False, "empty command after parsing"

    # Optional opt-in command allowlist (off by default). Operators who want
    # command-level filtering set SFLO_BASH_ALLOWED_COMMANDS in their env.
    if BASH_ALLOWED_COMMANDS:
        exe = os.path.basename(parts[0])
        if exe not in BASH_ALLOWED_COMMANDS:
            return False, (
                f"command '{exe}' not in SFLO_BASH_ALLOWED_COMMANDS allowlist. "
                f"Allowed: {', '.join(sorted(BASH_ALLOWED_COMMANDS))}"
            )

    return True, ""


def handle_bash(fn_args):
    command = fn_args.get("command", "")
    is_safe, reason = _check_bash_safety(command)
    if not is_safe:
        return f"[bash blocked: {reason}]"
    try:
        # Use list-form with shell=False to prevent injection
        cmd_list = shlex.split(command)
        proc = subprocess.run(
            cmd_list,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = proc.stdout
        if proc.stderr:
            output += ("\n[stderr]\n" + proc.stderr) if output else proc.stderr
        if not output:
            output = f"[exit code {proc.returncode}]"
    except subprocess.TimeoutExpired:
        output = "[bash error: command timed out after 30s]"
    except FileNotFoundError as e:
        output = f"[bash error: command not found — {e}]"
    except Exception as e:
        output = f"[bash error: {e}]"
    return output


def handle_read(fn_args):
    path = fn_args.get("path", "")
    offset = int(fn_args.get("offset", 1))
    limit = fn_args.get("limit")
    if limit is not None:
        limit = int(limit)
    try:
        p = pathlib.Path(path)
        if not p.is_absolute():
            p = pathlib.Path(os.getcwd()) / p
        raw_lines = p.read_text(encoding="utf-8").splitlines()
        start = max(0, offset - 1)
        selected = (
            raw_lines[start : start + limit] if limit is not None else raw_lines[start:]
        )
        result_lines = [f"{start + i + 1}\t{line}" for i, line in enumerate(selected)]
        return (
            "\n".join(result_lines)
            if result_lines
            else "[empty file or no lines in range]"
        )
    except FileNotFoundError:
        return f"[read error: file not found: {path}]"
    except UnicodeDecodeError:
        return f"[read error: file is not valid UTF-8: {path}]"
    except Exception as e:
        return f"[read error: {e}]"


def handle_write(fn_args):
    file_path = fn_args.get("file_path", "")
    content = fn_args.get("content", "")
    try:
        p = pathlib.Path(file_path)
        if not p.is_absolute():
            p = pathlib.Path(os.getcwd()) / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        return f"Written {byte_count} bytes to {p}"
    except Exception as e:
        return f"[write error: {e}]"


def handle_append(fn_args):
    file_path = fn_args.get("file_path", "")
    content = fn_args.get("content", "")
    try:
        p = pathlib.Path(file_path)
        if not p.is_absolute():
            p = pathlib.Path(os.getcwd()) / p
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        total = p.stat().st_size
        return f"Appended {len(content.encode('utf-8'))} bytes to {p} (total: {total} bytes)"
    except Exception as e:
        return f"[append error: {e}]"


def handle_edit(fn_args):
    file_path = fn_args.get("file_path", "")
    old_string = fn_args.get("old_string", "")
    new_string = fn_args.get("new_string", "")
    replace_all = bool(fn_args.get("replace_all", False))
    try:
        p = pathlib.Path(file_path)
        if not p.is_absolute():
            p = pathlib.Path(os.getcwd()) / p
        text = p.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            return f"[edit error: old_string not found in {file_path}]"
        elif count > 1 and not replace_all:
            return (
                f"[edit error: old_string found {count} times in {file_path} "
                f"— use replace_all=true or provide more context to make it unique]"
            )
        new_text = (
            text.replace(old_string, new_string)
            if replace_all
            else text.replace(old_string, new_string, 1)
        )
        p.write_text(new_text, encoding="utf-8")
        return f"Replaced {count if replace_all else 1} occurrence(s) in {file_path}"
    except FileNotFoundError:
        return f"[edit error: file not found: {file_path}]"
    except UnicodeDecodeError:
        return f"[edit error: file is not valid UTF-8: {file_path}]"
    except Exception as e:
        return f"[edit error: {e}]"


def handle_multiedit(fn_args):
    file_path = fn_args.get("file_path", "")
    edits = fn_args.get("edits", [])
    try:
        p = pathlib.Path(file_path)
        if not p.is_absolute():
            p = pathlib.Path(os.getcwd()) / p
        text = p.read_text(encoding="utf-8")
        original = text
        applied = 0
        for edit in edits:
            old = edit.get("old_string", "")
            new = edit.get("new_string", "")
            if old not in text:
                p.write_text(original, encoding="utf-8")
                return (
                    f"[multiedit error: edit {applied + 1} old_string not found "
                    f"in {file_path} — all edits rolled back]"
                )
            text = text.replace(old, new, 1)
            applied += 1
        p.write_text(text, encoding="utf-8")
        return f"Applied {applied} edit(s) to {file_path}"
    except FileNotFoundError:
        return f"[multiedit error: file not found: {file_path}]"
    except Exception as e:
        return f"[multiedit error: {e}]"


def handle_glob(fn_args):
    pattern = fn_args.get("pattern", "")
    try:
        cwd = pathlib.Path(os.getcwd())
        matches = sorted(str(p.resolve()) for p in cwd.glob(pattern))
        truncated = len(matches) > _TOOL_TRUNCATE_LIMIT
        matches = matches[:_TOOL_TRUNCATE_LIMIT]
        output = "\n".join(matches) if matches else "[glob: no matches]"
        if truncated:
            output += "\n[truncated]"
        return output
    except Exception as e:
        return f"[glob error: {e}]"


def handle_grep(fn_args):
    pattern = fn_args.get("pattern", "")
    path = fn_args.get("path", ".")
    case_insensitive = bool(fn_args.get("case_insensitive", False))
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error as e:
        return f"[grep error: invalid regex — {e}]"

    results = []
    truncated = False

    def _grep_file(fp):
        nonlocal truncated
        if truncated:
            return
        try:
            text = fp.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if len(results) >= _TOOL_TRUNCATE_LIMIT:
                    truncated = True
                    return
                if compiled.search(line):
                    results.append(f"{fp}:{lineno}:{line}")
        except (UnicodeDecodeError, Exception):
            pass

    p = pathlib.Path(path)
    if not p.is_absolute():
        p = pathlib.Path(os.getcwd()) / p
    if p.is_file():
        _grep_file(p)
    elif p.is_dir():
        for fp in sorted(p.rglob("*")):
            if truncated:
                break
            if fp.is_file():
                _grep_file(fp)
    else:
        return f"[grep error: path not found: {path}]"

    output = "\n".join(results) if results else "[grep: no matches]"
    if truncated:
        output += "\n[truncated]"
    return output


def handle_webfetch(fn_args):
    url = fn_args.get("url", "")
    try:
        proc = subprocess.run(
            [
                "curl",
                "-sL",
                "-m",
                "15",
                "-H",
                "User-Agent: SFLO-OllamaAdapter/1.0",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        raw = proc.stdout
        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        output = text[:4000]
        if len(text) > 4000:
            output += "\n[truncated]"
        if not output:
            output = f"[webfetch: empty response, status={proc.returncode}]"
        return output
    except subprocess.TimeoutExpired:
        return "[webfetch error: request timed out after 15s]"
    except Exception as e:
        return f"[webfetch error: {e}]"


# Registry — add new tools here. Handler takes fn_args dict, returns str.
TOOL_HANDLERS = {
    "bash": handle_bash,
    "read": handle_read,
    "write": handle_write,
    "append": handle_append,
    "edit": handle_edit,
    "multiedit": handle_multiedit,
    "glob": handle_glob,
    "grep": handle_grep,
    "webfetch": handle_webfetch,
}
