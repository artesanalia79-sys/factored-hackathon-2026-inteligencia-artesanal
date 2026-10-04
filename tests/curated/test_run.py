"""The curated end-to-end run on a synthetic curated-like bank (T19).

Two halves. The run passes on a sound agent, for every scenario and at every limit of the
policy. And the run fails, naming the check, when the agent is broken on purpose: a run that
cannot fail proves nothing about the organizer data it is meant for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from bankagent.auth.service import AuthService
from bankagent.contracts.decisions import PolicyDecision
from bankagent.contracts.domain import DisputeCase, Session
from bankagent.contracts.enums import (
    ActionType,
    ConversationState,
    DecisionType,
    Language,
    StepKind,
    StepOutcome,
    ToolName,
)
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.serving import SERVING_TABLES
from bankagent.curated.cases import CuratedCase, Expected, Scenario, expect, pick_cases, pool
from bankagent.curated.run import (
    ROW_COUNT_SQL,
    CaseResult,
    Conversation,
    CuratedRun,
    Reply,
    Step,
    Turn,
    check_records,
    check_replies,
    classify,
    run_cases,
)
from bankagent.interpret.keywords import MODEL_NAME
from bankagent.orchestrator.agent import Agent
from bankagent.policy import engine
from bankagent.policy.schema import PolicyConfig
from bankagent.render.templates import render_ineligible
from bankagent.store import serving as serving_module
from bankagent.store.ops import OpsStore
from bankagent.store.selection import ServingConfigError
from bankagent.store.serving import ServingDB
from bankagent.tools.base import BaseTool
from bankagent.tools.writes import CreateDispute


def _one(bank: Path, policy: PolicyConfig, as_of: date, scenario: Scenario) -> CuratedCase:
    (case,) = pick_cases(bank, policy, as_of, per_scenario=1, seed="19", scenarios=[scenario])
    return case


def _run(bank: Path, tmp_path: Path, policy: PolicyConfig, case: CuratedCase) -> CaseResult:
    with CuratedRun(bank, tmp_path / "run", policy) as run:
        return run.run_case(case)


# --- a sound agent passes ------------------------------------------------------------------


def test_every_scenario_passes_and_the_ops_store_holds_exactly_what_was_confirmed(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date
) -> None:
    cases = pick_cases(bank, policy, as_of, per_scenario=2, seed="19")
    assert {case.scenario for case in cases} == set(Scenario)
    result = run_cases(bank, tmp_path / "run", policy, cases)
    assert [(case.case.case_id, case.failures) for case in result.cases if not case.passed] == []
    assert result.probes == ()
    assert result.contract_problems == ()
    assert result.row_check.problems == ()
    assert result.passed
    assert result.metadata["data_mode"] == "curated"
    assert result.rows["transactions_enriched"] == 32

    kinds = {case.case.case_id: [turn.kind for turn in case.turns] for case in result.cases}
    assert kinds["dispute_card_not_active-01"] == [
        Reply.RECOGNIZE,
        Reply.CONFIRM_DISPUTE,
        Reply.DISPUTED,
    ]
    assert kinds["already_disputed-01"][-2:] == [Reply.RECOGNIZE, Reply.INELIGIBLE]
    assert kinds["other_customers_charge-01"] == [Reply.CLARIFY, Reply.CLARIFY, Reply.ABSTAINED]
    # Three charges of one amount: the list, the position, then the chosen charge's own path.
    choose = next(case for case in result.cases if case.case.charge.same_amount == 3)
    assert [turn.kind for turn in choose.turns][:2] == [Reply.CHOOSE, Reply.RECOGNIZE]

    def count(kind: Reply) -> int:
        return sum(turn.kind == kind for case in result.cases for turn in case.turns)

    disputes = count(Reply.DISPUTED) + count(Reply.DISPUTED_OFFER_BLOCK)
    with OpsStore(tmp_path / "run" / "ops.sqlite") as store:
        assert store.count("disputes") == disputes
        assert store.count("card_blocks") == count(Reply.BLOCKED) == 2
        assert store.count("handoffs") == count(Reply.ESCALATED) == 5
        assert store.count("confirmation_tokens") == disputes + 2
    assert disputes == 9  # 2 + 2 + 2 dispute scenarios, 1 blocked card, 2 chosen among matches


def test_the_steps_of_each_turn_are_recorded_for_the_report(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date
) -> None:
    result = _run(
        bank, tmp_path, policy, _one(bank, policy, as_of, Scenario.DISPUTE_BLOCK_ACCEPTED)
    )
    assert result.failures == []
    assert [turn.steps for turn in result.turns] == [
        (
            "authenticate success",
            "interpret success",
            "search_transactions success verified",
            "get_transaction success verified",
        ),
        (
            "authenticate success",
            "interpret success",
            "policy success DSP-ELIG-01+DSP-ELIG-02+DSP-WIN-01+DSP-ESC-01+DSP-ESC-02+DSP-ESC-03"
            "+DSP-ACT-01",
        ),
        (
            "authenticate success",
            "interpret success",
            "confirmation success",
            "create_dispute success verified",
            "verify success",
            "list_cards success verified",
        ),
        (
            "authenticate success",
            "interpret success",
            "confirmation success",
            "block_card success verified",
            "verify success",
        ),
    ]
    assert [turn.claimed for turn in result.turns] == [(), (), ("create_dispute",), ("block_card",)]


def test_a_charge_with_an_open_prior_complaint_is_refused_with_that_reason(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date, people: dict[str, str]
) -> None:
    # The bank's own open case (dispute_history), not a dispute the agent filed.
    with duckdb.connect(str(bank), read_only=True) as con:
        charge = next(
            charge
            for charge in pool(con, policy, as_of, "recent", seed="19", limit=100)
            if charge.customer_id == people["open_complaint"]
        )
    expected = expect(charge, policy, as_of)
    assert expected.rule_ids == ("DSP-ELIG-02",)
    case = CuratedCase(
        "open_complaint-01",
        Scenario.INELIGIBLE_STATUS,
        charge.customer_id,
        Language.ES,
        charge,
        expected,
    )
    result = _run(bank, tmp_path, policy, case)
    assert result.failures == []
    assert result.turns[-1].reply == render_ineligible("dispute.already_disputed", Language.ES)


def test_the_uis_question_finds_the_charge_only_when_it_carries_the_cents(
    bank: Path, tmp_path: Path, policy: PolicyConfig, people: dict[str, str]
) -> None:
    # What the web UI sends for "ask about this movement" (`transactionsQuestion` in
    # web/src/i18n.ts): the reference, which the keyword rules cannot read in the organizer
    # data's form, and the amount as the UI prints it. Before T19 it printed Colombian pesos
    # without cents, so 85,900.50 was asked about as 85.901 and never found.
    question = (
        "Tengo un problema con el cargo de la transacción TRX-T1900011 en Tienda del Sur, "
        "por {amount}\u00a0COP, del 12 jun 2026."
    )
    with CuratedRun(bank, tmp_path / "run", policy) as run:
        for amount, kind in (("85.900,50", Reply.RECOGNIZE), ("85.901", Reply.CLARIFY)):
            _, headers = run._open_session(people["card_blocked"], Language.ES)
            reply = run._client.post(
                "/api/chat/turn", headers=headers, json={"text": question.format(amount=amount)}
            )
            assert classify(reply.json()) == kind


def test_the_served_tables_counted_are_the_contracts(bank: Path) -> None:
    assert set(ROW_COUNT_SQL) == {table.name for table in SERVING_TABLES} - {"_serving_metadata"}


# --- the run refuses what it must not run on ------------------------------------------------


def test_the_run_never_starts_on_a_file_that_is_not_curated(
    tmp_path: Path, policy: PolicyConfig, make_bank: Callable[..., dict[str, str]]
) -> None:
    personas = tmp_path / "bank.duckdb"
    make_bank(personas, data_mode="synthetic")
    with pytest.raises(ServingConfigError, match="records 'synthetic'"):
        CuratedRun(personas, tmp_path / "run", policy)


def test_every_run_needs_a_new_ops_store(bank: Path, tmp_path: Path, policy: PolicyConfig) -> None:
    with CuratedRun(bank, tmp_path / "run", policy):
        pass
    with pytest.raises(FileExistsError, match="new, empty ops store"):
        CuratedRun(bank, tmp_path / "run", policy)


@pytest.mark.parametrize("refused", ["inactive", "suspended", "closed"])
def test_no_session_is_opened_for_a_customer_the_login_refuses(
    bank: Path,
    tmp_path: Path,
    policy: PolicyConfig,
    as_of: date,
    people: dict[str, str],
    refused: str,
) -> None:
    case = replace(_one(bank, policy, as_of, Scenario.RECOGNIZED), customer_id=people[refused])
    with (
        CuratedRun(bank, tmp_path / "run", policy) as run,
        pytest.raises(ValueError, match="active customer"),
    ):
        run.run_case(case)
    with OpsStore(tmp_path / "run" / "ops.sqlite") as store:
        assert store.count("sessions") == 0


def test_a_run_with_no_case_does_not_pass(bank: Path, tmp_path: Path, policy: PolicyConfig) -> None:
    result = run_cases(bank, tmp_path / "run", policy, [])
    assert result.probes == ("no case to run",)
    assert not result.passed


# --- a broken agent fails the run, and the failure names the check --------------------------


def _always_proceed(config: PolicyConfig, inputs: engine.PolicyInputs) -> PolicyDecision:
    """A policy that lets every dispute through: no rule refuses, no rule escalates."""
    decision = engine.evaluate(config, inputs)
    if decision.decision == DecisionType.PROCEED:
        return decision
    return PolicyDecision(
        decision=DecisionType.PROCEED,
        rule_ids=decision.rule_ids,
        allowed_actions=(ActionType.CREATE_DISPUTE,),
        requires_confirmation=True,
        sla_due_date=inputs.filed_on,
        target_transaction_id=decision.target_transaction_id,
        target_product_id=decision.target_product_id,
        policy_version=config.policy_version,
    )


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        (Scenario.ESCALATE_FRAUD, "escalated"),
        (Scenario.ESCALATE_REPEAT, "escalated"),
        (Scenario.ESCALATE_DATA, "escalated"),
        (Scenario.INELIGIBLE_WINDOW, "ineligible"),
        (Scenario.INELIGIBLE_STATUS, "ineligible"),
    ],
)
def test_a_policy_that_lets_everything_through_fails_the_run(
    bank: Path,
    tmp_path: Path,
    policy: PolicyConfig,
    as_of: date,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Scenario,
    expected: str,
) -> None:
    monkeypatch.setattr("bankagent.orchestrator.wiring.evaluate", _always_proceed)
    result = _run(bank, tmp_path, policy, _one(bank, policy, as_of, scenario))
    assert f"turn 2: expected {expected}, got confirm_dispute" in result.failures
    assert "the policy step has another outcome than expected" in result.failures


def test_a_dispute_filed_twice_fails_the_run(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bankagent.orchestrator.wiring.evaluate", _always_proceed)
    result = _run(bank, tmp_path, policy, _one(bank, policy, as_of, Scenario.ALREADY_DISPUTED))
    assert "session 2: turn 2: expected ineligible, got confirm_dispute" in result.failures


def test_reads_that_are_not_scoped_to_the_customer_fail_the_run(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    scoped = "WHERE t.customer_id = $customer_id"
    assert scoped in serving_module._TRANSACTIONS_SQL
    monkeypatch.setattr(
        serving_module,
        "_TRANSACTIONS_SQL",
        serving_module._TRANSACTIONS_SQL.replace(scoped, "WHERE $customer_id IS NOT NULL"),
    )
    case = _one(bank, policy, as_of, Scenario.OTHER_CUSTOMERS_CHARGE)
    result = _run(bank, tmp_path, policy, case)
    # The other customer's charge is found and shown, and the list of movements is not theirs.
    assert "the transaction list is not the customer's own newest transactions" in result.failures
    assert "turn 1: expected clarify, got recognize" in result.failures
    assert "turn 1: the reply shows an internal id or a foreign fact" in result.failures
    assert "turn 1: the reply shows a card to another customer" in result.failures


def test_a_write_whose_read_back_fails_is_not_a_dispute(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only the dispute's read-back fails: the agent claims nothing and hands the confirmed
    # request to a person, whose handoff is verified.
    monkeypatch.setattr(CreateDispute, "_read_back", lambda self, read, expected: False)
    result = _run(
        bank, tmp_path, policy, _one(bank, policy, as_of, Scenario.DISPUTE_BLOCK_DECLINED)
    )
    assert result.failures == [
        "turn 3: expected disputed_offer_block, got escalated",
        "a write is not verified",
        "the handoff steps are not the ones expected",
        "the ops store gained another number of handoffs than expected",
    ]
    assert result.turns[-1].claimed == ("create_handoff",)


def test_when_no_read_back_works_nothing_is_claimed_and_the_run_says_so(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(BaseTool, "_read_back", lambda self, read, expected: False)
    result = _run(
        bank, tmp_path, policy, _one(bank, policy, as_of, Scenario.DISPUTE_BLOCK_DECLINED)
    )
    assert "turn 3: expected disputed_offer_block, got other" in result.failures
    assert "a write is not verified" in result.failures
    assert [turn.claimed for turn in result.turns] == [(), (), ()]


def test_a_fraud_escalation_sent_to_the_wrong_team_fails_the_run(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bankagent.orchestrator.agent.FRAUD_TRIGGER", "no-such-trigger")
    result = _run(bank, tmp_path, policy, _one(bank, policy, as_of, Scenario.ESCALATE_FRAUD))
    assert result.failures == ["the stored handoff is not the one the decision asks for"]


@pytest.mark.parametrize(
    "change", [{"sla_days": 31}, {"policy_version": "dispute-v0.9"}], ids=["sla", "version"]
)
def test_a_dispute_stamped_with_another_policy_fails_the_run(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date, change: dict[str, Any]
) -> None:
    # The run is told a policy the app does not use, so what the app stamps cannot match.
    told = policy.model_copy(update=change)
    result = _run(bank, tmp_path, told, _one(bank, told, as_of, Scenario.DISPUTE_BLOCK_DECLINED))
    assert result.failures == ["the stored dispute is not the one the customer confirmed"]


class _OtherAmount:
    """The run's view of the ops store, with every dispute read back one cent off."""

    def __init__(self, store: OpsStore) -> None:
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def get_dispute(self, customer_id: str, **by: Any) -> DisputeCase | None:
        dispute = self._store.get_dispute(customer_id, **by)
        if dispute is None:
            return None
        return dispute.model_copy(update={"amount": dispute.amount + Decimal("0.01")})


