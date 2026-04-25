"""MCP bridge for OllamaAdapter.

Connects to MCP servers and exposes their tools to ollama agents.
Generic infrastructure — works with any MCP server.

Usage:
    from mcp_bridge import OllamaMCPBridge

    # Basic: no tool hints
    bridge = OllamaMCPBridge()

    # With hints: contextual guidance for specific tools
    bridge = OllamaMCPBridge(tool_hints={
        "query_db": "Use to run SQL queries against the database.",
        "list_tables": "Use to discover available tables.",
    })

    await bridge.connect_server("my-server", {
        "command": "npx", "args": ["my-mcp-server"],
    })
    tools = bridge.get_ollama_tools()
    result = await bridge.call_tool(name, args)
    await bridge.close()
"""

import asyncio
import os
import sys


class OllamaMCPBridge:
    """Bridge between ollama tool calls and MCP servers.

    Args:
        tool_hints: Optional dict mapping tool names to contextual hints.
            Hints tell models WHEN to use each tool (not just WHAT it does).
            Example: {"run_query": "Use to execute SQL against the database."}
            Without hints, tools use their native MCP descriptions only.
    """

    def __init__(self, tool_hints=None):
        self._sessions = {}  # server_name -> (session, read, write)
        self._tools = {}  # tool_name -> (server_name, tool_schema)
        self._tool_hints = tool_hints or {}

    async def connect_server(self, name, config):
        """Connect to an MCP server via stdio.

        Args:
            name: Server name (e.g. "my-tools")
            config: Dict with 'command' and 'args' keys, matching
                    mcpServers format from ~/.claude.json
        """
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command = config.get("command", "")
        args = config.get("args", [])
        env_override = config.get("env", {})

        # Build environment
        env = dict(os.environ)
        env.update(env_override)

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )

        # Connect using async context manager pattern
        self._stdio_cm = stdio_client(server_params)
        read, write = await self._stdio_cm.__aenter__()

        session = ClientSession(read, write)
        self._session_cm = session
        await session.__aenter__()

        # Initialize
        await session.initialize()

        # List tools
        tools_result = await session.list_tools()
        for tool in tools_result.tools:
            self._tools[tool.name] = (name, tool)
            print(
                f"  [MCP Bridge] Registered tool: {tool.name} (from {name})",
                file=sys.stderr,
            )

        self._sessions[name] = session
        print(
            f"  [MCP Bridge] Connected to {name}: {len(tools_result.tools)} tools",
            file=sys.stderr,
        )

    # No hardcoded tool hints — host project passes hints via constructor.
    # See tool_hints parameter in __init__.

    @staticmethod
    def _sanitize_schema_properties(properties):
        """Sanitize MCP tool parameter schemas for ollama compatibility.

        Ollama/local models handle simple flat schemas well but struggle with:
        - Deep nested objects inside arrays
        - anyOf/oneOf/allOf constructs
        - items with complex object schemas

        Strategy: flatten complex schemas to their primitive type where possible.
        Arrays of strings/numbers stay as-is (ollama handles them).
        Arrays of complex objects get simplified to 'string' with description note.
        """
        sanitized = {}
        for param_name, param_schema in properties.items():
            if not isinstance(param_schema, dict):
                sanitized[param_name] = param_schema
                continue

            ptype = param_schema.get("type")
            items = param_schema.get("items", {})
            desc = param_schema.get("description", "")

            if ptype == "array" and isinstance(items, dict):
                items_type = items.get("type", "")
                if items_type in ("string", "number", "integer", "boolean"):
                    # Simple typed array — safe to keep as-is
                    clean = {
                        k: v
                        for k, v in param_schema.items()
                        if k not in ("additionalProperties",)
                    }
                    sanitized[param_name] = clean
                else:
                    # Complex array (nested objects) — flatten to string
                    # Append note to description so model knows format
                    note = (
                        " (provide as JSON array string)"
                        if not desc.endswith(")")
                        else ""
                    )
                    sanitized[param_name] = {
                        "type": "string",
                        "description": desc + note,
                    }
            else:
                # Remove additionalProperties (causes issues in some ollama versions)
                clean = {
                    k: v
                    for k, v in param_schema.items()
                    if k not in ("additionalProperties", "$schema")
                }
                sanitized[param_name] = clean

        return sanitized

    def _build_tool_dict(self, tool_name, tool):
        """Convert a single MCP tool to ollama format with schema sanitization."""
        properties = {}
        required = []
        if tool.inputSchema and isinstance(tool.inputSchema, dict):
            raw_props = tool.inputSchema.get("properties", {})
            properties = self._sanitize_schema_properties(raw_props)
            required = tool.inputSchema.get("required", [])

        desc = tool.description or f"MCP tool: {tool_name}"
        hint = self._tool_hints.get(tool_name)
        if hint:
            desc = f"{hint} — {desc}"

        return {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def get_ollama_tools(self):
        """Convert MCP tools to ollama tool format.

        Enhances tool descriptions with contextual hints (from tool_hints
        passed to constructor) so models know WHEN to use each tool.
        Returns list of ollama tool dicts ready for chat(tools=[...]).
        """
        ollama_tools = []
        for tool_name, (server_name, tool) in self._tools.items():
            ollama_tools.append(self._build_tool_dict(tool_name, tool))
        return ollama_tools

    async def call_tool(self, name, arguments):
        """Call an MCP tool and return the result as string.

        Args:
            name: Tool name (must be registered via connect_server)
            arguments: Dict of tool arguments

        Returns:
            str: Tool result text
        """
        if name not in self._tools:
            return f"[MCP error: unknown tool '{name}']"

        server_name, tool = self._tools[name]
        session = self._sessions.get(server_name)
        if not session:
            return f"[MCP error: server '{server_name}' not connected]"

        try:
            result = await session.call_tool(name, arguments)
            # Extract text from result content
            if hasattr(result, "content") and result.content:
                parts = []
                for block in result.content:
                    if hasattr(block, "text"):
                        parts.append(block.text)
                    elif hasattr(block, "data"):
                        parts.append(f"[binary data: {len(block.data)} bytes]")
                return "\n".join(parts) if parts else "[empty result]"
            return str(result)
        except Exception as e:
            return f"[MCP error calling {name}: {e}]"

    def get_usage_guidance(self):
        """Generate usage guidance from connected MCP servers.

        Returns a short instruction block that explains when and how to
        use MCP tools, derived from tool names and descriptions.
        Lists EXACT tool names to prevent hallucination.
        Not hardcoded — adapts to whatever tools are connected.
        """
        if not self._tools:
            return ""

        # Group tools by server
        servers = {}
        for tool_name, (server_name, tool) in self._tools.items():
            servers.setdefault(server_name, []).append(
                (tool_name, tool.description or "")
            )

        lines = ["\n## Connected Tool Servers\n"]
        for server_name, tool_list in servers.items():
            # Show tools that have hints (host project decides which are important)
            hinted = [(n, d) for n, d in tool_list if n in self._tool_hints]
            if hinted:
                lines.append(f"**{server_name}** — key tools:")
                for name, desc in sorted(hinted):
                    lines.append(f"  - `{name}`: {self._tool_hints[name]}")
            else:
                lines.append(f"**{server_name}** — {len(tool_list)} tools available.")
            lines.append("")
        return "\n".join(lines)

    def is_mcp_tool(self, name):
        """Check if a tool name is an MCP tool (vs built-in bash/read/write)."""
        return name in self._tools

    async def close(self):
        """Close all MCP server connections.

        Suppresses anyio cancel scope errors that occur when asyncio.run()
        shuts down — the stdio_client context manager's task group cleanup
        races with the event loop teardown. Cosmetic issue, not a leak.
        """
        import warnings

        warnings.filterwarnings(
            "ignore", message=".*cancel scope.*", category=RuntimeWarning
        )
        for cm in ("_session_cm", "_stdio_cm"):
            if hasattr(self, cm):
                try:
                    await getattr(self, cm).__aexit__(None, None, None)
                except (RuntimeError, asyncio.CancelledError, OSError):
                    pass
        self._sessions.clear()
        self._tools.clear()
        print("  [MCP Bridge] Closed.", file=sys.stderr)
