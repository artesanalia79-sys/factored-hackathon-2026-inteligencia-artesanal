"""Session-scoped agent tools (reads, confirmed and verified writes). Owner: Juan José (T8).

The only path from a conversation to bank data (the policy engine reads its own risk inputs,
ADR 0003). `build_tools` returns the seven tools of
``TOOL_SPECS`` over one serving DB and one ops store; `issue_confirmation_token` issues the
token a confirmed write needs; `call_tool` runs a call and builds its ``ExecutionRecord``.

Identity always comes from ``ToolContext.session``. The tools hold no SQL (it lives in
`bankagent.store`) and never use the store's unscoped reads for human agents.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bankagent.contracts.enums import ToolName
from bankagent.contracts.tools import TOOL_SPECS, Tool
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB
from bankagent.tools.base import ToolDeps, new_id
from bankagent.tools.confirmation import DEFAULT_TOKEN_TTL, issue_confirmation_token
from bankagent.tools.reads import GetDispute, GetTransaction, ListCards, SearchTransactions
from bankagent.tools.records import ToolCall, call_tool
from bankagent.tools.writes import BlockCard, CreateDispute, CreateHandoff

__all__ = [
    "DEFAULT_TOKEN_TTL",
    "ToolCall",
    "build_tools",
    "call_tool",
    "issue_confirmation_token",
]


def build_tools(
    serving: ServingDB,
    store: OpsStore,
    *,
    require_policy: bool = True,
    id_factory: Callable[[str], str] = new_id,
) -> dict[ToolName, Tool[Any, Any]]:
    """The seven tools over one serving DB and one ops store, keyed by ``ToolName``.

    With ``require_policy`` (the default) ``create_dispute`` and ``block_card`` refuse a call
    whose context carries no policy decision. Only the evaluation harness passes ``False``:
    its LLM-only baseline has no policy by design and both systems must get identical tools.
    A decision that is present is enforced either way.

    Confirmation tokens must be issued on the same ``store`` (or another ``OpsStore`` on the
    same file): `issue_confirmation_token` writes the token the write tool later spends.

    One dispute per transaction and one block per card are permanent, so a caller that
    replays the same cases (the evaluation's repeats) needs a fresh ``OpsStore`` per run.
    """
    deps = ToolDeps(serving=serving, store=store, new_id=id_factory, require_policy=require_policy)
    tools: dict[ToolName, Tool[Any, Any]] = {
        tool.name: tool
        for tool in (
            ListCards(deps),
            SearchTransactions(deps),
            GetTransaction(deps),
            GetDispute(deps),
            CreateDispute(deps),
            BlockCard(deps),
            CreateHandoff(deps),
        )
    }
    if set(tools) != set(TOOL_SPECS):
        raise RuntimeError("build_tools must provide exactly the tools of TOOL_SPECS")
    return tools
