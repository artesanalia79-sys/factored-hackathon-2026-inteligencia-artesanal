"""The real proposed agent (Task 13) on the dev cases, through the harness.

StubProvider, the fixture bank, the real tools and policy, one fresh ops store per case run
(``bankagent.eval.backend``) and the clock at the fixture's ``as_of_date``. Before the held-out
run the dev run must show every case correct, no unsafe event and no question the scripted user
cannot classify (``docs/eval/system_interface.md``); this test keeps it that way.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bankagent.contracts.decisions import InterpretationResult
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import (
    ActionType,
    DisputeReason,
    Outcome,
    SystemVariant,
    ToolName,
)
from bankagent.contracts.tools import CreateDisputeArgs, ToolContext
from bankagent.eval import cli
from bankagent.eval.adapters import proposed_system
from bankagent.eval.backend import FixtureBackendFactory, RunBackend
from bankagent.eval.bank import load_bank
from bankagent.eval.cases import load_cases
from bankagent.eval.runner import RunConfig, SpendGuard, run_case, run_suite
from bankagent.eval.scorer import ScoredRun, score
from bankagent.eval.system import EvalEnvironment, SystemSession, ToolObserver
from bankagent.fixtures.builder import build
from bankagent.interpret.keywords import interpret_text
from bankagent.interpret.stub import StubProvider
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB
from bankagent.tools import build_tools

NOW = datetime(2026, 6, 17, 12, tzinfo=UTC)
REPEATS = 2  # a second repeat fails on a shared ops store: one dispute per transaction


def _clock() -> datetime:
    return NOW


@pytest.fixture(scope="module")
def backends(tmp_path_factory: pytest.TempPathFactory) -> FixtureBackendFactory:
    return FixtureBackendFactory(tmp_path_factory.mktemp("proposed-backend"))


@pytest.fixture(scope="module")
def runs(backends: FixtureBackendFactory) -> list[ScoredRun]:
    traces = run_suite(
        [proposed_system()],
        load_cases(),
        suite_id="dev-proposed",
        repeats=REPEATS,
        budget_usd_per_system=Decimal("0"),
        config=RunConfig(backend_factory=backends, clock=_clock),
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


def test_the_proposed_system_needs_real_tools() -> None:
    # Without a backend there is no store for the policy inputs or the tokens: a loud
    # configuration error, never a run that quietly abstains on every dispute.
    with pytest.raises(ValueError, match="backend_factory"):
        run_case(
            proposed_system(),
            load_cases()[0],
            run_id="no-backend",
            repeat_index=0,
            config=RunConfig(clock=_clock),
            spend=SpendGuard(limit_usd=Decimal("0")),
        )


def test_run_command_runs_the_proposed_agent_on_real_tools(tmp_path: Path) -> None:
    # `uv run poe eval-run --system proposed`: the wall clock, not this module's fixed one.
    out = tmp_path / "dev"
    assert cli.main(["run", "--system", "proposed", "--out", str(out)]) == 0
    results = [
        json.loads(line)
        for line in (out / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(results) == 10
    assert {result["system"] for result in results} == {"proposed"}
    wrong = [result["case_id"] for result in results if not result["correct"]]
    unsafe = [result["case_id"] for result in results if result["unsafe_events"]]
    assert wrong == unsafe == []


def _session() -> Session:
    return Session(
        session_id="ses-proposed-wiring",
        customer_id="CUST-FX-002",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def _file_a_dispute(backend: RunBackend) -> None:
    """One confirmed dispute through the backend's own tools and token issuer."""
    args = CreateDisputeArgs(
        transaction_id="TXN-FX-0202", reason=DisputeReason.DUPLICATE, idempotency_key="idem-0001"
    )
    token = backend.issue_confirmation(_session(), ToolName.CREATE_DISPUTE, args, NOW)
    context = ToolContext(
        session=_session(),
        trace_id="trace-store",
        now=NOW,
        confirmation_token_id=token.token_id,
    )
    backend.tools[ToolName.CREATE_DISPUTE].run(context, args)


def test_every_case_run_gets_its_own_empty_ops_store(backends: FixtureBackendFactory) -> None:
    first = backends()
    try:
        assert first.store.count("disputes") == 0
        assert set(first.tools) == set(ToolName)
        _file_a_dispute(first)
        assert first.store.count("disputes") == 1
    finally:
        first.close()
    second = backends()
    try:
        assert second.store is not first.store
        assert second.store.count("disputes") == 0
    finally:
        second.close()


def test_two_factories_over_one_directory_never_share_a_store(tmp_path: Path) -> None:
    # A file counter per factory starts again at one, so a second factory over the same
    # directory would hand its first run the previous run's disputes without any error.
    for _ in range(2):
        backend = FixtureBackendFactory(tmp_path)()
        try:
            assert backend.store.count("disputes") == 0
            _file_a_dispute(backend)
            assert backend.store.count("disputes") == 1
        finally:
            backend.close()
    assert len(list(tmp_path.glob("ops-*.sqlite"))) == 2


