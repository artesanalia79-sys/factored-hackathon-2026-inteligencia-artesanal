"""Provider-neutral LLM interface. The orchestrator only depends on ``LLMProvider``.

Implementations: ``bankagent.interpret.stub.StubProvider`` (Task 3, 0 USD, deterministic) and the
OpenAI provider (Task 10).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from bankagent.contracts.base import Contract


class ChatMessage(Contract):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=20_000)


class TokenUsage(Contract):
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0)


class StructuredCompletion[T: BaseModel](Contract):
    output: T
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str
    latency_ms: float = Field(ge=0.0)


class LLMError(Exception):
    """Base class. The orchestrator's fallback matrix handles each subclass."""


class LLMTimeout(LLMError):
    pass


class LLMMalformedOutput(LLMError):
    pass


class LLMUnavailable(LLMError):
    pass


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def complete_structured[T: BaseModel](
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        timeout_s: float,
    ) -> StructuredCompletion[T]: ...
