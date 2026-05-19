"""Tests for _scrub_secret — secret redaction before on-disk / log persistence.

Covers security finding LOW-1 / token-in-logs: SDK exception strings that
may carry tokens, Bearer headers, or credentialed URLs must be scrubbed
before they are written into .sflo/state.json or the pipeline log.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src._stderr import _scrub_secret


class TestRedactsTokens:
    def test_redacts_sk_ant_token(self):
        out = _scrub_secret("auth failed for sk-ant-api03-AbCdEf123456789XyZ tail")
        assert "sk-ant-api03-AbCdEf123456789XyZ" not in out
        assert "[REDACTED]" in out
        # Surrounding diagnostic text survives.
        assert "auth failed for" in out
        assert "tail" in out

    def test_redacts_generic_sk_token(self):
        out = _scrub_secret("key sk-proj-9f8e7d6c5b4a3210ZyXw used")
        assert "sk-proj-9f8e7d6c5b4a3210ZyXw" not in out
        assert "[REDACTED]" in out

    def test_redacts_bearer_token(self):
        out = _scrub_secret("Authorization: Bearer eyJhbG123abcDEF456ghiJKL789 done")
        assert "eyJhbG123abcDEF456ghiJKL789" not in out
        assert "[REDACTED]" in out
        assert "done" in out

    def test_redacts_token_query_fragment(self):
        out = _scrub_secret("GET https://api.host/v1?token=SuperSecretValue123&x=1")
        assert "SuperSecretValue123" not in out
        assert "[REDACTED]" in out
        # The non-secret query param is preserved.
        assert "x=1" in out

    def test_redacts_key_query_fragment(self):
        out = _scrub_secret("url https://h/p?key=abc123secretKEY456&ok=2 end")
        assert "abc123secretKEY456" not in out
        assert "[REDACTED]" in out
        assert "ok=2" in out

    def test_redacts_long_high_entropy_run(self):
        blob = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0UvWx"
        out = _scrub_secret(f"leaked blob {blob} in trace")
        assert blob not in out
        assert "[REDACTED]" in out


class TestPreservesOrdinaryText:
    def test_short_plain_error_unchanged(self):
        msg = "FileNotFoundError: claude.exe missing on PATH"
        assert _scrub_secret(msg) == msg

    def test_non_string_coerced(self):
        out = _scrub_secret(ValueError("boom"))
        assert "boom" in out

    def test_none_does_not_raise(self):
        # Must not blow up on None — coerced to string.
        out = _scrub_secret(None)
        assert isinstance(out, str)


class TestLengthCap:
    def test_truncates_over_long_input(self):
        long = "x" * 5000
        out = _scrub_secret(long, max_len=2000)
        assert len(out) <= 2000 + 64  # cap + truncation marker slack
        assert "truncated" in out

    def test_under_cap_not_truncated(self):
        msg = "short and safe"
        out = _scrub_secret(msg, max_len=2000)
        assert "truncated" not in out
        assert out == msg
