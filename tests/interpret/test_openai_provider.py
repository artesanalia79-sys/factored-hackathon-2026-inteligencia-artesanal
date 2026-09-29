"""Offline API-shape cassettes, budget and privacy checks for the real provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openai import APIConnectionError, APITimeoutError

from bankagent.contracts.decisions import InterpretationResult
from bankagent.contracts.llm import (
    ChatMessage,
    LLMMalformedOutput,
    LLMProvider,
    LLMTimeout,
    LLMUnavailable,
)
from bankagent.interpret.openai_provider import OpenAIProvider, SpendLimitExceeded
from bankagent.interpret.records import interpretation_record

CASSETTES = json.loads(
    (Path(__file__).parent / "cassettes" / "openai_interpret.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("cassette", CASSETTES)
def test_offline_structured_cassettes(cassette: dict[str, object]) -> None:
    output = InterpretationResult.model_validate(cassette["output"])
    response = SimpleNamespace(
        output_parsed=output,
        usage=SimpleNamespace(
            input_tokens=cassette["tokens_in"], output_tokens=cassette["tokens_out"]
        ),
    )
    client = Mock()
    client.responses.parse.return_value = response
    provider: LLMProvider = OpenAIProvider(client=client)
    completion = provider.complete_structured(
        system="Interpret customer message",
        messages=[ChatMessage(role="user", content=str(cassette["user"]))],
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
    assert str(cassette["user"]) not in record.model_dump_json()


def test_spend_limit_aborts_before_api_call() -> None:
    client = Mock()
    provider = OpenAIProvider(client=client, spend_limit_usd=Decimal("0.000001"))
    with pytest.raises(SpendLimitExceeded):
        provider.complete_structured(
            system="interpret",
            messages=[ChatMessage(role="user", content="cobro duplicado")],
            response_model=InterpretationResult,
            timeout_s=5,
        )
    client.responses.parse.assert_not_called()


def test_prompts_strip_customer_identifiers_and_runtime_labels() -> None:
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=InterpretationResult.model_validate(CASSETTES[0]["output"]),
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )
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
    ],
)
def test_api_failures_map_to_typed_errors(error: Exception, expected: type[Exception]) -> None:
    client = Mock()
    client.responses.parse.side_effect = error
    provider = OpenAIProvider(client=client)
    with pytest.raises(expected):
        provider.complete_structured(
            system="interpret",
            messages=[ChatMessage(role="user", content="cobro duplicado")],
            response_model=InterpretationResult,
            timeout_s=5,
        )


def test_missing_parsed_output_is_malformed() -> None:
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=None, usage=SimpleNamespace(input_tokens=100, output_tokens=20)
    )
    provider = OpenAIProvider(client=client)
    with pytest.raises(LLMMalformedOutput):
        provider.complete_structured(
            system="interpret",
            messages=[ChatMessage(role="user", content="cobro duplicado")],
            response_model=InterpretationResult,
            timeout_s=5,
        )
