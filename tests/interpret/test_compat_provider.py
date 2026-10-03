"""Offline tests for the OpenAI-compatible chat provider (OpenRouter, Groq, ...). No network."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
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
from bankagent.interpret.compat_provider import (
    PROMPT_VERSION,
    PROMPT_VERSION_JSON_OBJECT,
    OpenAICompatibleProvider,
    from_env,
)
from bankagent.interpret.openai_provider import SpendLimitExceeded, _ParsedInterpretation

FIXTURES = json.loads(
    (Path(__file__).parent / "synthetic_interpret.json").read_text(encoding="utf-8")
)
BASE_URL = "https://api.example.com/v1"
PRICED = "gpt-6-luna"  # present in config/pricing.yaml
UNPRICED = "vendor/free-model:free"  # deliberately absent


def _parsed(output: object = None) -> _ParsedInterpretation:
    return _ParsedInterpretation.model_validate(output or FIXTURES[0]["output"])


def _completion(
    *,
    parsed: object = "default",
    content: str | None = None,
    refusal: str | None = None,
    usage: object = True,
) -> SimpleNamespace:
    """A chat completion shaped like the SDK's, for both ``parse`` and ``create``."""
    message = SimpleNamespace(
        parsed=_parsed() if parsed == "default" else parsed,
        content=content,
        refusal=refusal,
    )
    metering = (
        SimpleNamespace(prompt_tokens=1000, completion_tokens=100) if usage is True else usage
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=metering)


def _provider(client: Mock, **kwargs: object) -> OpenAICompatibleProvider:
    options: dict[str, Any] = {"model": PRICED, "base_url": BASE_URL, "client": client}
    options.update(kwargs)
    return OpenAICompatibleProvider(**options)


def _complete(provider: OpenAICompatibleProvider) -> StructuredCompletion[InterpretationResult]:
    return provider.complete_structured(
        system="interpret",
        messages=[ChatMessage(role="user", content="cobro duplicado")],
        response_model=InterpretationResult,
        timeout_s=5,
    )


def _client_returning(completion: SimpleNamespace) -> Mock:
    client = Mock()
    client.chat.completions.parse.return_value = completion
    client.chat.completions.create.return_value = completion
    return client


@pytest.mark.parametrize("fixture", FIXTURES)
def test_json_schema_mode_returns_the_shared_contract(fixture: dict[str, object]) -> None:
    parsed = _parsed(fixture["output"])
    client = _client_returning(_completion(parsed=parsed))
    provider: LLMProvider = _provider(client)
    completion = provider.complete_structured(
        system="Interpret customer message",
        messages=[ChatMessage(role="user", content=str(fixture["user"]))],
        response_model=InterpretationResult,
        timeout_s=5,
    )
    expected = InterpretationResult.model_validate(
        {**parsed.model_dump(), "model": PRICED, "prompt_version": PROMPT_VERSION}
    )
    assert completion.output == expected
    assert completion.usage.tokens_in == 1000
    assert completion.usage.tokens_out == 100
    assert completion.usage.cost_usd > 0
    assert provider.name == "openai-compatible"
    kwargs = client.chat.completions.parse.call_args.kwargs
    assert kwargs["timeout"] == 5
    assert kwargs["response_format"] is _ParsedInterpretation
    assert kwargs["messages"][0]["role"] == "system"
    client.chat.completions.create.assert_not_called()


def test_json_object_mode_parses_text_and_puts_the_schema_in_the_prompt() -> None:
    content = _parsed().model_dump_json()
    client = _client_returning(_completion(parsed=None, content=content))
    provider = _provider(client, response_mode="json_object")
    completion = _complete(provider)
    assert completion.output.prompt_version == PROMPT_VERSION_JSON_OBJECT
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    system_prompt = kwargs["messages"][0]["content"]
    assert "JSON Schema" in system_prompt
    assert '"dialogue_act"' in system_prompt
    client.chat.completions.parse.assert_not_called()


@pytest.mark.parametrize("content", ["not json", "{}", "", None])
def test_json_object_mode_rejects_unusable_text(content: str | None) -> None:
    client = _client_returning(_completion(parsed=None, content=content))
    provider = _provider(client, response_mode="json_object")
    with pytest.raises(LLMMalformedOutput):
        _complete(provider)
    assert provider.spent_usd > 0  # the call was made and metered or reserved


def test_unpriced_model_is_refused_unless_explicitly_allowed() -> None:
    with pytest.raises(ValueError, match="no price configured"):
        _provider(Mock(), model=UNPRICED)
    client = _client_returning(_completion())
    provider = _provider(client, model=UNPRICED, allow_unpriced=True)
    completion = _complete(provider)
    assert completion.usage.cost_usd == 0
    assert completion.usage.tokens_in == 1000
    assert provider.spent_usd == 0


