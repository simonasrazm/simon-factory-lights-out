"""Tests for the Claude Code adapter.

Covers two surgical changes:

1. build_sdk_options: when the final system_prompt exceeds the Windows
   CLI command-line limit budget, the function writes it to a temp file
   and returns the options dict with system_prompt as a SystemPromptFile
   dict (`{"type": "file", "path": ...}`) — matching the SDK's existing
   --system-prompt-file primitive.

2. _classify_sdk_error: maps SDK exceptions that indicate a permanent
   environment problem (CLINotFoundError, FileNotFoundError for the
   bundled binary, etc.) onto NonRetryableError. Lets the runner skip
   the 3-attempt retry loop for failures that retries cannot help.
"""

import os

import pytest


def _minimal_security_config():
    """Smallest security config build_sdk_options accepts."""
    return {
        "require_permission": False,
        "no_session_persistence": False,
        "isolate_user_settings": False,
        "isolate_all_settings": False,
        "sandbox_config_dir": False,
        "wipe_sandbox": False,
    }


class TestBuildSdkOptionsPromptSize:
    def test_small_prompt_stays_as_string(self):
        from src.adapters.claude_code import build_sdk_options

        opts, sandbox_dir, needs_mcp, temp_files = build_sdk_options(
            system_prompt="short prompt",
            model="sonnet",
            security_config=_minimal_security_config(),
        )
        assert isinstance(opts["system_prompt"], str)
        assert opts["system_prompt"] == "short prompt"
        assert temp_files == []

    def test_large_prompt_written_to_file(self, tmp_path, monkeypatch):
        """Prompts larger than the threshold are written to a temp file
        and the opts dict references the file path. The CLI arg becomes
        the short path instead of a 30K+ inline string, dodging the
        Windows command-line length limit."""
        from src.adapters.claude_code import build_sdk_options

        # Use tmp_path as the temp-file root so we don't leave artifacts
        monkeypatch.setenv("SFLO_PROMPT_TMPDIR", str(tmp_path))

        big = "x" * 30_000  # 30 KB — comfortably over a 16 KB threshold

        opts, sandbox_dir, needs_mcp, temp_files = build_sdk_options(
            system_prompt=big,
            model="sonnet",
            security_config=_minimal_security_config(),
        )

        sp = opts["system_prompt"]
        assert isinstance(sp, dict), (
            f"system_prompt should be a SystemPromptFile dict for large content, "
            f"got {type(sp).__name__}: {sp!r}"
        )
        assert sp.get("type") == "file"
        path = sp["path"]
        assert os.path.isfile(path), f"temp prompt file should exist at {path}"
        with open(path, "r", encoding="utf-8") as f:
            assert f.read() == big
        assert path in temp_files, (
            "build_sdk_options must report the temp file in the 4th return value "
            "so the caller can clean it up after the SDK call returns"
        )

    def test_threshold_is_below_windows_cli_limit(self):
        """The threshold must leave room for other CLI args (model, flags,
        MCP config, etc.). Windows' CreateProcess accepts <= 32,767 chars;
        we keep ~half that as the prompt budget."""
        from src.adapters.claude_code import PROMPT_INLINE_LIMIT_BYTES

        assert PROMPT_INLINE_LIMIT_BYTES <= 16 * 1024, (
            "threshold must be <= 16 KB so other CLI args fit in the 32 KB "
            "Windows command-line limit"
        )


class TestClassifySdkError:
    def test_clinotfounderror_maps_to_nonretryable(self):
        """A missing claude.exe is a permanent environment problem.
        Retrying 30 times won't help — must be classified non-retryable
        so the runner skips the inner retry loop."""
        from src.adapters.claude_code import _classify_sdk_error
        from src.adapters.errors import NonRetryableError

        # Construct a CLINotFoundError if importable; fall back to
        # a synthetic exception with the same name (some SDK versions
        # gate the import on _internal modules).
        try:
            from claude_agent_sdk._errors import CLINotFoundError
            err = CLINotFoundError("Claude Code not found", "/path/to/claude.exe")
        except ImportError:
            err = type("CLINotFoundError", (Exception,), {})("Claude Code not found")

        classified = _classify_sdk_error(err)
        assert isinstance(classified, NonRetryableError)
        assert "Claude Code" in str(classified) or "CLI" in str(classified)

    def test_filenotfounderror_for_executable_maps_to_nonretryable(self):
        """WinError 206 / FileNotFoundError during CreateProcess
        indicates either a missing exe or a command-line that's too
        long — both permanent for this run's config. Retry won't help."""
        from src.adapters.claude_code import _classify_sdk_error
        from src.adapters.errors import NonRetryableError

        err = FileNotFoundError(206, "The filename or extension is too long")
        classified = _classify_sdk_error(err)
        assert isinstance(classified, NonRetryableError)

    def test_generic_exception_passes_through(self):
        """Don't reclassify unknown errors — let the existing retry path
        handle them. Only known-permanent SDK failures are wrapped."""
        from src.adapters.claude_code import _classify_sdk_error

        err = RuntimeError("transient network blip")
        classified = _classify_sdk_error(err)
        # Returns the original (not a NonRetryableError wrap)
        assert classified is err
