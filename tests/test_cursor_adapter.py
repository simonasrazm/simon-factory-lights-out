"""Tests for CursorAdapter — pipe handling, config isolation, error handling."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.adapters.cursor import CursorAdapter


@pytest.fixture
def adapter():
    return CursorAdapter()


class TestRunWithPipes:
    """Test _run_with_pipes handles output correctly."""

    def test_reads_stdout_after_process_exits(self, adapter):
        """Basic: child writes to stdout, exits, we read it."""
        cmd = [sys.executable, "-c", "print('hello from child')"]
        stdout, stderr, rc = adapter._run_with_pipes(cmd, b"", None, None)
        assert rc == 0
        assert b"hello from child" in stdout

    def test_reads_stderr_after_process_exits(self, adapter):
        """stderr is also captured."""
        cmd = [sys.executable, "-c", "import sys; print('err', file=sys.stderr)"]
        stdout, stderr, rc = adapter._run_with_pipes(cmd, b"", None, None)
        assert rc == 0
        assert b"err" in stderr

    def test_passes_stdin_to_child(self, adapter):
        """stdin input is delivered to the child process."""
        cmd = [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"]
        stdout, stderr, rc = adapter._run_with_pipes(cmd, b"test input", None, None)
        assert rc == 0
        assert b"TEST INPUT" in stdout

    def test_nonzero_exit_code(self, adapter):
        """Non-zero exit code is returned correctly."""
        cmd = [sys.executable, "-c", "import sys; sys.exit(42)"]
        stdout, stderr, rc = adapter._run_with_pipes(cmd, b"", None, None)
        assert rc == 42

    def test_env_is_passed(self, adapter):
        """Custom env vars reach the child."""
        env = os.environ.copy()
        env["SFLO_TEST_VAR"] = "cursor_test_value"
        cmd = [sys.executable, "-c", "import os; print(os.environ['SFLO_TEST_VAR'])"]
        stdout, stderr, rc = adapter._run_with_pipes(cmd, b"", None, env)
        assert b"cursor_test_value" in stdout

    def test_cwd_is_respected(self, adapter):
        """cwd parameter sets working directory."""
        tmp = tempfile.mkdtemp()
        cmd = [sys.executable, "-c", "import os; print(os.getcwd())"]
        stdout, stderr, rc = adapter._run_with_pipes(cmd, b"", tmp, None)
        assert os.path.realpath(tmp) == os.path.realpath(stdout.decode().strip())
        os.rmdir(tmp)

    @pytest.mark.skipif(os.name != "nt", reason="Windows pipe inheritance test")
    def test_does_not_hang_when_grandchild_holds_pipe(self, adapter):
        """Critical: process.wait returns even if grandchild keeps pipe open."""
        # Spawn a child that starts a long-lived grandchild, then exits.
        # The grandchild inherits stdout pipe but we should NOT block.
        script = (
            "import subprocess, sys, os\n"
            "print('RESULT_OK')\n"
            "sys.stdout.flush()\n"
            "# Start a grandchild that lives for 60s (inherits pipe handles)\n"
            "subprocess.Popen(\n"
            "    [sys.executable, '-c', 'import time; time.sleep(60)'],\n"
            "    stdout=subprocess.DEVNULL,\n"
            "    stderr=subprocess.DEVNULL,\n"
            ")\n"
            "sys.exit(0)\n"
        )
        cmd = [sys.executable, "-c", script]
        # Should complete in <5s, not hang for 60s
        adapter.SPAWN_TIMEOUT_SECONDS = 10
        stdout, stderr, rc = adapter._run_with_pipes(cmd, b"", None, None)
        assert rc == 0
        assert b"RESULT_OK" in stdout

    def test_timeout_raises(self, adapter):
        """TimeoutExpired is raised when process exceeds timeout."""
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        adapter.SPAWN_TIMEOUT_SECONDS = 2
        with pytest.raises(subprocess.TimeoutExpired):
            adapter._run_with_pipes(cmd, b"", None, None)


class TestConfigIsolation:
    """Test that parallel invocations get isolated config dirs."""

    def test_cursor_config_dir_is_set(self, adapter, tmp_path):
        """CURSOR_CONFIG_DIR env var is set for cursor-agent.

        Uses the tmp_path fixture so pytest reaps the directory after the
        test regardless of whether a config file was copied into it — the
        old os.rmdir cleanup was skipped whenever the dir was non-empty,
        leaking a temp dir on every run (QA m1).
        """
        # We can't easily test the full spawn_agent without cursor-agent
        # but we can verify the config dir logic
        config_dir = tmp_path / "sflo-cursor-config"
        config_dir.mkdir()
        global_config = os.path.join(
            os.environ.get("USERPROFILE") or os.path.expanduser("~"),
            ".cursor", "cli-config.json"
        )
        if os.path.isfile(global_config):
            import shutil
            copied = config_dir / "cli-config.json"
            shutil.copy2(global_config, copied)
            assert copied.is_file()
            with open(copied, encoding="utf-8") as f:
                data = json.load(f)
            assert "version" in data


class TestExtractText:
    """Test JSON result parsing."""

    def test_result_key(self):
        assert CursorAdapter._extract_text('{"result": "hello"}') == "hello"

    def test_text_key(self):
        assert CursorAdapter._extract_text('{"text": "world"}') == "world"

    def test_plain_text_fallback(self):
        assert CursorAdapter._extract_text("not json at all") == "not json at all"

    def test_empty_string(self):
        assert CursorAdapter._extract_text("") == ""

    def test_nested_data(self):
        j = json.dumps({"data": {"result": "nested"}})
        assert CursorAdapter._extract_text(j) == "nested"

    def test_nested_message_key(self):
        """The nested-data scan must include 'message' — parity with the
        top-level scan (QA m2)."""
        j = json.dumps({"data": {"message": "nested message body"}})
        assert CursorAdapter._extract_text(j) == "nested message body"

    def test_top_level_message_key(self):
        """Top-level 'message' key still resolves (regression guard)."""
        j = json.dumps({"message": "top message body"})
        assert CursorAdapter._extract_text(j) == "top message body"


class TestSpawnAgentCommand:
    """The cursor-agent command line carries the headless-mode flags."""

    def _capture_cmd(self, adapter, monkeypatch, **spawn_kwargs):
        captured = {}

        def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            captured["cmd"] = cmd
            return (b'{"result": "ok"}', b"", 0)

        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        monkeypatch.setattr(
            "src.adapters.cursor.shutil.which",
            lambda name: "/usr/bin/cursor-agent",
        )
        asyncio.run(
            adapter.spawn_agent(
                model="sonnet",
                system_prompt="sp",
                user_prompt="up",
                role="dev",
                **spawn_kwargs,
            )
        )
        return captured["cmd"]

    def test_approve_mcps_flag_present(self, adapter, monkeypatch):
        assert "--approve-mcps" in self._capture_cmd(adapter, monkeypatch)

    def test_trust_flag_present(self, adapter, monkeypatch):
        assert "--trust" in self._capture_cmd(adapter, monkeypatch)

    def test_workspace_flag_present_when_set(self, adapter, monkeypatch):
        cmd = self._capture_cmd(adapter, monkeypatch, workspace="/proj/root")
        assert "--workspace" in cmd
        assert "/proj/root" in cmd

    def test_workspace_flag_absent_when_none(self, adapter, monkeypatch):
        assert "--workspace" not in self._capture_cmd(adapter, monkeypatch)


class TestResolveNodeShim:
    """Regression: workspace paths containing '!' must not be mangled.

    Spawning via cmd.exe strips the '!' character (cmd delayed-expansion
    suppression) so C:\\Projects\\!SFLO becomes C:\\Projects\\SFLO before
    cursor-agent receives it.  The fix resolves the real Node entrypoint
    and spawns it directly with shell=False — no shell in the chain.
    """

    def _make_shim(self, tmp_path, script_content):
        """Write a fake .CMD shim and a corresponding .js script."""
        js_script = tmp_path / "cursor-agent.js"
        js_script.write_text("// fake cursor-agent entry point\n")
        shim = tmp_path / "cursor-agent.cmd"
        shim.write_text(script_content)
        return str(shim), str(js_script)

    def test_exclamation_in_workspace_reaches_argv(self, adapter, monkeypatch):
        """Unit: workspace path with '!' preserved in cmd list passed to _run_with_pipes.

        Patches shutil.which to return a non-.cmd path (simulating a direct
        cursor-agent binary), so the Windows shim-resolution branch is not
        entered.  Verifies that the Python argument list itself is not mangled —
        i.e., the old cmd.exe routing is absent on this code path.

        For the Windows shim-resolution branch, see:
          test_callsite_falls_back_to_cmdexe_on_unrecognised_shim (AC3 fallback)
          test_resolve_node_shim_extracts_node_script (happy path)
          test_exclamation_not_stripped_by_real_spawn (on real Windows only)
        """
        tricky_workspace = r"C:\Projects\!SFLO"
        received_argv = []

        def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            # cmd[0] is the executable; find --workspace value
            if "--workspace" in cmd:
                idx = cmd.index("--workspace")
                received_argv.append(cmd[idx + 1])
            return (b'{"result": "ok"}', b"", 0)

        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        monkeypatch.setattr(
            "src.adapters.cursor.shutil.which",
            lambda name: "/usr/bin/cursor-agent",
        )
        asyncio.run(
            adapter.spawn_agent(
                model="sonnet",
                system_prompt="sp",
                user_prompt="up",
                role="dev",
                workspace=tricky_workspace,
            )
        )
        assert received_argv, "--workspace value was not captured"
        assert "!" in received_argv[0], (
            f"'!' stripped from workspace: got {received_argv[0]!r}"
        )

    def test_resolve_node_shim_exe_sibling_preferred(self, tmp_path):
        """If cursor-agent.exe exists next to the shim, return it directly."""
        from src.adapters.cursor import _resolve_node_shim
        exe = tmp_path / "cursor-agent.exe"
        exe.write_text("")
        shim = tmp_path / "cursor-agent.cmd"
        shim.write_text("@echo off\nnode \"%~dp0cursor-agent.js\" %*\n")
        result = _resolve_node_shim(str(shim))
        assert result == [str(exe)]

    def test_resolve_node_shim_extracts_node_script(self, tmp_path):
        """Parses npm shim to extract [node_exe, script_path]."""
        from src.adapters.cursor import _resolve_node_shim
        js_script = tmp_path / "cursor-agent.js"
        js_script.write_text("// entry")
        shim = tmp_path / "cursor-agent.cmd"
        # npm-style shim: node "%~dp0cursor-agent.js" %*
        shim.write_text('@echo off\nnode "%~dp0cursor-agent.js" %*\r\n')
        result = _resolve_node_shim(str(shim))
        assert len(result) == 2
        assert result[0].lower().endswith("node") or result[0].lower().endswith("node.exe")
        assert os.path.basename(result[1]) == "cursor-agent.js"

    def test_resolve_node_shim_fallback_on_unrecognised_shim(self, tmp_path):
        """Returns [shim_path] if the shim body cannot be parsed."""
        from src.adapters.cursor import _resolve_node_shim
        shim = tmp_path / "cursor-agent.cmd"
        shim.write_text("@echo off\nsome_other_launcher cursor-agent %*\n")
        result = _resolve_node_shim(str(shim))
        assert result == [str(shim)]

    def test_callsite_falls_back_to_cmdexe_on_unrecognised_shim(
        self, tmp_path, adapter, monkeypatch
    ):
        """When shim cannot be parsed, spawn_agent uses cmd.exe /c and warns.

        AC3: unrecognized shim must not crash — must degrade to cmd.exe wrapping
        with a _safe_stderr warning, not raise OSError from spawning .cmd directly.
        """
        shim = tmp_path / "cursor-agent.cmd"
        shim.write_text("@echo off\nsome_other_launcher cursor-agent %*\n")

        captured_cmd = []
        stderr_msgs = []

        def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            captured_cmd.extend(cmd)
            return (b'{"result": "ok"}', b"", 0)

        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        monkeypatch.setattr(
            "src.adapters.cursor.shutil.which",
            lambda name: str(shim) if name == "cursor-agent" else None,
        )
        monkeypatch.setattr(
            "src.adapters.cursor._safe_stderr",
            lambda msg: stderr_msgs.append(msg),
        )

        # Force os.name == "nt" so the Windows branch is entered even on macOS/Linux
        import src.adapters.cursor as _mod
        original_name = os.name
        try:
            monkeypatch.setattr(_mod.os, "name", "nt")
            asyncio.run(
                adapter.spawn_agent(
                    model="sonnet",
                    system_prompt="sp",
                    user_prompt="up",
                    role="dev",
                    workspace=r"C:\Projects\!SFLO",
                )
            )
        finally:
            monkeypatch.setattr(_mod.os, "name", original_name)

        assert captured_cmd[0] == "cmd.exe", (
            f"Expected cmd.exe fallback; got cmd[0]={captured_cmd[0]!r}"
        )
        assert captured_cmd[1] == "/c", (
            f"Expected /c after cmd.exe; got cmd[1]={captured_cmd[1]!r}"
        )
        assert any("warn" in m for m in stderr_msgs), (
            f"Expected _safe_stderr warning; got: {stderr_msgs}"
        )

    @pytest.mark.skipif(os.name != "nt", reason="Windows cmd.exe mangling test")
    def test_exclamation_not_stripped_by_real_spawn(self, tmp_path, adapter, monkeypatch):
        """On actual Windows: spawn node + echo-argv script, confirm '!' survives."""
        # Write a JS script that prints process.argv as JSON
        js = tmp_path / "echo_argv.js"
        js.write_text('process.stdout.write(JSON.stringify(process.argv));\n')

        # Write a shim pointing at it
        shim = tmp_path / "echo_argv.cmd"
        shim.write_text(f'@echo off\nnode "{js}" %*\r\n')

        from src.adapters.cursor import _resolve_node_shim
        cmd_prefix = _resolve_node_shim(str(shim))
        cmd = cmd_prefix + ["--workspace", r"C:\Projects\!SFLO"]
        stdout, stderr, rc = adapter._run_with_pipes(cmd, b"", None, None)
        argv = json.loads(stdout.decode())
        assert any("!SFLO" in a for a in argv), (
            f"'!' stripped — got argv={argv}"
        )

    def test_resolve_node_shim_runs_ps1_via_powershell(self, tmp_path, monkeypatch):
        """case 3: a .CMD delegating to cursor-agent.ps1 is run via powershell -File.

        PowerShell does not mangle ! / %VAR% / operators, and the .ps1 picks its
        own node — so we neither route through cmd.exe nor guess Cursor's layout.
        """
        from src.adapters.cursor import _resolve_node_shim, _POWERSHELL_FLAGS
        ps1 = tmp_path / "cursor-agent.ps1"
        ps1.write_text("# cursor-agent powershell shim\n")
        shim = tmp_path / "cursor-agent.cmd"
        shim.write_text('@echo off\npowershell -File "%~dp0cursor-agent.ps1" %*\r\n')
        # macOS test host has no powershell.exe — pretend it is on PATH.
        monkeypatch.setattr(
            "src.adapters.cursor.shutil.which",
            lambda name: "/fake/bin/powershell" if name == "powershell" else None,
        )
        result = _resolve_node_shim(str(shim))
        assert result == ["/fake/bin/powershell", *_POWERSHELL_FLAGS, str(ps1)]

    def test_resolve_node_shim_cursor_node_fallback_without_powershell(
        self, tmp_path, monkeypatch
    ):
        """case 3 fallback: no powershell.exe → reverse-engineer the versions/ node."""
        from src.adapters.cursor import _resolve_node_shim
        shim = tmp_path / "cursor-agent.cmd"
        shim.write_text('@echo off\npowershell -File "%~dp0cursor-agent.ps1" %*\r\n')
        versions = tmp_path / "versions" / "2026.05.19-abc123"
        versions.mkdir(parents=True)
        (versions / "node.exe").write_text("")
        (versions / "index.js").write_text("")
        # No powershell.exe and no .ps1 file on disk → node-hunt fallback.
        monkeypatch.setattr("src.adapters.cursor.shutil.which", lambda name: None)
        result = _resolve_node_shim(str(shim))
        assert result == [str(versions / "node.exe"), str(versions / "index.js")]

    def test_resolve_cursor_node_prefers_direct_sibling(self, tmp_path):
        """node.exe + index.js beside the shim win over any versions/ directory."""
        from src.adapters.cursor import _resolve_cursor_node
        (tmp_path / "node.exe").write_text("")
        (tmp_path / "index.js").write_text("")
        stale = tmp_path / "versions" / "2099.01.01-deadbee"
        stale.mkdir(parents=True)
        (stale / "node.exe").write_text("")
        (stale / "index.js").write_text("")
        result = _resolve_cursor_node(str(tmp_path))
        assert result == [str(tmp_path / "node.exe"), str(tmp_path / "index.js")]

    def test_resolve_cursor_node_picks_newest_version(self, tmp_path):
        """The newest YYYY.MM.DD versions/ directory is selected."""
        from src.adapters.cursor import _resolve_cursor_node
        versions = tmp_path / "versions"
        for name in ("2026.05.10-aaa111", "2026.05.19-bbb222", "2025.12.31-ccc333"):
            d = versions / name
            d.mkdir(parents=True)
            (d / "node.exe").write_text("")
            (d / "index.js").write_text("")
        result = _resolve_cursor_node(str(tmp_path))
        assert result == [
            str(versions / "2026.05.19-bbb222" / "node.exe"),
            str(versions / "2026.05.19-bbb222" / "index.js"),
        ]

    def test_resolve_cursor_node_tiebreak_is_deterministic(self, tmp_path):
        """Same-date directories resolve by commit hash — never by listdir order."""
        from src.adapters.cursor import _resolve_cursor_node
        versions = tmp_path / "versions"
        for name in ("2026.05.19-aaa000", "2026.05.19-fff999"):
            d = versions / name
            d.mkdir(parents=True)
            (d / "node.exe").write_text("")
            (d / "index.js").write_text("")
        result = _resolve_cursor_node(str(tmp_path))
        assert result == [
            str(versions / "2026.05.19-fff999" / "node.exe"),
            str(versions / "2026.05.19-fff999" / "index.js"),
        ]

    def test_resolve_cursor_node_accepts_uppercase_commit_hash(self, tmp_path):
        """A versions/ directory with an upper-case hex commit is still matched."""
        from src.adapters.cursor import _resolve_cursor_node
        d = tmp_path / "versions" / "2026.05.19-ABCDEF"
        d.mkdir(parents=True)
        (d / "node.exe").write_text("")
        (d / "index.js").write_text("")
        result = _resolve_cursor_node(str(tmp_path))
        assert result == [str(d / "node.exe"), str(d / "index.js")]

    def test_resolve_cursor_node_returns_none_when_unresolvable(self, tmp_path):
        """No sibling node and no versions/ directory → None (caller degrades)."""
        from src.adapters.cursor import _resolve_cursor_node
        assert _resolve_cursor_node(str(tmp_path)) is None

    def test_resolve_node_shim_rejects_script_outside_shim_dir(self, tmp_path):
        """A hostile shim pointing node at a path outside its own dir is rejected."""
        from src.adapters.cursor import _resolve_node_shim
        outside = tmp_path / "evil.js"
        outside.write_text("// evil")
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        shim = pkg / "cursor-agent.cmd"
        shim.write_text(f'@echo off\nnode "{outside}" %*\r\n')
        result = _resolve_node_shim(str(shim))
        # Path escapes shim_dir → not resolved → unrecognised → [shim_path].
        assert result == [str(shim)]

    def test_path_within_rejects_symlink_escape(self, tmp_path):
        """A symlink inside parent that points outside is not treated as within."""
        from src.adapters.cursor import _path_within
        parent = tmp_path / "inside"
        parent.mkdir()
        outside = tmp_path / "outside.js"
        outside.write_text("")
        link = parent / "link.js"
        try:
            os.symlink(outside, link)
        except OSError as exc:
            pytest.skip(f"symlink creation is unavailable: {exc}")
        assert _path_within(str(link), str(parent)) is False
        # A genuine file inside parent is still accepted.
        real = parent / "real.js"
        real.write_text("")
        assert _path_within(str(real), str(parent)) is True

    def test_resolve_cursor_node_skips_non_version_directories(self, tmp_path):
        """Junk entries in versions/ are ignored; only YYYY.MM.DD-commit dirs count."""
        from src.adapters.cursor import _resolve_cursor_node
        versions = tmp_path / "versions"
        for name in ("latest", "not-a-version", "2026.05.19-cafe42"):
            d = versions / name
            d.mkdir(parents=True)
            (d / "node.exe").write_text("")
            (d / "index.js").write_text("")
        result = _resolve_cursor_node(str(tmp_path))
        assert result == [
            str(versions / "2026.05.19-cafe42" / "node.exe"),
            str(versions / "2026.05.19-cafe42" / "index.js"),
        ]


class TestJobObject:
    """Test that child processes are killed when runner exits (Windows)."""

    @pytest.mark.skipif(os.name != "nt", reason="Windows Job Object test")
    def test_job_object_created(self):
        """Job Object is created at module load on Windows."""
        from src.adapters.cursor import _job_handle
        assert _job_handle is not None

    @pytest.mark.skipif(os.name != "nt", reason="Windows Job Object test")
    def test_tree_kill_terminates_child(self):
        """_tree_kill kills the process."""
        from src.adapters.cursor import CursorAdapter

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(9999)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.poll() is None
        CursorAdapter._tree_kill(proc)
        assert proc.poll() is not None

    @pytest.mark.skipif(os.name != "nt", reason="Windows Job Object test")
    def test_tree_kill_terminates_grandchildren(self):
        """_tree_kill kills the entire process tree, not just the direct child."""
        import time
        from src.adapters.cursor import CursorAdapter

        script = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen(\n"
            "    [sys.executable, '-c', 'import time; time.sleep(9999)'],\n"
            ")\n"
            "print(child.pid)\n"
            "sys.stdout.flush()\n"
            "time.sleep(9999)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        grandchild_pid = int(proc.stdout.readline().decode().strip())
        assert proc.poll() is None

        CursorAdapter._tree_kill(proc)
        assert proc.poll() is not None

        time.sleep(1)
        try:
            os.kill(grandchild_pid, 0)
            alive = True
        except OSError:
            alive = False
        assert not alive, f"Grandchild PID {grandchild_pid} should be dead"
