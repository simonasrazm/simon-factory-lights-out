"""Shared test fixtures and helpers for SFLO tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
SCAFFOLD = os.path.join(SRC_DIR, "scaffold.py")
HOOK = os.path.join(SRC_DIR, "hooks", "claude-code", "stop_hook.py")

# Minimal valid bindings content
BINDINGS_YAML = "roles:\n  pm:\n    model: opus\n  dev:\n    model: sonnet\n  qa:\n    model: sonnet\n"

# Standard gate artifacts for test state construction
GATE_ARTIFACTS = {
    1: "SCOPE.md", 2: "BUILD-STATUS.md", 3: "QA-REPORT.md",
    4: "PM-VERIFY.md", 5: "SHIP-DECISION.md",
}

# Minimal passing artifact content for each gate
PASSING_ARTIFACTS = {
    "SCOPE.md": "## Data Sources\nNone\n## Acceptance Criteria\n- [x] AC1\n## Appetite\n30 min\n",
    "BUILD-STATUS.md": "Build: Success\nZero errors\n- [x] done\n",
    "QA-REPORT.md": "### Grade: A\n",
    "PM-VERIFY.md": "### Verdict: APPROVED\n",
    "SHIP-DECISION.md": "### Decision: SHIP\n",
}

# Standard bindings and assignments for state construction
DEFAULT_BINDINGS = {"pm": {"model": "opus"}, "dev": {"model": "sonnet"}, "qa": {"model": "sonnet"}}
DEFAULT_ASSIGNMENTS = {"pm": "agents/pm", "dev": "agents/dev", "qa": "agents/qa"}


def run_scaffold(*args, cwd=None):
    """Run scaffold.py as subprocess, return parsed JSON output."""
    result = subprocess.run(
        [sys.executable, SCAFFOLD, *args],
        capture_output=True, text=True, cwd=cwd,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"Non-JSON output: {result.stdout}\nStderr: {result.stderr}")


def run_hook(state_dir, stop_active=False):
    """Run the stop hook with an isolated .sflo directory."""
    project_dir = tempfile.mkdtemp()
    sflo_in_project = os.path.join(project_dir, ".sflo")
    shutil.copytree(state_dir, sflo_in_project)

    hook_input = json.dumps({
        "stop_hook_active": stop_active,
        "cwd": project_dir,
        "session_id": "test",
        "last_assistant_message": "test",
    })

    result = subprocess.run(
        [sys.executable, HOOK],
        input=hook_input, capture_output=True, text=True, timeout=15,
    )

    if os.path.isdir(sflo_in_project):
        marker = os.path.join(sflo_in_project, ".last_hook_state")
        if os.path.isfile(marker):
            shutil.copy2(marker, os.path.join(state_dir, ".last_hook_state"))

    shutil.rmtree(project_dir, ignore_errors=True)

    if result.stdout.strip():
        try:
            return json.loads(result.stdout), result.returncode
        except json.JSONDecodeError:
            return None, result.returncode
    return None, result.returncode


def make_state(current, inner=0, outer=0, bindings=None, assignments=None):
    """Create a state dict for testing."""
    return {
        "current_state": current,
        "bindings": bindings or DEFAULT_BINDINGS,
        "assignments": assignments or DEFAULT_ASSIGNMENTS,
        "inner_loops": inner,
        "outer_loops": outer,
        "gates": {str(g): {"status": "waiting", "artifact": a}
                  for g, a in GATE_ARTIFACTS.items()},
    }


class TempDirMixin:
    """Mixin providing a temp directory with .sflo/ for tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sflo_dir = os.path.join(self.tmpdir, ".sflo")
        os.makedirs(self.sflo_dir, exist_ok=True)
        os.makedirs(os.path.join(self.tmpdir, "gates"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def write_state(self, current, inner=0, outer=0):
        state = make_state(current, inner, outer)
        with open(os.path.join(self.sflo_dir, "state.json"), "w") as f:
            json.dump(state, f)

    def write_artifact(self, name, content):
        with open(os.path.join(self.sflo_dir, name), "w") as f:
            f.write(content)

    def read_state_file(self):
        with open(os.path.join(self.sflo_dir, "state.json")) as f:
            return json.load(f)
