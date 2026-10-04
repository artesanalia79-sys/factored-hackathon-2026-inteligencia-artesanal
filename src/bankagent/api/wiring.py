"""Production wiring for the single-worker dispute intake API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI

from bankagent.api.app import create_app
from bankagent.auth.wiring import DEFAULT_OPS_DB, create_auth_service, utc_now
from bankagent.contracts.llm import LLMProvider
from bankagent.contracts.tools import SearchTransactionsArgs
from bankagent.interpret.compat_provider import from_env as compat_from_env
from bankagent.interpret.openai_provider import OpenAIProvider
from bankagent.interpret.stub import StubProvider
from bankagent.obs import install_log_redaction
from bankagent.orchestrator.agent import create_agent
from bankagent.orchestrator.wiring import build_confirmation_issuer, build_policy_evaluator
from bankagent.store.ops import OpsStore
from bankagent.store.selection import ROOT, open_serving_db
from bankagent.tools import build_tools


def _configured_path(env: Mapping[str, str], key: str, default: Path) -> Path:
    value = env.get(key)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _spend_limit(env: Mapping[str, str]) -> Decimal | None:
    """``LLM_SPEND_LIMIT_USD`` for the server's whole lifetime; unset keeps the default cap."""
    raw = env.get("LLM_SPEND_LIMIT_USD")
    if not raw:
        return None
    try:
        limit = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("LLM_SPEND_LIMIT_USD must be a number") from exc
    if not limit.is_finite() or limit <= 0:
        raise ValueError("LLM_SPEND_LIMIT_USD must be a positive number")
    return limit


def build_llm(env: Mapping[str, str]) -> LLMProvider:
    """The interpreter behind ``LLM_PROVIDER``: ``stub`` (default, 0 USD), ``openai`` or ``compat``.

    One provider serves every conversation of the process, so its spend limit is the server's
    lifetime budget: once reached the agent falls back to the keyword interpreter instead of
    failing. Errors name variables, never values.
    """
    provider_name = env.get("LLM_PROVIDER", "stub")
    if provider_name == "stub":
        return StubProvider()
    if provider_name == "openai":
        return OpenAIProvider(
            model=env.get("LLM_MODEL") or "gpt-6-luna", spend_limit_usd=_spend_limit(env)
        )
    if provider_name == "compat":
        return compat_from_env(env, spend_limit_usd=_spend_limit(env))
    raise ValueError("LLM_PROVIDER must be 'stub', 'openai' or 'compat'")


def create_default_app(environ: Mapping[str, str] | None = None) -> FastAPI:
    """Build all components over one serving DB and one ops store.

    ``environ`` defaults to the process environment (``poe serve`` and the image call this with
    no argument). ``DATA_MODE`` picks the serving DB, which must hold that mode and fit the
    serving contract before anything else is opened (``bankagent.store.selection``).
    The web UI is served from ``WEB_DIST_DIR`` (default ``web/dist``) once it is built
    (``npm --prefix web run build``); without a build the app serves the API only.
    """
    # First: every log record created from here on is redacted, whatever logger or handler
    # writes it. uvicorn configures its loggers before it calls this factory and never replaces
    # the record factory, so its access and error logs are covered too.
    install_log_redaction()
    env = os.environ if environ is None else environ
    clock = utc_now
    serving = open_serving_db(env)
    store = OpsStore(_configured_path(env, "OPS_DB_PATH", DEFAULT_OPS_DB))
    auth = create_auth_service(env, clock=clock, store=store, customers=serving)
    tools = build_tools(serving, store)
    policy = build_policy_evaluator(serving, store, clock=clock)
    issuer = build_confirmation_issuer(store)
    llm = build_llm(env)
    return create_app(
        auth=auth,
        transaction_reader=lambda customer_id: serving.search_transactions(
            customer_id, SearchTransactionsArgs(), limit=50
        ),
        agent_factory=lambda: create_agent(
            llm=llm, tools=tools, clock=clock, policy=policy, issue_confirmation=issuer
        ),
        record_sink=store.append_records,
        readiness={
            "serving_db": serving.as_of_date,
            "ops_store": lambda: store.count("sessions"),
        },
        web_dist=_configured_path(env, "WEB_DIST_DIR", ROOT / "web" / "dist"),
    )
