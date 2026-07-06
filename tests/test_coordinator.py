from __future__ import annotations

import asyncio

import pytest

from human_intervention_mcp.browser_ui import BrowserLaunchError
from human_intervention_mcp.config import AppConfig, ServerConfig
from human_intervention_mcp.coordinator import HumanInterventionCoordinator
from human_intervention_mcp.models import (
    HumanActionResult,
    OperatorQuestionResult,
    OperatorResultStatus,
    PreparedOperatorQuestion,
    PreparedRequest,
    ResultStatus,
)


def request_payload() -> dict[str, object]:
    return {
        "task_title": "Manual step",
        "requested_action_markdown": "Do the step.",
        "reason_markdown": "Human needed.",
        "risk_level": "low",
    }


def operator_payload() -> dict[str, object]:
    return {
        "question_title": "Choose path",
        "question_markdown": "Which path should I take?",
        "options": [
            {"id": "fast", "label": "Fast", "value": "fast"},
            {"id": "safe", "label": "Safe", "value": "safe"},
        ],
    }


class SleepingPresenters:
    def __init__(self) -> None:
        self.action_cancelled = False
        self.operator_cancelled = False

    async def present_action(
        self,
        request: PreparedRequest,
        config: AppConfig,
    ) -> HumanActionResult:
        del request, config
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.action_cancelled = True
            raise
        raise AssertionError("unreachable")

    async def present_operator(
        self,
        question: PreparedOperatorQuestion,
        config: AppConfig,
    ) -> OperatorQuestionResult:
        del question, config
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.operator_cancelled = True
            raise
        raise AssertionError("unreachable")


class FuturePresenters:
    def __init__(self) -> None:
        self.action_future: asyncio.Future[HumanActionResult] = asyncio.Future()
        self.operator_future: asyncio.Future[OperatorQuestionResult] = asyncio.Future()

    async def present_action(
        self,
        request: PreparedRequest,
        config: AppConfig,
    ) -> HumanActionResult:
        del request, config
        return await self.action_future

    async def present_operator(
        self,
        question: PreparedOperatorQuestion,
        config: AppConfig,
    ) -> OperatorQuestionResult:
        del question, config
        return await self.operator_future


@pytest.mark.asyncio
async def test_timeout_maps_to_blocked_with_metadata() -> None:
    presenters = SleepingPresenters()
    coordinator = HumanInterventionCoordinator(
        AppConfig(server=ServerConfig(response_timeout_seconds=0.01)),
        action_presenter=presenters.present_action,
        operator_presenter=presenters.present_operator,
    )
    result = await coordinator.request_human_action(request_payload())
    assert result.status is ResultStatus.BLOCKED
    assert result.metadata["timed_out"] is True
    assert result.message == "Human intervention request timed out."
    assert presenters.action_cancelled is True


@pytest.mark.asyncio
async def test_single_tool_call_blocks_until_human_response_arrives() -> None:
    presenters = FuturePresenters()
    coordinator = HumanInterventionCoordinator(
        AppConfig(),
        action_presenter=presenters.present_action,
        operator_presenter=presenters.present_operator,
    )
    task = asyncio.create_task(coordinator.request_human_action(request_payload()))
    await asyncio.sleep(0)
    assert not task.done()
    presenters.action_future.set_result(
        HumanActionResult(status=ResultStatus.COMPLETED, message="done")
    )
    result = await task
    assert result.status is ResultStatus.COMPLETED
    assert result.message == "done"


@pytest.mark.asyncio
async def test_browser_launch_failure_returns_blocked() -> None:
    async def failing_action(
        request: PreparedRequest,
        config: AppConfig,
    ) -> HumanActionResult:
        del request, config
        raise BrowserLaunchError("browser open failed")

    async def unused_operator(
        question: PreparedOperatorQuestion,
        config: AppConfig,
    ) -> OperatorQuestionResult:
        del question, config
        raise AssertionError("unreachable")

    coordinator = HumanInterventionCoordinator(
        AppConfig(),
        action_presenter=failing_action,
        operator_presenter=unused_operator,
    )
    result = await coordinator.request_human_action(request_payload())
    assert result.status is ResultStatus.BLOCKED
    assert result.message == "browser open failed"


@pytest.mark.asyncio
async def test_operator_call_blocks_until_answer_arrives() -> None:
    presenters = FuturePresenters()
    coordinator = HumanInterventionCoordinator(
        AppConfig(),
        action_presenter=presenters.present_action,
        operator_presenter=presenters.present_operator,
    )
    task = asyncio.create_task(coordinator.ask_operator(operator_payload()))
    await asyncio.sleep(0)
    assert not task.done()
    presenters.operator_future.set_result(
        OperatorQuestionResult(
            status=OperatorResultStatus.ANSWERED,
            message="Use safe",
            selected_options=[{"id": "safe", "label": "Safe", "value": "safe"}],
        )
    )
    result = await task
    assert result.status is OperatorResultStatus.ANSWERED
    assert result.selected_options[0].id == "safe"


@pytest.mark.asyncio
async def test_operator_timeout_maps_to_blocked() -> None:
    presenters = SleepingPresenters()
    coordinator = HumanInterventionCoordinator(
        AppConfig(server=ServerConfig(response_timeout_seconds=0.01)),
        action_presenter=presenters.present_action,
        operator_presenter=presenters.present_operator,
    )
    result = await coordinator.ask_operator(operator_payload())
    assert result.status is OperatorResultStatus.BLOCKED
    assert result.message == "Operator question timed out."
    assert result.metadata["timed_out"] is True
    assert presenters.operator_cancelled is True


@pytest.mark.asyncio
async def test_operator_browser_launch_failure_returns_blocked() -> None:
    async def unused_action(
        request: PreparedRequest,
        config: AppConfig,
    ) -> HumanActionResult:
        del request, config
        raise AssertionError("unreachable")

    async def failing_operator(
        question: PreparedOperatorQuestion,
        config: AppConfig,
    ) -> OperatorQuestionResult:
        del question, config
        raise BrowserLaunchError("browser open failed")

    coordinator = HumanInterventionCoordinator(
        AppConfig(),
        action_presenter=unused_action,
        operator_presenter=failing_operator,
    )
    result = await coordinator.ask_operator(operator_payload())
    assert result.status is OperatorResultStatus.BLOCKED
    assert result.message == "browser open failed"
