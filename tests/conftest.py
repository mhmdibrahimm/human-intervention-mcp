from __future__ import annotations

import base64

import pytest

from human_intervention_mcp.config import AppConfig


@pytest.fixture
def config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/6X"
        "s2p8AAAAASUVORK5CYII="
    )


@pytest.fixture
def png_base64(png_bytes: bytes) -> str:
    return base64.b64encode(png_bytes).decode("ascii")
