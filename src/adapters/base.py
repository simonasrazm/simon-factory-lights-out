"""RuntimeAdapter — base class for all runtime adapters."""

import json
import os

# SFLO_ROOT needed for MCP defaults resolution
from ..constants import SFLO_ROOT


class RuntimeAdapter:
    """Base class — spawn an agent and return its response text."""

    # MCP server configs — shared across all adapters via configure_mcp().
    # Each adapter subclass honors what it can.
    _mcp_servers = None
    _extra_cli_args = {}
    # MCP bridge (class-level so all adapter instances share it)
    _mcp_bridge = None

    @classmethod
    def configure_mcp(cls, mcp_servers=None, extra_cli_args=None, load_user_mcp=False):
        """Configure MCP servers and extra CLI flags for all agent spawns.

        Runtime-agnostic: sets class-level config on RuntimeAdapter so all
        subclasses (ClaudeCodeAdapter, OpenClawAdapter, future adapters)
        can read it. Each adapter decides how to forward the config.

        Args:
            mcp_servers: dict of MCP server configs, or path to JSON config.
            extra_cli_args: dict of extra CLI flags, e.g. {"chrome": None}.
            load_user_mcp: if True, read ~/.claude.json mcpServers and
                merge with any explicitly passed mcp_servers.
        """
        if load_user_mcp:
            user_mcp = cls._load_user_mcp_servers()
            if user_mcp:
                if mcp_servers and isinstance(mcp_servers, dict):
                    user_mcp.update(mcp_servers)
                mcp_servers = user_mcp
        if mcp_servers is not None:
            cls._mcp_servers = mcp_servers
        if extra_cli_args is not None:
            cls._extra_cli_args = extra_cli_args

    # MCP defaults loaded from config file at runtime.
    _mcp_defaults = None

    @classmethod
    def _load_mcp_defaults(cls):
        """Load MCP defaults from mcp-defaults.json.

        Resolution: cwd → cwd/sflo → SFLO_PARENT → SFLO_ROOT.
        Same walk-up pattern as bindings.yaml.
        """
        if cls._mcp_defaults is not None:
            return cls._mcp_defaults

        cwd = os.getcwd()
        sflo_parent = os.path.dirname(SFLO_ROOT)
        for candidate in [
            os.path.join(cwd, "mcp-defaults.json"),
            os.path.join(cwd, "sflo", "mcp-defaults.json"),
            os.path.join(sflo_parent, "mcp-defaults.json"),
            os.path.join(SFLO_ROOT, "mcp-defaults.json"),
        ]:
            if os.path.isfile(candidate):
                try:
                    with open(candidate, "r") as f:
                        cls._mcp_defaults = json.load(f)
                    return cls._mcp_defaults
                except (OSError, json.JSONDecodeError):
                    continue

        cls._mcp_defaults = {}
        return cls._mcp_defaults

    @classmethod
    def _load_user_mcp_servers(cls):
        """Read MCP servers from ~/.claude.json and apply safe defaults."""
        config_path = os.path.join(os.path.expanduser("~"), ".claude.json")
        if not os.path.isfile(config_path):
            return {}
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
            servers = data.get("mcpServers", {})

            defaults = cls._load_mcp_defaults()
            for name, defs in defaults.items():
                if name in servers:
                    args = servers[name].get("args", [])
                    for req_arg in defs.get("required_args", []):
                        if req_arg.startswith("--") and req_arg not in args:
                            args.append(req_arg)
                            idx = defs["required_args"].index(req_arg)
                            if idx + 1 < len(defs["required_args"]):
                                val = defs["required_args"][idx + 1]
                                if not val.startswith("--"):
                                    args.append(val)
                    servers[name]["args"] = args
            return servers
        except (OSError, json.JSONDecodeError):
            return {}

    async def spawn_agent(self, model, system_prompt, user_prompt, cwd=None, **kwargs):
        """Spawn agent via runtime. Returns response text (str).

        Args:
            cwd: Working directory for agent (user deliverables land here).
                 If None, uses inherited process cwd.
            **kwargs: Runtime-specific options (role, allowed_tools, etc).
        """
        raise NotImplementedError
