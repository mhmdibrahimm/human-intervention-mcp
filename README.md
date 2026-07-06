# Human Intervention MCP

Human Intervention / "human-in-the-loop" MCP is an open-source Python MCP server that lets AI agents pause and ask a human for either:

- `request_human_action`: a manual step, confirmation, or structured input
- `ask_operator`: a consultative question or branching decision

Each tool call opens a local URL in the default browser. One browser tab is used for one operation. There is no queue, no polling API, and no background resume flow.

## How It Works

1. An agent calls `request_human_action` or `ask_operator`.
2. The MCP server validates the payload and starts a one-shot local HTTP page on
   `127.0.0.1` with a random port.
3. The server opens that URL in the default browser.
4. The human answers in that tab.
5. The original MCP tool call stays open until the form is submitted or the
   timeout expires.
6. The page is torn down immediately after the result is returned.

## Why?
In practice, AI agents are often optimized to keep moving.

When running long autonomous workflows, such as Codex `/goal` tasks, an agent may encounter an unclear decision, an unexpected state, or a step that requires human judgment. Even when instructed to “stop and report immediately if X happens,” many agents will continue trying alternatives first, and burning a lot of tokens, especially with the frontier models. They may spend time exploring workarounds, make assumptions, or choose what appears to be the best path without checking whether that path matches the operator’s intent.

For many use cases, that behavior is useful. The goal is autonomy, speed, and reduced interruption.

But that is not always the right trade-off.

Sometimes it is essential for a human to remain involved at key decision points. 

This MCP makes that workflow practical.

## Instructing Your Agent
Your AI agent must be explicitly told that this MCP is available and when it should use it. Otherwise, most agents will try to solve ambiguous situations on their own, make assumptions, or keep attempting alternatives before asking for help.

Add instructions like these to your agent prompt or in your markdown files.:

* If you encounter an important decision that I did not explicitly instruct you how to handle, call `ask_operator` before choosing a direction.
* If multiple valid approaches exist and the choice could affect cost, safety, data, time, architecture, or user experience, use `ask_operator`.
* If you need me to perform a manual action, use `request_human_action`.
* Use `request_human_action` for tasks such as logging in, entering a one-time code, approving a browser prompt, completing a CAPTCHA, clicking an interface element, connecting an account, or reviewing a visual result.

## Supported Systems

Python 3.11+ on macOS, Linux, and Windows.

The browser flow assumes the machine running the MCP server can open a local browser tab.

## Installation

For local development:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

With `uv`:

```bash
uv sync --extra dev
uv run human-intervention-mcp --help
```

With `pipx`:

```bash
pipx install .
```

## Commands

```bash
human-intervention-mcp mcp
human-intervention-mcp serve-http
human-intervention-mcp doctor
```

## Codex Configuration

```toml
[mcp_servers.human_intervention]
command = "human-intervention-mcp"
args = ["mcp"]
tool_timeout_sec = 960
```

`tool_timeout_sec` must be greater than `response_timeout_seconds`.

## Claude Code Configuration

```json
{
  "mcpServers": {
    "human_intervention": {
      "command": "human-intervention-mcp",
      "args": ["mcp"]
    }
  }
}
```

Set the client-side timeout above `response_timeout_seconds` if your MCP host supports that setting.

## Optional Streamable HTTP

```bash
human-intervention-mcp serve-http --http-host 127.0.0.1 --http-port 8000 --http-path /mcp
```

The Streamable HTTP transport uses the same local browser-page flow as STDIO. The browser page opens on the machine where the MCP server process is actually running.

## Configuration

Precedence:

1. CLI options
2. environment variables
3. TOML config file
4. built-in defaults

Default config path:

- Windows: `%APPDATA%\human-intervention-mcp\config.toml`
- macOS/Linux: `$XDG_CONFIG_HOME/human-intervention-mcp/config.toml` or
  `~/.config/human-intervention-mcp/config.toml`

Example:

```toml
[server]
response_timeout_seconds = 900
max_input_fields = 8
max_terminal_output_chars = 16000
max_screenshot_bytes = 8000000
max_image_width = 3840
max_image_height = 3840

[browser]
host = "127.0.0.1"

[http]
host = "127.0.0.1"
port = 8000
path = "/mcp"
```

Environment variables:

```text
HUMAN_INTERVENTION_MCP_RESPONSE_TIMEOUT_SECONDS
HUMAN_INTERVENTION_MCP_MAX_INPUT_FIELDS
HUMAN_INTERVENTION_MCP_MAX_TERMINAL_OUTPUT_CHARS
HUMAN_INTERVENTION_MCP_MAX_SCREENSHOT_BYTES
HUMAN_INTERVENTION_MCP_MAX_IMAGE_WIDTH
HUMAN_INTERVENTION_MCP_MAX_IMAGE_HEIGHT
HUMAN_INTERVENTION_MCP_BROWSER_HOST
HUMAN_INTERVENTION_MCP_HTTP_HOST
HUMAN_INTERVENTION_MCP_HTTP_PORT
HUMAN_INTERVENTION_MCP_HTTP_PATH
```

CLI overrides:

```bash
human-intervention-mcp mcp --response-timeout-seconds 900 --browser-host 127.0.0.1
```

## Doctor

```bash
human-intervention-mcp doctor --mcp-timeout-sec 960
```

Checks:

- config validity
- browser launcher availability
- timeout margin

## Tool Semantics

Use `request_human_action` when the operator needs to do something outside the agent, such as approving a browser step, entering a code, or performing a manual check.

Use `ask_operator` when the agent has multiple plausible decisions, was told to ask before deciding, or wants operator guidance without inventing a manual action.

## Example `request_human_action` Input

```json
{
  "task_title": "Continue checkout",
  "requested_action_markdown": "Please complete the payment confirmation in the browser.",
  "reason_markdown": "The site requires a human confirmation before the agent can continue.",
  "risk_level": "medium",
  "agent_name": "Codex",
  "working_directory": "/project",
  "terminal_output": "Last command output...",
  "screenshot": {
    "kind": "file_path",
    "path": "checkout.png"
  },
  "input_fields": [
    {
      "id": "confirmation_code",
      "label": "Confirmation code",
      "type": "password",
      "required": true,
      "placeholder": "Enter code",
      "default": null,
      "options": null
    }
  ]
}
```

## Example `request_human_action` Result

```json
{
  "status": "completed",
  "message": "Confirmed in browser.",
  "input_values": {
    "confirmation_code": "123456"
  },
  "metadata": {
    "timed_out": false
  }
}
```

## Example `ask_operator` Input

```json
{
  "question_title": "Choose implementation path",
  "question_markdown": "Which implementation path should I use?",
  "reason_markdown": "Both options are viable and I was instructed to ask before deciding.",
  "agent_name": "Codex",
  "working_directory": "/project",
  "options": [
    {
      "id": "safe",
      "label": "Safer path",
      "value": "safe",
      "description_markdown": "Lower risk and easier to verify."
    },
    {
      "id": "fast",
      "label": "Faster path",
      "value": "fast",
      "description_markdown": "Less work but higher regression risk."
    }
  ],
  "allow_multiple": false
}
```

## Example `ask_operator` Result

```json
{
  "status": "answered",
  "message": "Use the safer path.",
  "selected_options": [
    {
      "id": "safe",
      "label": "Safer path",
      "value": "safe"
    }
  ],
  "metadata": {
    "timed_out": false
  }
}
```