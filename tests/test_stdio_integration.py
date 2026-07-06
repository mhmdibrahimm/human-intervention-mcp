from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO
from urllib.parse import urlencode, urlsplit

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER_ROOT = Path(__file__).resolve().parents[1]
SERVER_COMMAND = str(Path(sys.executable).absolute())
SRC_PATH = str(SERVER_ROOT / "src")

SERVER_CODE = f"""
import sys
sys.path.insert(0, {SRC_PATH!r})
import human_intervention_mcp.browser_ui as browser_ui
from human_intervention_mcp.app import main

def opener(url: str) -> bool:
    print(url, file=sys.stderr, flush=True)
    return True

browser_ui._default_browser_opener = opener
raise SystemExit(main(["mcp"]))
"""


@pytest.mark.asyncio
async def test_request_human_action_completes_over_stdio() -> None:
    async with _StdioSession() as stdio:
        session = stdio.client
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == {"request_human_action", "ask_operator"}

        call = asyncio.create_task(
            session.call_tool(
                "request_human_action",
                {
                    "task_title": "Smoke",
                    "requested_action_markdown": "Do the thing.",
                    "reason_markdown": "Testing stdio end-to-end.",
                    "risk_level": "low",
                },
            )
        )
        url = await _wait_for_url(stdio.stderr_path)
        await _post_form(url, {"status": "completed", "message": "done"})
        result = await asyncio.wait_for(call, timeout=10)
        assert result.isError is False
        assert result.structuredContent == {
            "status": "completed",
            "message": "done",
            "input_values": {},
            "metadata": {"timed_out": False},
        }


@pytest.mark.asyncio
async def test_ask_operator_completes_over_stdio() -> None:
    async with _StdioSession() as stdio:
        session = stdio.client
        call = asyncio.create_task(
            session.call_tool(
                "ask_operator",
                {
                    "question_title": "Choose path",
                    "question_markdown": "Which path should I take?",
                    "options": [
                        {"id": "alpha", "label": "Alpha", "value": "alpha"},
                        {"id": "beta", "label": "Beta", "value": "beta"},
                    ],
                },
            )
        )
        url = await _wait_for_url(stdio.stderr_path)
        await _post_form(url, {"status": "answered", "selected_option_ids": "beta"})
        result = await asyncio.wait_for(call, timeout=10)
        assert result.isError is False
        assert result.structuredContent == {
            "status": "answered",
            "message": "",
            "selected_options": [{"id": "beta", "label": "Beta", "value": "beta"}],
            "metadata": {"timed_out": False},
        }


@pytest.mark.asyncio
async def test_request_human_action_abandon_returns_blocked() -> None:
    async with _StdioSession() as stdio:
        session = stdio.client
        call = asyncio.create_task(
            session.call_tool(
                "request_human_action",
                {
                    "task_title": "Smoke",
                    "requested_action_markdown": "Do the thing.",
                    "reason_markdown": "Testing abandon behavior.",
                    "risk_level": "low",
                },
            )
        )
        url = await _wait_for_url(stdio.stderr_path)
        await _post_raw(url + "/abandon", b"")
        result = await asyncio.wait_for(call, timeout=10)
        assert result.structuredContent == {
            "status": "blocked",
            "message": "Human intervention browser tab was closed before submission.",
            "input_values": {},
            "metadata": {"timed_out": False},
        }


class _StdioSession:
    def __init__(self) -> None:
        self._stderr: TextIO = tempfile.NamedTemporaryFile("w+", delete=False)
        self.stderr_path = Path(self._stderr.name)
        self._streams: Any = None
        self.session: ClientSession | None = None

    @property
    def client(self) -> ClientSession:
        assert self.session is not None
        return self.session

    async def __aenter__(self) -> _StdioSession:
        params = StdioServerParameters(
            command=SERVER_COMMAND,
            args=["-c", SERVER_CODE],
            cwd=str(SERVER_ROOT),
        )
        self._streams = stdio_client(params, errlog=self._stderr)
        read_stream, write_stream = await self._streams.__aenter__()
        self.session = ClientSession(read_stream, write_stream)
        await self.session.__aenter__()
        await self.session.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self.session is not None:
                await self.session.__aexit__(exc_type, exc, tb)
        finally:
            if self._streams is not None:
                await self._streams.__aexit__(exc_type, exc, tb)
            self._stderr.close()


async def _wait_for_url(log_path: Path) -> str:
    for _ in range(500):
        try:
            text = await asyncio.to_thread(log_path.read_text)
        except FileNotFoundError:
            text = ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("http://"):
                return line
        await asyncio.sleep(0.02)
    raise AssertionError("browser URL was not opened")


async def _post_form(url: str, form_data: dict[str, str]) -> None:
    encoded = urlencode(form_data, doseq=True).encode("utf-8")
    await _post_raw(url, encoded, content_type="application/x-www-form-urlencoded")


async def _post_raw(
    url: str,
    body: bytes,
    *,
    content_type: str = "text/plain",
) -> None:
    parsed = urlsplit(url)
    port = parsed.port
    if port is None:
        raise AssertionError("missing port")
    reader, writer = await asyncio.open_connection(parsed.hostname or "127.0.0.1", port)
    request = (
        f"POST {parsed.path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{port}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    writer.write(request)
    await writer.drain()
    await reader.read()
    writer.close()
    await writer.wait_closed()
