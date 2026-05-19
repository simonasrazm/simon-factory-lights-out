"""Safe stderr helper — isolated to avoid circular imports.

Every print-to-stderr in the SFLO pipeline must go through _safe_stderr()
so that BrokenPipeError from a closed parent process never kills the
pipeline. File-based logging (pipeline.log) is the durable channel;
stderr is best-effort diagnostics only.
"""

import re
import sys


def _safe_stderr(msg, **kwargs):
    """Print to stderr, swallowing BrokenPipeError/OSError."""
    try:
        print(msg, file=sys.stderr, **kwargs)
    except (BrokenPipeError, OSError, ValueError):
        pass


# --- Secret scrubbing -------------------------------------------------------
# SDK / CLI exception strings can carry credentials (an OAuth token in the
# process env, a Bearer header echoed in an error, a URL with ?token=...).
# Those strings flow into NonRetryableError -> GateAgentFailure.cause and are
# serialized verbatim into .sflo/state.json (escalation.cause) and the
# pipeline log. _scrub_secret() redacts token-shaped substrings and caps
# length so an on-disk artifact never captures a live credential.
#
# (compiled pattern, replacement) pairs, applied in order. Order matters:
# the prefix/header patterns run before the generic high-entropy pattern so
# e.g. "Bearer <tok>" collapses to one "Bearer [REDACTED]" rather than
# leaving the literal "Bearer" plus a separately-redacted blob. The Bearer
# replacement keeps the human-readable label and redacts only the token.
_SECRET_PATTERNS = (
    # Authorization: Bearer <token>
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-+/=]{6,}", re.IGNORECASE),
     "Bearer [REDACTED]"),
    # Anthropic / OpenAI-style keys: sk-ant-..., sk-proj-..., sk-...
    (re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}"), "[REDACTED]"),
    # Credentialed URL query fragments: ?token=..., &api_key=..., key=...
    (re.compile(
        r"(?i)\b(?:access[_-]?token|api[_-]?key|auth[_-]?token|token|key|"
        r"password|secret)=[^&\s\"']+"
    ), "[REDACTED]"),
    # Generic long high-entropy run (base64 / hex blob) — last so the more
    # specific patterns above get first claim on their matches.
    (re.compile(r"\b[A-Za-z0-9+/=_\-]{40,}\b"), "[REDACTED]"),
)


def _scrub_secret(text, max_len=2000):
    """Redact token-shaped substrings and cap length.

    Applied to error/cause strings at the points where they are persisted to
    disk (state.json) or written to the pipeline log. Best-effort: it cannot
    catch every possible secret shape, but it removes the common credential
    forms (Bearer headers, sk- keys, credentialed URLs, long opaque blobs)
    and bounds the blast radius with a length cap.

    Non-string input is coerced via str(); None becomes "None".
    """
    s = text if isinstance(text, str) else str(text)
    # Cap length FIRST: a giant string is itself a leak risk and an unbounded
    # blob would otherwise collapse to a single "[REDACTED]" before we ever
    # record that truncation happened. Redact the (now bounded) result.
    if len(s) > max_len:
        s = s[:max_len] + f" …[+{len(s) - max_len} chars truncated]"
    for pat, replacement in _SECRET_PATTERNS:
        s = pat.sub(replacement, s)
    return s
