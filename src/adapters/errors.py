"""Typed exception hierarchy for SFLO adapters.

Adapters classify their own errors into transient (retryable) vs
non-retryable. The runner catches these types and decides retry policy
without string-matching exception messages.
"""


class AdapterError(Exception):
    """Base for all adapter-raised errors."""

    pass


class TransientError(AdapterError):
    """Retryable failure: timeouts, rate limits, 5xx, service degradation.

    When an adapter raises this, the runner may retry the entire gate spawn.
    The adapter is responsible for classification — it knows its own error
    semantics (exit codes, stderr patterns, SDK exception types).
    """

    pass


class NonRetryableError(AdapterError):
    """Permanent failure: auth errors, invalid model, bad config.

    Retrying won't help. Runner should abort the gate immediately.
    """

    pass


class GateAgentFailure(AdapterError):
    """A gate's agent could not produce a valid artifact via any attempt.

    Raised by the runner (NOT by the adapter directly) when:
      - The adapter raises NonRetryableError on any attempt, OR
      - The adapter raises generic Exceptions on all 3 retry attempts.

    Carries enough context for the gate loop to escalate cleanly to a
    human reviewer without writing the error text into the gate artifact
    (the "credulity bug" — see changes.md).
    """

    def __init__(self, role, gate, attempts, cause):
        self.role = role
        self.gate = gate
        self.attempts = attempts
        self.cause = cause
        super().__init__(
            f"gate {gate} ({role}) failed after {attempts} attempt(s): "
            f"{type(cause).__name__}: {cause}"
        )


class ErrorDeduper:
    """Suppress repeated identical exceptions in a tight retry loop.

    SFLO's gate retry loop can fire 30 identical tracebacks (10 outer
    retries x 3 resume attempts) for a single permanent error. That
    buries the real signal in noise. This helper returns True for the
    first occurrence of a given (type, message) signature and False
    for subsequent identical occurrences in a row.

    A different signature resets the suppression.
    """

    def __init__(self):
        self._last_signature = None
        self.suppressed_count = 0

    def should_emit(self, exc):
        sig = (type(exc), str(exc))
        if sig == self._last_signature:
            self.suppressed_count += 1
            return False
        self._last_signature = sig
        return True