def test_unpriced_model_tolerates_missing_usage_but_priced_does_not() -> None:
    free = _provider(
        _client_returning(_completion(usage=None)), model=UNPRICED, allow_unpriced=True
    )
    assert _complete(free).usage.tokens_in == 0
    paid = _provider(_client_returning(_completion(usage=None)))
    with pytest.raises(LLMMalformedOutput):
        _complete(paid)
    assert paid.spent_usd > 0


def test_daily_call_limit_blocks_before_the_network_and_resets_each_utc_day() -> None:
    now = [datetime(2026, 10, 3, 23, 0, tzinfo=UTC)]
    client = _client_returning(_completion())
    provider = _provider(client, daily_call_limit=2, clock=lambda: now[0])
    _complete(provider)
    _complete(provider)
    with pytest.raises(SpendLimitExceeded) as caught:
        _complete(provider)
    assert isinstance(caught.value, LLMUnavailable)  # the caller's fallback handles it
    assert client.chat.completions.parse.call_count == 2
    now[0] += timedelta(hours=2)  # next UTC day
    _complete(provider)
    assert client.chat.completions.parse.call_count == 3
    assert provider.calls_today == 1


def test_failed_attempts_count_against_the_daily_quota() -> None:
    client = Mock()
    client.chat.completions.parse.side_effect = APIConnectionError(request=Mock())
    provider = _provider(client, daily_call_limit=2)
    for _ in range(2):
        with pytest.raises(LLMUnavailable):
            _complete(provider)
    with pytest.raises(SpendLimitExceeded):
        _complete(provider)
    assert client.chat.completions.parse.call_count == 2


def test_daily_limit_holds_under_concurrent_calls() -> None:
    client = _client_returning(_completion())
    provider = _provider(client, daily_call_limit=5)
    outcomes: list[str] = []
    guard = threading.Lock()

    def worker() -> None:
        try:
            _complete(provider)
            result = "ok"
        except SpendLimitExceeded:
            result = "limited"
        with guard:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("ok") == 5
    assert outcomes.count("limited") == 25
    assert client.chat.completions.parse.call_count == 5


def test_spend_limit_aborts_before_api_call() -> None:
    client = Mock()
    provider = _provider(client, spend_limit_usd=Decimal("0.000001"))
    with pytest.raises(SpendLimitExceeded):
        _complete(provider)
    client.chat.completions.parse.assert_not_called()


def test_prompts_strip_customer_identifiers_and_runtime_labels() -> None:
    client = _client_returning(_completion())
    provider = _provider(client)
    provider.complete_structured(
        system="customer_id=CUST-FX-007; fraud_score=7",
        messages=[
            ChatMessage(role="user", content="Soy CUST-FX-007. is_fraud=true. Me cobraron doble.")
        ],
        response_model=InterpretationResult,
        timeout_s=5,
    )
    sent = str(client.chat.completions.parse.call_args.kwargs)
    for forbidden in ("CUST-FX-007", "customer_id", "fraud_score", "is_fraud"):
        assert forbidden not in sent


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
    client.chat.completions.parse.side_effect = error
    provider = _provider(client)
    with pytest.raises(expected):
        _complete(provider)
    assert provider.spent_usd > 0


def test_refusal_and_missing_parse_are_malformed() -> None:
    for completion in (
        _completion(parsed=None, refusal="I cannot help with that"),
        _completion(parsed=None),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2)),
    ):
        provider = _provider(_client_returning(completion))
        with pytest.raises(LLMMalformedOutput):
            _complete(provider)


@pytest.mark.parametrize(("field", "value"), [("confidence", 1.5), ("card_last4", "12")])
def test_output_failing_shared_contract_is_malformed(field: str, value: object) -> None:
    output = dict(FIXTURES[0]["output"])
    if field == "card_last4":
        output["slots"] = {**output["slots"], "card_last4": value}
    else:
        output[field] = value
    provider = _provider(_client_returning(_completion(parsed=_parsed(output))))
    with pytest.raises(LLMMalformedOutput):
        _complete(provider)
    assert provider.spent_usd == provider._price(1000, 100)


def test_a_model_without_a_declared_task_is_refused() -> None:
    provider = _provider(Mock())
    with pytest.raises(LLMMalformedOutput):
        provider.complete_structured(
            system="x",
            messages=[ChatMessage(role="user", content="hola")],
            response_model=_ParsedInterpretation,
            timeout_s=5,
        )


