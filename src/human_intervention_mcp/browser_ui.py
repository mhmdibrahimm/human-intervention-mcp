from __future__ import annotations

import asyncio
import base64
import html
import secrets
import webbrowser
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import parse_qs

from human_intervention_mcp.config import AppConfig
from human_intervention_mcp.models import (
    FieldType,
    HumanActionResult,
    HumanInputField,
    JsonValue,
    OperatorQuestionResult,
    OperatorResultStatus,
    PreparedOperatorQuestion,
    PreparedRequest,
    ResultStatus,
    ScreenshotInfo,
    validate_operator_answer,
    validate_submission,
)

BrowserOpener = Callable[[str], bool]


class BrowserLaunchError(RuntimeError):
    """Raised when the browser UI could not be launched."""


async def present_human_action(
    request: PreparedRequest,
    config: AppConfig,
    *,
    opener: BrowserOpener | None = None,
) -> HumanActionResult:
    result = await _serve_operation(
        config=config,
        title=request.task_title,
        render=lambda token, error: _render_human_action_page(token, request, error),
        handle=lambda form: _handle_human_action_submit(request, form),
        abandon_result=lambda: HumanActionResult.blocked(
            "Human intervention browser tab was closed before submission.",
        ),
        opener=opener,
    )
    return cast("HumanActionResult", result)


async def present_operator_question(
    question: PreparedOperatorQuestion,
    config: AppConfig,
    *,
    opener: BrowserOpener | None = None,
) -> OperatorQuestionResult:
    result = await _serve_operation(
        config=config,
        title=question.question_title,
        render=lambda token, error: _render_operator_page(token, question, error),
        handle=lambda form: _handle_operator_submit(question, form),
        abandon_result=lambda: OperatorQuestionResult.blocked(
            "Operator question browser tab was closed before submission.",
        ),
        opener=opener,
    )
    return cast("OperatorQuestionResult", result)


async def _serve_operation(
    *,
    config: AppConfig,
    title: str,
    render: Callable[[str, str | None], str],
    handle: Callable[[dict[str, list[str]]], Any],
    abandon_result: Callable[[], Any],
    opener: BrowserOpener | None,
) -> Any:
    token = secrets.token_urlsafe(24)
    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    async def client_connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await _read_http_request(reader)
            response = _route_request(
                request=request,
                token=token,
                render=render,
                handle=handle,
                abandon_result=abandon_result,
                future=future,
            )
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(client_connected, config.browser.host, 0)
    try:
        port = int(server.sockets[0].getsockname()[1]) if server.sockets else 0
        url = f"http://{config.browser.host}:{port}/{token}"
        browser_opener = opener or _default_browser_opener
        opened = await asyncio.to_thread(browser_opener, url)
        if not opened:
            raise BrowserLaunchError(f"Could not open default browser for {title}.")
        return await future
    finally:
        server.close()
        await server.wait_closed()


def _default_browser_opener(url: str) -> bool:
    return bool(webbrowser.open(url, new=2, autoraise=True))


async def _read_http_request(reader: asyncio.StreamReader) -> dict[str, Any]:
    header_bytes = await reader.readuntil(b"\r\n\r\n")
    header_text = header_bytes.decode("utf-8", errors="replace")
    lines = header_text.split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.lower()] = value.strip()
    content_length = int(headers.get("content-length", "0") or "0")
    body = await reader.readexactly(content_length) if content_length else b""
    return {"method": method, "path": path, "headers": headers, "body": body}


def _route_request(
    *,
    request: dict[str, Any],
    token: str,
    render: Callable[[str, str | None], str],
    handle: Callable[[dict[str, list[str]]], Any],
    abandon_result: Callable[[], Any],
    future: asyncio.Future[Any],
) -> bytes:
    expected_path = f"/{token}"
    request_path = request["path"].split("?", 1)[0]
    is_ajax_submit = request.get("headers", {}).get("x-human-intervention-ajax") == "1"
    if request_path == f"{expected_path}/abandon" and request["method"] == "POST":
        if not future.done():
            future.set_result(abandon_result())
        return _http_response("abandoned", content_type="text/plain")
    if request_path != expected_path:
        return _http_response("Not found", status="404 Not Found", content_type="text/plain")
    if request["method"] == "GET":
        return _http_response(render(token, None))
    if request["method"] != "POST":
        return _http_response(
            "Method not allowed",
            status="405 Method Not Allowed",
            content_type="text/plain",
        )
    form = parse_qs(request["body"].decode("utf-8", errors="replace"), keep_blank_values=True)
    try:
        result = handle(form)
    except ValueError as exc:
        return _http_response(
            render(token, str(exc)),
            status="422 Unprocessable Entity" if is_ajax_submit else "200 OK",
        )
    if not future.done():
        future.set_result(result)
    if is_ajax_submit:
        return _http_response("", status="204 No Content", content_type="text/plain")
    return _http_response(_submitted_page())


