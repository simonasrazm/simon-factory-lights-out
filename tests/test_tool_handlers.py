"""Tests for OllamaAdapter tool handlers and text-based tool call parsing."""

import os
import pytest
from src.adapters.tool_handlers import (
    TOOL_HANDLERS,
    handle_bash, handle_read, handle_write, handle_append,
    handle_edit, handle_multiedit, handle_glob, handle_grep,
)
from src.adapters.ollama import OllamaAdapter


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def test_all_handlers_registered(self):
        expected = {"bash", "read", "write", "append", "edit",
                    "multiedit", "glob", "grep", "webfetch"}
        assert set(TOOL_HANDLERS.keys()) == expected

    def test_handlers_are_callable(self):
        for name, handler in TOOL_HANDLERS.items():
            assert callable(handler), f"{name} handler not callable"


# ---------------------------------------------------------------------------
# File tools (write, read, append, edit, multiedit)
# ---------------------------------------------------------------------------

class TestWrite:
    def test_creates_file(self, tmp_path):
        path = str(tmp_path / "test.txt")
        result = handle_write({"file_path": path, "content": "hello"})
        assert "Written" in result
        assert open(path).read() == "hello"

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "test.txt")
        result = handle_write({"file_path": path, "content": "deep"})
        assert "Written" in result
        assert open(path).read() == "deep"


class TestRead:
    def test_reads_file(self, tmp_path):
        path = str(tmp_path / "test.txt")
        open(path, "w").write("line1\nline2\nline3")
        result = handle_read({"path": path})
        assert "line1" in result
        assert "line3" in result

    def test_offset_limit(self, tmp_path):
        path = str(tmp_path / "test.txt")
        open(path, "w").write("a\nb\nc\nd\ne")
        result = handle_read({"path": path, "offset": 2, "limit": 2})
        assert "b" in result
        assert "c" in result
        assert "a" not in result
        assert "d" not in result

    def test_missing_file(self):
        result = handle_read({"path": "/nonexistent/file.txt"})
        assert "not found" in result


class TestAppend:
    def test_appends_to_existing(self, tmp_path):
        path = str(tmp_path / "test.txt")
        open(path, "w").write("first")
        result = handle_append({"file_path": path, "content": " second"})
        assert "Appended" in result
        assert open(path).read() == "first second"

    def test_creates_if_missing(self, tmp_path):
        path = str(tmp_path / "new.txt")
        result = handle_append({"file_path": path, "content": "fresh"})
        assert "Appended" in result
        assert open(path).read() == "fresh"


class TestEdit:
    def test_replaces_string(self, tmp_path):
        path = str(tmp_path / "test.txt")
        open(path, "w").write("hello world")
        result = handle_edit({"file_path": path, "old_string": "world", "new_string": "earth"})
        assert "Replaced" in result
        assert open(path).read() == "hello earth"

    def test_not_found(self, tmp_path):
        path = str(tmp_path / "test.txt")
        open(path, "w").write("hello")
        result = handle_edit({"file_path": path, "old_string": "xyz", "new_string": "abc"})
        assert "not found" in result

    def test_ambiguous_without_replace_all(self, tmp_path):
        path = str(tmp_path / "test.txt")
        open(path, "w").write("aa bb aa")
        result = handle_edit({"file_path": path, "old_string": "aa", "new_string": "cc"})
        assert "found 2 times" in result


class TestMultiedit:
    def test_applies_multiple(self, tmp_path):
        path = str(tmp_path / "test.txt")
        open(path, "w").write("aaa bbb ccc")
        result = handle_multiedit({
            "file_path": path,
            "edits": [
                {"old_string": "aaa", "new_string": "111"},
                {"old_string": "ccc", "new_string": "333"},
            ],
        })
        assert "Applied 2" in result
        assert open(path).read() == "111 bbb 333"

    def test_rollback_on_failure(self, tmp_path):
        path = str(tmp_path / "test.txt")
        open(path, "w").write("aaa bbb")
        result = handle_multiedit({
            "file_path": path,
            "edits": [
                {"old_string": "aaa", "new_string": "111"},
                {"old_string": "zzz", "new_string": "999"},  # won't match
            ],
        })
        assert "rolled back" in result
        assert open(path).read() == "aaa bbb"  # original preserved


