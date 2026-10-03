"""Production wiring for the single-worker dispute intake API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI

from bankagent.api.app import create_app
from bankagent.auth.wiring import (
    DEFAULT_OPS_DB,
    DEFAULT_SERVING_DB,
    ROOT,
    create_auth_service,
    utc_now,
)
from bankagent.interpret.openai_provider import OpenAIProvider
from bankagent.interpret.stub import StubProvider
from bankagent.orchestrator.agent import create_agent
from bankagent.orchestrator.wiring import build_confirmation_issuer, build_policy_evaluator
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB
from bankagent.tools import build_tools


def _configured_path(env: Mapping[str, str], key: str, default: Path) -> Path:
    value = env.get(key)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def create_default_app() -> FastAPI:
    """Build all components over one serving DB and one ops store."""
    env = os.environ
    clock = utc_now
    store = OpsStore(_configured_path(env, "OPS_DB_PATH", DEFAULT_OPS_DB))
    serving = ServingDB(_configured_path(env, "SERVING_DB_PATH", DEFAULT_SERVING_DB))
    auth = create_auth_service(env, clock=clock, store=store)
    tools = build_tools(serving, store)
    policy = build_policy_evaluator(serving, store, clock=clock)
    issuer = build_confirmation_issuer(store)
    provider_name = env.get("LLM_PROVIDER", "stub")
    if provider_name == "openai":
        llm = OpenAIProvider(model=env.get("LLM_MODEL", "gpt-6-luna"))
    elif provider_name == "stub":
        llm = StubProvider()
    else:
        raise ValueError("LLM_PROVIDER must be 'stub' or 'openai'")
    return create_app(
        auth=auth,
        agent_factory=lambda: create_agent(
            llm=llm, tools=tools, clock=clock, policy=policy, issue_confirmation=issuer
        ),
        record_sink=store.append_records,
    )
