"""Offline synthetic transport fixtures, budget and privacy checks for the real provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock

import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
)
from pydantic import BaseModel

from bankagent.contracts.decisions import InterpretationResult
from bankagent.contracts.llm import (
    ChatMessage,
    LLMMalformedOutput,
    LLMProvider,
    LLMTimeout,
    LLMUnavailable,
)
from bankagent.interpret.openai_provider import (
    OpenAIProvider,
    SpendLimitExceeded,
    _ParsedInterpretation,
)
from bankagent.interpret.records import interpretation_record

FIXTURES = json.loads(
    (Path(__file__).parent / "synthetic_interpret.json").read_text(encoding="utf-8")
)


def _response(output: object = None, *, usage: object = True) -> SimpleNamespace:
    parsed = _ParsedInterpretation.model_validate(output or FIXTURES[0]["output"])
    metering = SimpleNamespace(input_tokens=1000, output_tokens=100) if usage is True else usage
    return SimpleNamespace(output_parsed=parsed, usage=metering)


def _complete(provider: OpenAIProvider) -> object:
    return provider.complete_structured(
        system="interpret",
        messages=[ChatMessage(role="user", content="cobro duplicado")],
        response_model=InterpretationResult,
        timeout_s=5,
    )


@pytest.mark.parametrize("fixture", FIXTURES)
def test_offline_structured_fixtures(fixture: dict[str, object]) -> None:
    parsed = _ParsedInterpretation.model_validate(fixture["output"])
    output = InterpretationResult.model_validate(
        {**parsed.model_dump(), "model": "gpt-6-luna", "prompt_version": "interpret-v1"}
    )
    response = SimpleNamespace(
        output_parsed=parsed,
        usage=SimpleNamespace(
            input_tokens=fixture["tokens_in"], output_tokens=fixture["tokens_out"]
        ),
    )
    client = Mock()
    client.responses.parse.return_value = response
    provider: LLMProvider = OpenAIProvider(client=client)
    completion = provider.complete_structured(
        system="Interpret customer message",
        messages=[ChatMessage(role="user", content=str(fixture["user"]))],
        response_model=InterpretationResult,
        timeout_s=5,
    )
    assert completion.output == output
    assert completion.usage.cost_usd > 0
    assert client.responses.parse.call_args.kwargs["store"] is False
    assert client.responses.parse.call_args.kwargs["timeout"] == 5
    record = interpretation_record(
        completion,
        record_id="rec-1",
        trace_id="trace-1",
        turn_index=0,
        step_index=0,
        created_at=datetime.now(UTC),
    )
    assert record.cost_usd == completion.usage.cost_usd
    assert record.prompt_version == "interpret-v1"
    assert str(fixture["user"]) not in record.model_dump_json()


def test_spend_limit_aborts_before_api_call() -> None:
    client = Mock()
    provider = OpenAIProvider(client=client, spend_limit_usd=Decimal("0.000001"))
    with pytest.raises(SpendLimitExceeded) as caught:
        _complete(provider)
    assert isinstance(caught.value, LLMUnavailable)
    client.responses.parse.assert_not_called()


def test_budget_accumulates_and_blocks_next_call() -> None:
    client = Mock()
    client.responses.parse.return_value = _response()
    provider = OpenAIProvider(client=client, spend_limit_usd=Decimal("0.0009"))
    _complete(provider)
    first = provider.spent_usd
    _complete(provider)
    assert provider.spent_usd == 2 * first
    with pytest.raises(SpendLimitExceeded):
        _complete(provider)
    assert client.responses.parse.call_count == 2


def test_prompts_strip_customer_identifiers_and_runtime_labels() -> None:
    client = Mock()
    client.responses.parse.return_value = _response()
    provider = OpenAIProvider(client=client)
    provider.complete_structured(
        system="customer_id=CUST-FX-007; fraud_score=7",
        messages=[
            ChatMessage(
                role="user",
                content="Soy CUST-FX-007. is_fraud=true. Me cobraron doble.",
            )
        ],
        response_model=InterpretationResult,
        timeout_s=5,
    )
    sent = str(client.responses.parse.call_args.kwargs)
    assert "CUST-FX-007" not in sent
    assert "customer_id" not in sent
    assert "fraud_score" not in sent
    assert "is_fraud" not in sent


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (APITimeoutError(request=Mock()), LLMTimeout),
        (APIConnectionError(request=Mock()), LLMUnavailable),
        (APIStatusError("rate limited", response=Mock(status_code=429), body=None), LLMUnavailable),
        (APIStatusError("server error", response=Mock(status_code=500), body=None), LLMUnavailable),
        (ContentFilterFinishReasonError(), LLMMalformedOutput),
        (LengthFinishReasonError(completion=Mock()), LLMMalformedOutput),
    ],
)
def test_api_failures_map_to_typed_errors(error: Exception, expected: type[Exception]) -> None:
    client = Mock()
    client.responses.parse.side_effect = error
    provider = OpenAIProvider(client=client)
    with pytest.raises(expected):
        _complete(provider)
    assert provider.spent_usd > 0


def test_missing_usage_reserves_possible_spend() -> None:
    client = Mock()
    client.responses.parse.return_value = _response(usage=None)
    provider = OpenAIProvider(client=client)
    with pytest.raises(LLMMalformedOutput):
        _complete(provider)
    assert provider.spent_usd > 0


@pytest.mark.parametrize(("field", "value"), [("confidence", 1.5), ("card_last4", "12")])
def test_transport_output_failing_shared_contract_is_malformed(field: str, value: object) -> None:
    client = Mock()
    output = dict(FIXTURES[0]["output"])
    if field == "card_last4":
        output["slots"] = {**output["slots"], "card_last4": value}
    else:
        output[field] = value
    client.responses.parse.return_value = _response(output)
    provider = OpenAIProvider(client=client)
    with pytest.raises(LLMMalformedOutput):
        _complete(provider)
    assert provider.spent_usd == provider._price(1000, 100)


def test_missing_parsed_output_is_malformed() -> None:
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=None, usage=SimpleNamespace(input_tokens=100, output_tokens=20)
    )
    provider = OpenAIProvider(client=client)
    with pytest.raises(LLMMalformedOutput):
        _complete(provider)
    assert provider.spent_usd == provider._price(100, 20)


class _Task(BaseModel):
    """A model that declares its own task, like the evaluation's LLM-only baseline step."""

    LLM_INSTRUCTIONS: ClassVar[str] = "Pick the next step."
    PROMPT_VERSION: ClassVar[str] = "task-v1"

    answer: str


def test_a_model_that_declares_its_task_is_parsed_with_its_own_instructions() -> None:
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=_Task(answer="ok"),
        usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
    )
    provider = OpenAIProvider(client=client)
    completion = provider.complete_structured(
        system="Customer id of this conversation: CUST-FX-001.",
        messages=[ChatMessage(role="user", content="hola")],
        response_model=_Task,
        timeout_s=5,
    )
    assert completion.output == _Task(answer="ok")
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs["text_format"] is _Task
    assert kwargs["instructions"].startswith("Pick the next step.")
    assert "Interpret the customer's latest message" not in kwargs["instructions"]
    assert "CUST-FX-001" not in str(kwargs)
    assert kwargs["store"] is False
    assert provider.spent_usd == provider._price(1000, 100)


def test_a_model_without_a_declared_task_is_refused_before_any_call() -> None:
    class Undeclared(BaseModel):
        answer: str

    client = Mock()
    provider = OpenAIProvider(client=client)
    with pytest.raises(LLMMalformedOutput):
        provider.complete_structured(
            system="x",
            messages=[ChatMessage(role="user", content="hola")],
            response_model=Undeclared,
            timeout_s=5,
        )
    client.responses.parse.assert_not_called()
