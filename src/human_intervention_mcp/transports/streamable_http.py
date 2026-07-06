from __future__ import annotations

from human_intervention_mcp.config import AppConfig
from human_intervention_mcp.server import build_mcp_server


async def run_streamable_http(config: AppConfig) -> None:
    server = build_mcp_server(config)
    await server.run_streamable_http_async()
