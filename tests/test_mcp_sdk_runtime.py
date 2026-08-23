"""Runtime compatibility checks for the supported MCP SDK."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from openchronicle.config import Config
from openchronicle.mcp.server import build_server, run_async


class _RecordingServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run_stdio_async(self) -> None:
        self.calls.append(("stdio", {}))

    async def run_sse_async(self, **kwargs: object) -> None:
        self.calls.append(("sse", kwargs))

    async def run_streamable_http_async(self, **kwargs: object) -> None:
        self.calls.append(("streamable-http", kwargs))


class MCPServerRuntimeTests(unittest.TestCase):
    def test_build_server_registers_all_tools(self) -> None:
        server = build_server(Config())

        tools = asyncio.run(server.list_tools())

        self.assertEqual(
            {tool.name for tool in tools},
            {
                "current_context",
                "get_schema",
                "list_memories",
                "read_memory",
                "read_recent_capture",
                "recent_activity",
                "search",
                "search_captures",
            },
        )

    def test_run_async_passes_http_bind_settings(self) -> None:
        cfg = Config()
        cfg.mcp.host = "127.0.0.9"
        cfg.mcp.port = 9876
        server = _RecordingServer()

        with patch("openchronicle.mcp.server.build_server", return_value=server):
            asyncio.run(run_async(cfg, transport="streamable-http"))

        self.assertEqual(
            server.calls,
            [("streamable-http", {"host": "127.0.0.9", "port": 9876})],
        )

    def test_run_async_passes_sse_bind_settings(self) -> None:
        cfg = Config()
        cfg.mcp.host = "127.0.0.8"
        cfg.mcp.port = 8765
        server = _RecordingServer()

        with patch("openchronicle.mcp.server.build_server", return_value=server):
            asyncio.run(run_async(cfg, transport="sse"))

        self.assertEqual(server.calls, [("sse", {"host": "127.0.0.8", "port": 8765})])

    def test_run_async_selects_stdio_without_network_settings(self) -> None:
        cfg = Config()
        server = _RecordingServer()

        with patch("openchronicle.mcp.server.build_server", return_value=server):
            asyncio.run(run_async(cfg, transport="stdio"))

        self.assertEqual(server.calls, [("stdio", {})])


if __name__ == "__main__":
    unittest.main()
