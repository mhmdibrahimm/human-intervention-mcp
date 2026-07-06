from __future__ import annotations

import base64
import binascii
import json
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from human_intervention_mcp.config import AppConfig

JsonValue = Any


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FieldType(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    BOOLEAN = "boolean"
    SELECT = "select"
    PASSWORD = "password"


class ResultStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    DECLINED = "declined"
    NEED_MORE_CONTEXT = "need_more_context"
    ABORT_TASK = "abort_task"


class OperatorResultStatus(StrEnum):
    ANSWERED = "answered"
    BLOCKED = "blocked"
    DECLINED = "declined"


class HumanInputOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: JsonValue

    @field_validator("label")
    @classmethod
    def label_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("option label must not be empty")
        return value

    @field_validator("value")
    @classmethod
    def value_json_compatible(cls, value: JsonValue) -> JsonValue:
        return ensure_json_compatible(value)


class HumanInputField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    type: FieldType
    required: bool = False
    placeholder: str | None = None
    default: JsonValue = None
    options: list[HumanInputOption] | None = None

    @field_validator("id")
    @classmethod
    def id_is_valid(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field id must not be empty")
        if len(stripped) > 128:
            raise ValueError("field id is too long")
        return stripped

    @field_validator("label")
    @classmethod
    def label_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field label must not be empty")
        return value

    @model_validator(mode="after")
    def validate_select_options(self) -> HumanInputField:
        if self.type is FieldType.SELECT and not self.options:
            raise ValueError("select fields require options")
        if self.type is not FieldType.SELECT and self.options is not None:
            raise ValueError("options are only supported for select fields")
        return self

    @field_validator("default")
    @classmethod
    def default_json_compatible(cls, value: JsonValue) -> JsonValue:
        return ensure_json_compatible(value)


class FilePathScreenshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["file_path"]
    path: str

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("screenshot path must not be empty")
        return value


class Base64Screenshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["base64"]
    mime_type: str
    data: str

    @field_validator("mime_type")
    @classmethod
    def supported_mime_type(cls, value: str) -> str:
        if value not in SUPPORTED_IMAGE_MIME_TYPES:
            raise ValueError("unsupported screenshot MIME type")
        return value

    @field_validator("data")
    @classmethod
    def data_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("base64 screenshot data must not be empty")
        return value


ScreenshotPayload = Annotated[
    FilePathScreenshot | Base64Screenshot,
    Field(discriminator="kind"),
]


class HumanActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_title: str
    requested_action_markdown: str
    reason_markdown: str
    risk_level: RiskLevel
    agent_name: str | None = None
    working_directory: str | None = None
    terminal_output: str | None = None
    screenshot: ScreenshotPayload | None = None
    input_fields: list[HumanInputField] = Field(default_factory=list)

    @field_validator("task_title", "requested_action_markdown", "reason_markdown")
    @classmethod
    def required_text_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required text field must not be empty")
        return value

    @model_validator(mode="after")
    def validate_unique_input_fields(self) -> HumanActionRequest:
        ids = [field.id for field in self.input_fields]
        if len(set(ids)) != len(ids):
            raise ValueError("input field ids must be unique")
        return self


class OperatorQuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    value: JsonValue
    description_markdown: str | None = None

    @field_validator("id")
    @classmethod
    def id_is_valid(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("option id must not be empty")
        if len(stripped) > 128:
            raise ValueError("option id is too long")
        return stripped

    @field_validator("label")
    @classmethod
    def label_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("option label must not be empty")
        return value

    @field_validator("value")
    @classmethod
    def value_json_compatible(cls, value: JsonValue) -> JsonValue:
        return ensure_json_compatible(value)


class OperatorQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_title: str
    question_markdown: str
    reason_markdown: str | None = None
    agent_name: str | None = None
    working_directory: str | None = None
    terminal_output: str | None = None
    screenshot: ScreenshotPayload | None = None
    options: list[OperatorQuestionOption] = Field(default_factory=list)
    allow_multiple: bool = False

    @field_validator("question_title", "question_markdown")
    @classmethod
    def required_text_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required text field must not be empty")
        return value

    @model_validator(mode="after")
    def validate_unique_options(self) -> OperatorQuestionRequest:
        ids = [option.id for option in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("operator option ids must be unique")
        return self


class HumanActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ResultStatus
    message: str = ""
    input_values: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=lambda: {"timed_out": False})

    @field_validator("input_values", "metadata")
    @classmethod
    def mapping_json_compatible(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return cast("dict[str, JsonValue]", ensure_json_compatible(value))

    @classmethod
    def blocked(cls, message: str, *, timed_out: bool = False) -> HumanActionResult:
        return cls(
            status=ResultStatus.BLOCKED,
            message=message,
            input_values={},
            metadata={"timed_out": timed_out},
        )


class OperatorSelectedOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    value: JsonValue

    @field_validator("value")
    @classmethod
    def value_json_compatible(cls, value: JsonValue) -> JsonValue:
        return ensure_json_compatible(value)


class OperatorQuestionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OperatorResultStatus
    message: str = ""
    selected_options: list[OperatorSelectedOption] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=lambda: {"timed_out": False})

    @field_validator("metadata")
    @classmethod
    def mapping_json_compatible(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return cast("dict[str, JsonValue]", ensure_json_compatible(value))

    @classmethod
    def blocked(cls, message: str, *, timed_out: bool = False) -> OperatorQuestionResult:
        return cls(
            status=OperatorResultStatus.BLOCKED,
            message=message,
            selected_options=[],
            metadata={"timed_out": timed_out},
        )


@dataclass(frozen=True, slots=True)
class ScreenshotInfo:
    kind: Literal["file_path", "base64"]
    mime_type: str
    bytes_size: int
    width: int | None
    height: int | None
    data: bytes = dataclass_field(repr=False)
    path: str | None = None


class PreparedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["action"] = "action"
    task_title: str
    requested_action_markdown: str
    reason_markdown: str
    risk_level: RiskLevel
    agent_name: str
    working_directory: str | None = None
    terminal_output: str | None = None
    terminal_output_truncated: bool = False
    screenshot: ScreenshotInfo | None = None
    input_fields: list[HumanInputField] = Field(default_factory=list)


class PreparedOperatorQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ask_operator"] = "ask_operator"
    question_title: str
    question_markdown: str
    reason_markdown: str | None = None
    agent_name: str
    working_directory: str | None = None
    terminal_output: str | None = None
    terminal_output_truncated: bool = False
    screenshot: ScreenshotInfo | None = None
    options: list[OperatorQuestionOption] = Field(default_factory=list)
    allow_multiple: bool = False


SUPPORTED_IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
}

REQUEST_ADAPTER: TypeAdapter[HumanActionRequest] = TypeAdapter(HumanActionRequest)
OPERATOR_QUESTION_ADAPTER: TypeAdapter[OperatorQuestionRequest] = TypeAdapter(
    OperatorQuestionRequest
)


def parse_request(data: MappingLike) -> HumanActionRequest:
    return REQUEST_ADAPTER.validate_python(data)


def parse_operator_question(data: MappingLike) -> OperatorQuestionRequest:
    return OPERATOR_QUESTION_ADAPTER.validate_python(data)


def prepare_request(request: HumanActionRequest, config: AppConfig) -> PreparedRequest:
    if len(request.input_fields) > config.server.max_input_fields:
        raise ValueError(f"too many input fields; maximum is {config.server.max_input_fields}")
    terminal_output, truncated = truncate_terminal_output(
        request.terminal_output,
        config.server.max_terminal_output_chars,
    )
    screenshot_info = validate_screenshot(request.screenshot, config, request.working_directory)
    return PreparedRequest(
        task_title=request.task_title,
        requested_action_markdown=request.requested_action_markdown,
        reason_markdown=request.reason_markdown,
        risk_level=request.risk_level,
        agent_name=(request.agent_name.strip() if request.agent_name else "") or "Unknown agent",
        working_directory=request.working_directory,
        terminal_output=terminal_output,
        terminal_output_truncated=truncated,
        screenshot=screenshot_info,
        input_fields=request.input_fields,
    )


def prepare_operator_question(
    request: OperatorQuestionRequest,
    config: AppConfig,
) -> PreparedOperatorQuestion:
    terminal_output, truncated = truncate_terminal_output(
        request.terminal_output,
        config.server.max_terminal_output_chars,
    )
    screenshot_info = validate_screenshot(request.screenshot, config, request.working_directory)
    return PreparedOperatorQuestion(
        question_title=request.question_title,
        question_markdown=request.question_markdown,
        reason_markdown=request.reason_markdown,
        agent_name=(request.agent_name.strip() if request.agent_name else "") or "Unknown agent",
        working_directory=request.working_directory,
        terminal_output=terminal_output,
        terminal_output_truncated=truncated,
        screenshot=screenshot_info,
        options=request.options,
        allow_multiple=request.allow_multiple,
    )


def validate_submission(
    input_fields: list[HumanInputField],
    values: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    field_by_id = {field.id: field for field in input_fields}
    unknown = set(values) - set(field_by_id)
    if unknown:
        raise ValueError(f"unknown input field ids: {', '.join(sorted(unknown))}")
    normalized: dict[str, JsonValue] = {}
    for field in input_fields:
        raw = values.get(field.id, field.default)
        if field.required and _is_empty_value(raw):
            raise ValueError(f"{field.label} is required")
        if field.type is FieldType.NUMBER and raw is not None and not isinstance(raw, int | float):
            raise ValueError(f"{field.label} must be a number")
        if field.type is FieldType.BOOLEAN and raw is not None and not isinstance(raw, bool):
            raise ValueError(f"{field.label} must be a boolean")
        if field.type is FieldType.SELECT and field.options:
            allowed = [option.value for option in field.options]
            if raw is not None and raw not in allowed:
                raise ValueError(f"{field.label} must be one of the configured options")
        normalized[field.id] = raw
    return normalized


def validate_operator_answer(
    question: PreparedOperatorQuestion,
    *,
    message: str,
    selected_option_ids: list[str],
) -> tuple[str, list[OperatorSelectedOption]]:
    option_by_id = {option.id: option for option in question.options}
    unknown = set(selected_option_ids) - set(option_by_id)
    if unknown:
        raise ValueError(f"unknown option ids: {', '.join(sorted(unknown))}")
    if not question.options:
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("An answer is required")
        return normalized_message, []
    if question.allow_multiple:
        if not selected_option_ids:
            raise ValueError("Select at least one option")
    elif len(selected_option_ids) != 1:
        raise ValueError("Select exactly one option")
    selected = [
        OperatorSelectedOption(
            id=option_by_id[option_id].id,
            label=option_by_id[option_id].label,
            value=option_by_id[option_id].value,
        )
        for option_id in selected_option_ids
    ]
    return message, selected


def truncate_terminal_output(value: str | None, max_chars: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if max_chars == 0:
        return "", bool(value)
    if len(value) <= max_chars:
        return value, False
    suffix = "\n\n[Terminal output truncated by human-intervention-mcp]"
    keep = max(0, max_chars - len(suffix))
    return value[:keep] + suffix, True


def validate_screenshot(
    screenshot: ScreenshotPayload | None,
    config: AppConfig,
    working_directory: str | None = None,
) -> ScreenshotInfo | None:
    if screenshot is None:
        return None
    if isinstance(screenshot, FilePathScreenshot):
        path = Path(screenshot.path).expanduser()
        if not path.is_absolute() and working_directory:
            path = Path(working_directory).expanduser() / path
        if not path.exists() or not path.is_file():
            raise ValueError("screenshot path does not exist or is not a file")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ValueError("screenshot path is not readable") from exc
        if size > config.server.max_screenshot_bytes:
            raise ValueError("screenshot file exceeds configured size limit")
        try:
            header = path.read_bytes()
        except OSError as exc:
            raise ValueError("screenshot path is not readable") from exc
        return _info_from_bytes(
            data=header,
            kind="file_path",
            path=str(path),
            config=config,
            expected_mime_type=None,
        )
    assert isinstance(screenshot, Base64Screenshot)
    try:
        decoded = base64.b64decode(screenshot.data, validate=True)
    except binascii.Error as exc:
        raise ValueError("screenshot data is not valid base64") from exc
    if len(decoded) > config.server.max_screenshot_bytes:
        raise ValueError("decoded screenshot exceeds configured size limit")
    return _info_from_bytes(
        data=decoded,
        kind="base64",
        path=None,
        config=config,
        expected_mime_type=screenshot.mime_type,
    )


def _info_from_bytes(
    *,
    data: bytes,
    kind: Literal["file_path", "base64"],
    path: str | None,
    config: AppConfig,
    expected_mime_type: str | None,
) -> ScreenshotInfo:
    if len(data) > config.server.max_screenshot_bytes:
        raise ValueError("screenshot exceeds configured size limit")
    mime_type = detect_image_mime_type(data)
    if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
        raise ValueError("unsupported screenshot image format")
    if expected_mime_type is not None and expected_mime_type != mime_type:
        raise ValueError("screenshot MIME type does not match image data")
    width, height = image_dimensions(data, mime_type)
    if width is not None and width > config.server.max_image_width:
        raise ValueError("screenshot width exceeds configured limit")
    if height is not None and height > config.server.max_image_height:
        raise ValueError("screenshot height exceeds configured limit")
    return ScreenshotInfo(
        kind=kind,
        mime_type=mime_type,
        bytes_size=len(data),
        width=width,
        height=height,
        data=data,
        path=path,
    )


def detect_image_mime_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("unsupported screenshot image format")


def image_dimensions(data: bytes, mime_type: str) -> tuple[int | None, int | None]:
    if mime_type == "image/png":
        if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid PNG screenshot")
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if mime_type == "image/jpeg":
        return _jpeg_dimensions(data)
    if mime_type == "image/webp":
        return _webp_dimensions(data)
    return None, None


def _jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        raise ValueError("invalid JPEG screenshot")
    index = 2
    while index < len(data):
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9, 0x01}:
            continue
        if index + 2 > len(data):
            break
        segment_length = struct.unpack(">H", data[index : index + 2])[0]
        if segment_length < 2:
            raise ValueError("invalid JPEG segment")
        segment_start = index + 2
        segment_end = index + segment_length
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if segment_start + 5 > len(data):
                raise ValueError("invalid JPEG SOF segment")
            height, width = struct.unpack(">HH", data[segment_start + 1 : segment_start + 5])
            return int(width), int(height)
        index = segment_end
    return None, None


def _webp_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 30 or not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        raise ValueError("invalid WebP screenshot")
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    if chunk == b"VP8 " and len(data) >= 30:
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return int(width), int(height)
    return None, None


def _is_empty_value(value: JsonValue) -> bool:
    return value is None or value == "" or value == []


MappingLike = Mapping[str, Any]


def ensure_json_compatible(value: JsonValue) -> JsonValue:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be JSON-compatible") from exc
    return value
