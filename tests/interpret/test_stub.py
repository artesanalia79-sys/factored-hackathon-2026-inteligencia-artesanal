"""StubProvider satisfies LLMProvider, is free and supports fault injection."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from bankagent.contracts.decisions import InterpretationResult
from bankagent.contracts.enums import DialogueAct, FaultInjection, Intent, Language
from bankagent.contracts.llm import (
    ChatMessage,
    LLMMalformedOutput,
    LLMProvider,
    LLMTimeout,
    LLMUnavailable,
    StructuredCompletion,
)
from bankagent.interpret.stub import FAULTS_BY_INJECTION, StubFault, StubProvider

MESSAGES = [
    ChatMessage(role="assistant", content="Hola, ¿en qué te ayudo?"),
    ChatMessage(role="user", content="Me cobraron dos veces en Rappi"),
]


def _call(provider: LLMProvider) -> StructuredCompletion[InterpretationResult]:
    return provider.complete_structured(
        system="interpret", messages=MESSAGES, response_model=InterpretationResult, timeout_s=5.0
    )


def test_keyword_interpretation_of_last_user_message() -> None:
    provider: LLMProvider = StubProvider()  # structural check against the protocol
    completion = _call(provider)
    assert isinstance(completion.output, InterpretationResult)
    assert completion.output.intent == Intent.DISPUTE_DUPLICATE
    assert completion.usage.cost_usd == 0
    assert completion.usage.tokens_in > 0
    assert completion.model == "stub-keywords"


def test_scripted_responses_are_returned_in_order() -> None:
    scripted = InterpretationResult(
        intent=Intent.CARD_BLOCK,
        dialogue_act=DialogueAct.NEW_REQUEST,
        language=Language.PT,
        confidence=0.99,
        model="scripted",
        prompt_version="t",
    )
    provider = StubProvider(scripted=[scripted])
    assert _call(provider).output == scripted
    assert _call(provider).output.intent == Intent.DISPUTE_DUPLICATE  # back to keywords


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        (StubFault.TIMEOUT, LLMTimeout),
        (StubFault.MALFORMED, LLMMalformedOutput),
        (StubFault.UNAVAILABLE, LLMUnavailable),
    ],
)
def test_scheduled_faults_then_recovery(fault: StubFault, error: type[Exception]) -> None:
    provider = StubProvider(faults=[fault, None])
    with pytest.raises(error):
        _call(provider)
    assert _call(provider).output.intent == Intent.DISPUTE_DUPLICATE
    assert provider.calls == 2


def test_always_fault() -> None:
    provider = StubProvider(always_fault=StubFault.UNAVAILABLE)
    for _ in range(3):
        with pytest.raises(LLMUnavailable):
            _call(provider)


def test_every_llm_fault_injection_maps_to_a_stub_fault() -> None:
    llm_faults = {f for f in FaultInjection if f.value.startswith("llm_")}
    assert set(FAULTS_BY_INJECTION) == llm_faults


def test_unknown_response_model_is_malformed() -> None:
    class Other(BaseModel):
        value: int

    with pytest.raises(LLMMalformedOutput):
        StubProvider().complete_structured(
            system="x", messages=MESSAGES, response_model=Other, timeout_s=1.0
        )


def test_is_deterministic() -> None:
    assert _call(StubProvider()).output == _call(StubProvider()).output
