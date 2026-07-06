from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from human_intervention_mcp.browser_ui import browser_launcher_description
from human_intervention_mcp.config import AppConfig, ConfigError, load_config
from human_intervention_mcp.transports.stdio import run_stdio
from human_intervention_mcp.transports.streamable_http import run_streamable_http


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(
            config_path=Path(args.config).expanduser() if args.config else None,
            cli_values=_cli_values(args),
        )
    except ConfigError as exc:
        if getattr(args, "command", None) == "mcp":
            return 2
        parser.exit(2, f"configuration error: {exc}\n")
    command = args.command
    if command == "mcp":
        asyncio.run(run_stdio(config))
        return 0
    if command == "serve-http":
        asyncio.run(run_streamable_http(config))
        return 0
    if command == "doctor":
        return asyncio.run(run_doctor(config, mcp_timeout_sec=args.mcp_timeout_sec))
    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="human-intervention-mcp",
        description="Synchronous human intervention MCP server with per-request browser pages.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("mcp", "serve-http", "doctor"):
        subparser = subparsers.add_parser(name)
        add_common_options(subparser)
        if name == "serve-http":
            subparser.add_argument("--http-host")
            subparser.add_argument("--http-port", type=int)
            subparser.add_argument("--http-path")
        if name == "doctor":
            subparser.add_argument("--mcp-timeout-sec", type=int)
    return parser


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")
    parser.add_argument("--response-timeout-seconds", type=int)
    parser.add_argument("--max-input-fields", type=int)
    parser.add_argument("--max-terminal-output-chars", type=int)
    parser.add_argument("--max-screenshot-bytes", type=int)
    parser.add_argument("--max-image-width", type=int)
    parser.add_argument("--max-image-height", type=int)
    parser.add_argument("--browser-host")


async def run_doctor(config: AppConfig, *, mcp_timeout_sec: int | None = None) -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("config", True, "valid"))
    browser_launcher = browser_launcher_description()
    if browser_launcher is None:
        checks.append(("browser launcher", False, "could not locate runnable browser"))
    else:
        checks.append(("browser launcher", True, browser_launcher))
    if mcp_timeout_sec is None:
        checks.append(
            (
                "MCP timeout margin",
                True,
                "not checked; pass --mcp-timeout-sec to compare client timeout",
            )
        )
    else:
        checks.append(
            (
                "MCP timeout margin",
                mcp_timeout_sec > config.server.response_timeout_seconds,
                f"mcp={mcp_timeout_sec}s app={config.server.response_timeout_seconds}s",
            )
        )
    for name, ok, detail in checks:
        status = "ok" if ok else "fail"
        sys.stdout.write(f"{status:4} {name}: {detail}\n")
    return 0 if all(ok for _, ok, _ in checks) else 1


def _cli_values(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {
        "server": {
            "response_timeout_seconds": args.response_timeout_seconds,
            "max_input_fields": args.max_input_fields,
            "max_terminal_output_chars": args.max_terminal_output_chars,
            "max_screenshot_bytes": args.max_screenshot_bytes,
            "max_image_width": args.max_image_width,
            "max_image_height": args.max_image_height,
        },
        "browser": {
            "host": args.browser_host,
        },
        "http": {},
    }
    if hasattr(args, "http_host"):
        values["http"]["host"] = args.http_host
        values["http"]["port"] = args.http_port
        values["http"]["path"] = args.http_path
    return values
