"""Which LLM the API process gets from its environment. Offline: no call is ever made."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bankagent.api.wiring import build_llm
from bankagent.interpret.compat_provider import OpenAICompatibleProvider
from bankagent.interpret.openai_provider import OpenAIProvider
from bankagent.interpret.stub import StubProvider

FAKE_KEY = "sk-test-not-a-real-key-123456"


@pytest.fixture(autouse=True)
def _no_real_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The OpenAI client reads this variable itself; never let a developer's real key in."""
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)


def test_stub_is_the_default_and_costs_nothing() -> None:
    assert isinstance(build_llm({}), StubProvider)
    assert isinstance(build_llm({"LLM_PROVIDER": "stub"}), StubProvider)


def test_openai_uses_the_default_model_when_the_variable_is_empty() -> None:
    """A variable that exists but is empty must fall back to the default, not reach the API."""
    for env in ({"LLM_PROVIDER": "openai"}, {"LLM_PROVIDER": "openai", "LLM_MODEL": ""}):
        provider = build_llm(env)
        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "gpt-6-luna"


def test_openai_honours_the_configured_model() -> None:
    provider = build_llm({"LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-6-sol"})
    assert provider.model == "gpt-6-sol"


def test_an_unknown_openai_model_is_refused_at_startup_not_at_the_first_customer() -> None:
    with pytest.raises(ValueError, match="no price configured"):
        build_llm({"LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-not-priced"})


def test_the_server_lifetime_cap_is_configurable() -> None:
    capped = build_llm({"LLM_PROVIDER": "openai", "LLM_SPEND_LIMIT_USD": "2.50"})
    default = build_llm({"LLM_PROVIDER": "openai"})
    assert isinstance(capped, OpenAIProvider)
    assert isinstance(default, OpenAIProvider)
    assert capped._limit == Decimal("2.50")
    assert default._limit == Decimal("0.10")  # config/pricing.yaml default_run_limit_usd
    blank = build_llm({"LLM_PROVIDER": "openai", "LLM_SPEND_LIMIT_USD": ""})
    assert isinstance(blank, OpenAIProvider)
    assert blank._limit == Decimal("0.10")


@pytest.mark.parametrize("bad", ["abc", "0", "-1", "NaN", "Infinity", "1e400x"])
def test_a_bad_cap_is_refused_without_echoing_it(bad: str) -> None:
    with pytest.raises(ValueError, match="LLM_SPEND_LIMIT_USD") as caught:
        build_llm({"LLM_PROVIDER": "openai", "LLM_SPEND_LIMIT_USD": bad})
    assert bad not in str(caught.value)


def test_compat_builds_from_the_llm_variables_and_shares_the_cap() -> None:
    env = {
        "LLM_PROVIDER": "compat",
        "LLM_BASE_URL": "https://openrouter.ai/api/v1",
        "LLM_API_KEY": "test-key-not-real",
        "LLM_MODEL": "gpt-6-luna",
        "LLM_SPEND_LIMIT_USD": "1.25",
    }
    provider = build_llm(env)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "gpt-6-luna"
    assert provider._limit == Decimal("1.25")


def test_compat_without_its_variables_fails_naming_them_and_never_uses_the_openai_key() -> None:
    with pytest.raises(ValueError, match="LLM_BASE_URL") as caught:
        build_llm({"LLM_PROVIDER": "compat", "OPENAI_API_KEY": FAKE_KEY})
    assert FAKE_KEY not in str(caught.value)


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(ValueError, match="'stub', 'openai' or 'compat'"):
        build_llm({"LLM_PROVIDER": "anthropic"})
