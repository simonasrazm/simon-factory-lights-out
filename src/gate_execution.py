"""Shared execution helpers for pluggable gate runners."""

import importlib.util
import inspect
import os
import traceback
from dataclasses import dataclass
from typing import Callable


@dataclass
class CustomGateOutcome:
    """Result of executing a custom gate runner."""

    ok: bool
    artifact_path: str | None = None
    error: str | None = None


def load_custom_runner(runner_path):
    """Load a custom runner module from a relative file path via importlib.

    Returns (module, error_string). Rejects absolute paths and '..' traversal.
    Path resolves relative to cwd and must stay within cwd or SFLO_ROOT.
    """
    if not runner_path:
        return None, "runner path is empty"
    if os.path.isabs(runner_path):
        return None, f"Runner path must be relative: {runner_path}"
    parts = runner_path.replace("\\", "/").split("/")
    if ".." in parts:
        return None, f"Runner path must not contain '..': {runner_path}"

    abs_path = os.path.realpath(os.path.join(os.getcwd(), runner_path))
    cwd_real = os.path.realpath(os.getcwd())
    sflo_root_real = os.path.realpath(
        os.environ.get("SFLO_ROOT")
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    if not (
        abs_path.startswith(cwd_real + os.sep)
        or abs_path == cwd_real
        or abs_path.startswith(sflo_root_real + os.sep)
        or abs_path == sflo_root_real
    ):
        return None, f"Runner path resolves outside project: {abs_path}"

    if not os.path.isfile(abs_path):
        return None, f"Runner file not found: {abs_path}"

    spec = importlib.util.spec_from_file_location("_sflo_runner", abs_path)
    if spec is None:
        return None, f"Cannot load module from {abs_path}"
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        return None, f"Failed to load runner {abs_path}: {e}"

    if not hasattr(module, "run_gate") and not hasattr(module, "run"):
        return None, f"Runner {abs_path} has no run_gate() or run() function"

    return module, None


def _write_degraded_artifact(sflo_dir, artifact_name, body):
    if not artifact_name:
        return None
    artifact_path = os.path.join(sflo_dir, artifact_name)
    os.makedirs(os.path.dirname(artifact_path) or ".", exist_ok=True)
    with open(artifact_path, "w", encoding="utf-8") as f:
        f.write(body)
    return artifact_path


async def execute_custom_gate(
    *,
    gate_num,
    runner_path,
    gate_config,
    sflo_dir,
    output_dir,
    log: Callable[[str], None],
    label_prefix="Gate",
):
    """Run a custom gate module and write DEGRADED artifact on runner failure."""
    artifact_name = (
        gate_config.get("artifact") if isinstance(gate_config, dict) else None
    )
    label = f"{label_prefix} {gate_num}"
    log(f"{label} [custom runner: {runner_path}] ...")

    runner_module, load_err = load_custom_runner(runner_path)
    if load_err:
        log(f"{label} runner load FAILED: {load_err}")
        artifact_path = _write_degraded_artifact(
            sflo_dir,
            artifact_name,
            f"# Runner Error\n\nVerdict: DEGRADED\n\n{load_err}\n",
        )
        return CustomGateOutcome(ok=False, artifact_path=artifact_path, error=load_err)

    try:
        run_fn = getattr(runner_module, "run_gate", None) or runner_module.run
        result_or_coro = run_fn(gate_config, sflo_dir, output_dir)
        if inspect.iscoroutine(result_or_coro):
            await result_or_coro
        return CustomGateOutcome(ok=True)
    except Exception as e:
        tb = traceback.format_exc()
        log(f"{label} custom runner FAILED (DEGRADED): {e}")
        log(f"{label} traceback:\n{tb}")
        artifact_path = _write_degraded_artifact(
            sflo_dir,
            artifact_name,
            f"# Runner Error\n\nVerdict: DEGRADED\n\n{e}\n\n```\n{tb}\n```\n",
        )
        return CustomGateOutcome(ok=False, artifact_path=artifact_path, error=str(e))
