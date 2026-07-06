from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from human_intervention_mcp.config import AppConfig
from human_intervention_mcp.coordinator import HumanInterventionCoordinator

SERVER_INSTRUCTIONS = """\
request_human_action opens a local browser page for one manual intervention.
ask_operator opens a local browser page for one consultative operator question.
Each tool call opens one browser tab, blocks until a human response, browser
launch failure, or configured timeout, and then returns one final result.
The server does not provide polling, status APIs, or background completion.
Use these tools only when the agent's own instructions permit or request human escalation.
"""


def build_mcp_server(config: AppConfig) -> FastMCP:
    server = FastMCP(
        "human-intervention-mcp",
        instructions=SERVER_INSTRUCTIONS,
        host=config.http.host,
        port=config.http.port,
        streamable_http_path=config.http.path,
        log_level="ERROR",
    )
    coordinator = HumanInterventionCoordinator(config)

    @server.tool(
        name="request_human_action",
        description=(
            "Synchronously request a manual human action, input, decision, or confirmation "
            "through a local browser page."
        ),
        structured_output=True,
    )
    async def request_human_action(
        task_title: str,
        requested_action_markdown: str,
        reason_markdown: str,
        risk_level: str,
        agent_name: str | None = None,
        working_directory: str | None = None,
        terminal_output: str | None = None,
        screenshot: dict[str, Any] | None = None,
        input_fields: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result = await coordinator.request_human_action(
            {
                "task_title": task_title,
                "requested_action_markdown": requested_action_markdown,
                "reason_markdown": reason_markdown,
                "risk_level": risk_level,
                "agent_name": agent_name,
                "working_directory": working_directory,
                "terminal_output": terminal_output,
                "screenshot": screenshot,
                "input_fields": input_fields or [],
            }
        )
        return result.model_dump(mode="json")

    @server.tool(
        name="ask_operator",
        description=(
            "Synchronously ask the operator a consultative question or branching decision "
            "through a local browser page."
        ),
        structured_output=True,
    )
    async def ask_operator(
        question_title: str,
        question_markdown: str,
        reason_markdown: str | None = None,
        agent_name: str | None = None,
        working_directory: str | None = None,
        terminal_output: str | None = None,
        screenshot: dict[str, Any] | None = None,
        options: list[dict[str, Any]] | None = None,
        allow_multiple: bool = False,
    ) -> dict[str, Any]:
        result = await coordinator.ask_operator(
            {
                "question_title": question_title,
                "question_markdown": question_markdown,
                "reason_markdown": reason_markdown,
                "agent_name": agent_name,
                "working_directory": working_directory,
                "terminal_output": terminal_output,
                "screenshot": screenshot,
                "options": options or [],
                "allow_multiple": allow_multiple,
            }
        )
        return result.model_dump(mode="json")

    return server
