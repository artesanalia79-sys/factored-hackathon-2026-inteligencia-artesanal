"""Deterministic, zero-cost ``LLMProvider`` for tests, CI and offline demos.

Behavior per call, in order:
1. If a fault is scheduled for this call, raise it (``timeout``, ``malformed``, ``unavailable``).
2. If scripted responses remain, return the next one (validated against ``response_model``).
3. If ``response_model`` is ``InterpretationResult``, interpret the last user message with the
   keyword rules in ``bankagent.interpret.keywords``.
4. Otherwise raise ``LLMMalformedOutput``: the stub cannot invent other structures.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ValidationError

from bankagent.contracts.decisions import InterpretationResult
from bankagent.contracts.enums import FaultInjection
from bankagent.contracts.llm import (
    ChatMessage,
    LLMMalformedOutput,
    LLMTimeout,
    LLMUnavailable,
    StructuredCompletion,
    TokenUsage,
)
from bankagent.interpret.keywords import MODEL_NAME, interpret_text


class StubFault(StrEnum):
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    UNAVAILABLE = "unavailable"


FAULTS_BY_INJECTION: dict[FaultInjection, StubFault] = {
    FaultInjection.LLM_TIMEOUT: StubFault.TIMEOUT,
    FaultInjection.LLM_MALFORMED_OUTPUT: StubFault.MALFORMED,
    FaultInjection.LLM_UNAVAILABLE: StubFault.UNAVAILABLE,
}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class StubProvider:
    """Implements ``bankagent.contracts.llm.LLMProvider`` without network or cost."""

    def __init__(
        self,
        *,
        scripted: Iterable[BaseModel] = (),
        faults: Iterable[StubFault | None] = (),
        always_fault: StubFault | None = None,
    ) -> None:
        self._scripted: deque[BaseModel] = deque(scripted)
        self._faults: deque[StubFault | None] = deque(faults)
        self._always_fault = always_fault
        self.calls = 0

    @property
    def name(self) -> str:
        return "stub"

    @property
    def model(self) -> str:
        return MODEL_NAME

    def complete_structured[T: BaseModel](
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        timeout_s: float,
    ) -> StructuredCompletion[T]:
        self.calls += 1
        fault = self._faults.popleft() if self._faults else self._always_fault
        if fault is StubFault.TIMEOUT:
            raise LLMTimeout(f"stub timeout after {timeout_s}s")
        if fault is StubFault.UNAVAILABLE:
            raise LLMUnavailable("stub provider unavailable")
        if fault is StubFault.MALFORMED:
            raise LLMMalformedOutput("stub returned malformed output")

        user_text = next((m.content for m in reversed(messages) if m.role == "user"), "")
        output = self._produce(response_model, user_text)
        usage = TokenUsage(
            tokens_in=_estimate_tokens(system) + sum(_estimate_tokens(m.content) for m in messages),
            tokens_out=_estimate_tokens(output.model_dump_json()),
        )
        return StructuredCompletion[response_model](
            output=output, usage=usage, model=MODEL_NAME, latency_ms=0.0
        )

    def _produce[T: BaseModel](self, response_model: type[T], user_text: str) -> T:
        if self._scripted:
            scripted = self._scripted.popleft()
            try:
                return response_model.model_validate(scripted.model_dump())
            except ValidationError as exc:
                raise LLMMalformedOutput(
                    f"scripted {type(scripted).__name__} does not fit {response_model.__name__}"
                ) from exc
        if issubclass(response_model, InterpretationResult):
            return response_model.model_validate(interpret_text(user_text).model_dump())
        raise LLMMalformedOutput(f"stub cannot produce {response_model.__name__}")
