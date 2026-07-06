from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from human_intervention_mcp.browser_ui import (
    BrowserLaunchError,
    present_human_action,
    present_operator_question,
)
from human_intervention_mcp.config import AppConfig
from human_intervention_mcp.models import (
    HumanActionRequest,
    HumanActionResult,
    OperatorQuestionRequest,
    OperatorQuestionResult,
    PreparedOperatorQuestion,
    PreparedRequest,
    parse_operator_question,
    parse_request,
    prepare_operator_question,
    prepare_request,
)

ActionPresenter = Callable[[PreparedRequest, AppConfig], Awaitable[HumanActionResult]]
OperatorPresenter = Callable[
    [PreparedOperatorQuestion, AppConfig],
    Awaitable[OperatorQuestionResult],
]


class HumanInterventionCoordinator:
    def __init__(
        self,
        config: AppConfig,
        *,
        action_presenter: ActionPresenter | None = None,
        operator_presenter: OperatorPresenter | None = None,
    ) -> None:
        self._config = config
        self._action_presenter = action_presenter or present_human_action
        self._operator_presenter = operator_presenter or present_operator_question

    async def request_human_action(self, data: object) -> HumanActionResult:
        try:
            request = (
                data if isinstance(data, HumanActionRequest) else parse_request(data)  # type: ignore[arg-type]
            )
            prepared = prepare_request(request, self._config)
        except Exception as exc:
            return HumanActionResult.blocked(f"Invalid human intervention request: {exc}")
        try:
            return await asyncio.wait_for(
                self._action_presenter(prepared, self._config),
                timeout=self._config.server.response_timeout_seconds,
            )
        except TimeoutError:
            return HumanActionResult.blocked(
                "Human intervention request timed out.",
                timed_out=True,
            )
        except BrowserLaunchError as exc:
            return HumanActionResult.blocked(str(exc))

    async def ask_operator(self, data: object) -> OperatorQuestionResult:
        try:
            question = (
                data if isinstance(data, OperatorQuestionRequest) else parse_operator_question(data)  # type: ignore[arg-type]
            )
            prepared = prepare_operator_question(question, self._config)
        except Exception as exc:
            return OperatorQuestionResult.blocked(f"Invalid operator question: {exc}")
        try:
            return await asyncio.wait_for(
                self._operator_presenter(prepared, self._config),
                timeout=self._config.server.response_timeout_seconds,
            )
        except TimeoutError:
            return OperatorQuestionResult.blocked(
                "Operator question timed out.",
                timed_out=True,
            )
        except BrowserLaunchError as exc:
            return OperatorQuestionResult.blocked(str(exc))
