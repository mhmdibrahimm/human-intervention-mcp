from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from human_intervention_mcp.browser_ui import render_input_field_html, render_text_block
from human_intervention_mcp.config import AppConfig, ServerConfig
from human_intervention_mcp.models import (
    FieldType,
    HumanActionRequest,
    HumanInputField,
    HumanInputOption,
    OperatorQuestionRequest,
    OperatorResultStatus,
    ResultStatus,
    parse_operator_question,
    parse_request,
    prepare_operator_question,
    prepare_request,
    truncate_terminal_output,
    validate_operator_answer,
    validate_submission,
)


def base_request(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "task_title": "Approve deploy",
        "requested_action_markdown": "Press the deploy button.",
        "reason_markdown": "The deploy console requires a human.",
        "risk_level": "medium",
    }
    data.update(overrides)
    return data


def base_operator_question(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "question_title": "Choose strategy",
        "question_markdown": "Which implementation path should I use?",
    }
    data.update(overrides)
    return data


def test_tool_input_schema_validation() -> None:
    request = parse_request(base_request(risk_level="high"))
    assert request.risk_level.value == "high"
    with pytest.raises(ValidationError):
        parse_request(base_request(risk_level="urgent"))
    with pytest.raises(ValidationError):
        parse_request(base_request(task_title=""))


def test_structured_input_field_validation() -> None:
    with pytest.raises(ValidationError):
        HumanInputField(id="choice", label="Choice", type=FieldType.SELECT)
    with pytest.raises(ValidationError):
        parse_request(
            base_request(
                input_fields=[
                    {"id": "x", "label": "One", "type": "text", "required": False},
                    {"id": "x", "label": "Two", "type": "text", "required": False},
                ]
            )
        )
    request = HumanActionRequest.model_validate(
        base_request(
            input_fields=[
                {"id": "a", "label": "A", "type": "text", "required": False},
                {"id": "b", "label": "B", "type": "text", "required": False},
            ]
        )
    )
    tiny_config = AppConfig(server=ServerConfig(max_input_fields=1))
    with pytest.raises(ValueError, match="too many input fields"):
        prepare_request(request, tiny_config)


def test_operator_question_schema_validation() -> None:
    question = parse_operator_question(
        base_operator_question(
            options=[
                {"id": "fast", "label": "Fast", "value": "fast"},
                {"id": "safe", "label": "Safe", "value": {"mode": "safe"}},
            ]
        )
    )
    assert question.question_title == "Choose strategy"
    assert question.options[1].value == {"mode": "safe"}
    with pytest.raises(ValidationError):
        parse_operator_question(base_operator_question(question_markdown=""))
    with pytest.raises(ValidationError, match="unique"):
        parse_operator_question(
            base_operator_question(
                options=[
                    {"id": "same", "label": "One", "value": 1},
                    {"id": "same", "label": "Two", "value": 2},
                ]
            )
        )


def test_operator_answer_validation_open_single_and_multi() -> None:
    free_text = prepare_operator_question(
        OperatorQuestionRequest.model_validate(base_operator_question()),
        AppConfig(),
    )
    message, selected = validate_operator_answer(
        free_text,
        message="Use the simpler implementation.",
        selected_option_ids=[],
    )
    assert message == "Use the simpler implementation."
    assert selected == []
    with pytest.raises(ValueError, match="required"):
        validate_operator_answer(free_text, message="", selected_option_ids=[])

    single = prepare_operator_question(
        OperatorQuestionRequest.model_validate(
            base_operator_question(
                options=[
                    {"id": "a", "label": "A", "value": "a"},
                    {"id": "b", "label": "B", "value": "b"},
                ]
            )
        ),
        AppConfig(),
    )
    with pytest.raises(ValueError, match="exactly one"):
        validate_operator_answer(single, message="", selected_option_ids=[])
    _, selected = validate_operator_answer(single, message="note", selected_option_ids=["b"])
    assert [(option.id, option.value) for option in selected] == [("b", "b")]

    multi = prepare_operator_question(
        OperatorQuestionRequest.model_validate(
            base_operator_question(
                allow_multiple=True,
                options=[
                    {"id": "a", "label": "A", "value": "a"},
                    {"id": "b", "label": "B", "value": "b"},
                ],
            )
        ),
        AppConfig(),
    )
    with pytest.raises(ValueError, match="at least one"):
        validate_operator_answer(multi, message="", selected_option_ids=[])
    _, selected = validate_operator_answer(multi, message="", selected_option_ids=["a", "b"])
    assert [option.id for option in selected] == ["a", "b"]