def test_a_stored_dispute_with_another_amount_fails_the_run(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date
) -> None:
    case = _one(bank, policy, as_of, Scenario.DISPUTE_BLOCK_DECLINED)
    with CuratedRun(bank, tmp_path / "run", policy) as run:
        run._store = _OtherAmount(run._store)  # type: ignore[assignment]
        result = run.run_case(case)
    assert result.failures == ["the stored dispute is not the one the customer confirmed"]


def test_tokens_that_are_not_checked_fail_the_service_checks(
    bank: Path,
    tmp_path: Path,
    policy: PolicyConfig,
    as_of: date,
    people: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    anyone = Session(
        session_id="ses-anyone",
        customer_id=people["plain_mx"],
        issued_at=now,
        expires_at=now.replace(year=now.year + 1),
    )
    monkeypatch.setattr(AuthService, "authenticate", lambda self, token: anyone)
    with CuratedRun(bank, tmp_path / "run", policy) as run:
        failed = run.probes(people["plain_co"])
    assert failed == [
        "a signed token of a session that was never stored was accepted",
        "a token signed with another key was accepted",
    ]


def test_an_error_inside_the_app_is_a_failed_check_not_the_end_of_the_run(
    bank: Path, tmp_path: Path, policy: PolicyConfig, as_of: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _one(bank, policy, as_of, Scenario.RECOGNIZED)
    search = ServingDB.search_transactions

    def list_fails(self: ServingDB, customer_id: str, filters: Any, *, limit: int) -> Any:
        if limit == 50:  # the list of movements; the tool asks for 11
            raise RuntimeError("the list broke")
        return search(self, customer_id, filters, limit=limit)

    monkeypatch.setattr(ServingDB, "search_transactions", list_fails)
    with CuratedRun(bank, tmp_path / "list", policy) as run:
        assert run.run_case(case).failures == ["the customer's transaction list did not answer"]

    monkeypatch.setattr(ServingDB, "search_transactions", search)
    monkeypatch.setattr(Agent, "handle_turn", lambda *_, **__: 1 / 0)
    with CuratedRun(bank, tmp_path / "turn", policy) as run:
        result = run.run_case(case)
    assert result.failures[0] == "turn 1: HTTP 500 instead of a reply"
    assert result.turns == []


# --- the record and reply checks, one broken thing at a time --------------------------------

SESSION = "ses-1"
ALL_RULES = ("DSP-ELIG-01", "DSP-ACT-01")
HASH = "a" * 64
OTHER_HASH = "b" * 64


def _record(turn: int, step: int, kind: StepKind, **fields: Any) -> ExecutionRecord:
    return ExecutionRecord(
        record_id=f"rec-{turn}-{step}",
        trace_id="trace-1",
        session_id=SESSION,
        turn_index=turn,
        step_index=step,
        step=kind,
        state=ConversationState.UNDERSTAND,
        outcome=StepOutcome.SUCCESS,
        latency_ms=1.0,
        created_at=datetime(2026, 10, 4, tzinfo=UTC),
        **fields,
    )


def _tool(turn: int, step: int, tool: ToolName, **fields: Any) -> ExecutionRecord:
    return _record(turn, step, StepKind.TOOL_CALL, tool=tool, **{"verified": True, **fields})


def _opening(turn: int) -> list[ExecutionRecord]:
    return [
        _record(turn, 0, StepKind.AUTHENTICATE),
        _record(turn, 1, StepKind.INTERPRET, model=MODEL_NAME),
    ]


def _sound() -> list[ExecutionRecord]:
    """The records of a dispute with the card block declined, as a sound agent stores them."""
    return [
        *_opening(0),
        _tool(0, 2, ToolName.SEARCH_TRANSACTIONS),
        _tool(0, 3, ToolName.GET_TRANSACTION),
        *_opening(1),
        _record(1, 2, StepKind.POLICY, rule_ids=ALL_RULES),
        *_opening(2),
        _record(2, 2, StepKind.CONFIRMATION, args_hash=HASH),
        _tool(2, 3, ToolName.CREATE_DISPUTE, args_hash=HASH),
        _record(2, 4, StepKind.VERIFY),
        _tool(2, 5, ToolName.LIST_CARDS),
        *_opening(3),
        _record(3, 2, StepKind.RENDER),
    ]


CONVERSATION = Conversation(
    steps=(
        Step("a", "a", Reply.RECOGNIZE),
        Step("b", "b", Reply.CONFIRM_DISPUTE),
        Step("c", "c", Reply.DISPUTED_OFFER_BLOCK, confirms=True),
        Step("d", "d", Reply.BLOCK_DECLINED),
    ),
    expected=Expected(DecisionType.PROCEED, ALL_RULES, block_offered=True),
    disputes=True,
)


def _turns(claimed_at_2: tuple[str, ...] = ("create_dispute",)) -> list[Turn]:
    kinds = [step.expects for step in CONVERSATION.steps]
    return [
        Turn(1, "x", "x", "reply", kind, kind, claimed_at_2 if index == 2 else (), index == 3)
        for index, kind in enumerate(kinds)
    ]


def _change(
    records: list[ExecutionRecord], turn: int, step: int, **fields: Any
) -> list[ExecutionRecord]:
    return [
        record.model_copy(update=fields)
        if (record.turn_index, record.step_index) == (turn, step)
        else record
        for record in records
    ]


def _without(records: list[ExecutionRecord], turn: int, step: int) -> list[ExecutionRecord]:
    return [r for r in records if (r.turn_index, r.step_index) != (turn, step)]


def test_the_records_of_a_sound_conversation_raise_nothing() -> None:
    assert check_records(SESSION, CONVERSATION, _turns(), _sound()) == []


@pytest.mark.parametrize(
    ("records", "failure"),
    [
        (_without(_sound(), 2, 2), "a write has no confirmation of its exact arguments"),
        (
            _change(_sound(), 2, 2, args_hash=OTHER_HASH),
            "a write has no confirmation of its exact arguments",
        ),
        (
            # The confirmation is recorded after the write it should have allowed.
            _change(_sound(), 2, 2, step_index=9),
            "a write has no confirmation of its exact arguments",
        ),
        (
            # Confirmed in an earlier turn, written in a later one.
            _change(_sound(), 2, 2, turn_index=1, step_index=3),
            "a write has no confirmation of its exact arguments",
        ),
        (
            _change(_sound(), 2, 3, verified=False),
            "a write is not verified",
        ),
        (
            _change(_sound(), 2, 3, outcome=StepOutcome.BLOCKED, verified=False),
            "a write is not verified",
        ),
        (
            [*_sound(), _tool(3, 3, ToolName.BLOCK_CARD, args_hash=OTHER_HASH)],
            "a write ran in a turn where the customer did not confirm",
        ),
        (
            [*_sound(), _tool(3, 3, ToolName.BLOCK_CARD, args_hash=OTHER_HASH)],
            "2 write steps instead of 1",
        ),
        (_without(_sound(), 2, 3), "0 write steps instead of 1"),
        (_without(_sound(), 2, 3), "turn 3: a claim has no verified write behind it"),
        (
            _change(_sound(), 2, 3, verified=False),
            "turn 3: a claim has no verified write behind it",
        ),
        (
            _change(_sound(), 1, 2, rule_ids=("DSP-ELIG-01",)),
            "the policy step names other rules than expected",
        ),
        (
            _change(_sound(), 1, 2, outcome=StepOutcome.BLOCKED),
            "the policy step has another outcome than expected",
        ),
        (_without(_sound(), 1, 2), "0 policy steps instead of one"),
        (
            [*_sound(), _record(3, 3, StepKind.POLICY, rule_ids=ALL_RULES)],
            "2 policy steps instead of one",
        ),
        (_change(_sound(), 0, 1, outcome=StepOutcome.FALLBACK), "the interpreter fell back"),
        (
            _change(_sound(), 3, 1, model="gpt-6-luna"),
            "another interpreter than the keyword rules answered",
        ),
        (_change(_sound(), 1, 1, cost_usd=Decimal("0.0001")), "a step cost money"),
        (_change(_sound(), 0, 3, verified=False), "a read is not verified"),
        (_change(_sound(), 2, 5, verified=False), "a read is not verified"),
        (_change(_sound(), 2, 4, outcome=StepOutcome.FAILURE), "a step failed"),
        (
            _change(_sound(), 0, 0, session_id="ses-2"),
            "a record of another session was stored during the conversation",
        ),
        (
            _change(_sound(), 3, 2, trace_id="trace-2"),
            "the conversation has more than one trace",
        ),
        (
            [record for record in _sound() if record.turn_index != 3],
            "the stored records do not cover every turn",
        ),
        (
            [*_sound(), _tool(3, 3, ToolName.CREATE_HANDOFF)],
            "the handoff steps are not the ones expected",
        ),
    ],
)
def test_each_broken_record_is_named(records: list[ExecutionRecord], failure: str) -> None:
    assert failure in check_records(SESSION, CONVERSATION, _turns(), records)


def test_a_policy_step_where_none_is_due_is_named() -> None:
    recognized = Conversation(
        steps=(Step("a", "a", Reply.RECOGNIZE), Step("b", "b", Reply.DEFLECTED)),
        expected=Expected(None),
    )
    records = [
        *_opening(0),
        _tool(0, 2, ToolName.SEARCH_TRANSACTIONS),
        *_opening(1),
        _record(1, 2, StepKind.POLICY, rule_ids=ALL_RULES),
    ]
    turns = [
        Turn(1, "x", "x", "r", Reply.RECOGNIZE, Reply.RECOGNIZE, (), False),
        Turn(1, "x", "x", "r", Reply.DEFLECTED, Reply.DEFLECTED, (), True),
    ]
    assert check_records(SESSION, recognized, turns, records) == [
        "a policy step ran where none was due"
    ]
    assert check_records(SESSION, recognized, turns, records[:-1]) == []


def test_a_claim_needs_its_own_turn_and_its_own_tool() -> None:
    # The dispute is verified in turn 3. A card block claimed there has no step behind it, and
    # neither has the dispute when it is claimed a turn late.
    both = _turns(("create_dispute", "block_card"))
    assert check_records(SESSION, CONVERSATION, both, _sound()) == [
        "turn 3: a claim has no verified write behind it"
    ]
    late = [
        replace(turn, claimed=("create_dispute",) if index == 3 else ())
        for index, turn in enumerate(_turns())
    ]
    assert check_records(SESSION, CONVERSATION, late, _sound()) == [
        "turn 4: a claim has no verified write behind it"
    ]


def test_a_reply_never_shows_an_id_or_another_customers_charge(
    bank: Path, policy: PolicyConfig, as_of: date
) -> None:
    own = _one(bank, policy, as_of, Scenario.RECOGNIZED)
    conversation = Conversation((Step("a", "a", Reply.RECOGNIZE),), own.expected)

    def failures(case: CuratedCase, reply: str) -> list[str]:
        turn = Turn(1, "x", "x", reply, Reply.RECOGNIZE, Reply.RECOGNIZE, (), False)
        return check_replies(case, conversation, [turn])

    charge = own.charge
    amount = f"{charge.amount:,.2f}"
    assert failures(own, f"comercio {charge.merchant_name}, importe {amount}") == []
    for shown in (own.customer_id, charge.transaction_id, charge.product_id):
        assert failures(own, f"Tu caso {shown}.") == [
            "turn 1: the reply shows an internal id or a foreign fact"
        ]

    foreign = _one(bank, policy, as_of, Scenario.OTHER_CUSTOMERS_CHARGE)
    theirs = foreign.charge
    assert failures(foreign, "Necesito un dato más.") == []
    for shown in (
        f"{theirs.amount:,.2f}",
        str(theirs.merchant_name),
        theirs.transaction_id,
        theirs.customer_id,
    ):
        assert "turn 1: the reply shows an internal id or a foreign fact" in failures(
            foreign, f"Encontré: {shown}"
        )
    assert failures(foreign, "tarjeta terminada en 0000") == [
        "turn 1: the reply shows a card to another customer"
    ]


def test_a_refusal_must_give_the_reason_of_the_rule_that_failed(
    bank: Path, policy: PolicyConfig, as_of: date
) -> None:
    case = _one(bank, policy, as_of, Scenario.INELIGIBLE_WINDOW)
    assert case.expected.explanation_key == "dispute.out_of_window"
    conversation = Conversation((Step("a", "a", Reply.INELIGIBLE),), case.expected)

    def refusal(key: str) -> Turn:
        text = render_ineligible(key, Language.ES)
        return Turn(1, "x", "x", text, Reply.INELIGIBLE, Reply.INELIGIBLE, (), True)

    assert check_replies(case, conversation, [refusal("dispute.out_of_window")]) == []
    for other in ("dispute.not_settled", "dispute.already_disputed"):
        assert check_replies(case, conversation, [refusal(other)]) == [
            "the refusal gives another reason than the rule that failed"
        ]