# ---------------------------------------------------------------------------
# Search tools (glob, grep)
# ---------------------------------------------------------------------------

class TestGlob:
    def test_finds_files(self, tmp_path, monkeypatch):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")
        (tmp_path / "c.txt").write_text("z")
        monkeypatch.chdir(tmp_path)
        result = handle_glob({"pattern": "*.py"})
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_no_matches(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = handle_glob({"pattern": "*.xyz"})
        assert "no matches" in result


class TestGrep:
    def test_finds_pattern(self, tmp_path):
        (tmp_path / "test.py").write_text("def hello():\n    return 42\n")
        result = handle_grep({"pattern": "hello", "path": str(tmp_path / "test.py")})
        assert "hello" in result

    def test_no_match(self, tmp_path):
        (tmp_path / "test.py").write_text("nothing here")
        result = handle_grep({"pattern": "xyz", "path": str(tmp_path / "test.py")})
        assert "no matches" in result

    def test_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "a.py").write_text("target_string")
        result = handle_grep({"pattern": "target_string", "path": str(tmp_path)})
        assert "target_string" in result


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------

class TestBash:
    def test_runs_command(self):
        result = handle_bash({"command": "echo hello"})
        assert "hello" in result

    def test_timeout(self):
        result = handle_bash({"command": "sleep 60"})
        assert "timed out" in result


# ---------------------------------------------------------------------------
# Text-based tool call parsing
# ---------------------------------------------------------------------------

class TestParseToolCalls:
    def test_json_format(self):
        text = '{"name": "bash", "arguments": {"command": "ls"}}'
        calls = OllamaAdapter._parse_tool_calls_from_text(text)
        assert len(calls) == 1
        assert calls[0][0] == "bash"
        assert calls[0][1]["command"] == "ls"

    def test_json_in_code_block(self):
        text = '```json\n{"name": "write", "arguments": {"file_path": "x.txt", "content": "hi"}}\n```'
        calls = OllamaAdapter._parse_tool_calls_from_text(text)
        assert len(calls) == 1
        assert calls[0][0] == "write"

    def test_xml_format(self):
        text = '<function=bash>\n<parameter=command>ls -la</parameter>\n</function>'
        calls = OllamaAdapter._parse_tool_calls_from_text(text)
        assert len(calls) == 1
        assert calls[0][0] == "bash"
        assert calls[0][1]["command"] == "ls -la"

    def test_calltool_prefix(self):
        text = 'CALL_TOOL: {"tool_name": "bash", "args": {"command": "pwd"}}'
        calls = OllamaAdapter._parse_tool_calls_from_text(text)
        assert len(calls) == 1
        assert calls[0][0] == "bash"

    def test_think_tags_preserved(self):
        text = '<think>Let me write a file</think>{"name": "write", "arguments": {"file_path": "a.txt", "content": "x"}}'
        calls = OllamaAdapter._parse_tool_calls_from_text(text)
        assert len(calls) == 1
        assert calls[0][0] == "write"

    def test_no_calls(self):
        text = "Just a regular response with no tool calls."
        calls = OllamaAdapter._parse_tool_calls_from_text(text)
        assert len(calls) == 0

    def test_multiline_json(self):
        text = '{"name": "bash", "arguments": {"command": "cat <<EOF > test.txt\\nline1\\nline2\\nEOF"}}'
        calls = OllamaAdapter._parse_tool_calls_from_text(text)
        assert len(calls) == 1
        assert "line1" in calls[0][1]["command"]


# ---------------------------------------------------------------------------
# Text tool instruction generation
# ---------------------------------------------------------------------------

class TestBuildTextToolInstruction:
    def test_includes_tool_names(self):
        tools = [
            {"function": {"name": "bash", "description": "Run command",
                          "parameters": {"properties": {"command": {"type": "string"}}}}},
            {"function": {"name": "read", "description": "Read file",
                          "parameters": {"properties": {"path": {"type": "string"}}}}},
        ]
        result = OllamaAdapter._build_text_tool_instruction(tools)
        assert "bash" in result
        assert "read" in result
        assert "Tool Usage Protocol" in result

    def test_done_signal_documented(self):
        tools = [{"function": {"name": "bash", "description": "x",
                                "parameters": {"properties": {}}}}]
        result = OllamaAdapter._build_text_tool_instruction(tools)
        assert "done" in result.lower()