def test_required_field_submission_behavior() -> None:
    fields = [
        HumanInputField(id="code", label="Code", type=FieldType.PASSWORD, required=True),
        HumanInputField(id="count", label="Count", type=FieldType.NUMBER, required=False),
        HumanInputField(
            id="choice",
            label="Choice",
            type=FieldType.SELECT,
            required=True,
            options=[
                HumanInputOption(label="A", value="a"),
                HumanInputOption(label="B", value="b"),
            ],
        ),
    ]
    with pytest.raises(ValueError, match="Code is required"):
        validate_submission(fields, {"code": "", "choice": "a"})
    with pytest.raises(ValueError, match="must be one"):
        validate_submission(fields, {"code": "secret", "choice": "c"})
    values = validate_submission(fields, {"code": "secret", "count": 3, "choice": "b"})
    assert values == {"code": "secret", "count": 3, "choice": "b"}


def test_password_field_renders_masked_html() -> None:
    field = HumanInputField(id="pw", label="Password", type=FieldType.PASSWORD)
    html = render_input_field_html(field)
    assert "type='password'" in html


def test_screenshot_validation_and_limits(
    tmp_path: Path,
    png_bytes: bytes,
    png_base64: str,
) -> None:
    config = AppConfig(server=ServerConfig(max_screenshot_bytes=1000))
    request = HumanActionRequest.model_validate(
        base_request(screenshot={"kind": "base64", "mime_type": "image/png", "data": png_base64})
    )
    prepared = prepare_request(request, config)
    assert prepared.screenshot is not None
    assert prepared.screenshot.mime_type == "image/png"
    assert prepared.screenshot.width == 1
    assert prepared.screenshot.kind == "base64"
    assert prepared.screenshot.data == png_bytes

    path = tmp_path / "shot.png"
    path.write_bytes(png_bytes)
    file_request = HumanActionRequest.model_validate(
        base_request(screenshot={"kind": "file_path", "path": str(path)})
    )
    prepared_file = prepare_request(file_request, config)
    assert prepared_file.screenshot is not None
    assert prepared_file.screenshot.data == png_bytes

    tiny_config = AppConfig(server=ServerConfig(max_screenshot_bytes=5))
    with pytest.raises(ValueError, match="exceeds"):
        prepare_request(request, tiny_config)


def test_screenshot_unreadable_or_missing_path_is_rejected() -> None:
    request = HumanActionRequest.model_validate(
        base_request(screenshot={"kind": "file_path", "path": "/definitely/missing.png"})
    )
    with pytest.raises(ValueError, match="does not exist"):
        prepare_request(request, AppConfig())


def test_base64_screenshot_data_never_written_to_disk(tmp_path: Path, png_base64: str) -> None:
    config = AppConfig()
    request = HumanActionRequest.model_validate(
        base_request(screenshot={"kind": "base64", "mime_type": "image/png", "data": png_base64})
    )
    prepare_request(request, config)
    assert list(tmp_path.rglob("*")) == []


def test_terminal_output_truncation() -> None:
    output, truncated = truncate_terminal_output("abcdef", 4)
    assert truncated is True
    assert "truncated" in output.lower()
    request = HumanActionRequest.model_validate(base_request(terminal_output="abcdef"))
    prepared = prepare_request(
        request,
        AppConfig(server=ServerConfig(max_terminal_output_chars=4)),
    )
    assert prepared.terminal_output_truncated is True


def test_markdown_display_safety() -> None:
    rendered = render_text_block(
        "Requested action", "Hello <script>alert(1)</script> [x](https://example.com)"
    )
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "[x](https://example.com)" in rendered


def test_timeout_result_status_is_not_separate() -> None:
    allowed = {status.value for status in ResultStatus}
    assert "timed_out" not in allowed
    assert "timed_out" not in {status.value for status in OperatorResultStatus}
