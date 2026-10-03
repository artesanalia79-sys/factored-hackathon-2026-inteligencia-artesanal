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
    StructuredCompletion,
)
from bankagent.interpret.openai_provider import (
    _INSTRUCTIONS,
    PROMPT_VERSION,
    OpenAIProvider,
    SpendLimitExceeded,
    _ParsedInterpretation,
    normalize_amount_slot,
)
from bankagent.interpret.records import interpretation_record

FIXTURES = json.loads(
    (Path(__file__).parent / "synthetic_interpret.json").read_text(encoding="utf-8")
)


def _response(output: object = None, *, usage: object = True) -> SimpleNamespace:
    parsed = _ParsedInterpretation.model_validate(output or FIXTURES[0]["output"])
    metering = SimpleNamespace(input_tokens=1000, output_tokens=100) if usage is True else usage
    return SimpleNamespace(output_parsed=parsed, usage=metering)


def _complete(provider: OpenAIProvider) -> StructuredCompletion[InterpretationResult]:
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
        {**parsed.model_dump(), "model": "gpt-6-luna", "prompt_version": PROMPT_VERSION}
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
    assert record.prompt_version == PROMPT_VERSION
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
    # A call is admitted when spent + reservation fits the limit. Derive both from the
    # provider's own formula, so the test does not depend on how long the instructions are:
    # the limit sits between "fits after one call" and "fits after two".
    probe = OpenAIProvider(client=client)
    sent = len((_INSTRUCTIONS + "\nCaller context: interpret").encode())
    reservation = probe._price(sent + len(b"cobro duplicado") + 4096, 512)
    per_call = probe._price(1000, 100)
    limit = per_call + reservation + per_call / 2
    provider = OpenAIProvider(client=client, spend_limit_usd=limit)
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


# --- interpret-v2: a request for a person wins, and amounts are read the LATAM way ---------------


def test_the_prompt_version_names_the_current_instructions() -> None:
    assert PROMPT_VERSION == "interpret-v2"


def test_instructions_put_an_explicit_request_for_a_person_first() -> None:
    """Regression: with a charge in the same message the model answered as a dispute, so a
    customer who asked for a person did not reach one in 5 of 12 real runs."""
    text = _INSTRUCTIONS
    assert "human_request" in text
    assert "request_human" in text
    assert "even when the same message also describes" in text
    # ...but a complaint alone, or a refusal of a person, must not escalate:
    assert "a complaint alone is not such a request" in text
    assert "I do not want to talk to a person" in text


def test_instructions_reach_the_api() -> None:
    client = Mock()
    client.responses.parse.return_value = _response()
    _complete(OpenAIProvider(client=client))
    assert "human_request" in client.responses.parse.call_args.kwargs["instructions"]


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("1,249", "1249.00"),  # was rejected as malformed
        ("1.249", "1249.00"),  # was accepted as 1.249 pesos, silently wrong
        ("85.900", "85900.00"),
        ("2.450,50", "2450.50"),
        ("1,234.56", "1234.56"),
        ("$ 2.450", "2450.00"),
        ("129.00", "129.00"),
        ("1249", "1249.00"),
        ("42.5", "42.50"),
    ],
)
def test_amounts_are_read_the_way_a_latam_customer_writes_them(typed: str, expected: str) -> None:
    fields = {"slots": {"amount": typed, "currency": "COP"}}
    normalize_amount_slot(fields)
    assert fields["slots"]["amount"] == expected
    assert fields["slots"]["currency"] == "COP"


@pytest.mark.parametrize("typed", ["mil doscientos", "n/a", "", "0", "-", "..."])
def test_an_amount_that_cannot_be_read_is_dropped_not_guessed(typed: str) -> None:
    fields = {"slots": {"amount": typed}}
    normalize_amount_slot(fields)
    assert fields["slots"]["amount"] is None


def test_missing_amount_and_odd_shapes_are_left_alone() -> None:
    for fields in ({"slots": {"amount": None}}, {"slots": {}}, {"slots": "x"}, {}):
        before = repr(fields)
        normalize_amount_slot(fields)
        assert repr(fields) == before


def test_an_amount_with_a_thousands_separator_is_no_longer_malformed() -> None:
    """Regression from a real run: the model returned "1,249" and the whole answer was rejected."""
    client = Mock()
    output = dict(FIXTURES[0]["output"])
    output["slots"] = {**output["slots"], "amount": "1,249"}
    client.responses.parse.return_value = _response(output)
    completion = _complete(OpenAIProvider(client=client))
    assert str(completion.output.slots.amount) == "1249.00"


def test_a_silently_wrong_amount_is_corrected_before_it_reaches_the_agent() -> None:
    client = Mock()
    output = dict(FIXTURES[0]["output"])
    output["slots"] = {**output["slots"], "amount": "1.249"}
    client.responses.parse.return_value = _response(output)
    completion = _complete(OpenAIProvider(client=client))
    assert str(completion.output.slots.amount) == "1249.00"