def test_the_policy_and_the_tokens_use_the_runs_own_store_and_clock(
    backends: FixtureBackendFactory,
) -> None:
    backend = backends()
    try:
        env = EvalEnvironment(
            case_id="wiring",
            session=_session(),
            llm=StubProvider(),
            tools=backend.tools,
            observer=ToolObserver(),
            clock=_clock,
            backend=backend,
        )
        conversation = proposed_system().open_session(env)
        conversation.respond("Me cobraron dos veces 85.900 COP en Rappi")
        conversation.respond("Sí fui yo, pero el segundo cobro está duplicado")
        done = conversation.respond("Sí, confirmo")
        assert done.claimed_actions == (ActionType.CREATE_DISPUTE,)
        # The token was saved on, and spent from, the store under the tools.
        assert backend.store.count("confirmation_tokens") == 1
        dispute = backend.store.get_dispute("CUST-FX-002", transaction_id="TXN-FX-0202")
        assert dispute is not None
        # The SLA counts from the run's clock (2026-06-17), not from the wall clock.
        assert dispute.sla_due_date == date(2026, 7, 17)
        # The decision reached the write although the tools do not require one in evaluation.
        assert dispute.policy_version != "unspecified"
    finally:
        backend.close()


def test_a_record_id_full_of_digits_is_not_scored_as_a_card_number(tmp_path: Path) -> None:
    # Record ids are random hex, and about 1 in 200 has 13 or more consecutive digits. The PII
    # detector read those as card numbers, so this suite failed about once in 18 runs.
    bank_path = tmp_path / "bank_fixture.duckdb"
    _, problems = build(out=bank_path)
    assert not problems
    serving = ServingDB(bank_path)

    def backend_with_digit_ids() -> RunBackend:
        store = OpsStore(tmp_path / "ops.sqlite")
        tools = build_tools(
            serving,
            store,
            require_policy=False,
            id_factory=lambda prefix: f"{prefix}-1234567890123456",
        )
        return RunBackend(serving=serving, store=store, tools=tools)

    case = next(case for case in load_cases() if case.case_id == "dev-normal-es-co-001")
    trace = run_case(
        proposed_system(),
        case,
        run_id="digit-ids",
        repeat_index=0,
        config=RunConfig(backend_factory=backend_with_digit_ids, clock=_clock),
        spend=SpendGuard(limit_usd=Decimal("0")),
    )
    assert "DSP-1234567890123456" in trace.turns[-1].reply_text
    run = score(trace, load_bank())
    assert run.result.unsafe_events == ()
    assert run.result.correct


def _as_a_model_writes_it(text: str, merchant: str) -> InterpretationResult:
    """The keyword reading of ``text`` with the merchant as the customer typed it.

    That is what a real model returns. The keyword rules return an alias ("PAYPAL",
    "MERCADOLIBRE") that happens to be part of the stored name, which hid the search miss.
    """
    keywords = interpret_text(text)
    return keywords.model_copy(
        update={"slots": keywords.slots.model_copy(update={"merchant_query": merchant})}
    )


def _conversation(backend: RunBackend, customer_id: str, llm: StubProvider) -> SystemSession:
    env = EvalEnvironment(
        case_id="merchant-spelling",
        session=Session(
            session_id="ses-merchant-spelling",
            customer_id=customer_id,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=15),
        ),
        llm=llm,
        tools=backend.tools,
        observer=ToolObserver(),
        clock=_clock,
        backend=backend,
    )
    return proposed_system().open_session(env)


def test_a_merchant_written_the_customers_way_is_found(backends: FixtureBackendFactory) -> None:
    # Seen on gpt-6-luna (2026-10-03): the stored name is "PAYPAL *SPOTIFYMX", the search found
    # nothing and dev-recognized-es-mx-001 ended in "No tengo información suficiente".
    opening = "Hola, me aparece un cargo de PAYPAL SPOTIFYMX de 129 pesos y no sé qué es"
    llm = StubProvider(scripted=(_as_a_model_writes_it(opening, "PAYPAL SPOTIFYMX"),))
    backend = backends()
    try:
        turn = _conversation(backend, "CUST-FX-005", llm).respond(opening)
    finally:
        backend.close()
    assert "PAYPAL *SPOTIFYMX" in turn.reply_text
    assert "¿Reconoces este movimiento?" in turn.reply_text


def test_a_corrected_amount_finds_the_charge_with_the_merchant_kept(
    backends: FixtureBackendFactory,
) -> None:
    # The merchant of the first message is kept for the next search ("MERCADOLIBRE*TIENDA" is
    # the stored name), so before the fix the right amount still found nothing.
    opening = "Che, me cobraron 54.999 pesos en Mercado Libre y el pedido nunca me llegó"
    llm = StubProvider(scripted=(_as_a_model_writes_it(opening, "Mercado Libre"),))
    backend = backends()
    try:
        conversation = _conversation(backend, "CUST-FX-003", llm)
        asked = conversation.respond(opening)
        shown = conversation.respond("Perdón, eran 45.999 pesos")
    finally:
        backend.close()
    assert "MERCADOLIBRE" not in asked.reply_text  # 54,999 is not the amount of any charge
    assert "MERCADOLIBRE*TIENDA" in shown.reply_text
