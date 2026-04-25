"""OllamaAdapter — runs agents via local Ollama server with tool support."""

import json
import os
import re
import sys
import time as _time

from .base import RuntimeAdapter
from .tool_handlers import TOOL_HANDLERS

# ---------------------------------------------------------------------------
# Tool policy for OllamaAdapter — driven by `tools:` field in bindings.yaml.
# Same philosophy as ClaudeCodeAdapter: workhorse roles get every locally-
# implemented tool by default; only `tools: readonly` clamps to read/search.
#
# Tool names here are lowercase because Ollama function-calling sends names
# verbatim and most local models prefer lowercase. Hosts can override per-
# spawn via the allowed_tools kwarg (caller-supplied wins, backward-compat).
# ---------------------------------------------------------------------------

TOOL_MODE_PRESETS_OLLAMA = {
    "readonly": ["read", "glob", "grep"],
    "full": None,  # None = all locally-defined tools (no restriction)
}


def resolve_allowed_tools_ollama(tools_mode, caller_supplied=None):
    """Resolve tool name set for OllamaAdapter. Returns set of names or None.

    None means "use everything in the local tool map".
    """
    if caller_supplied is not None:
        return {t.lower() for t in caller_supplied}
    if tools_mode in TOOL_MODE_PRESETS_OLLAMA:
        preset = TOOL_MODE_PRESETS_OLLAMA[tools_mode]
        return None if preset is None else set(preset)
    return None  # default = all tools


def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks emitted by thinking/reasoning models."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


