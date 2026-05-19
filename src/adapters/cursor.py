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
                 --approve-mcps              auto-approve MCP servers (no prompts)
                 --trust                      trust workspace without prompting
                 --workspace <path>           explicit workspace root

Auth: the CLI uses the user's existing `cursor-agent login` session or
the CURSOR_API_KEY env var. We don't manage credentials — fail fast with
a clear message if the CLI returns 401/auth-related errors so the user
runs `cursor-agent login` themselves.
"""

import asyncio
import atexit
import ctypes
import json
import os
import shutil
import subprocess
import tempfile
import time as _time

from .base import RuntimeAdapter
from .errors import TransientError, NonRetryableError
from .._stderr import _safe_stderr

if os.name == "nt":
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.windll.kernel32
    _PeekNamedPipe = _kernel32.PeekNamedPipe
    _PeekNamedPipe.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _PeekNamedPipe.restype = wintypes.BOOL


# ---------------------------------------------------------------------------
# Windows Job Object — ensures ALL child/grandchild processes are killed when
# the runner exits, preventing orphaned cursor-agent trees from consuming API
# credits and corrupting .sflo/state.json.
#
# How it works:
#   1. We create a Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
#   2. Every Popen'd process is assigned to this Job.
#   3. When the runner exits (normally, crash, or TerminateProcess), Windows
#      closes the Job handle, which kills ALL processes in the Job.
#
# This is the Windows equivalent of Unix process groups + SIGKILL propagation.
# ---------------------------------------------------------------------------

_job_handle = None


# ---------------------------------------------------------------------------
# Windows shim resolution — avoids routing cursor-agent through cmd.exe.
#
# npm/npx install Node CLIs as .CMD shims on Windows.  Spawning them via
# ["cmd.exe", "/c", shim, ...] causes cmd.exe to re-parse every argument:
#   - strips the `!` character (delayed-expansion echo suppression)
#   - expands %VAR% inside argument values
#   - treats & | < > ^ ( ) as shell operators
# A workspace path like C:\Projects\!SFLO becomes C:\Projects\SFLO before
# cursor-agent ever sees it.
#
# Resolution order, best to worst:
#   1. A native .exe sibling          — spawn directly, shell=False.
#   2. npm-classic `node "<script>"`  — spawn node directly, shell=False.
#   3. cursor-agent's own .ps1        — run it via `powershell.exe -File`.
#      PowerShell performs no ! / %VAR% / operator mangling, and the .ps1
#      version-picks node itself, so we never reverse-engineer Cursor's
#      private install layout. If powershell.exe is missing, fall back to
#      mimicking the .ps1's picker (locate the newest versioned node.exe).
# Last resort: return the shim itself; the caller routes it through
# cmd.exe /c and warns that metacharacters may be mangled.
# ---------------------------------------------------------------------------

import re as _re

# PowerShell switches for running a .ps1 shim: skip the user profile, never
# prompt, and bypass the execution policy so an unsigned shim still runs. The
# -ExecutionPolicy override is scoped to this single process — machine and
# user policies are left untouched.
_POWERSHELL_FLAGS = [
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
]

# A cursor-agent version directory: YYYY.MM.DD-<commit>, e.g. 2026.05.19-a1b2c3d.
# The commit segment is hex of any length and either case.
_CURSOR_VERSION_RE = _re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})-([0-9a-fA-F]+)$")


def _path_within(path: str, parent: str) -> bool:
    """Return True if ``path`` resolves to a location inside ``parent``.

    Confines a script path extracted from an (untrusted) shim file so a hostile
    shim cannot redirect the spawn to an executable elsewhere on disk. Both
    paths are fully resolved with realpath first, so a symlink planted inside
    ``parent`` cannot point the spawn outside it.
    """
    parent = os.path.realpath(parent)
    try:
        return os.path.commonpath([os.path.realpath(path), parent]) == parent
    except ValueError:
        # commonpath raises when the paths share no root (e.g. different
        # Windows drives) — treat that as "not within".
        return False


def _resolve_cursor_node(shim_dir: str) -> "list | None":
    """Locate cursor-agent's Node entrypoint by inspecting its install layout.

    Fallback used only when ``powershell.exe`` is unavailable: it mimics what
    cursor-agent.ps1 does internally — prefer ``node.exe`` + ``index.js`` beside
    the shim, otherwise the newest ``versions/<YYYY.MM.DD-commit>/`` directory.

    Returns ``[node_exe, index_js]`` or ``None``. Brittle by nature — it depends
    on Cursor's private layout — which is why it sits behind the PowerShell path.
    """
    direct_node = os.path.join(shim_dir, "node.exe")
    direct_js = os.path.join(shim_dir, "index.js")
    if os.path.isfile(direct_node) and os.path.isfile(direct_js):
        return [direct_node, direct_js]

    versions_dir = os.path.join(shim_dir, "versions")
    if not os.path.isdir(versions_dir):
        return None

    # Sort key (year, month, day, commit): the commit segment breaks same-date
    # ties deterministically, so the winner never depends on os.listdir order.
    candidates = []
    for name in os.listdir(versions_dir):
        match = _CURSOR_VERSION_RE.match(name)
        if match and os.path.isdir(os.path.join(versions_dir, name)):
            year, month, day, commit = match.groups()
            candidates.append(
                ((int(year), int(month), int(day), commit.lower()), name)
            )
    if not candidates:
        return None

    candidates.sort(reverse=True)
    newest = candidates[0][1]
    ver_node = os.path.join(versions_dir, newest, "node.exe")
    ver_js = os.path.join(versions_dir, newest, "index.js")
    if os.path.isfile(ver_node) and os.path.isfile(ver_js):
        return [ver_node, ver_js]
    return None


def _resolve_node_shim(shim_path: str) -> list:
    """Return an argv prefix to spawn instead of routing a Windows .CMD/.BAT
    shim through cmd.exe.

    Returns a list:
    - [exe_path]                      a native .exe sibling
    - [node_exe, script_path]         an npm-classic Node shim
    - [powershell, *flags, ps1_path]  cursor-agent's own PowerShell shim
    - [node_exe, index_js]            cursor-agent layout (powershell.exe absent)
    - [shim_path]                     unrecognised — the call site detects this
                                      via ``prefix == [shim_path]`` and routes
                                      through ``cmd.exe /c`` with a _safe_stderr
                                      warning; with shell=False the OS never
                                      sees the raw .cmd
    """
    # 1. Native .exe sibling?
    stem = os.path.splitext(shim_path)[0]
    exe_candidate = stem + ".exe"
    if os.path.isfile(exe_candidate):
        return [exe_candidate]

    node_exe = shutil.which("node") or "node"
    try:
        with open(shim_path, encoding="utf-8", errors="replace") as _f:
            shim_text = _f.read(65536)  # cap at 64 KB — shims are tiny
    except OSError:
        return [shim_path]  # fallback

    shim_dir = os.path.dirname(os.path.abspath(shim_path))

    # 2. npm-classic shim: a literal `node[.exe] "<script>"` line, e.g.
    #      node  "%~dp0\..\cursor-agent\bin\cursor-agent.js" %*
    #    A shim carrying a direct node line is resolved here even if it also
    #    references a .ps1 — a direct node spawn is the cleanest path. The real
    #    cursor-agent shim has no node line, so it falls through to case 3.
    m = _re.search(r'(?i)\bnode(?:\.exe)?\s+"([^"]+)"', shim_text)
    if m:
        raw_script = m.group(1)
        # Strip a leading %~dp0 / %DP0%, then resolve against the shim dir.
        script = _re.sub(r"(?i)%~?dp0%?\\?", "", raw_script)
        script = os.path.normpath(os.path.join(shim_dir, script))
        # Confine to shim_dir: a hostile shim must not point the spawn at an
        # arbitrary executable (absolute path or ../ escape).
        if os.path.isfile(script) and _path_within(script, shim_dir):
            return [node_exe, script]

    # 3. cursor-agent style: the .CMD delegates to a sibling cursor-agent.ps1
    #    that version-picks node itself. Run that .ps1 directly through
    #    PowerShell — it performs no ! / %VAR% / operator mangling, and Cursor's
    #    own script does the version selection, so we never reverse-engineer the
    #    private install layout.
    if _re.search(r"cursor-agent\.ps1", shim_text, _re.IGNORECASE):
        ps1 = stem + ".ps1"
        powershell = shutil.which("powershell")
        if powershell and os.path.isfile(ps1):
            return [powershell, *_POWERSHELL_FLAGS, ps1]
        # No powershell.exe — fall back to reverse-engineering the layout.
        node_prefix = _resolve_cursor_node(shim_dir)
        if node_prefix:
            return node_prefix

    return [shim_path]  # fallback


def _create_job_object():
    """Create a Windows Job Object that kills children on close."""
    global _job_handle
    if os.name != "nt" or _job_handle is not None:
        return

    kernel32 = ctypes.windll.kernel32

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )

    _job_handle = job


def _assign_to_job(proc):
    """Assign a subprocess.Popen process to the Job Object.

    Must be called immediately after Popen. If the Job doesn't exist (non-Windows
    or creation failed), this is a no-op.
    """
    if _job_handle is None or os.name != "nt":
        return
    # proc._handle is private — no-op rather than crash if a Python drops it
    handle = getattr(proc, "_handle", None)
    if handle is None:
        return
    ctypes.windll.kernel32.AssignProcessToJobObject(_job_handle, int(handle))


def _kill_job():
    """Terminate all processes in the Job Object. Called via atexit."""
    global _job_handle
    if _job_handle is None:
        return
    ctypes.windll.kernel32.TerminateJobObject(_job_handle, 1)
    ctypes.windll.kernel32.CloseHandle(_job_handle)
    _job_handle = None


# Initialize Job Object at module load time. atexit fires on clean exit;
# JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE handles unclean exit (the OS closes
# the handle when our process dies, triggering the kill).
_create_job_object()
if _job_handle is not None:
    atexit.register(_kill_job)


class CursorAdapter(RuntimeAdapter):
    """Spawn agents via `cursor-agent --print --output-format json`.

    The `cursor-agent` binary handles tool execution (Read/Write/Shell/MCP)
    inside its own process. We only need to feed it a combined prompt and
    parse the final result string.
    """

    # Cursor uses vendor-specific model identifiers. Generic aliases from
    # pipeline.yaml are mapped here; unknown values pass through unchanged.
    MODEL_ALIASES = {
        "opus": "claude-opus-4-7-thinking-high",
        "sonnet": "claude-4.6-sonnet-medium-thinking",
        "haiku": "claude-4.5-haiku-thinking",
        "gpt": "gpt-5.5",
        "gpt-codex": "gpt-5.2-codex",
        "auto": "auto",
    }

    # Hard cap on a single gate spawn. Most gates finish well under this;
    # a runaway tool loop would otherwise hang the runner indefinitely.
    SPAWN_TIMEOUT_SECONDS = int(os.environ.get("SFLO_CURSOR_TIMEOUT", "1800"))

    # CLI binary name. Override via SFLO_CURSOR_BIN for non-PATH installs.
    BIN = os.environ.get("SFLO_CURSOR_BIN", "cursor-agent")

    async def spawn_agent(
        self,
        model,
        system_prompt,
        user_prompt,
        cwd=None,
        role=None,
        allowed_tools=None,
        workspace=None,
        **kwargs,
    ):
        # workspace: absolute path to the project root where .cursor/ lives.
        # Passed as --workspace to cursor-agent so it discovers rules and
        # MCP servers configured in the IDE. Caller (runner) is responsible
        # for providing the correct value. If None, cursor-agent falls back
        # to its own cwd (which may not have .cursor/).
        #
        # cwd: working directory for the subprocess. Used for dev/qa gates
        # so file operations resolve relative to the output directory.
        #
        # allowed_tools: accepted for API parity with ClaudeCodeAdapter
        # but cursor-agent CLI doesn't expose per-spawn tool gating in
        # print mode. Tool restrictions live in the rule prompt.
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
            "--output-format",
            "json",
            "--force",
            "--approve-mcps",
            "--trust",
            "--model",
            self.resolve_model(model),
        ]

        if workspace:
            cmd += ["--workspace", workspace]

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

        # Windows: cursor-agent ships as a .CMD shim. CreateProcess cannot
        # launch .CMD files directly. Instead of routing through cmd.exe
        # (which strips '!', expands %VAR%, and treats & | < > ^ as
        # operators — corrupting workspace paths like C:\Projects\!SFLO),
        # resolve the real entrypoint and spawn it directly with shell=False.
        if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
            # Replace the shim path at cmd[0] with the resolved entrypoint.
            # _resolve_node_shim returns a direct prefix — [exe], [node, script],
            # or a `powershell -File <.ps1>` invocation — none of which let
            # cmd.exe mangle arguments. If the shim is unrecognised it returns
            # [resolved] (the .cmd itself); CreateProcess can't launch a .cmd
            # with shell=False, so fall back to cmd.exe /c to keep the gate
            # running, and warn on stderr that metacharacters may be mangled.
            prefix = _resolve_node_shim(resolved)
            if prefix == [resolved]:
                _safe_stderr(
                    f"[cursor-adapter] warn: could not parse shim {resolved!r}; "
                    "falling back to cmd.exe /c "
                    "(workspace paths with ! or % may be corrupted)"
                )
                cmd = ["cmd.exe", "/c"] + cmd
            else:
                cmd = prefix + cmd[1:]

        start = _time.time()
        # Isolate cli-config.json per invocation to prevent race conditions
        # when multiple cursor-agent instances run in parallel (Gate 3).
        # Each instance gets a temp config dir; cursor-agent copies the
        # global config on startup so auth/model settings are preserved.
        config_dir = tempfile.mkdtemp(prefix="sflo-cursor-")
        # Seed with current config so auth works
        global_config = os.path.join(
            os.environ.get("USERPROFILE") or os.path.expanduser("~"),
            ".cursor", "cli-config.json"
        )
        if os.path.isfile(global_config):
            shutil.copy2(global_config, os.path.join(config_dir, "cli-config.json"))

        env = os.environ.copy()
        env["CURSOR_CONFIG_DIR"] = config_dir

        try:
            stdout_b, stderr_b, returncode = await asyncio.to_thread(
                self._run_with_pipes, cmd, prompt_bytes, cwd, env
            )
        except subprocess.TimeoutExpired:
            raise TransientError(
                f"cursor-agent timed out after {self.SPAWN_TIMEOUT_SECONDS}s "
                f"(role={role}, model={model}). "
                "Increase via SFLO_CURSOR_TIMEOUT env var if expected."
            )
        except FileNotFoundError as e:
            raise NonRetryableError(
                f"Failed to spawn cursor-agent: {e}. "
                "Verify the CLI is installed and on PATH."
            )
        finally:
            shutil.rmtree(config_dir, ignore_errors=True)

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        elapsed = _time.time() - start

        if returncode != 0:
            low = (stderr + stdout).lower()
            tail = stderr.strip().splitlines()[-20:]
            msg = (
                f"cursor-agent failed (exit {returncode}, "
                f"elapsed {elapsed:.0f}s)\n"
                f"stderr (last 20 lines):\n  " + "\n  ".join(tail)
            )
            # Auth failures are non-retryable — user must fix credentials
            if "unauthor" in low or "login" in low or "401" in low:
                raise NonRetryableError(
                    msg + " (run `cursor-agent login` or set CURSOR_API_KEY)"
                )
            # Rate limits and server errors are transient
            if "429" in low or "rate" in low or "503" in low or "502" in low:
                raise TransientError(msg)
            # Unknown failures — treat as transient (safe default for retry)
            raise TransientError(msg)

        # In --output-format json mode, cursor-agent emits exactly ONE JSON
        # object on stdout when the run completes. Parse defensively — fall
        # back to raw stdout if for any reason it isn't valid JSON.
        text = self._extract_text(stdout)

        _safe_stderr(
            f"  [Cursor agent — role={role}, model={self.resolve_model(model)}, "
            f"elapsed={elapsed:.0f}s, chars={len(text)}]"
        )

        return text

    def _run_with_pipes(self, cmd, input_bytes, cwd=None, env=None):
        """Run cursor-agent with pipes, reading output without waiting for EOF.

        Returns (stdout_bytes, stderr_bytes, returncode).

        On Windows, cursor-agent may spawn long-lived child processes (e.g.
        HTTP servers for testing) that inherit our pipe handles. Standard
        communicate() blocks until ALL holders close the pipe (waiting for
        EOF). We avoid this by:
          1. process.wait(timeout) — wait for cursor-agent itself to exit
          2. PeekNamedPipe — read available bytes from pipe buffer without
             blocking on EOF

        On non-Windows platforms, communicate() works normally (child
        processes don't inherit handles the same way).
        """
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            shell=False,
        )
        _assign_to_job(proc)
        if os.name != "nt":
            # Unix: communicate() handles stdin and drains both pipes
            # safely — child processes don't inherit pipe handles the way
            # they do on Windows, so there is no grandchild-holds-the-pipe
            # hang to work around. communicate() owns stdin; do NOT write
            # or close proc.stdin beforehand (that breaks communicate()).
            try:
                stdout_b, stderr_b = proc.communicate(
                    input=input_bytes, timeout=self.SPAWN_TIMEOUT_SECONDS
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise
            return stdout_b, stderr_b, proc.returncode

        # Windows: write the whole prompt to stdin up front, wait for
        # cursor-agent to exit, then drain the pipes non-blocking. The
        # drain avoids hanging on a grandchild process that inherited our
        # pipe handle. SFLO prompts sit well under the OS pipe buffer
        # (~64 KB), so the single up-front write won't deadlock.
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=self.SPAWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self._tree_kill(proc)
            raise

        stdout_b = self._drain_pipe(proc.stdout)
        stderr_b = self._drain_pipe(proc.stderr)
        return stdout_b, stderr_b, proc.returncode

    @staticmethod
    def _tree_kill(proc):
        """Kill a process and all its descendants.

        On Windows, uses taskkill /T /F (tree kill) which terminates the
        entire process tree rooted at proc.pid. This catches cmd.exe →
        cursor-agent → node/python grandchild chains that proc.kill()
        alone would orphan.

        Falls back to proc.kill() if taskkill is unavailable or fails.
        """
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                proc.kill()
        else:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    @staticmethod
    def _drain_pipe(pipe):
        """Read all available bytes from a pipe without blocking on EOF.

        Uses PeekNamedPipe (Windows) to check buffer contents, then reads
        exactly the available amount. Doesn't wait for grandchild processes
        to close the pipe handle.
        """
        if pipe is None:
            return b""
        try:
            handle = msvcrt.get_osfhandle(pipe.fileno())
            avail = wintypes.DWORD(0)
            _PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None)
            if avail.value > 0:
                return os.read(pipe.fileno(), avail.value)
            return b""
        except (OSError, ValueError):
            return b""

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
        # Single key set scanned at BOTH levels — top-level and nested-data
        # must stay consistent so a body nested under "data" is extracted
        # the same way it would be at the top level (previously "message"
        # was scanned only at the top level).
        _RESULT_KEYS = ("result", "text", "content", "message")
        for key in _RESULT_KEYS:
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
        # Some shapes nest the body under "data"
        nested = data.get("data") if isinstance(data.get("data"), dict) else None
        if nested:
            for key in _RESULT_KEYS:
                val = nested.get(key)
                if isinstance(val, str) and val:
                    return val
        # Last resort: dump the JSON so downstream validators see *something*
        return json.dumps(data)