@pytest.mark.parametrize(
    "url",
    ["http://api.example.com/v1", "ftp://api.example.com", "api.example.com", "https://", ""],
)
def test_cleartext_or_malformed_endpoints_are_refused(url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleProvider(model=PRICED, base_url=url, api_key="k")


@pytest.mark.parametrize("url", ["https://openrouter.ai/api/v1", "http://localhost:8000/v1"])
def test_https_and_localhost_endpoints_are_accepted(url: str) -> None:
    assert OpenAICompatibleProvider(model=PRICED, base_url=url, api_key="k").model == PRICED


def test_a_key_is_required_when_no_client_is_given() -> None:
    with pytest.raises(ValueError, match="api_key"):
        OpenAICompatibleProvider(model=PRICED, base_url=BASE_URL)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"daily_call_limit": 0},
        {"max_output_tokens": 0},
        {"spend_limit_usd": Decimal(0)},
        {"response_mode": "xml"},
    ],
)
def test_invalid_options_are_refused(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="must be"):
        _provider(Mock(), **kwargs)


def test_from_env_builds_a_provider() -> None:
    provider = from_env(
        {
            "LLM_BASE_URL": "https://openrouter.ai/api/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": UNPRICED,
            "LLM_ALLOW_UNPRICED": "true",
            "LLM_DAILY_CALL_LIMIT": "40",
            "LLM_RESPONSE_MODE": "json_object",
        }
    )
    assert provider.model == UNPRICED
    assert provider._mode == "json_object"
    assert provider._daily_call_limit == 40


def test_from_env_names_missing_keys_and_never_echoes_values() -> None:
    secret = "sk-secret-value-123"
    with pytest.raises(ValueError, match="LLM_BASE_URL") as missing:
        from_env({"LLM_API_KEY": secret, "LLM_MODEL": PRICED})
    assert secret not in str(missing.value)
    with pytest.raises(ValueError, match="base_url") as cleartext:
        from_env(
            {"LLM_BASE_URL": "http://evil.example", "LLM_API_KEY": secret, "LLM_MODEL": PRICED}
        )
    assert secret not in str(cleartext.value)
    for env in (
        {
            "LLM_BASE_URL": BASE_URL,
            "LLM_API_KEY": secret,
            "LLM_MODEL": PRICED,
            "LLM_RESPONSE_MODE": "x",
        },
        {
            "LLM_BASE_URL": BASE_URL,
            "LLM_API_KEY": secret,
            "LLM_MODEL": PRICED,
            "LLM_DAILY_CALL_LIMIT": "many",
        },
    ):
        with pytest.raises(ValueError, match="LLM_") as bad:
            from_env(env)
        assert secret not in str(bad.value)


def test_from_env_ignores_the_openai_key() -> None:
    """The OpenAI key must never be sent to a third-party endpoint."""
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        from_env({"LLM_BASE_URL": BASE_URL, "LLM_MODEL": PRICED, "OPENAI_API_KEY": "sk-openai"})


class _Task(BaseModel):
    """A model that declares its own task, like the evaluation's LLM-only baseline step."""

    LLM_INSTRUCTIONS: ClassVar[str] = "Pick the next step."
    PROMPT_VERSION: ClassVar[str] = "task-v1"

    answer: str


def _complete_task(provider: OpenAICompatibleProvider) -> StructuredCompletion[_Task]:
    return provider.complete_structured(
        system="Customer id of this conversation: CUST-FX-001.",
        messages=[ChatMessage(role="user", content="hola")],
        response_model=_Task,
        timeout_s=5,
    )


def test_json_schema_mode_parses_a_declared_task_with_its_own_instructions() -> None:
    client = _client_returning(_completion(parsed=_Task(answer="ok")))
    completion = _complete_task(_provider(client))
    assert completion.output == _Task(answer="ok")
    kwargs = client.chat.completions.parse.call_args.kwargs
    assert kwargs["response_format"] is _Task
    system = kwargs["messages"][0]["content"]
    assert system.startswith("Pick the next step.")
    assert "CUST-FX-001" not in json.dumps(kwargs["messages"])


def test_json_object_mode_puts_the_declared_task_schema_in_the_prompt() -> None:
    client = _client_returning(_completion(parsed=None, content='{"answer": "ok"}'))
    completion = _complete_task(_provider(client, response_mode="json_object"))
    assert completion.output == _Task(answer="ok")
    system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert json.dumps(_Task.model_json_schema()) in system


def test_amounts_typed_with_separators_are_normalized_like_the_openai_provider() -> None:
    """Same regression as test_openai_provider: "1,249" was rejected, "1.249" read as 1.249."""
    for typed in ("1,249", "1.249"):
        output = dict(FIXTURES[0]["output"])
        output["slots"] = {**output["slots"], "amount": typed}
        provider = _provider(_client_returning(_completion(parsed=_parsed(output))))
        assert str(_complete(provider).output.slots.amount) == "1249.00"


def test_instructions_sent_to_a_compatible_endpoint_put_a_request_for_a_person_first() -> None:
    client = _client_returning(_completion())
    _complete(_provider(client))
    system_prompt = client.chat.completions.parse.call_args.kwargs["messages"][0]["content"]
    assert "human_request" in system_prompt
    assert "even when the same message also describes" in system_prompt
