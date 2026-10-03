"""Real Task 8 tools for evaluation runs: one shared serving DB, a fresh ops store per run.

One dispute per transaction and one block per card are permanent, and the repeat-disputer rule
counts the agent's own disputes, so a case run must never see the writes of another run (or of
another repeat of the same case). ``FixtureBackendFactory`` builds the synthetic fixture bank
once and opens a new, empty ``OpsStore`` file for every case run. The file name is random, so
no call can reopen a store: a counter per factory starts again with each factory, so a second
one over the same directory would hand its first run the previous run's disputes, silently.

Both systems get the same tools, built with ``require_policy=False``: the LLM-only baseline has no
policy by design, and a decision the proposed agent passes is still enforced by the tools.
The confirmation tokens a confirmed write needs are issued on the run's own store through
``RunBackend.issue_confirmation``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from bankagent.contracts.base import Contract
from bankagent.contracts.domain import ConfirmationToken, Session
from bankagent.contracts.enums import ToolName
from bankagent.contracts.tools import Tool
from bankagent.fixtures.builder import build
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB
from bankagent.tools import build_tools, issue_confirmation_token


@dataclass(slots=True)
class RunBackend:
    """The stores and tools of one case run. Closed by the runner when the run ends."""

    serving: ServingDB
    store: OpsStore
    tools: dict[ToolName, Tool[Any, Any]]

    def issue_confirmation(
        self, session: Session, tool: ToolName, args: Contract, now: datetime
    ) -> ConfirmationToken:
        """A token for exactly ``tool(args)`` on this run's store (Task 8)."""
        return issue_confirmation_token(self.store, session=session, tool=tool, args=args, now=now)

    def close(self) -> None:
        self.store.close()


class FixtureBackendFactory:
    """Builds the fixture bank under ``workdir`` once; every call returns a fresh backend."""

    def __init__(self, workdir: Path) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        bank = workdir / "bank_fixture.duckdb"
        _, problems = build(out=bank)
        if problems:
            raise RuntimeError(f"fixture bank failed its contract: {problems}")
        self._workdir = workdir
        self._serving = ServingDB(bank)

    def __call__(self) -> RunBackend:
        store = OpsStore(self._workdir / f"ops-{uuid4().hex}.sqlite")
        tools = build_tools(self._serving, store, require_policy=False)
        return RunBackend(serving=self._serving, store=store, tools=tools)
