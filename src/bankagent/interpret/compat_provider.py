"""Structured interpretation through any OpenAI-compatible chat endpoint (OpenRouter, Groq, ...).

Why a second provider: ``OpenAIProvider`` uses the OpenAI Responses API (``responses.parse`` and
``reasoning``), which other vendors do not reliably implement. This one speaks ``chat.completions``,
which they do. It reuses the interpreter prompt, the transport schema and the redaction of
``openai_provider`` so both providers interpret the same way.

Two differences that matter for free tiers:

- A free model has no price, so a USD limit means nothing. ``daily_call_limit`` caps requests per
  UTC day instead and raises ``SpendLimitExceeded`` (an ``LLMUnavailable``), so the caller's
  fallback takes over before the vendor starts answering 429.
- An unpriced model needs ``allow_unpriced=True`` and is recorded with cost 0. That is "not
  priced", not "free of charge": do not use it as evidence of cost per resolution.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast
from urllib.parse import urlparse

import yaml
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
    OpenAI,
    OpenAIError,
)
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from bankagent.contracts.llm import (
    ChatMessage,
    LLMMalformedOutput,
    LLMTimeout,
    LLMUnavailable,
    StructuredCompletion,
    TokenUsage,
)
from bankagent.interpret.openai_provider import (
    _DEFAULT_PRICING,
    _MILLION,
    MAX_OUTPUT_TOKENS,
    SpendLimitExceeded,
    _redact,
    structured_task,
)

ResponseMode = Literal["json_schema", "json_object"]
RESPONSE_MODES: tuple[ResponseMode, ...] = ("json_schema", "json_object")
# Same instructions as interpret-v1 in json_schema mode; json_object mode adds the schema text.
PROMPT_VERSION = "interpret-v1"
PROMPT_VERSION_JSON_OBJECT = "interpret-v1-json"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_SCHEMA_SUFFIX = (
    "\nRespond with a single JSON object that matches this JSON Schema and nothing else:\n"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _check_base_url(base_url: str) -> None:
    """Refuse a cleartext endpoint: the API key and customer text would travel unencrypted."""
    parsed = urlparse(base_url)
    host = parsed.hostname
    if parsed.scheme == "https" and host:
        return
    if parsed.scheme == "http" and host in _LOCAL_HOSTS:
        return
    raise ValueError("base_url must be https (http is only allowed for localhost)")


class OpenAICompatibleProvider:
    """A synchronous LLMProvider over ``chat.completions``. One instance per budgeted run."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        client: OpenAI | None = None,
        response_mode: ResponseMode = "json_schema",
        pricing_path: Path = _DEFAULT_PRICING,
        allow_unpriced: bool = False,
        spend_limit_usd: Decimal | None = None,
        daily_call_limit: int | None = None,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        _check_base_url(base_url)
        if response_mode not in RESPONSE_MODES:
            raise ValueError(f"response_mode must be one of {', '.join(RESPONSE_MODES)}")
        if max_output_tokens <= 0:
            raise ValueError("max output tokens must be positive")
        if daily_call_limit is not None and daily_call_limit <= 0:
            raise ValueError("daily call limit must be positive")
        if client is None and not api_key:
            raise ValueError("api_key is required when no client is given")

        config = yaml.safe_load(pricing_path.read_text(encoding="utf-8"))
        rates = config["models"].get(model)
        if rates is None:
            if not allow_unpriced:
                raise ValueError(
                    f"no price configured for model {model}; add it to config/pricing.yaml "
                    "or pass allow_unpriced=True (cost is then recorded as 0, not priced)"
                )
            self._priced = False
            self._input_rate = Decimal(0)
            self._output_rate = Decimal(0)
        else:
            self._priced = True
            self._input_rate = Decimal(rates["input_per_million_usd"])
            self._output_rate = Decimal(rates["output_per_million_usd"])
        self._limit = (
            spend_limit_usd
            if spend_limit_usd is not None
            else Decimal(config["default_run_limit_usd"])
        )
        if self._limit <= 0:
            raise ValueError("spend limit must be positive")

        self._model = model
        self._mode: ResponseMode = response_mode
        self._prompt_version = (
            PROMPT_VERSION if response_mode == "json_schema" else (PROMPT_VERSION_JSON_OBJECT)
        )
        self._max_output_tokens = max_output_tokens
        self._daily_call_limit = daily_call_limit
        self._clock = clock
        self._spent = Decimal("0")
        self._calls_day: date | None = None
        self._calls_today = 0
        self._lock = threading.Lock()
        self._client = (
            client
            if client is not None
            else OpenAI(base_url=base_url, api_key=api_key, max_retries=0)
        )

    @property
    def name(self) -> str:
        return "openai-compatible"

    @property
    def model(self) -> str:
        return self._model

    @property
    def spent_usd(self) -> Decimal:
        return self._spent

    @property
    def calls_today(self) -> int:
        return self._calls_today

    def complete_structured[T: BaseModel](
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        timeout_s: float,
    ) -> StructuredCompletion[T]:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        interpretation, base_instructions, transport = structured_task(response_model)
        instructions = base_instructions + "\nCaller context: " + _redact(system)
        if self._mode == "json_object":
            instructions += _SCHEMA_SUFFIX + json.dumps(transport.model_json_schema())
        sanitized = [{"role": m.role, "content": _redact(m.content)} for m in messages]
        payload = cast(
            list[ChatCompletionMessageParam],
            [{"role": "system", "content": instructions}, *sanitized],
        )
        input_bound = (
            len(instructions.encode())
            + sum(len(item["content"].encode()) for item in sanitized)
            + 4096
        )
        reservation = self._price(input_bound, self._max_output_tokens)
        self._admit(reservation)

        start = perf_counter()
        try:
            if self._mode == "json_schema":
                parsed_response = self._client.chat.completions.parse(
                    model=self._model,
                    messages=payload,
                    response_format=transport,
                    max_tokens=self._max_output_tokens,
                    timeout=timeout_s,
                )
                response = parsed_response
                choice = parsed_response.choices[0] if parsed_response.choices else None
                refusal = choice.message.refusal if choice is not None else None
                parsed = choice.message.parsed if choice is not None else None
            else:
                raw_response = self._client.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    response_format={"type": "json_object"},
                    max_tokens=self._max_output_tokens,
                    timeout=timeout_s,
                )
                response = raw_response
                choice = raw_response.choices[0] if raw_response.choices else None
                refusal = choice.message.refusal if choice is not None else None
                content = choice.message.content if choice is not None else None
                parsed = transport.model_validate_json(content) if content else None
        except APITimeoutError as exc:
            self._charge(reservation)
            raise LLMTimeout("LLM request timed out") from exc
        except (APIConnectionError, APIStatusError) as exc:
            self._charge(reservation)
            raise LLMUnavailable("LLM request unavailable") from exc
        except (
            ValidationError,
            ValueError,
            LengthFinishReasonError,
            ContentFilterFinishReasonError,
            APIResponseValidationError,
        ) as exc:
            self._charge(reservation)
            raise LLMMalformedOutput("LLM returned invalid structured output") from exc
        except OpenAIError as exc:
            self._charge(reservation)
            raise LLMUnavailable("LLM request failed") from exc

        usage = response.usage
        if usage is None:
            if self._priced:
                self._charge(reservation)
                raise LLMMalformedOutput("LLM response has no token usage")
            tokens_in = tokens_out = 0
        else:
            tokens_in, tokens_out = usage.prompt_tokens, usage.completion_tokens
        cost = self._price(tokens_in, tokens_out)
        self._charge(cost)
        if refusal or parsed is None:
            raise LLMMalformedOutput("LLM returned no parsed output")
        fields: dict[str, Any] = parsed.model_dump()
        if interpretation:
            fields |= {"model": self._model, "prompt_version": self._prompt_version}
        try:
            output = response_model.model_validate(fields)
        except ValidationError as exc:
            raise LLMMalformedOutput("LLM returned invalid structured output") from exc
        return StructuredCompletion[response_model](
            output=output,
            usage=TokenUsage(tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost),
            model=self._model,
            latency_ms=(perf_counter() - start) * 1000,
        )

    def _admit(self, reservation: Decimal) -> None:
        """Refuse the call before any network I/O if a limit would be crossed."""
        with self._lock:
            if self._spent + reservation > self._limit:
                raise SpendLimitExceeded("run spend limit reached")
            if self._daily_call_limit is not None:
                today = self._clock().astimezone(UTC).date()
                if today != self._calls_day:
                    self._calls_day, self._calls_today = today, 0
                if self._calls_today >= self._daily_call_limit:
                    raise SpendLimitExceeded("daily call limit reached")
                # Count the attempt now: vendors count failed requests against the quota too.
                self._calls_today += 1

    def _charge(self, amount: Decimal) -> None:
        with self._lock:
            self._spent += amount

    def _price(self, tokens_in: int, tokens_out: int) -> Decimal:
        return (
            Decimal(tokens_in) * self._input_rate + Decimal(tokens_out) * self._output_rate
        ) / _MILLION


