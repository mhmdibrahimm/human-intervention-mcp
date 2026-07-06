from __future__ import annotations

import pytest

from human_intervention_mcp.config import AppConfig
from human_intervention_mcp.server import SERVER_INSTRUCTIONS, build_mcp_server


@pytest.mark.asyncio
async def test_no_polling_ids_or_status_api() -> None:
    server = build_mcp_server(AppConfig())
    tools = await server.list_tools()
    names = [tool.name for tool in tools]
    assert names == ["request_human_action", "ask_operator"]
    assert "get_status" not in names
    assert "polling" in SERVER_INSTRUCTIONS.lower()
