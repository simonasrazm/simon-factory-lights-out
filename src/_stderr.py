"""Safe stderr helper — isolated to avoid circular imports.

Every print-to-stderr in the SFLO pipeline must go through _safe_stderr()
so that BrokenPipeError from a closed parent process never kills the
pipeline. File-based logging (pipeline.log) is the durable channel;
stderr is best-effort diagnostics only.
"""

import sys


def _safe_stderr(msg, **kwargs):
    """Print to stderr, swallowing BrokenPipeError/OSError."""
    try:
        print(msg, file=sys.stderr, **kwargs)
    except (BrokenPipeError, OSError, ValueError):
        pass
