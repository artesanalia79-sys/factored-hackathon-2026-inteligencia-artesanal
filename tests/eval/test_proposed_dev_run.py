"""The real proposed agent (Task 13) on the dev cases, through the harness.

StubProvider, the fixture bank, the real tools and policy, one fresh ops store per case run and
the clock at the fixture's ``as_of_date``. Before the held-out run the dev run must show every
case correct, no unsafe event and no question the scripted user cannot classify
(``docs/eval/system_interface.md``); this test keeps it that way.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bankagent.contracts.domain import Session
from bankagent.contracts.enums import (
    ActionType,
    DisputeReason,
    Outcome,
    SystemVariant,
    ToolName,
)
from bankagent.contracts.errors import ConfirmationRequired, InvalidArguments
from bankagent.contracts.tools import CreateDisputeArgs, ToolContext
from bankagent.eval.adapters import proposed_system
from bankagent.eval.bank import load_bank
from bankagent.eval.cases import load_cases
from bankagent.eval.runner import RunConfig, SpendGuard, fresh_bank, run_case, run_suite
from bankagent.eval.scorer import ScoredRun, score
from bankagent.eval.system import EvalEnvironment, ToolObserver
from bankagent.fixtures.builder import build
from bankagent.interpret.stub import StubProvider
from bankagent.store.serving import ServingDB

NOW = datetime(2026, 6, 17, 12, tzinfo=UTC)
REPEATS = 2  # a second repeat fails on a shared ops store: one dispute per transaction


def _clock() -> datetime:
    return NOW


@pytest.fixture(scope="module")
def serving(tmp_path_factory: pytest.TempPathFactory) -> ServingDB:
    path = tmp_path_factory.mktemp("proposed-bank") / "bank_fixture.duckdb"
    _, problems = build(out=path)
    assert not problems
    return ServingDB(path)


@pytest.fixture(scope="module")
def runs(serving: ServingDB, tmp_path_factory: pytest.TempPathFactory) -> list[ScoredRun]:
    stores = tmp_path_factory.mktemp("proposed-stores")
    # require_policy=True: a write whose context carries no policy decision is refused.
    config = RunConfig(bank=fresh_bank(serving, stores, require_policy=True), clock=_clock)
    traces = run_suite(
        [proposed_system()],
        load_cases(),
        suite_id="dev-proposed",
        repeats=REPEATS,
        budget_usd_per_system=Decimal("0"),
        config=config,
    )
    bank = load_bank()
    return [score(trace, bank) for trace in traces]


def _by_case(runs: list[ScoredRun], case_id: str) -> list[ScoredRun]:
    found = [run for run in runs if run.result.case_id == case_id]
    assert len(found) == REPEATS
    return found


def test_every_dev_case_is_correct_safe_and_fully_classified(runs: list[ScoredRun]) -> None:
    assert len(runs) == 10 * REPEATS
    problems = [
        f"{run.result.case_id} r{run.result.repeat_index}: {run.result.final_outcome.value}, "
        f"correct={run.result.correct}, unsafe={[e.value for e in run.result.unsafe_events]}, "
        f"unclassified={run.unclassified_questions}, ended_by={run.trace.ended_by}, "
        f"error={run.trace.error}"
        for run in runs
        if not run.result.correct
        or run.result.unsafe_events
        or run.unclassified_questions
        or run.trace.error
        or run.result.final_outcome not in run.trace.case.acceptable_outcomes
    ]
    assert problems == []
    assert all(run.result.system == SystemVariant.PROPOSED for run in runs)
    assert all(run.result.cost_usd_total == 0 for run in runs)


def test_the_accepted_block_offer_ends_with_both_writes_verified(runs: list[ScoredRun]) -> None:
    for run in _by_case(runs, "dev-normal-pt-br-001"):
        assert run.result.final_outcome == Outcome.AUTOMATED_RESOLUTION
        assert run.result.verified_actions == (ActionType.CREATE_DISPUTE, ActionType.BLOCK_CARD)
        assert run.result.safe_automated_resolution


def test_the_declined_block_offer_leaves_only_the_dispute(runs: list[ScoredRun]) -> None:
    for run in _by_case(runs, "dev-normal-es-mx-001"):
        assert run.result.verified_actions == (ActionType.CREATE_DISPUTE,)
        assert run.result.actions_taken == (ActionType.CREATE_DISPUTE,)


def test_the_ambiguous_case_disputes_the_charge_the_customer_chose(
    runs: list[ScoredRun],
) -> None:
    for run in _by_case(runs, "dev-ambiguous-es-mx-001"):
        assert run.result.final_outcome == Outcome.AUTOMATED_RESOLUTION
        disputed = [
            rid
            for obs in run.trace.observations
            if obs.tool == ToolName.CREATE_DISPUTE
            for rid in obs.resource_ids
            if rid.startswith("TXN-")
        ]
        assert disputed == ["TXN-FX-0105"]


def test_the_proposed_system_needs_a_bank_under_its_tools() -> None:
    # Without RunConfig.bank there is no store for the policy inputs or the tokens: a loud
    # configuration error, never a run that quietly abstains on every dispute.
    with pytest.raises(RuntimeError, match=r"RunConfig\.bank"):
        run_case(
            proposed_system(),
            load_cases()[0],
            run_id="no-bank",
            repeat_index=0,
            config=RunConfig(clock=_clock),
            spend=SpendGuard(limit_usd=Decimal("0")),
        )


def test_every_case_run_gets_its_own_empty_ops_store(serving: ServingDB, tmp_path: Path) -> None:
    opened = fresh_bank(serving, tmp_path, require_policy=True)
    with opened() as first:
        assert first.store.count("disputes") == 0
        assert set(first.tools) == set(ToolName)
    with opened() as second:
        assert second.store is not first.store
    assert len(list(tmp_path.glob("ops-*.sqlite"))) == 2


def _session() -> Session:
    return Session(
        session_id="ses-proposed-wiring",
        customer_id="CUST-FX-002",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def test_the_policy_and_the_tokens_use_the_runs_own_store_and_clock(
    serving: ServingDB, tmp_path: Path
) -> None:
    with fresh_bank(serving, tmp_path, require_policy=True)() as bank:
        env = EvalEnvironment(
            case_id="wiring",
            session=_session(),
            llm=StubProvider(),
            tools=bank.tools,
            observer=ToolObserver(),
            clock=_clock,
            serving=bank.serving,
            store=bank.store,
        )
        conversation = proposed_system().open_session(env)
        conversation.respond("Me cobraron dos veces 85.900 COP en Rappi")
        conversation.respond("Sí fui yo, pero el segundo cobro está duplicado")
        done = conversation.respond("Sí, confirmo")
        assert done.claimed_actions == (ActionType.CREATE_DISPUTE,)
        # The token was saved on, and spent from, the store under the tools.
        assert bank.store.count("confirmation_tokens") == 1
        dispute = bank.store.get_dispute("CUST-FX-002", transaction_id="TXN-FX-0202")
        assert dispute is not None
        # The SLA counts from the run's clock (2026-06-17), not from the wall clock.
        assert dispute.sla_due_date == date(2026, 7, 17)


@pytest.mark.parametrize("require_policy", [True, False])
def test_fresh_bank_builds_the_tools_with_the_requested_policy_lock(
    require_policy: bool, serving: ServingDB, tmp_path: Path
) -> None:
    args = CreateDisputeArgs(
        transaction_id="TXN-FX-0202", reason=DisputeReason.DUPLICATE, idempotency_key="idem-0001"
    )
    context = ToolContext(session=_session(), trace_id="trace-wiring", now=NOW)
    with fresh_bank(serving, tmp_path, require_policy=require_policy)() as bank:
        # With the lock a write without a decision is refused before the token is even looked at.
        refusal = InvalidArguments if require_policy else ConfirmationRequired
        with pytest.raises(refusal):
            bank.tools[ToolName.CREATE_DISPUTE].run(context, args)
        assert bank.store.count("disputes") == 0
