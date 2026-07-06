from __future__ import annotations

import pytest

import human_intervention_mcp.app as app_module
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


@pytest.mark.asyncio
async def test_doctor_uses_browser_fallback_detection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        app_module,
        "browser_launcher_description",
        lambda: "/usr/bin/xdg-open http://127.0.0.1/",
    )
    exit_code = await app_module.run_doctor(AppConfig(), mcp_timeout_sec=960)
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "browser launcher" in output
    assert "xdg-open" in output
