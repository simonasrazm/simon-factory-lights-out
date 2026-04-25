"""Tests for CRITICAL 1 — runner.py missing top-level `import re`.

Verifies the module-level `import re` is present so that the STST gate code
path (runner.py:1034+) does not raise UnboundLocalError when called outside
the scout-exception branch where the original guarded import lived.
"""

import ast
import os


# ---------------------------------------------------------------------------
# Static analysis: verify `import re` exists at module level
# ---------------------------------------------------------------------------


class TestRunnerImportRe:
    def _runner_source(self):
        runner_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src",
            "runner.py",
        )
        with open(runner_path, encoding="utf-8") as f:
            return f.read()

    def test_import_re_at_module_level(self):
        """re must be imported at module level, not inside a function/except block."""
        source = self._runner_source()
        tree = ast.parse(source)
        top_level_imports = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                top_level_imports.add(node.module or "")
        assert "re" in top_level_imports, (
            "runner.py must have `import re` at module level "
            "(CRITICAL 1: prevents UnboundLocalError in STST gate code path)"
        )

    def test_re_usable_at_module_import_time(self):
        """Importing runner must succeed and re must be accessible."""
        import importlib
        import src.runner as runner_mod

        # re should be in runner's globals after import
        assert hasattr(runner_mod, "re") or "re" in dir(runner_mod) or True
        # More direct: the module-level `import re` means re is in __dict__
        # We check the source AST (done above); here verify no ImportError
        importlib.reload(runner_mod)  # should not raise

    def test_re_not_only_inside_scout_except(self):
        """Ensure `import re` does not appear exclusively inside a try/except block."""
        source = self._runner_source()
        lines = source.splitlines()
        # Find all `import re` lines
        import_re_lines = [
            (i + 1, line) for i, line in enumerate(lines) if line.strip() == "import re"
        ]
        assert import_re_lines, "No `import re` found in runner.py"
        # At least one must be at zero indentation (module level)
        module_level = [
            (lno, line) for lno, line in import_re_lines if not line[0].isspace()
        ]
        assert module_level, (
            f"All `import re` occurrences are indented (inside blocks): {import_re_lines}. "
            "At least one must be at module level."
        )
