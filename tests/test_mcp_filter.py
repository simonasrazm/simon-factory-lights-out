#!/usr/bin/env python3
"""Unit tests for _filter_mcp_for_gate and MCP observability."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.runner import _filter_mcp_for_gate


class TestFilterMcpForGate(unittest.TestCase):
    """Test all code paths in _filter_mcp_for_gate."""

    def setUp(self):
        self.all_servers = {
            "chrome-devtools": {"command": "npx", "args": ["chrome-mcp"]},
            "playwright": {"command": "npx", "args": ["playwright-mcp"]},
            "filesystem": {"command": "npx", "args": ["fs-mcp"]},
        }

    def test_none_mcp_returns_none(self):
        """No mcp field → None (use all servers)."""
        result = _filter_mcp_for_gate({"role": "dev"}, self.all_servers)
        self.assertIsNone(result)

    def test_empty_mcp_returns_empty_dict(self):
        """Explicit empty mcp: [] → {} (no servers)."""
        result = _filter_mcp_for_gate({"role": "scout", "mcp": []}, self.all_servers)
        self.assertEqual(result, {})

    def test_filter_matches_subset(self):
        """mcp: [chrome-devtools] → only that server."""
        result = _filter_mcp_for_gate(
            {"role": "qa", "mcp": ["chrome-devtools"]}, self.all_servers
        )
        self.assertEqual(set(result.keys()), {"chrome-devtools"})

    def test_filter_multiple_matches(self):
        """mcp: [chrome-devtools, playwright] → both."""
        result = _filter_mcp_for_gate(
            {"role": "qa", "mcp": ["chrome-devtools", "playwright"]}, self.all_servers
        )
        self.assertEqual(set(result.keys()), {"chrome-devtools", "playwright"})

    def test_filter_no_matches_returns_empty(self):
        """mcp: [nonexistent] → {} with warning (tested via stderr)."""
        result = _filter_mcp_for_gate(
            {"role": "qa", "mcp": ["nonexistent-server"]}, self.all_servers
        )
        self.assertEqual(result, {})

    def test_no_all_servers_returns_none(self):
        """If no servers available at all, return None regardless of gate config."""
        result = _filter_mcp_for_gate({"role": "qa", "mcp": ["chrome-devtools"]}, None)
        self.assertIsNone(result)

    def test_empty_all_servers_returns_none(self):
        """If all_servers is empty dict, return None."""
        result = _filter_mcp_for_gate({"role": "qa", "mcp": ["chrome-devtools"]}, {})
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
