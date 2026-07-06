from __future__ import annotations

import asyncio
from urllib.parse import urlencode, urlsplit

import pytest

import human_intervention_mcp.browser_ui as browser_ui
from human_intervention_mcp.browser_ui import (
    BrowserLaunchError,
    _default_browser_opener,
    _render_human_action_page,
    _submitted_page,
    browser_launcher_description,
    present_human_action,
    present_operator_question,
)
from human_intervention_mcp.config import AppConfig
from human_intervention_mcp.models import (
    HumanActionRequest,
    OperatorQuestionRequest,
    OperatorResultStatus,
    ResultStatus,
    prepare_operator_question,
    prepare_request,
)


def _base_request(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "task_title": "Approve deploy",
        "requested_action_markdown": "Press the deploy button.",
        "reason_markdown": "Human approval is required.",
        "risk_level": "medium",
    }
    data.update(overrides)
    return data


def _base_operator(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "question_title": "Choose strategy",
        "question_markdown": "Which implementation path should I use?",
        "options": [
            {"id": "fast", "label": "Fast", "value": "fast"},
            {"id": "safe", "label": "Safe", "value": "safe"},
        ],
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_human_action_browser_flow_returns_submitted_result() -> None:
    prepared = prepare_request(
        HumanActionRequest.model_validate(
            _base_request(
                input_fields=[
                    {
                        "id": "confirmation_code",
                        "label": "Confirmation code",
                        "type": "password",
                        "required": True,
                    }
                ]
            )
        ),
        AppConfig(),
    )
    opened_urls: list[str] = []

    def opener(url: str) -> bool:
        opened_urls.append(url)
        return True

    task = asyncio.create_task(present_human_action(prepared, AppConfig(), opener=opener))
    await _wait_for_url(opened_urls)
    await _post_form(
        opened_urls[0],
        {
            "status": "completed",
            "field_confirmation_code": "123456",
            "message": "Confirmed",
        },
    )
    result = await task
    assert result.status is ResultStatus.COMPLETED
    assert result.input_values["confirmation_code"] == "123456"
    assert result.message == "Confirmed"


@pytest.mark.asyncio
async def test_operator_browser_flow_returns_selected_option() -> None:
    prepared = prepare_operator_question(
        OperatorQuestionRequest.model_validate(_base_operator()),
        AppConfig(),
    )
    opened_urls: list[str] = []

    def opener(url: str) -> bool:
        opened_urls.append(url)
        return True

    task = asyncio.create_task(present_operator_question(prepared, AppConfig(), opener=opener))
    await _wait_for_url(opened_urls)
    await _post_form(
        opened_urls[0],
        {
            "status": "answered",
            "selected_option_ids": "safe",
            "message": "Use the safer path.",
        },
    )
    result = await task
    assert result.status is OperatorResultStatus.ANSWERED
    assert result.selected_options[0].id == "safe"
    assert result.message == "Use the safer path."


@pytest.mark.asyncio
async def test_human_action_ajax_submit_returns_result() -> None:
    prepared = prepare_request(HumanActionRequest.model_validate(_base_request()), AppConfig())
    opened_urls: list[str] = []

    def opener(url: str) -> bool:
        opened_urls.append(url)
        return True

    task = asyncio.create_task(present_human_action(prepared, AppConfig(), opener=opener))
    await _wait_for_url(opened_urls)
    await _post_form(
        opened_urls[0],
        {"status": "completed", "message": "Confirmed"},
        headers={"X-Human-Intervention-Ajax": "1"},
    )
    result = await task
    assert result.status is ResultStatus.COMPLETED
    assert result.message == "Confirmed"


def test_screenshot_preview_is_rendered_in_page(png_base64: str) -> None:
    prepared = prepare_request(
        HumanActionRequest.model_validate(
            _base_request(
                screenshot={
                    "kind": "base64",
                    "mime_type": "image/png",
                    "data": png_base64,
                }
            )
        ),
        AppConfig(),
    )
    page = _render_human_action_page("token", prepared, None)
    screenshot_section = page.split("<h2>Screenshot attached</h2>", 1)[1].split("</section>", 1)[0]
    assert "Attached screenshot" in page
    assert "data:image/png;base64," in page
    assert "screenshot-preview" in page
    assert "<p class='muted'>" not in screenshot_section


@pytest.mark.asyncio
async def test_browser_launch_failure_raises() -> None:
    prepared = prepare_request(HumanActionRequest.model_validate(_base_request()), AppConfig())

    def opener(url: str) -> bool:
        del url
        return False

    with pytest.raises(BrowserLaunchError, match="default browser"):
        await present_human_action(prepared, AppConfig(), opener=opener)


def test_submitted_page_attempts_to_close_tab() -> None:
    page = _submitted_page()
    assert "window.close()" in page
    assert "blocked automatic closing" in page


def test_default_browser_opener_uses_command_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_ui.webbrowser, "open", lambda *args, **kwargs: False)
    monkeypatch.setattr(browser_ui, "_fallback_browser_commands", lambda url: [["xdg-open", url]])
    monkeypatch.setattr(
        browser_ui,
        "_run_browser_command",
        lambda command: command[0] == "xdg-open",
    )
    assert _default_browser_opener("http://127.0.0.1/")


def test_browser_launcher_description_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error() -> object:
        raise browser_ui.webbrowser.Error("could not locate runnable browser")

    monkeypatch.setattr(browser_ui.webbrowser, "get", raise_error)
    monkeypatch.setattr(browser_ui, "_platform_name", lambda: "linux")
    monkeypatch.setattr(
        browser_ui.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "xdg-open" else None,
    )
    assert browser_launcher_description() == "/usr/bin/xdg-open http://127.0.0.1/"


async def _wait_for_url(urls: list[str]) -> None:
    for _ in range(50):
        if urls:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("browser URL was not opened")


async def _post_form(
    url: str,
    form_data: dict[str, str],
    headers: dict[str, str] | None = None,
) -> None:
    parsed = urlsplit(url)
    port = parsed.port
    if port is None:
        raise AssertionError("missing port")
    reader, writer = await asyncio.open_connection(parsed.hostname or "127.0.0.1", port)
    encoded = urlencode(form_data, doseq=True).encode("utf-8")
    extra_headers = "".join(f"{key}: {value}\r\n" for key, value in (headers or {}).items())
    request = (
        f"POST {parsed.path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{port}\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(encoded)}\r\n"
        f"{extra_headers}"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + encoded
    writer.write(request)
    await writer.drain()
    await reader.read()
    writer.close()
    await writer.wait_closed()
