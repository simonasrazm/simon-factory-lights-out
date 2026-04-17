"""CursorAdapter — runs agents via the Cursor Agent CLI (`cursor-agent`).

First-class Cursor support for SFLO. Mirrors the OpenClawAdapter pattern:
each gate spawn is a single non-interactive invocation of `cursor-agent`
in print mode with JSON output. The runner orchestrates gate sequencing;
this adapter just spawns one agent and returns its final text.

Cursor CLI reference (verified against cursor.com/docs/cli/reference):
    cursor-agent --print                      headless / non-interactive
                 --output-format json         single JSON object on stdout
                 --model <name>               model selection (cursor model id)
                 --force                      auto-approve commands (yolo)
                 --workspace <path>           cwd override (we use Popen cwd)

Auth: the CLI uses the user's existing `cursor-agent login` session or
the CURSOR_API_KEY env var. We don't manage credentials — fail fast with
a clear message if the CLI returns 401/auth-related errors so the user
runs `cursor-agent login` themselves.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time as _time

from .base import RuntimeAdapter


# Map SFLO's generic model aliases (used in bindings.yaml: "opus", "sonnet")
# to Cursor model identifiers. Cursor accepts vendor-specific names directly,
# so unknown models are passed through unchanged — power users can put any
# `cursor-agent --list-models` value in bindings.yaml and it will work.
_MODEL_ALIASES = {
    "opus": "claude-opus-4-7-thinking-high",
    "sonnet": "claude-4.6-sonnet-medium-thinking",
    "haiku": "claude-4.5-haiku-thinking",
    "gpt": "gpt-5.4-medium",
    "gpt-codex": "gpt-5.3-codex",
    "auto": "auto",
}


def _resolve_model(model):
    if not model:
        return "auto"
    return _MODEL_ALIASES.get(model.lower(), model)


class CursorAdapter(RuntimeAdapter):
    """Spawn agents via `cursor-agent --print --output-format json`.

    The `cursor-agent` binary handles tool execution (Read/Write/Shell/MCP)
    inside its own process. We only need to feed it a combined prompt and
    parse the final result string.
    """

    # Hard cap on a single gate spawn. Most gates finish well under this;
    # a runaway tool loop would otherwise hang the runner indefinitely.
    SPAWN_TIMEOUT_SECONDS = int(os.environ.get("SFLO_CURSOR_TIMEOUT", "1800"))

    # CLI binary name. Override via SFLO_CURSOR_BIN for non-PATH installs.
    BIN = os.environ.get("SFLO_CURSOR_BIN", "cursor-agent")

    async def spawn_agent(self, model, system_prompt, user_prompt, role=None,
                          allowed_tools=None):
        # allowed_tools is accepted for API parity with ClaudeCodeAdapter
        # but the cursor-agent CLI doesn't currently expose per-spawn tool
        # gating in print mode. Tool restrictions live in the rule prompt.
        #
        # Resolve the binary up front via shutil.which so we can:
        #   (a) fail fast with a clear message if it isn't on PATH, AND
        #   (b) launch .CMD/.BAT shims correctly on Windows. asyncio's
        #       create_subprocess_exec uses CreateProcess which CANNOT
        #       launch batch files — it only accepts real executables.
        #       We run via subprocess.run inside a thread so .CMD shims
        #       work, while keeping the runner's event loop responsive.
        resolved = shutil.which(self.BIN) or self.BIN
        if shutil.which(self.BIN) is None and not os.path.isfile(self.BIN):
            raise RuntimeError(
                f"Cursor CLI '{self.BIN}' not found on PATH. "
                "Install via https://cursor.com/cli or set SFLO_CURSOR_BIN. "
                "After install, run `cursor-agent login` once."
            )

        # Cursor's CLI takes a single prompt arg. We embed the system prompt
        # as a fenced ROLE block at the top so the model treats it as the
        # operating spec for the gate. This is the same pattern OpenClaw
        # uses (system + '---' + user).
        combined = (
            f"# Role spec (you MUST follow this)\n\n"
            f"{system_prompt}\n\n"
            f"---\n\n"
            f"{user_prompt}"
        )

        cmd = [
            resolved,
            "--print",
            "--output-format", "json",
            "--force",                 # auto-approve shell/file tools (yolo)
            "--model", _resolve_model(model),
        ]

        # Honor scout's read-only contract by switching to ask mode. ask mode
        # is documented as "explore code without making changes" — ideal for
        # the JSON-only role-assignment response we want from scout.
        if role == "scout":
            cmd += ["--mode", "ask"]

        # IMPORTANT: do NOT pass the prompt as a CLI argument.
        # Multi-KB prompts with embedded newlines get mangled through
        # cmd.exe on Windows (newlines truncate, quoting breaks). Cursor's
        # --print mode reads the prompt from stdin when no positional
        # prompt is supplied. Same upstream discipline SFLO uses for the
        # runner itself: "Always pipe the prompt via stdin — never pass
        # it as a CLI argument".
        prompt_bytes = combined.encode("utf-8")

        # Windows: cursor-agent ships as a .CMD shim. CreateProcess (used by
        # both asyncio.create_subprocess_exec AND subprocess.run with
        # shell=False) cannot launch .CMD files directly. Wrap with
        # `cmd.exe /c` — shell=False, list form, no quoting nightmares with
        # the prompt content.
        if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd.exe", "/c"] + cmd

        start = _time.time()
        try:
            # subprocess.run via to_thread — keeps the runner's event loop
            # responsive while delegating the .CMD-aware launch logic to
            # the synchronous subprocess module.
            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                input=prompt_bytes,
                capture_output=True,
                text=False,
                timeout=self.SPAWN_TIMEOUT_SECONDS,
                shell=False,
            )
            stdout_b = proc.stdout or b""
            stderr_b = proc.stderr or b""
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"cursor-agent timed out after {self.SPAWN_TIMEOUT_SECONDS}s "
                f"(role={role}, model={model}). "
                "Increase via SFLO_CURSOR_TIMEOUT env var if expected."
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"Failed to spawn cursor-agent: {e}. "
                "Verify the CLI is installed and on PATH."
            )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        elapsed = _time.time() - start

        if proc.returncode != 0:
            # Surface auth errors with an actionable hint so the user knows
            # to run `cursor-agent login` instead of debugging adapter code.
            hint = ""
            low = (stderr + stdout).lower()
            if "unauthor" in low or "login" in low or "401" in low:
                hint = " (run `cursor-agent login` or set CURSOR_API_KEY)"
            tail = stderr.strip().splitlines()[-20:]
            raise RuntimeError(
                f"cursor-agent failed (exit {proc.returncode}, "
                f"elapsed {elapsed:.0f}s){hint}\n"
                f"stderr (last 20 lines):\n  " + "\n  ".join(tail)
            )

        # In --output-format json mode, cursor-agent emits exactly ONE JSON
        # object on stdout when the run completes. Parse defensively — fall
        # back to raw stdout if for any reason it isn't valid JSON.
        text = self._extract_text(stdout)

        print(
            f"  [Cursor agent — role={role}, model={_resolve_model(model)}, "
            f"elapsed={elapsed:.0f}s, chars={len(text)}]",
            file=sys.stderr,
        )

        return text

    @staticmethod
    def _extract_text(stdout):
        """Pull the assistant's final text out of a cursor-agent JSON result.

        Tolerates several known shapes:
          - {"type":"result","subtype":"success","result":"...","is_error":false}
          - {"result":"...","duration_ms":...}
          - Plain text fallback if the body wasn't JSON at all.
        """
        s = stdout.strip()
        if not s:
            return ""
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return s
        if not isinstance(data, dict):
            return str(data)
        for key in ("result", "text", "content", "message"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
        # Some shapes nest the body under "data"
        nested = data.get("data") if isinstance(data.get("data"), dict) else None
        if nested:
            for key in ("result", "text", "content"):
                val = nested.get(key)
                if isinstance(val, str) and val:
                    return val
        # Last resort: dump the JSON so downstream validators see *something*
        return json.dumps(data)