def from_env(
    env: Mapping[str, str], *, spend_limit_usd: Decimal | None = None
) -> OpenAICompatibleProvider:
    """Build the provider from environment variables. Errors name keys, never values.

    Required: ``LLM_BASE_URL``, ``LLM_API_KEY``, ``LLM_MODEL``. Optional: ``LLM_RESPONSE_MODE``
    (``json_schema`` or ``json_object``), ``LLM_DAILY_CALL_LIMIT``, ``LLM_ALLOW_UNPRICED``
    (``true``). ``spend_limit_usd`` overrides the default run limit (the evaluation shares one
    provider per run so the daily call limit holds). The key is deliberately not
    ``OPENAI_API_KEY``: that one must never be sent to a third-party endpoint.
    """
    required = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise ValueError("missing environment variables: " + ", ".join(missing))
    mode = env.get("LLM_RESPONSE_MODE", "json_schema")
    if mode not in RESPONSE_MODES:
        raise ValueError("LLM_RESPONSE_MODE must be json_schema or json_object")
    raw_limit = env.get("LLM_DAILY_CALL_LIMIT")
    try:
        limit = int(raw_limit) if raw_limit else None
    except ValueError as exc:
        raise ValueError("LLM_DAILY_CALL_LIMIT must be an integer") from exc
    return OpenAICompatibleProvider(
        model=env["LLM_MODEL"],
        base_url=env["LLM_BASE_URL"],
        api_key=env["LLM_API_KEY"],
        response_mode=cast(ResponseMode, mode),
        allow_unpriced=env.get("LLM_ALLOW_UNPRICED", "").lower() == "true",
        daily_call_limit=limit,
        spend_limit_usd=spend_limit_usd,
    )