def render_input_field_html(field: HumanInputField) -> str:
    label = html.escape(field.label)
    field_name = f"field_{html.escape(field.id)}"
    required = " required" if field.required else ""
    placeholder = html.escape(field.placeholder or "")
    value = _string_default(field.default)
    if field.type is FieldType.TEXTAREA:
        return (
            f"<label>{label}<textarea name='{field_name}' placeholder='{placeholder}'"
            f"{required}>{html.escape(value)}</textarea></label>"
        )
    if field.type is FieldType.PASSWORD:
        return (
            f"<label>{label}<input type='password' name='{field_name}' value='{html.escape(value)}'"
            f" placeholder='{placeholder}'{required}></label>"
        )
    if field.type is FieldType.NUMBER:
        return (
            f"<label>{label}<input type='number' step='any' name='{field_name}'"
            f" value='{html.escape(value)}' placeholder='{placeholder}'{required}></label>"
        )
    if field.type is FieldType.BOOLEAN:
        checked = " checked" if field.default is True else ""
        return (
            f"<label class='choice'><input type='checkbox' name='{field_name}' "
            f"value='true'{checked}>"
            f" {label}</label>"
        )
    if field.type is FieldType.SELECT:
        options = "".join(
            _render_select_option_html(option.label, option.value, field.default)
            for option in field.options or []
        )
        return f"<label>{label}<select name='{field_name}'{required}>{options}</select></label>"
    return (
        f"<label>{label}<input type='text' name='{field_name}' value='{html.escape(value)}'"
        f" placeholder='{placeholder}'{required}></label>"
    )


def render_text_block(title: str, text: str) -> str:
    return (
        f"<section><h2>{html.escape(title)}</h2>"
        f"<div class='text-block'>{html.escape(text)}</div></section>"
    )


def _render_select_option_html(label: str, value: JsonValue, default: JsonValue) -> str:
    value_text = str(value)
    selected = " selected" if default == value else ""
    return f"<option value='{html.escape(value_text)}'{selected}>{html.escape(label)}</option>"


def _render_human_action_page(
    token: str,
    request: PreparedRequest,
    error: str | None,
) -> str:
    sections = [
        _kv("Agent", request.agent_name),
        _kv("Risk", request.risk_level.value.upper()),
        _kv("Working directory", request.working_directory or ""),
        render_text_block("Requested action", request.requested_action_markdown),
        render_text_block("Reason", request.reason_markdown),
    ]
    if request.screenshot:
        sections.append(_render_screenshot_block(request.screenshot))
    if request.terminal_output:
        sections.append(
            _render_terminal_output(
                request.terminal_output,
                request.terminal_output_truncated,
            )
        )
    input_fields = "".join(render_input_field_html(field) for field in request.input_fields)
    warning = ""
    if request.input_fields:
        warning = (
            "<p class='warning'>Anything entered here is returned to the agent. "
            "Enter sensitive information only when you intend to share it with that agent.</p>"
        )
    actions = "".join(
        f"<button type='submit' data-status='{status}'>{label}</button>"
        for status, label in (
            ("completed", "Completed"),
            ("blocked", "Blocked"),
            ("declined", "Declined"),
            ("need_more_context", "Need More Context"),
            ("abort_task", "Abort Task"),
        )
    )
    body = (
        _error_html(error)
        + "".join(section for section in sections if section)
        + warning
        + input_fields
        + "<input type='hidden' name='status' value='completed'>"
        + "<label>Optional note<textarea name='message'></textarea></label>"
        + f"<div class='actions'>{actions}</div>"
    )
    return _form_page(request.task_title, token, body, default_status="completed")


def _render_operator_page(
    token: str,
    question: PreparedOperatorQuestion,
    error: str | None,
) -> str:
    sections = [
        _kv("Agent", question.agent_name),
        _kv("Working directory", question.working_directory or ""),
        render_text_block("Question", question.question_markdown),
    ]
    if question.reason_markdown:
        sections.append(render_text_block("Reason", question.reason_markdown))
    if question.screenshot:
        sections.append(_render_screenshot_block(question.screenshot))
    if question.terminal_output:
        sections.append(
            _render_terminal_output(
                question.terminal_output,
                question.terminal_output_truncated,
            )
        )
    choice_fields = _render_operator_choices(question)
    if question.options:
        note_field = "<label>Optional note<textarea name='message'></textarea></label>"
    else:
        note_field = "<label>Answer<textarea name='message' required></textarea></label>"
    actions = (
        "<button type='submit' data-status='answered'>Answer</button>"
        "<button type='submit' data-status='blocked'>Blocked</button>"
        "<button type='submit' data-status='declined'>Declined</button>"
    )
    body = (
        _error_html(error)
        + "".join(section for section in sections if section)
        + choice_fields
        + "<input type='hidden' name='status' value='answered'>"
        + note_field
        + f"<div class='actions'>{actions}</div>"
    )
    return _form_page(question.question_title, token, body, default_status="answered")


