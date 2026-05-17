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