class OllamaAdapter(RuntimeAdapter):
    """Uses local Ollama server — tool-capable models via ollama.chat() API.

    Requires:
      - pip install ollama
      - Ollama server running (local or remote via OLLAMA_HOST env var)
      - A tool-capable model pulled

    Scout role gets no tools — context in prompt, returns JSON as plain text.
    All other roles get a single bash tool for full filesystem access.
    """

    _BASH_TOOL = {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a bash command. Use for all filesystem, search, and OS operations. "
                "Returns stdout + stderr combined."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute.",
                    }
                },
                "required": ["command"],
            },
        },
    }

    _READ_TOOL = {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read file contents with line numbers (cat -n style). "
                "Use offset/limit for partial reads. All paths resolve against cwd."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to read.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "1-indexed line number to start reading from (default: 1).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of lines to return from offset (default: all remaining).",
                    },
                },
                "required": ["path"],
            },
        },
    }

    _WRITE_TOOL = {
        "type": "function",
        "function": {
            "name": "write",
            "description": (
                "Write content to a file. Creates parent directories if needed. "
                "Overwrites existing file. Uses UTF-8 encoding."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path of the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    }

    _APPEND_TOOL = {
        "type": "function",
        "function": {
            "name": "append",
            "description": (
                "Append content to an existing file. Creates the file if it doesn't exist. "
                "Use when building large files in multiple steps — write the first part "
                "with 'write', then add more with 'append'. Uses UTF-8 encoding."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path of the file to append to.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to append to the file.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    }

    _EDIT_TOOL = {
        "type": "function",
        "function": {
            "name": "edit",
            "description": (
                "Replace old_string with new_string in a file. "
                "Errors if old_string not found or found >1 time without replace_all. "
                "Uses UTF-8 encoding."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path of the file to edit.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact string to find and replace.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement string.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "If true, replace all occurrences (default: false).",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    }

    _GLOB_TOOL = {
        "type": "function",
        "function": {
            "name": "glob",
            "description": (
                "Find files matching a glob pattern, searched from cwd. "
                "Returns sorted absolute paths, one per line. "
                "Example: pattern='**/*.py' finds all Python files recursively."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match (e.g. '**/*.py', 'src/*.ts').",
                    },
                },
                "required": ["pattern"],
            },
        },
    }

    _MULTIEDIT_TOOL = {
        "type": "function",
        "function": {
            "name": "multiedit",
            "description": (
                "Apply multiple find-and-replace edits to a single file in one call. "
                "All edits succeed or none apply (atomic). Edits applied sequentially — "
                "ensure earlier edits don't affect text that later edits target."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path of the file to edit.",
                    },
                    "edits": {
                        "type": "array",
                        "description": "List of edits. Each has old_string and new_string.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_string": {"type": "string"},
                                "new_string": {"type": "string"},
                            },
                            "required": ["old_string", "new_string"],
                        },
                    },
                },
                "required": ["file_path", "edits"],
            },
        },
    }

    _WEBFETCH_TOOL = {
        "type": "function",
        "function": {
            "name": "webfetch",
            "description": (
                "Fetch content from a URL via curl and return as text. "
                "HTML is stripped to readable text. Use to verify API endpoints, "
                "read documentation, or check web pages. Max 4KB returned."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch (http:// or https://).",
                    },
                },
                "required": ["url"],
            },
        },
    }

    _GREP_TOOL = {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search file contents using a regex pattern. "
                "If path is a file, searches that file. If path is a directory, searches recursively. "
                "Returns matches in filepath:linenum:content format. Skips binary files silently."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path to search in.",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "If true, search is case-insensitive (default: false).",
                    },
                },
                "required": ["pattern", "path"],
            },
        },
    }

    @staticmethod
    def _build_text_tool_instruction(tools):
        """Generate text-based tool instruction dynamically from tool list.

        Models without native tool support get this appended to system prompt.
        Built from whatever tools are available — base tools, MCP tools, etc.
        """
        lines = [
            "\n\n## Tool Usage Protocol\n",
            "You MUST use tools to complete tasks. Do NOT describe — ACT.",
            "Do NOT write artifact content in your response — use the write tool.\n",
            'To use a tool, output a JSON object: {"name": "<tool>", "arguments": {<args>}}',
            "One tool call per message. Wait for result.",
            'When done, output: {"done": true, "summary": "brief description of what you did"}\n',
            "Available tools:",
        ]
        for tool in tools:
            fn = tool.get("function", {})
            name = fn.get("name", "?")
            desc = fn.get("description", "")
            # Truncate long descriptions (MCP tools can be verbose)
            if len(desc) > 80:
                desc = desc[:77] + "..."
            params = fn.get("parameters", {}).get("properties", {})
            param_keys = list(params.keys())
            example_args = {}
            for k in param_keys[:4]:  # Max 4 params in example
                ptype = params[k].get("type", "string")
                example_args[k] = f"<{ptype}>"
            example = json.dumps({"name": name, "arguments": example_args})
            lines.append(f"- {name}: {desc}")
            lines.append(f"  Example: {example}")
        return "\n".join(lines)

    MAX_TURNS = 80
    TOTAL_TIMEOUT = 1800  # 30 min — local models can be slow

    @staticmethod
    def _parse_tool_calls_from_text(text):
        """Extract tool calls from model text output.

        Some models output tool calls
        as JSON text rather than using ollama's native tool_call mechanism.
        Uses json.JSONDecoder().raw_decode() for robust multi-line JSON
        parsing — regex fails on heredoc content with embedded braces.
        """
        calls = []
        # Strip <think>...</think> tag markers but preserve their content —
        # models like Qwen/DeepSeek embed tool calls inside thinking blocks,
        # removing the block entirely would lose the call.
        text = re.sub(r"<think>(.*?)</think>", r"\1", text, flags=re.DOTALL).strip()
        # Strip markdown code blocks (some models wrap JSON in ```)
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```", "", text)
        # Also detect CALL_TOOL: prefix (granite models)
        if "CALL_TOOL:" in text:
            call_str = text.split("CALL_TOOL:", 1)[1].strip()
            call_str = re.sub(r"```(?:json)?\s*", "", call_str)
            call_str = re.sub(r"```", "", call_str).strip()
            try:
                obj = json.loads(call_str)
                fn_name = obj.get("tool_name", obj.get("name", ""))
                fn_args = obj.get(
                    "args", obj.get("arguments", obj.get("parameters", {}))
                )
                if fn_name:
                    calls.append((fn_name, fn_args))
                    return calls
            except json.JSONDecodeError:
                pass
        # Qwen3-Coder XML tool call format:
        #   <function=name>
        #   <parameter=key>value</parameter>
        #   </function>
        # Some models output XML tool calls instead of JSON when many tools available.
        for m in re.finditer(
            r"<function=(\w+)>\s*(.*?)</function>",
            text,
            re.DOTALL,
        ):
            fn_name = m.group(1)
            params_block = m.group(2)
            fn_args = {}
            for pm in re.finditer(
                r"<parameter=(\w+)>\s*(.*?)\s*</parameter>",
                params_block,
                re.DOTALL,
            ):
                fn_args[pm.group(1)] = pm.group(2).strip()
            if fn_name:
                calls.append((fn_name, fn_args))
        if calls:
            return calls
        # Use json.JSONDecoder for robust multi-line JSON parsing
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(text):
            idx = text.find("{", idx)
            if idx == -1:
                break
            try:
                obj, end = decoder.raw_decode(text, idx)
                if isinstance(obj, dict) and "name" in obj:
                    fn_name = obj.get("name", "")
                    fn_args = obj.get("arguments", obj.get("parameters", {}))
                    if fn_name:
                        calls.append((fn_name, fn_args))
                idx = end if end > idx else idx + 1
            except json.JSONDecodeError:
                idx += 1
        return calls

    async def spawn_agent(
        self,
        model,
        system_prompt,
        user_prompt,
        cwd=None,
        role=None,
        allowed_tools=None,
        tools_mode=None,
        max_turns=None,
        timeout=None,
    ):
        try:
            import ollama
        except ImportError:
            raise RuntimeError("ollama package not installed. Fix: pip install ollama")

        is_scout = role == "scout"

        # Determine allowed tool names via tools_mode preset (caller-supplied
        # wins). None = all locally-defined tools (full toolkit, no restriction).
        _all_tool_map = {
            "bash": self._BASH_TOOL,
            "read": self._READ_TOOL,
            "write": self._WRITE_TOOL,
            "append": self._APPEND_TOOL,
            "edit": self._EDIT_TOOL,
            "multiedit": self._MULTIEDIT_TOOL,
            "glob": self._GLOB_TOOL,
            "grep": self._GREP_TOOL,
            "webfetch": self._WEBFETCH_TOOL,
        }
        _resolved = resolve_allowed_tools_ollama(tools_mode, allowed_tools)
        _permitted = set(_all_tool_map.keys()) if _resolved is None else _resolved

        # Try native tools first; fall back to text-based tool calling
        use_native_tools = True
        tools = [v for k, v in _all_tool_map.items() if k in _permitted]

        # MCP bridge tools (set by host project or extensions)
        mcp_bridge = getattr(self, "_mcp_bridge", None)
        if mcp_bridge and not is_scout:
            tools.extend(mcp_bridge.get_ollama_tools())
            # Add usage guidance from MCP tool descriptions to system prompt
            guidance = mcp_bridge.get_usage_guidance()
            if guidance:
                system_prompt = system_prompt + guidance

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        max_t = max_turns or self.MAX_TURNS
        timeout_s = timeout or self.TOTAL_TIMEOUT
        turn_count = 0
        _recent_calls = []  # Track recent tool calls for loop detection
        tool_use_count = 0
        start_time = _time.time()

        # cwd is passed via subprocess cwd= kwarg — do NOT os.chdir here as it
        # mutates the process working directory for all threads and is not
        # reverted on exception paths in nested calls.
        original_cwd = None  # retained for API compat; not used

        try:
            while turn_count < max_t:
                elapsed = _time.time() - start_time
                if elapsed >= timeout_s:
                    raise RuntimeError(
                        f"OllamaAdapter: {timeout_s}s total timeout exceeded after "
                        f"{turn_count} turns (role={role}, model={model})"
                    )

                kwargs = {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_predict": 8192},  # Prevent output truncation
                }
                if use_native_tools and tools:
                    kwargs["tools"] = tools

                try:
                    response = ollama.chat(**kwargs)
                except ollama.ResponseError as e:
                    msg = str(e)
                    if "not found" in msg.lower() or "pull" in msg.lower():
                        raise RuntimeError(
                            f"Ollama model '{model}' not found. "
                            f"Pull it first: ollama pull {model}"
                        ) from e
                    if "does not support tools" in msg.lower():
                        # Model doesn't support native tools — switch to
                        # text-based tool calling and retry this turn
                        use_native_tools = False
                        if is_scout:
                            # Scout just returns JSON — no tools needed
                            tools = []
                        else:
                            # Generate instruction dynamically from available tools
                            text_instruction = self._build_text_tool_instruction(tools)
                            messages[0]["content"] = system_prompt + text_instruction
                            tools = []  # Clear native list (using text-based now)
                        print(
                            f"  [OllamaAdapter] {model} has no native tool support, "
                            f"switching to text-based tool calling",
                            file=sys.stderr,
                        )
                        continue
                    raise RuntimeError(f"Ollama ResponseError: {e}") from e
                except Exception as e:
                    if (
                        "connection" in type(e).__name__.lower()
                        or "connect" in str(e).lower()
                    ):
                        raise RuntimeError(
                            "Ollama is not running. Start it: ollama serve"
                        ) from e
                    raise

                turn_count += 1
                msg_obj = response.message
                content = msg_obj.content or ""

                # Check for {"done": true} signal (text-based tool models)
                if not use_native_tools and content.strip():
                    try:
                        done_check = json.loads(content)
                        if isinstance(done_check, dict) and done_check.get("done"):
                            elapsed = _time.time() - start_time
                            print(
                                f"  [OllamaAdapter metrics — role={role}, model={model}, "
                                f"turns={turn_count}, tool_uses={tool_use_count}, "
                                f"elapsed={elapsed:.0f}s]",
                                file=sys.stderr,
                            )
                            return done_check.get("summary", content)
                    except (json.JSONDecodeError, ValueError):
                        pass

                # Determine tool calls — native or text-parsed
                native_calls = msg_obj.tool_calls if use_native_tools else None
                text_calls = None
                if not native_calls and content:
                    text_calls = self._parse_tool_calls_from_text(content)

                has_calls = bool(native_calls) or bool(text_calls)

                messages.append(
                    {
                        "role": "assistant",
                        "content": content,
                        **({"tool_calls": native_calls} if native_calls else {}),
                    }
                )

                if not has_calls:
                    elapsed = _time.time() - start_time
                    print(
                        f"  [OllamaAdapter metrics — role={role}, model={model}, "
                        f"turns={turn_count}, tool_uses={tool_use_count}, "
                        f"elapsed={elapsed:.0f}s]",
                        file=sys.stderr,
                    )
                    # Strip <think>...</think> blocks from thinking models
                    return strip_think_tags(content)

                # Build unified call list from native or text-parsed calls
                call_list = []
                if native_calls:
                    for tc in native_calls:
                        try:
                            fn_name = tc.function.name
                            fn_args = tc.function.arguments or {}
                            if isinstance(fn_args, str):
                                try:
                                    fn_args = json.loads(fn_args)
                                except json.JSONDecodeError:
                                    fn_args = {}
                            call_list.append((fn_name, fn_args))
                        except (AttributeError, KeyError, TypeError) as e:
                            print(
                                f"  [OllamaAdapter] malformed tool_call, skipping: {e}",
                                file=sys.stderr,
                            )
                            messages.append(
                                {
                                    "role": "tool",
                                    "content": f"[ERROR: malformed tool call — {e}]",
                                }
                            )
                elif text_calls:
                    call_list = text_calls

                # Loop detection: if same call repeats 3+ times, break
                call_sig = str(call_list)
                _recent_calls.append(call_sig)
                if len(_recent_calls) >= 3 and len(set(_recent_calls[-3:])) == 1:
                    print(
                        f"  [OllamaAdapter] loop detected — same tool call repeated 3 times, "
                        f"treating as final answer (role={role}, model={model})",
                        file=sys.stderr,
                    )
                    elapsed = _time.time() - start_time
                    print(
                        f"  [OllamaAdapter metrics — role={role}, model={model}, "
                        f"turns={turn_count}, tool_uses={tool_use_count}, "
                        f"elapsed={elapsed:.0f}s, LOOP_BREAK]",
                        file=sys.stderr,
                    )

                    return strip_think_tags(content)

                # Execute calls
                # Truncation limit for glob (paths) and grep (matching lines).
                # Capped at 200 results each; both append [truncated] marker.
                _TOOL_TRUNCATE_LIMIT = 200

                for fn_name, fn_args in call_list:
                    tool_use_count += 1
                    print(f"  [TOOL] role={role} call={fn_name}", file=sys.stderr)

                    handler = TOOL_HANDLERS.get(fn_name)
                    if handler:
                        output = handler(fn_args)
                    elif mcp_bridge and mcp_bridge.is_mcp_tool(fn_name):
                        _mcp_t0 = _time.time()
                        output = await mcp_bridge.call_tool(fn_name, fn_args)
                        _mcp_latency_ms = int((_time.time() - _mcp_t0) * 1000)
                        _raw_len = len(output)
                        if _raw_len > 4000:
                            output = output[:4000] + "\n[truncated]"
                        print(
                            f"  [MCP] {fn_name}({json.dumps(fn_args)[:80]}) "
                            f"\u2192 {_raw_len} chars, {_mcp_latency_ms}ms",
                            file=sys.stderr,
                        )
                    else:
                        available = ", ".join(TOOL_HANDLERS)
                        output = f"[unknown tool '{fn_name}' — available: {available}]"

                    messages.append(
                        {"role": "tool", "tool_name": fn_name, "content": output}
                    )

            raise RuntimeError(
                f"OllamaAdapter: {max_t}-turn limit reached without final answer "
                f"(role={role}, model={model})"
            )

        except RuntimeError:
            raise
        except Exception as e:
            if "connection" in type(e).__name__.lower() or "connect" in str(e).lower():
                raise RuntimeError(
                    "Ollama is not running. Start it: ollama serve"
                ) from e
            raise
        finally:
            pass  # cwd management moved to subprocess cwd= kwarg (M1)