def _render_operator_choices(question: PreparedOperatorQuestion) -> str:
    if not question.options:
        return ""
    input_type = "checkbox" if question.allow_multiple else "radio"
    return "".join(
        "<label class='choice option'>"
        f"<input type='{input_type}' name='selected_option_ids' value='{html.escape(option.id)}'>"
        f"<strong>{html.escape(option.label)}</strong>"
        f"<span>{html.escape(option.description_markdown or '')}</span>"
        "</label>"
        for option in question.options
    )


def _handle_human_action_submit(
    request: PreparedRequest,
    form: dict[str, list[str]],
) -> HumanActionResult:
    raw_status = _first_value(form, "status") or ResultStatus.BLOCKED.value
    status = ResultStatus(raw_status)
    message = (_first_value(form, "message") or "").strip()
    if status is not ResultStatus.COMPLETED:
        return HumanActionResult(
            status=status,
            message=message,
            input_values={},
            metadata={"timed_out": False},
        )
    values: dict[str, JsonValue] = {}
    for field in request.input_fields:
        raw = _first_value(form, f"field_{field.id}")
        values[field.id] = _coerce_field_value(field, raw)
    return HumanActionResult(
        status=ResultStatus.COMPLETED,
        message=message,
        input_values=validate_submission(request.input_fields, values),
        metadata={"timed_out": False},
    )


def _handle_operator_submit(
    question: PreparedOperatorQuestion,
    form: dict[str, list[str]],
) -> OperatorQuestionResult:
    raw_status = _first_value(form, "status") or OperatorResultStatus.BLOCKED.value
    status = OperatorResultStatus(raw_status)
    message = (_first_value(form, "message") or "").strip()
    if status is OperatorResultStatus.BLOCKED:
        return OperatorQuestionResult.blocked(message)
    if status is OperatorResultStatus.DECLINED:
        return OperatorQuestionResult(
            status=OperatorResultStatus.DECLINED,
            message=message,
            selected_options=[],
            metadata={"timed_out": False},
        )
    normalized_message, selected_options = validate_operator_answer(
        question,
        message=message,
        selected_option_ids=form.get("selected_option_ids", []),
    )
    return OperatorQuestionResult(
        status=OperatorResultStatus.ANSWERED,
        message=normalized_message,
        selected_options=selected_options,
        metadata={"timed_out": False},
    )


def _coerce_field_value(field: HumanInputField, raw: str | None) -> JsonValue:
    if field.type is FieldType.BOOLEAN:
        return raw == "true"
    if field.type is FieldType.NUMBER:
        if raw is None or not raw.strip():
            return None
        return _coerce_number(raw)
    return raw


def _coerce_number(value: str) -> int | float:
    stripped = value.strip()
    if "." in stripped:
        return float(stripped)
    return int(stripped)


def _render_screenshot_block(screenshot: ScreenshotInfo) -> str:
    preview = _render_screenshot_preview(screenshot)
    return f"<section><h2>Screenshot attached</h2>{preview}</section>"


def _render_screenshot_preview(screenshot: ScreenshotInfo) -> str:
    encoded = base64.b64encode(screenshot.data).decode("ascii")
    src = f"data:{screenshot.mime_type};base64,{encoded}"
    image = f"<img class='screenshot-preview' alt='Attached screenshot' src='{src}'>"
    if screenshot.path:
        return (
            f"<a class='screenshot-link' href='{src}' target='_blank' rel='noopener noreferrer'>"
            f"{image}</a>"
        )
    return image


def _render_terminal_output(value: str, truncated: bool) -> str:
    notice = "<p class='muted'>Terminal output was truncated.</p>" if truncated else ""
    return f"<section><h2>Terminal output</h2>{notice}<pre>{html.escape(value)}</pre></section>"


def _error_html(error: str | None) -> str:
    return f"<p class='error'>{html.escape(error)}</p>" if error else ""


