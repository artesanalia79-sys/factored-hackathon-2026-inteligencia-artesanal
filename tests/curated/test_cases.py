"""Which rows the curated run picks, and what it expects of each (T19).

The expectation is worked out without the policy engine (`bankagent.curated.cases.expect`). Here,
on synthetic rows, it is compared with the engine on every row and at every limit, so the two
can only disagree where a test says so.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from bankagent.contracts.enums import ActionType, DecisionType, Language, Specialty
from bankagent.contracts.tools import SearchTransactionsArgs
from bankagent.curated.cases import (
    FAMILIES,
    Charge,
    Expected,
    Scenario,
    expect,
    pick_cases,
    pool,
)
from bankagent.orchestrator.agent import FRAUD_TRIGGER
from bankagent.policy import build_inputs, evaluate
from bankagent.policy.schema import PolicyConfig
from bankagent.store.serving import ServingDB

EVERY_RULE = (
    "DSP-ELIG-01",
    "DSP-ELIG-02",
    "DSP-WIN-01",
    "DSP-ESC-01",
    "DSP-ESC-02",
    "DSP-ESC-03",
    "DSP-ACT-01",
)
AS_OF = date(2026, 6, 17)


def _charge(**changes: object) -> Charge:
    """An eligible charge with nothing to escalate; ``changes`` move it across one limit."""
    base = Charge(
        transaction_id="TRX-1",
        customer_id="CLI-1",
        product_id="PRD-1",
        country="CO",
        language=Language.ES,
        status="Approved",
        transaction_ts=datetime(2026, 6, 10, 15, 0),
        transaction_type="Purchase",
        amount=Decimal("100.50"),
        currency="COP",
        merchant_name="Tienda",
        card_last4="4001",
        card_status="Active",
        fraud_score=4.5,
        dq_flags=(),
        last_dispute_at=None,
        open_case=False,
        same_amount=1,
        position=1,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _days_ago(days: int) -> datetime:
    return datetime(2026, 6, 17, 23, 59) - timedelta(days=days)


def test_an_eligible_charge_proceeds_with_every_rule_checked(policy: PolicyConfig) -> None:
    assert expect(_charge(), policy, AS_OF) == Expected(
        DecisionType.PROCEED, EVERY_RULE, block_offered=True
    )


@pytest.mark.parametrize("status", ["Pending", "Declined", "Reversed", "Bogus"])
def test_only_a_settled_charge_is_eligible(policy: PolicyConfig, status: str) -> None:
    expected = expect(_charge(status=status, fraud_score=99.0), policy, AS_OF)
    # Eligibility is decided first: the fraud score of an unsettled charge escalates nothing.
    assert expected == Expected(
        DecisionType.INELIGIBLE, ("DSP-ELIG-01",), explanation_key="dispute.not_settled"
    )


def test_an_open_case_refuses_whether_the_bank_or_the_agent_opened_it(
    policy: PolicyConfig,
) -> None:
    refused = Expected(
        DecisionType.INELIGIBLE, ("DSP-ELIG-02",), explanation_key="dispute.already_disputed"
    )
    assert expect(_charge(open_case=True), policy, AS_OF) == refused
    assert expect(_charge(), policy, AS_OF, already_disputed=True) == refused


@pytest.mark.parametrize(("days", "eligible"), [(89, True), (90, True), (91, False), (-1, True)])
def test_the_window_counts_days_from_as_of_date(
    policy: PolicyConfig, days: int, eligible: bool
) -> None:
    expected = expect(_charge(transaction_ts=_days_ago(days)), policy, AS_OF)
    if eligible:
        assert expected.decision == DecisionType.PROCEED
    else:
        assert expected == Expected(
            DecisionType.INELIGIBLE, ("DSP-WIN-01",), explanation_key="dispute.out_of_window"
        )


def test_a_country_with_no_window_fails_closed(policy: PolicyConfig) -> None:
    assert expect(_charge(country="BR"), policy, AS_OF).rule_ids == ("DSP-WIN-01",)


@pytest.mark.parametrize(
    ("score", "escalates"),
    [(None, False), (0.0, False), (29.99, False), (30.0, True), (88.0, True)],
)
def test_the_fraud_score_escalates_from_the_threshold_and_a_missing_one_never(
    policy: PolicyConfig, score: float | None, escalates: bool
) -> None:
    expected = expect(_charge(fraud_score=score), policy, AS_OF)
    if escalates:
        assert expected == Expected(
            DecisionType.ESCALATE, ("DSP-ESC-01",), specialty=Specialty.FRAUD
        )
    else:
        assert expected.decision == DecisionType.PROCEED


@pytest.mark.parametrize(("days", "escalates"), [(0, True), (90, True), (91, False), (-1, True)])
def test_a_recent_dispute_in_the_history_escalates_to_the_disputes_team(
    policy: PolicyConfig, days: int, escalates: bool
) -> None:
    expected = expect(_charge(last_dispute_at=_days_ago(days)), policy, AS_OF)
    if escalates:
        assert expected == Expected(
            DecisionType.ESCALATE, ("DSP-ESC-02",), specialty=Specialty.DISPUTES
        )
    else:
        assert expected.decision == DecisionType.PROCEED


def test_only_the_flag_the_policy_names_escalates(policy: PolicyConfig) -> None:
    ignored = ("dq_txn_after_card_expiry", "dq_possible_duplicate", "dq_missing_merchant")
    assert expect(_charge(dq_flags=ignored), policy, AS_OF).decision == DecisionType.PROCEED
    flagged = expect(_charge(dq_flags=(*ignored, "dq_txn_before_product_opening")), policy, AS_OF)
    assert flagged == Expected(DecisionType.ESCALATE, ("DSP-ESC-03",), specialty=Specialty.DISPUTES)


def test_every_trigger_that_fires_is_named_in_the_file_order_and_fraud_routes(
    policy: PolicyConfig,
) -> None:
    charge = _charge(
        fraud_score=55.0,
        last_dispute_at=_days_ago(3),
        dq_flags=("dq_txn_before_product_opening",),
    )
    assert expect(charge, policy, AS_OF) == Expected(
        DecisionType.ESCALATE,
        ("DSP-ESC-01", "DSP-ESC-02", "DSP-ESC-03"),
        specialty=Specialty.FRAUD,
    )


@pytest.mark.parametrize("status", ["Blocked", "Closed", "Suspended"])
def test_a_card_that_is_not_active_gets_the_dispute_and_no_block_offer(
    policy: PolicyConfig, status: str
) -> None:
    assert expect(_charge(card_status=status), policy, AS_OF) == Expected(
        DecisionType.PROCEED, EVERY_RULE, block_offered=False
    )


def test_the_expectation_agrees_with_the_policy_engine_on_every_row(
    bank: Path, policy: PolicyConfig, as_of: date
) -> None:
    # Two readings of the same policy file: this module's (own SQL, own Python) and the
    # engine's (`build_inputs` + `evaluate`). On every row of every family they must agree.
    serving = ServingDB(bank)
    seen: set[str] = set()
    with duckdb.connect(str(bank), read_only=True) as con:
        charges = [
            charge
            for family in FAMILIES
            for charge in pool(con, policy, as_of, family, seed="any", limit=1000)
        ]
    for charge in charges:
        seen.add(charge.transaction_id)
        inputs = build_inputs(
            serving, None, charge.customer_id, charge.transaction_id, filed_on=as_of
        )
        assert inputs is not None
        decision = evaluate(policy, inputs)
        expected = expect(charge, policy, as_of)
        assert expected.decision == decision.decision, charge.transaction_id
        assert expected.rule_ids == decision.rule_ids, charge.transaction_id
        assert expected.block_offered == (ActionType.BLOCK_CARD in decision.allowed_actions)
        if decision.decision == DecisionType.INELIGIBLE:
            assert (expected.explanation_key,) == decision.explanation_keys
        if decision.decision == DecisionType.ESCALATE:
            fraud = FRAUD_TRIGGER in decision.escalation_triggers
            assert expected.specialty == (Specialty.FRAUD if fraud else Specialty.DISPUTES)
    # Every transaction of an active customer is in some family, so none was skipped.
    with duckdb.connect(str(bank), read_only=True) as con:
        active = con.execute(
            "SELECT count(*) FROM transactions_enriched AS t JOIN customer_profile_min AS p "
            "ON p.customer_id = t.customer_id WHERE p.customer_status = 'Active'"
        ).fetchone()
    assert active is not None
    assert len(seen) == active[0]
    assert {expect(c, policy, as_of).decision for c in charges} == {
        DecisionType.PROCEED,
        DecisionType.ESCALATE,
        DecisionType.INELIGIBLE,
    }


def _picked(
    bank: Path, policy: PolicyConfig, as_of: date, people: dict[str, str], **options: object
) -> dict[Scenario, list[str]]:
    """Scenario -> the situations of the customers picked for it (`conftest.write_bank`)."""
    names = {customer_id: name for name, customer_id in people.items()}
    cases = pick_cases(bank, policy, as_of, **{"per_scenario": 2, "seed": "19", **options})  # type: ignore[arg-type]
    picked: dict[Scenario, list[str]] = {}
    for case in cases:
        picked.setdefault(case.scenario, []).append(names[case.customer_id])
    return picked


def test_each_scenario_picks_the_customers_written_for_it(
    bank: Path, policy: PolicyConfig, as_of: date, people: dict[str, str]
) -> None:
    picked = _picked(bank, policy, as_of, people)
    assert set(picked) == set(Scenario)
    assert len(picked[Scenario.ESCALATE_FRAUD]) == 2
    assert set(picked[Scenario.ESCALATE_FRAUD]) <= {
        "fraud",
        "fraud_on_threshold",
        "fraud_and_repeat",
    }
    assert set(picked[Scenario.ESCALATE_REPEAT]) == {"repeat", "repeat_on_limit"}
    assert picked[Scenario.ESCALATE_DATA] == ["flagged"]
    assert set(picked[Scenario.CHOOSE_AMONG_MATCHES]) == {"twins", "triplets"}
    assert picked[Scenario.DISPUTE_CARD_NOT_ACTIVE] == ["card_blocked"]
    assert set(picked[Scenario.INELIGIBLE_WINDOW]) == {"old", "very_old"}
    assert set(picked[Scenario.INELIGIBLE_STATUS]) <= {"pending", "declined", "reversed"}
    # A charge is disputed only when the policy lets it proceed with a block to offer: the
    # rows on the allowed side of each limit, never the escalating or refused ones.
    eligible = {
        "plain_mx",
        "plain_co",
        "plain_ar",
        "plain_ar_usd",
        "no_score",
        "window_edge",
        "below_threshold",
        "old_claim",
        "other_claim",
        "tomorrow",
        "closed_complaint",
    }
    for scenario in (
        Scenario.DISPUTE_BLOCK_DECLINED,
        Scenario.DISPUTE_BLOCK_ACCEPTED,
        Scenario.ALREADY_DISPUTED,
    ):
        assert len(picked[scenario]) == 2
        assert set(picked[scenario]) <= eligible


def test_a_customer_the_login_refuses_is_never_picked(
    bank: Path, policy: PolicyConfig, as_of: date, people: dict[str, str]
) -> None:
    cases = pick_cases(bank, policy, as_of, per_scenario=50, seed="19")
    involved = {case.customer_id for case in cases} | {case.charge.customer_id for case in cases}
    for refused in ("inactive", "suspended", "closed", "no_charges"):
        assert people[refused] not in involved


def test_a_fraud_signal_is_never_shown_as_a_repeat_disputer_case(
    bank: Path, policy: PolicyConfig, as_of: date, people: dict[str, str]
) -> None:
    # One customer has both triggers. Alone, with room for every row, the repeat scenario still
    # takes only the two customers the history alone escalates.
    picked = _picked(
        bank, policy, as_of, people, per_scenario=10, scenarios=[Scenario.ESCALATE_REPEAT]
    )
    assert set(picked[Scenario.ESCALATE_REPEAT]) == {"repeat", "repeat_on_limit"}
    fraud = _picked(
        bank, policy, as_of, people, per_scenario=10, scenarios=[Scenario.ESCALATE_FRAUD]
    )
    assert set(fraud[Scenario.ESCALATE_FRAUD]) == {
        "fraud",
        "fraud_on_threshold",
        "fraud_and_repeat",
    }


def test_every_customer_is_used_once_and_the_picks_repeat(
    bank: Path, policy: PolicyConfig, as_of: date
) -> None:
    first = pick_cases(bank, policy, as_of, per_scenario=3, seed="19")
    customers = [case.customer_id for case in first]
    assert len(set(customers)) == len(customers)
    assert pick_cases(bank, policy, as_of, per_scenario=3, seed="19") == first
    # Scenario by scenario, numbered from 1.
    order = [case.scenario for case in first]
    assert order == sorted(order, key=list(Scenario).index)
    for scenario in set(order):
        numbers = [case.case_id for case in first if case.scenario == scenario]
        assert numbers == [f"{scenario.value}-{n:02d}" for n in range(1, len(numbers) + 1)]


def test_nobody_is_both_the_owner_of_a_cited_charge_and_a_session_customer(
    bank: Path, policy: PolicyConfig, as_of: date
) -> None:
    cases = pick_cases(
        bank,
        policy,
        as_of,
        per_scenario=5,
        seed="19",
        scenarios=[Scenario.OTHER_CUSTOMERS_CHARGE],
    )
    assert len(cases) == 5
    sessions = {case.customer_id for case in cases}
    owners = {case.charge.customer_id for case in cases}
    assert len(sessions) == len(owners) == 5
    assert sessions.isdisjoint(owners)


def test_a_customer_who_paid_the_same_amount_cannot_stand_in_for_another_customer(
    tmp_path: Path,
    policy: PolicyConfig,
    as_of: date,
    make_bank: Callable[..., dict[str, str]],
) -> None:
    # Only two customers have eligible charges, and of the same amount: citing the other's
    # charge would find one's own, so there is no such case to run.
    path = tmp_path / "bank_curated.duckdb"
    people = make_bank(path)
    with duckdb.connect(str(path)) as con:
        con.execute(
            "DELETE FROM transactions_enriched WHERE customer_id NOT IN (?, ?)",
            [people["plain_mx"], people["no_score"]],
        )
        con.execute("UPDATE transactions_enriched SET amount = 500.25")
    asked = [Scenario.OTHER_CUSTOMERS_CHARGE]
    assert pick_cases(path, policy, as_of, per_scenario=5, seed="19", scenarios=asked) == []
    # With different amounts the same two customers make one case.
    with duckdb.connect(str(path)) as con:
        con.execute(
            "UPDATE transactions_enriched SET amount = 600.75 WHERE customer_id = ?",
            [people["no_score"]],
        )
    (case,) = pick_cases(path, policy, as_of, per_scenario=5, seed="19", scenarios=asked)
    assert {case.customer_id, case.charge.customer_id} == {people["plain_mx"], people["no_score"]}


def test_the_seed_changes_who_is_picked_not_what_fits(
    bank: Path, policy: PolicyConfig, as_of: date
) -> None:
    orders = {
        tuple(
            case.customer_id
            for case in pick_cases(
                bank, policy, as_of, per_scenario=1, seed=seed, scenarios=[Scenario.RECOGNIZED]
            )
        )
        for seed in ("1", "2", "3", "4", "5", "6", "7", "8")
    }
    assert len(orders) > 1


def test_only_the_scenarios_asked_for_are_picked(
    bank: Path, policy: PolicyConfig, as_of: date
) -> None:
    asked = [Scenario.RECOGNIZED, Scenario.ESCALATE_FRAUD]
    cases = pick_cases(bank, policy, as_of, per_scenario=1, seed="19", scenarios=asked)
    assert [case.scenario for case in cases] == [Scenario.ESCALATE_FRAUD, Scenario.RECOGNIZED]
    with pytest.raises(ValueError, match="at least 1"):
        pick_cases(bank, policy, as_of, per_scenario=0, seed="19")


def test_another_customers_charge_has_an_amount_the_session_customer_never_paid(
    bank: Path, policy: PolicyConfig, as_of: date
) -> None:
    cases = pick_cases(
        bank,
        policy,
        as_of,
        per_scenario=5,
        seed="19",
        scenarios=[Scenario.OTHER_CUSTOMERS_CHARGE],
    )
    assert cases
    serving = ServingDB(bank)
    for case in cases:
        assert case.customer_id != case.charge.customer_id
        assert case.expected == Expected(None)
        amount = case.charge.amount
        same = serving.search_transactions(
            case.customer_id, SearchTransactionsArgs(amount_min=amount, amount_max=amount), limit=5
        )
        assert same == []


def test_position_is_the_place_in_the_list_the_agent_shows(
    bank: Path, policy: PolicyConfig, as_of: date
) -> None:
    cases = pick_cases(
        bank, policy, as_of, per_scenario=5, seed="19", scenarios=[Scenario.CHOOSE_AMONG_MATCHES]
    )
    assert {case.charge.same_amount for case in cases} == {2, 3}
    serving = ServingDB(bank)
    for case in cases:
        amount = case.charge.amount
        listed = serving.search_transactions(
            case.customer_id, SearchTransactionsArgs(amount_min=amount, amount_max=amount), limit=10
        )
        assert len(listed) == case.charge.same_amount
        assert listed[case.charge.position - 1].transaction_id == case.charge.transaction_id
