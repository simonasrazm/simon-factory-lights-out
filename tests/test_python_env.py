"""Tests for factory venv provisioning in runner.py.

The runner should provision .sflo/.venv and pass it to spawned agents
so they have a working Python environment with pip.

Run:
    cd <sflo-root>
    python3 -m pytest tests/test_python_env.py -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import venv

def _make_sflo_dir(with_venv: bool = False) -> str:
    d = tempfile.mkdtemp(prefix="sflo-test-factory-")
    if with_venv:
        venv.create(os.path.join(d, ".venv"), with_pip=False)
    return d


class TestFactoryEnvExists:

    def test_function_importable(self):
        from src.runner import _get_factory_env
        assert callable(_get_factory_env)


class TestNoSfloDir:

    def test_returns_none_for_none(self):
        from src.runner import _get_factory_env
        assert _get_factory_env(None) is None

    def test_returns_none_for_empty(self):
        from src.runner import _get_factory_env
        assert _get_factory_env("") is None


class TestProvisionVenv:

    def test_creates_venv_when_missing(self):
        from src.runner import _get_factory_env
        d = _make_sflo_dir(with_venv=False)
        try:
            env = _get_factory_env(d)
            assert env is not None
            assert os.path.isdir(os.path.join(d, ".venv"))
        finally:
            shutil.rmtree(d)

    def test_reuses_existing_venv(self):
        from src.runner import _get_factory_env
        d = _make_sflo_dir(with_venv=True)
        try:
            mtime = os.path.getmtime(os.path.join(d, ".venv", "pyvenv.cfg"))
            _get_factory_env(d)
            assert os.path.getmtime(os.path.join(d, ".venv", "pyvenv.cfg")) == mtime
        finally:
            shutil.rmtree(d)


class TestEnvDict:

    def test_returns_virtual_env(self):
        from src.runner import _get_factory_env
        d = _make_sflo_dir(with_venv=True)
        try:
            env = _get_factory_env(d)
            assert env["VIRTUAL_ENV"] == os.path.join(d, ".venv")
        finally:
            shutil.rmtree(d)

    def test_path_starts_with_venv_bin(self):
        from src.runner import _get_factory_env
        d = _make_sflo_dir(with_venv=True)
        try:
            env = _get_factory_env(d)
            scripts_dir = "Scripts" if os.name == "nt" else "bin"
            assert env["PATH"].startswith(os.path.join(d, ".venv", scripts_dir))
        finally:
            shutil.rmtree(d)

    def test_pip_works_in_provisioned_venv(self):
        from src.runner import _get_factory_env
        d = _make_sflo_dir(with_venv=False)
        try:
            env = _get_factory_env(d)
            full_env = {**os.environ, **env}
            result = subprocess.run(
                ["pip", "install", "--help"],
                env=full_env, capture_output=True, text=True,
            )
            assert result.returncode == 0
        finally:
            shutil.rmtree(d)


class TestRunnerWiring:

    def test_default_agent_runner_uses_factory_env(self):
        import inspect
        from src.runner import default_agent_runner
        source = inspect.getsource(default_agent_runner)
        assert "_get_factory_env" in source

    def test_adapter_accepts_env(self):
        import inspect
        from src.adapters.claude_code import ClaudeCodeAdapter
        sig = inspect.signature(ClaudeCodeAdapter.spawn_agent)
        assert "env" in sig.parameters
