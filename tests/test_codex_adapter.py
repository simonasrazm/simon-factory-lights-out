"""Tests for CodexAdapter - command shape, output, and error handling."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.adapters.codex import (
    CodexAdapter,
    _sandbox_for_tools_mode,
    resolve_codex_argv,
)
from src.adapters.errors import NonRetryableError, TransientError


@pytest.fixture
def adapter():
    return CodexAdapter()


class TestCodexSpawnAgent:
    def _run_spawn(self, adapter, monkeypatch, **spawn_kwargs):
        captured = {}
        model = spawn_kwargs.pop("model", "gpt-5.6-sol")

        async def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            captured["cmd"] = cmd
            captured["input"] = input_bytes
            captured["cwd"] = cwd
            captured["env"] = env
            out_path = cmd[cmd.index("--output-last-message") + 1]
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("final answer")
            return (b"transcript text", b"", 0)

        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        monkeypatch.setattr(
            "src.adapters.codex.resolve_codex_argv",
            lambda env: ["/usr/bin/codex"],
        )

        result = asyncio.run(
            adapter.spawn_agent(
                model=model,
                system_prompt="system spec",
                user_prompt="user task",
                role="dev",
                **spawn_kwargs,
            )
        )
        return result, captured

    def test_uses_codex_exec_noninteractive(self, adapter, monkeypatch):
        _, captured = self._run_spawn(adapter, monkeypatch)
        cmd = captured["cmd"]
        assert cmd[:4] == ["/usr/bin/codex", "-a", "never", "exec"]
        assert "--ephemeral" in cmd
        assert "--ignore-user-config" in cmd
        assert "--ignore-rules" in cmd
        assert "--skip-git-repo-check" in cmd
        assert cmd[cmd.index("--disable") + 1] == "plugins"
        assert cmd[-1] == "-"

    def test_pipes_combined_prompt_via_stdin(self, adapter, monkeypatch):
        _, captured = self._run_spawn(adapter, monkeypatch)
        body = captured["input"].decode("utf-8")
        assert "system spec" in body
        assert "user task" in body
        assert "# Role spec" in body

    def test_reads_final_output_file(self, adapter, monkeypatch):
        result, _ = self._run_spawn(adapter, monkeypatch)
        assert result == "final answer"

    def test_passes_cwd_as_codex_working_root(self, adapter, monkeypatch, tmp_path):
        project = tmp_path / "project"
        project.mkdir()

        _, captured = self._run_spawn(adapter, monkeypatch, cwd=str(project))
        cmd = captured["cmd"]
        assert "-C" in cmd
        assert cmd[cmd.index("-C") + 1] == str(project)
        assert captured["cwd"] == str(project)

    def test_output_file_uses_os_temp_and_is_deleted(self, adapter, monkeypatch, tmp_path):
        sflo_dir = tmp_path / ".sflo" / "fancy-click-counter"
        venv = sflo_dir / ".venv"
        _, captured = self._run_spawn(
            adapter,
            monkeypatch,
            env={"VIRTUAL_ENV": str(venv)},
        )
        cmd = captured["cmd"]
        out_path = Path(cmd[cmd.index("--output-last-message") + 1])
        assert out_path.parent != sflo_dir
        assert tmp_path not in out_path.parents
        assert out_path.name.startswith("codex-last-message-dev-")
        assert not out_path.exists()

    def test_merges_env_and_normalizes_term(self, adapter, monkeypatch):
        _, captured = self._run_spawn(
            adapter,
            monkeypatch,
            env={"SFLO_TEST": "codex", "TERM": "dumb"},
        )
        assert captured["env"]["SFLO_TEST"] == "codex"
        assert captured["env"]["TERM"] == "xterm-256color"

    def test_sflo_codex_home_sets_child_codex_home(self, adapter, monkeypatch, tmp_path):
        clean_home = tmp_path / "codex-clean"
        clean_home.mkdir()

        _, captured = self._run_spawn(
            adapter,
            monkeypatch,
            env={"SFLO_CODEX_HOME": str(clean_home)},
        )

        assert captured["env"]["CODEX_HOME"] == str(clean_home)

    def test_disable_features_can_be_overridden(self, adapter, monkeypatch):
        monkeypatch.setenv("SFLO_CODEX_DISABLE_FEATURES", "")
        _, captured = self._run_spawn(adapter, monkeypatch)
        assert "--disable" not in captured["cmd"]

    def test_readonly_tools_mode_uses_readonly_sandbox(self, adapter, monkeypatch):
        _, captured = self._run_spawn(adapter, monkeypatch, tools_mode="readonly")
        cmd = captured["cmd"]
        assert cmd[cmd.index("--sandbox") + 1] == "read-only"

    def test_effort_maps_to_reasoning_config(self, adapter, monkeypatch):
        _, captured = self._run_spawn(adapter, monkeypatch, effort="xhigh")
        cmd = captured["cmd"]
        assert "-c" in cmd
        assert 'model_reasoning_effort="xhigh"' in cmd

    def test_missing_model_uses_codex_default(self, adapter, monkeypatch):
        result, captured = self._run_spawn(adapter, monkeypatch, model=None)
        cmd = captured["cmd"]
        assert result == "final answer"
        assert cmd[cmd.index("--model") + 1] == "gpt-5.6-sol"

    def test_codex_alias_uses_current_codex_model(self, adapter, monkeypatch):
        _, captured = self._run_spawn(adapter, monkeypatch, model="gpt-codex")
        cmd = captured["cmd"]
        assert cmd[cmd.index("--model") + 1] == "gpt-5.6-sol"


class TestCodexErrors:
    @staticmethod
    def _stub_resolver(monkeypatch):
        monkeypatch.setattr(
            "src.adapters.codex.resolve_codex_argv",
            lambda env: ["/usr/bin/codex"],
        )

    def test_missing_binary_is_non_retryable(self, adapter, monkeypatch):
        monkeypatch.setenv("SFLO_CODEX_BIN", "definitely-missing-codex")
        with pytest.raises(NonRetryableError):
            asyncio.run(
                adapter.spawn_agent(
                    model="gpt-5.5",
                    system_prompt="sp",
                    user_prompt="up",
                )
            )

    def test_login_failure_is_non_retryable(self, adapter, monkeypatch):
        async def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            return (b"", b"Please login to Codex", 1)

        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        monkeypatch.setattr(
            "src.adapters.codex.resolve_codex_argv",
            lambda env: ["/usr/bin/codex"],
        )
        with pytest.raises(NonRetryableError):
            asyncio.run(
                adapter.spawn_agent(
                    model="gpt-5.5",
                    system_prompt="sp",
                    user_prompt="up",
                )
            )

    def test_rate_failure_is_transient(self, adapter, monkeypatch):
        async def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            return (b"", b"429 rate limit", 1)

        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        monkeypatch.setattr(
            "src.adapters.codex.resolve_codex_argv",
            lambda env: ["/usr/bin/codex"],
        )
        with pytest.raises(TransientError):
            asyncio.run(
                adapter.spawn_agent(
                    model="gpt-5.5",
                    system_prompt="sp",
                    user_prompt="up",
                )
            )

    def test_nonzero_exit_deletes_scratch_file(self, adapter, monkeypatch):
        captured = {}

        async def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            path = Path(cmd[cmd.index("--output-last-message") + 1])
            captured["path"] = path
            path.write_text("sensitive partial output", encoding="utf-8")
            return (b"", b"429 rate limit", 1)

        self._stub_resolver(monkeypatch)
        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        with pytest.raises(TransientError):
            asyncio.run(
                adapter.spawn_agent(
                    model="gpt-5.5", system_prompt="sp", user_prompt="up"
                )
            )
        assert not captured["path"].exists()

    def test_timeout_deletes_scratch_file(self, adapter, monkeypatch):
        captured = {}

        async def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            path = Path(cmd[cmd.index("--output-last-message") + 1])
            captured["path"] = path
            path.write_text("sensitive partial output", encoding="utf-8")
            raise subprocess.TimeoutExpired(cmd, 1)

        self._stub_resolver(monkeypatch)
        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        with pytest.raises(TransientError):
            asyncio.run(
                adapter.spawn_agent(
                    model="gpt-5.5", system_prompt="sp", user_prompt="up"
                )
            )
        assert not captured["path"].exists()

    def test_spawn_file_not_found_deletes_scratch_file(self, adapter, monkeypatch):
        captured = {}

        async def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            path = Path(cmd[cmd.index("--output-last-message") + 1])
            captured["path"] = path
            path.write_text("sensitive partial output", encoding="utf-8")
            raise FileNotFoundError("launcher disappeared")

        self._stub_resolver(monkeypatch)
        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        with pytest.raises(NonRetryableError, match="Failed to spawn Codex CLI"):
            asyncio.run(
                adapter.spawn_agent(
                    model="gpt-5.5", system_prompt="sp", user_prompt="up"
                )
            )
        assert not captured["path"].exists()

    def test_output_read_error_deletes_scratch_file(self, adapter, monkeypatch):
        captured = {}

        async def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            path = Path(cmd[cmd.index("--output-last-message") + 1])
            captured["path"] = path
            path.write_text("sensitive final output", encoding="utf-8")
            return (b"stdout", b"", 0)

        self._stub_resolver(monkeypatch)
        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        monkeypatch.setattr(
            adapter,
            "_read_output_file",
            lambda path: (_ for _ in ()).throw(OSError("read failed")),
        )
        with pytest.raises(OSError, match="read failed"):
            asyncio.run(
                adapter.spawn_agent(
                    model="gpt-5.5", system_prompt="sp", user_prompt="up"
                )
            )
        assert not captured["path"].exists()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixture")
    def test_cancellation_terminates_child_before_deleting_scratch(
        self, adapter, tmp_path
    ):
        fake_codex = tmp_path / "fake-codex"
        marker = tmp_path / "scratch-path"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, sys, time\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
            "out.write_text('partial', encoding='utf-8')\n"
            "pathlib.Path(os.environ['FAKE_CODEX_MARKER']).write_text(str(out))\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)

        async def cancel_spawn():
            task = asyncio.create_task(
                adapter.spawn_agent(
                    model="gpt-5.6-sol",
                    system_prompt="sp",
                    user_prompt="up",
                    env={
                        "SFLO_CODEX_BIN": str(fake_codex),
                        "FAKE_CODEX_MARKER": str(marker),
                    },
                )
            )
            for _ in range(200):
                if marker.exists():
                    break
                await asyncio.sleep(0.01)
            assert marker.exists()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(cancel_spawn())

        scratch = Path(marker.read_text(encoding="utf-8"))
        assert not scratch.exists()


class TestCodexHelpers:
    def test_sandbox_override_wins(self, monkeypatch):
        monkeypatch.setenv("SFLO_CODEX_SANDBOX", "danger-full-access")
        assert _sandbox_for_tools_mode("readonly") == "danger-full-access"

    def test_default_sandbox_is_workspace_write(self, monkeypatch):
        monkeypatch.delenv("SFLO_CODEX_SANDBOX", raising=False)
        assert _sandbox_for_tools_mode(None) == "workspace-write"


class TestCodexExecutableResolution:
    def _make_executable(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_reads_override_at_spawn_time_after_module_import(self, monkeypatch, tmp_path):
        binary = self._make_executable(tmp_path / "late codex")
        monkeypatch.setenv("SFLO_CODEX_BIN", str(binary))

        assert resolve_codex_argv(dict(os.environ)) == [str(binary)]

    def test_uses_effective_child_path(self, tmp_path):
        binary = self._make_executable(tmp_path / "bin" / "codex")
        env = {"PATH": str(binary.parent), "SFLO_CODEX_BIN": "codex"}

        assert resolve_codex_argv(env) == [str(binary)]

    @pytest.mark.parametrize("value", ['"/tmp/codex"', "'/tmp/codex'"])
    def test_rejects_shell_quoted_override(self, value):
        with pytest.raises(NonRetryableError, match="must not include shell quotes"):
            resolve_codex_argv({"SFLO_CODEX_BIN": value, "PATH": ""})

    def test_rejects_non_executable_unix_file(self, tmp_path):
        binary = tmp_path / "codex"
        binary.write_text("not executable", encoding="utf-8")
        binary.chmod(0o600)

        with pytest.raises(NonRetryableError, match="not executable"):
            resolve_codex_argv({"SFLO_CODEX_BIN": str(binary), "PATH": ""})

    def test_windows_pathext_resolves_cmd_to_sibling_exe(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.adapters.codex._is_windows", lambda: True)
        bin_dir = tmp_path / "windows bin"
        bin_dir.mkdir()
        (bin_dir / "codex.CMD").write_text("@echo off\n", encoding="utf-8")
        native = bin_dir / "codex.exe"
        native.write_bytes(b"MZ")
        env = {
            "PATH": str(bin_dir),
            "PATHEXT": ".COM;.EXE;.BAT;.CMD",
            "SFLO_CODEX_BIN": "codex",
        }

        assert resolve_codex_argv(env)[0].lower() == str(native).lower()

    def test_windows_cmd_uses_sibling_powershell_without_cmd_exe(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr("src.adapters.codex._is_windows", lambda: True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "codex.cmd"
        shim.write_text("@echo off\n", encoding="utf-8")
        ps1 = bin_dir / "codex.ps1"
        ps1.write_text("", encoding="utf-8")
        powershell = bin_dir / "pwsh.EXE"
        powershell.write_bytes(b"MZ")
        env = {
            "PATH": str(bin_dir),
            "PATHEXT": ".EXE;.CMD",
            "SFLO_CODEX_BIN": str(shim),
        }

        argv = resolve_codex_argv(env)
        assert argv[0] == str(powershell)
        assert argv[-2:] == ["-File", str(ps1)]
        assert "cmd.exe" not in [part.lower() for part in argv]

    def test_windows_path_search_does_not_implicitly_use_cwd(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr("src.adapters.codex._is_windows", lambda: True)
        (tmp_path / "codex.EXE").write_bytes(b"MZ")
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        monkeypatch.chdir(tmp_path)

        with pytest.raises(NonRetryableError, match="not found on PATH"):
            resolve_codex_argv(
                {
                    "PATH": str(empty_bin),
                    "PATHEXT": ".EXE;.CMD",
                    "SFLO_CODEX_BIN": "codex",
                }
            )

    def test_windows_explicit_ps1_uses_powershell_argv(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.adapters.codex._is_windows", lambda: True)
        script = tmp_path / "codex.ps1"
        script.write_text("", encoding="utf-8")
        powershell = tmp_path / "powershell.EXE"
        powershell.write_bytes(b"MZ")

        argv = resolve_codex_argv(
            {
                "PATH": str(tmp_path),
                "PATHEXT": ".EXE;.CMD",
                "SFLO_CODEX_BIN": str(script),
            }
        )
        assert argv[0].lower() == str(powershell).lower()
        assert argv[-2:] == ["-File", str(script)]

    def test_windows_rejects_batch_without_safe_launcher(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.adapters.codex._is_windows", lambda: True)
        shim = tmp_path / "codex.bat"
        shim.write_text("@echo off\n", encoding="utf-8")

        with pytest.raises(NonRetryableError, match="native codex.exe"):
            resolve_codex_argv(
                {
                    "PATH": str(tmp_path),
                    "PATHEXT": ".EXE;.BAT;.CMD",
                    "SFLO_CODEX_BIN": str(shim),
                }
            )


class TestCodexAdapterSelection:
    def test_explicit_codex_runtime_returns_codex_adapter(self):
        import src.adapters as adapters

        assert isinstance(adapters.get_adapter("codex"), adapters.CodexAdapter)

    def test_missing_runtime_refuses_auto_detection(self):
        import src.adapters as adapters

        with pytest.raises(RuntimeError, match="Runtime is required"):
            adapters.get_adapter(None)
