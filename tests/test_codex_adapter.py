"""Tests for CodexAdapter - command shape, output, and error handling."""

import asyncio
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.adapters.codex import CodexAdapter, _sandbox_for_tools_mode
from src.adapters.errors import NonRetryableError, TransientError


@pytest.fixture
def adapter():
    return CodexAdapter()


class TestCodexSpawnAgent:
    def _run_spawn(self, adapter, monkeypatch, **spawn_kwargs):
        captured = {}
        model = spawn_kwargs.pop("model", "gpt-5.5")

        def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
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
            "src.adapters.codex.shutil.which",
            lambda name: "/usr/bin/codex" if name == "codex" else None,
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

    def test_output_file_uses_factory_state_dir(self, adapter, monkeypatch, tmp_path):
        sflo_dir = tmp_path / ".sflo" / "fancy-click-counter"
        venv = sflo_dir / ".venv"
        _, captured = self._run_spawn(
            adapter,
            monkeypatch,
            env={"VIRTUAL_ENV": str(venv)},
        )
        cmd = captured["cmd"]
        out_path = Path(cmd[cmd.index("--output-last-message") + 1])
        assert out_path.parent == sflo_dir
        assert out_path.name.startswith("codex-last-message-dev-")
        assert out_path.exists()

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
        assert cmd[cmd.index("--model") + 1] == "gpt-5.5"

    def test_codex_alias_uses_current_codex_model(self, adapter, monkeypatch):
        _, captured = self._run_spawn(adapter, monkeypatch, model="gpt-codex")
        cmd = captured["cmd"]
        assert cmd[cmd.index("--model") + 1] == "gpt-5.5"


class TestCodexErrors:
    def test_missing_binary_is_non_retryable(self, adapter, monkeypatch):
        monkeypatch.setattr("src.adapters.codex.shutil.which", lambda name: None)
        with pytest.raises(NonRetryableError):
            asyncio.run(
                adapter.spawn_agent(
                    model="gpt-5.5",
                    system_prompt="sp",
                    user_prompt="up",
                )
            )

    def test_login_failure_is_non_retryable(self, adapter, monkeypatch):
        def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            return (b"", b"Please login to Codex", 1)

        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        monkeypatch.setattr(
            "src.adapters.codex.shutil.which",
            lambda name: "/usr/bin/codex" if name == "codex" else None,
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
        def fake_run_with_pipes(cmd, input_bytes, cwd=None, env=None):
            return (b"", b"429 rate limit", 1)

        monkeypatch.setattr(adapter, "_run_with_pipes", fake_run_with_pipes)
        monkeypatch.setattr(
            "src.adapters.codex.shutil.which",
            lambda name: "/usr/bin/codex" if name == "codex" else None,
        )
        with pytest.raises(TransientError):
            asyncio.run(
                adapter.spawn_agent(
                    model="gpt-5.5",
                    system_prompt="sp",
                    user_prompt="up",
                )
            )


class TestCodexHelpers:
    def test_sandbox_override_wins(self, monkeypatch):
        monkeypatch.setenv("SFLO_CODEX_SANDBOX", "danger-full-access")
        assert _sandbox_for_tools_mode("readonly") == "danger-full-access"

    def test_default_sandbox_is_workspace_write(self, monkeypatch):
        monkeypatch.delenv("SFLO_CODEX_SANDBOX", raising=False)
        assert _sandbox_for_tools_mode(None) == "workspace-write"


class TestCodexAdapterSelection:
    def test_explicit_codex_runtime_returns_codex_adapter(self):
        import src.adapters as adapters

        assert isinstance(adapters.get_adapter("codex"), adapters.CodexAdapter)

    def test_missing_runtime_refuses_auto_detection(self):
        import src.adapters as adapters

        with pytest.raises(RuntimeError, match="Runtime is required"):
            adapters.get_adapter(None)
