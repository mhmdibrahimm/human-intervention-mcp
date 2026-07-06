from __future__ import annotations

from pathlib import Path

from human_intervention_mcp.config import load_config


def test_configuration_precedence_cli_over_environment_over_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[server]
response_timeout_seconds = 100
max_input_fields = 2

[browser]
host = "127.0.0.1"
""",
        encoding="utf-8",
    )
    config = load_config(
        config_path=config_path,
        env={
            "HUMAN_INTERVENTION_MCP_RESPONSE_TIMEOUT_SECONDS": "200",
            "HUMAN_INTERVENTION_MCP_BROWSER_HOST": "127.0.0.1",
        },
        cli_values={"server": {"response_timeout_seconds": 300}},
    )
    assert config.server.response_timeout_seconds == 300
    assert config.server.max_input_fields == 2
    assert config.browser.host == "127.0.0.1"
