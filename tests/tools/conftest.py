"""Shared fixtures for the tools: the real fixture bank (DuckDB) and a temporary ops store.

`desk` is the counter the tests work at: it builds contexts for a persona, issues confirmation
tokens and runs tools. `make_desk` builds one over another store or serving DB (faulty stores,
altered copies of the bank); `altered_bank` returns such a copy.
"""

from __future__ import annotations

import itertools
import shutil
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from bankagent.contracts.base import Contract
from bankagent.contracts.decisions import PolicyDecision
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import ActionType, DecisionType, ToolName
from bankagent.contracts.tools import Tool, ToolContext
from bankagent.fixtures.builder import build
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB
from bankagent.tools import build_tools, issue_confirmation_token

START = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
# The session starts before the first call, so "now" and "session start" are different instants
# and a tool that stamps a record with the wrong one is caught.
SESSION_START = START - timedelta(minutes=2)
SESSION_TTL = timedelta(minutes=15)
MARIANA = "CUST-FX-001"

PROCEED = PolicyDecision(
    decision=DecisionType.PROCEED,
    rule_ids=("DSP-ELIG-01", "DSP-WIN-02"),
    allowed_actions=(ActionType.CREATE_DISPUTE, ActionType.BLOCK_CARD),
    requires_confirmation=True,
    sla_due_date=date(2026, 7, 17),
    policy_version="test-policy-v1",
)

# Sentinel: "use the default PROCEED decision" (None must stay expressible).
DEFAULT_POLICY: Any = object()


class Desk:
    """One serving DB, one ops store, the seven tools and a movable clock."""

    def __init__(self, serving: ServingDB, store: OpsStore, *, require_policy: bool = True) -> None:
        self.serving = serving
        self.store = store
        self.now = START
        self._minted = itertools.count(1)  # next() is atomic: ids stay unique across threads
        self.tools: dict[ToolName, Tool[Any, Any]] = build_tools(
            serving, store, require_policy=require_policy, id_factory=self._new_id
        )

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._minted):04d}"

    def session(self, customer_id: str = MARIANA, session_id: str | None = None) -> Session:
        return Session(
            # Opaque on purpose: a session id never contains the customer id.
            session_id=session_id or f"ses-{customer_id.removeprefix('CUST-FX-')}",
            customer_id=customer_id,
            issued_at=SESSION_START,
            expires_at=SESSION_START + SESSION_TTL,
        )

    def ctx(
        self,
        customer_id: str = MARIANA,
        *,
        token: str | None = None,
        policy: PolicyDecision | None = DEFAULT_POLICY,
        session: Session | None = None,
        trace_id: str = "trace-1",
    ) -> ToolContext:
        return ToolContext(
            session=session or self.session(customer_id),
            trace_id=trace_id,
            now=self.now,
            confirmation_token_id=token,
            policy=PROCEED if policy is DEFAULT_POLICY else policy,
        )

    def confirm(
        self,
        tool: ToolName,
        args: Contract,
        customer_id: str = MARIANA,
        *,
        session: Session | None = None,
        ttl: timedelta | None = None,
    ) -> str:
        """Issue a confirmation token for exactly ``tool(args)``; returns its id."""
        token = issue_confirmation_token(
            self.store,
            session=session or self.session(customer_id),
            tool=tool,
            args=args,
            now=self.now,
            **({} if ttl is None else {"ttl": ttl}),
        )
        return token.token_id

    def run(self, tool: ToolName, args: Contract, customer_id: str = MARIANA, **ctx: Any) -> Any:
        return self.tools[tool].run(self.ctx(customer_id, **ctx), args)

    def confirmed(
        self, tool: ToolName, args: Contract, customer_id: str = MARIANA, **ctx: Any
    ) -> Any:
        """Issue a fresh token for the call and run it."""
        return self.run(tool, args, customer_id, token=self.confirm(tool, args, customer_id), **ctx)

    def token_is_spent(self, token_id: str, customer_id: str = MARIANA) -> bool:
        token = self.store.get_confirmation_token(token_id, self.session(customer_id).session_id)
        assert token is not None
        return token.used_at is not None


@pytest.fixture(scope="session")
def fixture_bank(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("tools-bank") / "bank_fixture.duckdb"
    _, problems = build(out=out)
    assert problems == []
    return out


@pytest.fixture
def serving(fixture_bank: Path) -> ServingDB:
    return ServingDB(fixture_bank)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[OpsStore]:
    with OpsStore(tmp_path / "ops.sqlite") as opened:
        yield opened


@pytest.fixture
def desk(serving: ServingDB, store: OpsStore) -> Desk:
    return Desk(serving, store)


@pytest.fixture
def make_desk(serving: ServingDB, store: OpsStore) -> Callable[..., Desk]:
    def make(
        *, serving_db: ServingDB | None = None, ops: OpsStore | None = None, **options: Any
    ) -> Desk:
        return Desk(serving_db or serving, ops or store, **options)

    return make


@pytest.fixture
def altered_bank(fixture_bank: Path, tmp_path: Path) -> Callable[..., ServingDB]:
    """A copy of the fixture bank changed by one parameterized statement."""

    def alter(statement: str, parameters: Sequence[Any] = ()) -> ServingDB:
        copy = tmp_path / f"bank_altered_{len(list(tmp_path.glob('bank_altered_*')))}.duckdb"
        shutil.copy(fixture_bank, copy)
        with duckdb.connect(str(copy)) as con:
            con.execute(statement, parameters)
        return ServingDB(copy)

    return alter
