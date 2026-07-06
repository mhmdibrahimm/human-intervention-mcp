from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_RESPONSE_TIMEOUT_SECONDS = 900
DEFAULT_MAX_INPUT_FIELDS = 8
DEFAULT_MAX_TERMINAL_OUTPUT_CHARS = 16_000
DEFAULT_MAX_SCREENSHOT_BYTES = 8_000_000
DEFAULT_MAX_IMAGE_WIDTH = 3840
DEFAULT_MAX_IMAGE_HEIGHT = 3840
DEFAULT_BROWSER_HOST = "127.0.0.1"
ENV_PREFIX = "HUMAN_INTERVENTION_MCP_"


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


@dataclass(frozen=True, slots=True)
class ServerConfig:
    response_timeout_seconds: int = DEFAULT_RESPONSE_TIMEOUT_SECONDS
    max_input_fields: int = DEFAULT_MAX_INPUT_FIELDS
    max_terminal_output_chars: int = DEFAULT_MAX_TERMINAL_OUTPUT_CHARS
    max_screenshot_bytes: int = DEFAULT_MAX_SCREENSHOT_BYTES
    max_image_width: int = DEFAULT_MAX_IMAGE_WIDTH
    max_image_height: int = DEFAULT_MAX_IMAGE_HEIGHT


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    host: str = DEFAULT_BROWSER_HOST


@dataclass(frozen=True, slots=True)
class HttpConfig:
    host: str = DEFAULT_BROWSER_HOST
    port: int = 8000
    path: str = "/mcp"


@dataclass(frozen=True, slots=True)
class AppConfig:
    server: ServerConfig = ServerConfig()
    browser: BrowserConfig = BrowserConfig()
    http: HttpConfig = HttpConfig()


def default_config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "human-intervention-mcp" / "config.toml"


def load_config(
    *,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    cli_values: Mapping[str, Any] | None = None,
) -> AppConfig:
    """Load configuration with CLI > env > TOML > defaults precedence."""

    config = AppConfig()
    selected_path = config_path if config_path is not None else default_config_path()
    if selected_path.exists():
        config = merge_mapping(config, _read_toml(selected_path))
    env_values = _env_mapping(os.environ if env is None else env)
    config = merge_mapping(config, env_values)
    if cli_values:
        config = merge_mapping(config, _prune_none(cli_values))
    validate_config(config)
    return config


def validate_config(config: AppConfig) -> None:
    if config.server.response_timeout_seconds <= 0:
        raise ConfigError("server.response_timeout_seconds must be greater than zero")
    if config.server.max_input_fields < 0:
        raise ConfigError("server.max_input_fields must not be negative")
    if config.server.max_terminal_output_chars < 0:
        raise ConfigError("server.max_terminal_output_chars must not be negative")
    if config.server.max_screenshot_bytes <= 0:
        raise ConfigError("server.max_screenshot_bytes must be greater than zero")
    if config.server.max_image_width <= 0 or config.server.max_image_height <= 0:
        raise ConfigError("server image dimensions must be greater than zero")
    if config.browser.host != DEFAULT_BROWSER_HOST:
        raise ConfigError("browser.host must be 127.0.0.1 in v1")
    if config.http.port <= 0 or config.http.port > 65_535:
        raise ConfigError("http.port must be between 1 and 65535")
    if not config.http.path.startswith("/"):
        raise ConfigError("http.path must start with /")


def merge_mapping(config: AppConfig, data: Mapping[str, Any]) -> AppConfig:
    server_data = _section(data, "server")
    browser_data = _section(data, "browser")
    http_data = _section(data, "http")
    return replace(
        config,
        server=replace(config.server, **_coerce_section(ServerConfig, server_data)),
        browser=replace(config.browser, **_coerce_section(BrowserConfig, browser_data)),
        http=replace(config.http, **_coerce_section(HttpConfig, http_data)),
    )


def _read_toml(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("rb") as handle:
            parsed = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML config: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError("config file must contain a TOML table")
    return parsed


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    raw = data.get(name, {})
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{name} config section must be a table")
    return raw


def _coerce_section(cls: type[Any], data: Mapping[str, Any]) -> dict[str, Any]:
    valid_names = cls.__dataclass_fields__.keys()
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key not in valid_names:
            raise ConfigError(f"unknown configuration key: {key}")
        result[key] = _coerce_value(key, value)
    return result


def _coerce_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
        int_keys = {
            "response_timeout_seconds",
            "max_input_fields",
            "max_terminal_output_chars",
            "max_screenshot_bytes",
            "max_image_width",
            "max_image_height",
            "port",
        }
        if key in int_keys:
            try:
                return int(value)
            except ValueError as exc:
                raise ConfigError(f"{key} must be an integer") from exc
    return value


def _env_mapping(env: Mapping[str, str]) -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {"server": {}, "browser": {}, "http": {}}
    env_to_key = {
        f"{ENV_PREFIX}RESPONSE_TIMEOUT_SECONDS": ("server", "response_timeout_seconds"),
        f"{ENV_PREFIX}MAX_INPUT_FIELDS": ("server", "max_input_fields"),
        f"{ENV_PREFIX}MAX_TERMINAL_OUTPUT_CHARS": ("server", "max_terminal_output_chars"),
        f"{ENV_PREFIX}MAX_SCREENSHOT_BYTES": ("server", "max_screenshot_bytes"),
        f"{ENV_PREFIX}MAX_IMAGE_WIDTH": ("server", "max_image_width"),
        f"{ENV_PREFIX}MAX_IMAGE_HEIGHT": ("server", "max_image_height"),
        f"{ENV_PREFIX}BROWSER_HOST": ("browser", "host"),
        f"{ENV_PREFIX}HTTP_HOST": ("http", "host"),
        f"{ENV_PREFIX}HTTP_PORT": ("http", "port"),
        f"{ENV_PREFIX}HTTP_PATH": ("http", "path"),
    }
    for env_name, (section, key) in env_to_key.items():
        if env_name in env:
            mapping[section][key] = env[env_name]
    return mapping


def _prune_none(data: Mapping[str, Any]) -> dict[str, Any]:
    pruned: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, Mapping):
            nested = _prune_none(value)
            if nested:
                pruned[key] = nested
        elif value is not None:
            pruned[key] = value
    return pruned
