"""Tests for runner-side error handling.

Covers three coupled behaviors (the "credulity bug" cluster):

1. GateAgentFailure: typed exception raised when a gate cannot produce
   a valid artifact via any of its attempts. Carries enough context for
   the gate loop to escalate cleanly.

2. ErrorDeduper: collapses identical repeated errors so a single root
   cause doesn't spam 30+ tracebacks across the retry loop.

3. default_agent_runner: when the adapter raises NonRetryableError or
   when all 3 attempts have been exhausted, the runner must RAISE
   GateAgentFailure — NOT stuff the error string into a response that
   then gets written into the gate artifact as if it were agent output.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.adapters.errors import NonRetryableError


class TestGateAgentFailure:
    """The typed exception that signals a gate cannot proceed."""

    def test_carries_role_gate_attempts_cause(self):
        from src.adapters.errors import GateAgentFailure

        cause = RuntimeError("underlying SDK error")
        e = GateAgentFailure(
            role="dev", gate=2, attempts=3, cause=cause,
        )
        assert e.role == "dev"
        assert e.gate == 2
        assert e.attempts == 3
        # The structured `cause` attribute lets catch sites access the
        # underlying error programmatically (Python's __cause__ chaining
        # only fires when `raise ... from ...` is actually used, so we
        # don't test that here — that's a Python-language invariant, not
        # ours to assert).
        assert e.cause is cause

    def test_str_includes_role_and_cause(self):
        from src.adapters.errors import GateAgentFailure

        e = GateAgentFailure(
            role="qa", gate=3, attempts=1, cause=RuntimeError("boom"),
        )
        s = str(e)
        assert "qa" in s
        assert "boom" in s

    def test_is_subclass_of_adapter_error(self):
        from src.adapters.errors import AdapterError, GateAgentFailure

        assert issubclass(GateAgentFailure, AdapterError)


class TestErrorDeduper:
    """Suppresses repeated identical tracebacks in a tight retry loop."""

    def test_first_occurrence_is_unique(self):
        from src.adapters.errors import ErrorDeduper

        d = ErrorDeduper()
        assert d.should_emit(RuntimeError("same")) is True

    def test_repeat_of_same_signature_is_suppressed(self):
        from src.adapters.errors import ErrorDeduper

        d = ErrorDeduper()
        d.should_emit(RuntimeError("same"))
        assert d.should_emit(RuntimeError("same")) is False
        assert d.should_emit(RuntimeError("same")) is False

    def test_different_signature_emits_again(self):
        from src.adapters.errors import ErrorDeduper

        d = ErrorDeduper()
        d.should_emit(RuntimeError("first"))
        assert d.should_emit(RuntimeError("second")) is True

    def test_different_exception_class_same_message_emits_again(self):
        from src.adapters.errors import ErrorDeduper

        d = ErrorDeduper()
        d.should_emit(RuntimeError("oops"))
        assert d.should_emit(ValueError("oops")) is True

    def test_suppressed_count_tracks_runs(self):
        from src.adapters.errors import ErrorDeduper

        d = ErrorDeduper()
        d.should_emit(RuntimeError("e"))
        d.should_emit(RuntimeError("e"))
        d.should_emit(RuntimeError("e"))
        # After 3 repeats: 1 emitted + 2 suppressed
        assert d.suppressed_count == 2


def test_default_agent_runner_raises_on_non_retryable():
    """When adapter raises NonRetryableError on first attempt, the runner
    must propagate as GateAgentFailure — not stuff '[Agent error ...]'
    into response (which would then poison the gate artifact)."""
    from src.adapters.errors import GateAgentFailure
    from src.runner import default_agent_runner

    adapter = MagicMock()
    adapter._mcp_servers = None
    adapter.spawn_agent = AsyncMock(
        side_effect=NonRetryableError("Claude Code CLI not found")
    )

    agent = {
        "role": "dev",
        "model": "sonnet",
        "gate_num": 2,
        "produces": "BUILD-STATUS.md",
        "skills": [],
    }

    with pytest.raises(GateAgentFailure) as exc_info:
        asyncio.run(
            default_agent_runner(
                agent,
                sflo_dir=".sflo",
                output_dir=None,
                adapter=adapter,
                runtime="claude-code",
                user_prompt="build it",
                log=lambda x: None,
            )
        )
    assert exc_info.value.role == "dev"


def test_default_agent_runner_raises_after_3_generic_failures():
    """When adapter raises a generic Exception 3 times in a row,
    the runner exhausts retries and raises GateAgentFailure with
    attempts=3 — not stuff '[Agent error after 3 attempts ...]'."""
    from src.adapters.errors import GateAgentFailure
    from src.runner import default_agent_runner

    adapter = MagicMock()
    adapter._mcp_servers = None
    adapter.spawn_agent = AsyncMock(side_effect=RuntimeError("transient-ish"))

    agent = {
        "role": "dev",
        "model": "sonnet",
        "gate_num": 2,
        "produces": "BUILD-STATUS.md",
        "skills": [],
    }

    with pytest.raises(GateAgentFailure) as exc_info:
        asyncio.run(
            default_agent_runner(
                agent,
                sflo_dir=".sflo",
                output_dir=None,
                adapter=adapter,
                runtime="claude-code",
                user_prompt="build it",
                log=lambda x: None,
            )
        )
    assert exc_info.value.attempts == 3
    # Adapter was called 3 times (3 attempts, no retries past the cap)
    assert adapter.spawn_agent.await_count == 3