def _form_page(title: str, token: str, body: str, *, default_status: str) -> str:
    script = (
        "<script>"
        "var form=document.querySelector('form');"
        "var submitted=false;"
        "var submitting=false;"
        "var submittedMarkup="
        '"<main><h1>Submitted</h1><p>Submission received. This tab will close '
        'automatically if the browser allows it.</p></main>";'
        "var submitFailureMarkup="
        "\"<p class='error'>Submission failed. Try again.</p>\";"
        "function showSubmitted(){"
        "document.body.innerHTML=submittedMarkup;"
        "window.close();"
        "setTimeout(function(){"
        "document.body.insertAdjacentHTML("
        "'beforeend',"
        "\"<p class='muted'>If this tab stays open, the browser blocked automatic closing. "
        'You can close it manually.</p>"'
        ");"
        "},250);"
        "}"
        "if(form){"
        "form.querySelectorAll('button[data-status]').forEach(function(button){"
        "button.addEventListener('click',function(){"
        "var status=form.querySelector('input[name=\"status\"]');"
        f"if(status){{status.value=button.dataset.status||{default_status!r};}}"
        "});"
        "});"
        "form.addEventListener('submit',function(event){"
        "event.preventDefault();"
        "if(submitting){return;}"
        "submitting=true;"
        "var payload=new URLSearchParams(new FormData(form));"
        "fetch(form.action,{"
        "method:'POST',"
        "headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8','X-Human-Intervention-Ajax':'1'},"
        "body:payload"
        "}).then(function(response){"
        "if(response.status===204){submitted=true;showSubmitted();return '';}"
        "return response.text().then(function(text){"
        "if(text){document.open();document.write(text);document.close();return '';}"
        "throw new Error('Submission failed.');"
        "});"
        "}).catch(function(){"
        "submitting=false;"
        "var error=document.querySelector('.error');"
        "if(error){error.textContent='Submission failed. Try again.';return;}"
        "form.insertAdjacentHTML('afterbegin',submitFailureMarkup);"
        "});"
        "});"
        "window.addEventListener('pagehide',function(){"
        "if(!submitted && !submitting && navigator.sendBeacon){"
        "navigator.sendBeacon(form.action + '/abandon','');"
        "}"
        "});"
        "}"
        "</script>"
    )
    return _page(
        title,
        f"<form method='post' action='/{html.escape(token)}'>{body}</form>{script}",
    )


def _submitted_page() -> str:
    body = (
        "<p>Submission received. This tab will close automatically if the browser allows it.</p>"
        "<script>"
        "window.close();"
        "setTimeout(function(){"
        "document.body.insertAdjacentHTML("
        "'beforeend',"
        "\"<p class='muted'>If this tab stays open, the browser blocked automatic closing. "
        'You can close it manually.</p>"'
        ");"
        "}, 250);"
        "</script>"
    )
    return _page("Submitted", body)


def _page(title: str, body: str) -> str:
    styles = [
        "body{font:16px system-ui,sans-serif;max-width:880px;margin:32px auto;",
        "padding:0 16px;line-height:1.45;color:#1a1a1a;background:#fafafa}",
        "main{background:#fff;border:1px solid #ddd;border-radius:8px;padding:24px}",
        "h1{margin-top:0;font-size:28px}h2{font-size:18px;margin-bottom:8px}",
        "label{display:block;margin:16px 0}textarea,input,select{display:block;",
        "width:100%;box-sizing:border-box;margin-top:6px;padding:10px;",
        "border:1px solid #bbb;border-radius:6px;background:#fff}",
        "textarea{min-height:120px}pre,.text-block{white-space:pre-wrap;",
        "background:#f4f4f4;padding:12px;border-radius:6px;overflow:auto}",
        ".actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:20px}",
        ".actions button{padding:10px 14px;border:1px solid #999;",
        "background:#f8f8f8;border-radius:6px;cursor:pointer}",
        ".choice input{display:inline;width:auto;margin-right:8px}",
        ".choice span{display:block;color:#555;margin-left:24px}",
        ".option{padding:10px 12px;border:1px solid #ddd;border-radius:6px;",
        "background:#fcfcfc}",
        ".error{border:1px solid #b00020;color:#b00020;padding:10px;",
        "border-radius:6px;background:#fff3f5}",
        ".warning{font-weight:600}.muted{color:#555}",
        "section{margin:18px 0}",
        ".screenshot-preview{display:block;max-width:100%;height:auto;"
        "border:1px solid #ddd;border-radius:6px;background:#f4f4f4}",
        ".screenshot-link{display:block}",
    ]
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        f"<style>{''.join(styles)}</style></head><body><main>"
        f"<h1>{html.escape(title)}</h1>{body}</main></body></html>"
    )


def _kv(title: str, value: str) -> str:
    if not value:
        return ""
    return f"<p><strong>{html.escape(title)}:</strong> {html.escape(value)}</p>"


def _first_value(form: dict[str, list[str]], key: str) -> str | None:
    values = form.get(key)
    return values[0] if values else None


def _http_response(
    body: str,
    *,
    status: str = "200 OK",
    content_type: str = "text/html; charset=utf-8",
) -> bytes:
    encoded = body.encode("utf-8")
    headers = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(encoded)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    return headers + encoded


def _string_default(value: JsonValue) -> str:
    if value is None:
        return ""
    return str(value)
