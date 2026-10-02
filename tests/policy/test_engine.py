"""The policy engine: pure boundary-day unit tests plus end-to-end fixture-bank scenarios.

Unit tests build a throwaway `PolicyConfig` so the boundary assertions do not depend on the
exact (synthetic, pending verification) numbers in the committed `dispute_policy_v1.yaml`.
Fixture-bank tests load the real file and exercise the real `ServingDB` reads, one per rule.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from bankagent.contracts.domain import DisputeCase, TransactionRiskSignals, TransactionView
from bankagent.contracts.enums import (
    Channel,
    DecisionType,
    DisputeReason,
    DisputeStatus,
    TransactionStatus,
    TransactionType,
)
from bankagent.policy.engine import PolicyInputs, evaluate
from bankagent.policy.schema import (
    ApprovedStatusRule,
    FraudScoreRule,
    NoOpenCaseRule,
    PolicyConfig,
    Provenance,
    RepeatDisputerRule,
    WindowRule,
    load_policy,
)
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB

AS_OF = date(2026, 6, 17)
MARIANA, CARLOS, VALENTINA, DIEGO, ANDRES = (
    "CUST-FX-001",
    "CUST-FX-006",
    "CUST-FX-007",
    "CUST-FX-008",
    "CUST-FX-002",
)


def _txn(
    *,
    status: TransactionStatus = TransactionStatus.APPROVED,
    ts: datetime = datetime(2026, 6, 1, tzinfo=UTC),
) -> TransactionView:
    return TransactionView(
        transaction_id="TXN-TEST-0001",
        product_id="CARD-TEST-01",
        card_last4="1234",
        transaction_ts=ts,
        transaction_type=TransactionType.PURCHASE,
        transaction_category="Other",
        amount=Decimal("100.00"),
        currency="ARS",
        amount_usd=None,
        channel=Channel.WEB,
        merchant_name="TEST MERCHANT",
        merchant_category="Other",
        transaction_country="AR",
        transaction_city=None,
        transaction_status=status,
        is_foreign=False,
    )


def _config() -> PolicyConfig:
    provenance = Provenance(source="test")
    return PolicyConfig(
        policy_version="test-v1",
        sla_days=30,
        rules=(
            ApprovedStatusRule(
                rule_id="E1", description="d", explanation_key="k.approved", provenance=provenance
            ),
            NoOpenCaseRule(
                rule_id="E2", description="d", explanation_key="k.open", provenance=provenance
            ),
            WindowRule(
                rule_id="W1",
                description="d",
                explanation_key="k.window",
                window_days={"AR": 90, "MX": 90, "CO": 90},
                provenance=provenance,
            ),
            FraudScoreRule(
                rule_id="F1",
                description="d",
                explanation_key="k.fraud",
                threshold=30.0,
                provenance=provenance,
            ),
            RepeatDisputerRule(
                rule_id="R1",
                description="d",
                explanation_key="k.repeat",
                window_days=90,
                provenance=provenance,
            ),
        ),
    )


def _inputs(**overrides: object) -> PolicyInputs:
    base: dict[str, object] = {
        "transaction": _txn(),
        "customer_country": "AR",
        "as_of_date": AS_OF,
        "filed_on": AS_OF,
        "open_dispute_id": None,
        "risk": None,
        "last_claim_date": None,
    }
    base.update(overrides)
    return PolicyInputs(**base)  # type: ignore[arg-type]


# -- eligibility (pure, boundary) ---------------------------------------------------------


def test_a_clean_case_proceeds() -> None:
    decision = evaluate(_config(), _inputs())
    assert decision.decision == DecisionType.PROCEED
    assert decision.rule_ids == ("E1", "E2", "W1")
    assert decision.requires_confirmation is True
    assert decision.sla_due_date == AS_OF + timedelta(days=30)
    assert decision.target_transaction_id == "TXN-TEST-0001"


def test_the_sla_is_counted_from_filed_on_not_from_as_of_date() -> None:
    # as_of_date (the frozen snapshot) and filed_on (today) can be far apart; only filed_on
    # drives the SLA, or every dispute filed today would get an SLA stamped on a stale date.
    filed_on = AS_OF + timedelta(days=120)
    decision = evaluate(_config(), _inputs(filed_on=filed_on))
    assert decision.decision == DecisionType.PROCEED
    assert decision.sla_due_date == filed_on + timedelta(days=30)
    assert decision.sla_due_date != AS_OF + timedelta(days=30)


def test_target_transaction_id_is_set_even_when_ineligible_or_escalated() -> None:
    ineligible = evaluate(_config(), _inputs(transaction=_txn(status=TransactionStatus.DECLINED)))
    assert ineligible.target_transaction_id == "TXN-TEST-0001"
    risk = TransactionRiskSignals(transaction_id="TXN-TEST-0001", fraud_score=99.0)
    escalated = evaluate(_config(), _inputs(risk=risk))
    assert escalated.target_transaction_id == "TXN-TEST-0001"


def test_a_non_approved_transaction_is_ineligible() -> None:
    decision = evaluate(_config(), _inputs(transaction=_txn(status=TransactionStatus.DECLINED)))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("E1",)
    assert decision.allowed_actions == ()


def test_an_open_case_is_ineligible_even_though_status_and_window_pass() -> None:
    decision = evaluate(_config(), _inputs(open_dispute_id="CMP-EXISTING"))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("E2",)


def test_a_transaction_exactly_at_the_window_boundary_still_proceeds() -> None:
    ts = datetime.combine(AS_OF - timedelta(days=90), datetime.min.time(), tzinfo=UTC)
    decision = evaluate(_config(), _inputs(transaction=_txn(ts=ts)))
    assert decision.decision == DecisionType.PROCEED


def test_a_transaction_one_day_past_the_window_is_ineligible() -> None:
    ts = datetime.combine(AS_OF - timedelta(days=91), datetime.min.time(), tzinfo=UTC)
    decision = evaluate(_config(), _inputs(transaction=_txn(ts=ts)))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("W1",)


def test_a_country_with_no_verified_window_fails_closed() -> None:
    decision = evaluate(_config(), _inputs(customer_country="BR"))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("W1",)


def test_eligibility_short_circuits_before_any_escalation_check() -> None:
    decision = evaluate(
        _config(),
        _inputs(
            transaction=_txn(status=TransactionStatus.DECLINED),
            risk=TransactionRiskSignals(transaction_id="TXN-TEST-0001", fraud_score=99.0),
        ),
    )
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("E1",)


# -- escalation (pure, boundary) -----------------------------------------------------------


def test_fraud_score_at_the_threshold_escalates() -> None:
    risk = TransactionRiskSignals(transaction_id="TXN-TEST-0001", fraud_score=30.0)
    decision = evaluate(_config(), _inputs(risk=risk))
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("F1",)
    assert decision.escalation_triggers == ("fraud_score",)
    assert decision.allowed_actions == ()


def test_fraud_score_just_below_the_threshold_proceeds() -> None:
    risk = TransactionRiskSignals(transaction_id="TXN-TEST-0001", fraud_score=29.99)
    decision = evaluate(_config(), _inputs(risk=risk))
    assert decision.decision == DecisionType.PROCEED


def test_a_claim_exactly_at_the_repeat_disputer_boundary_escalates() -> None:
    decision = evaluate(_config(), _inputs(last_claim_date=AS_OF - timedelta(days=90)))
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("R1",)


def test_a_claim_one_day_past_the_repeat_disputer_window_proceeds() -> None:
    decision = evaluate(_config(), _inputs(last_claim_date=AS_OF - timedelta(days=91)))
    assert decision.decision == DecisionType.PROCEED


def test_both_escalation_triggers_combine_in_rule_order() -> None:
    risk = TransactionRiskSignals(transaction_id="TXN-TEST-0001", fraud_score=50.0)
    decision = evaluate(_config(), _inputs(risk=risk, last_claim_date=AS_OF - timedelta(days=1)))
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("F1", "R1")


# -- end to end on the real policy and the real fixture bank ------------------------------


def _real_inputs(
    serving: ServingDB,
    customer_id: str,
    transaction_id: str,
    *,
    store: OpsStore | None = None,
    filed_on: date | None = None,
) -> PolicyInputs:
    transaction = serving.transaction(customer_id, transaction_id)
    assert transaction is not None
    customer = serving.customer(customer_id)
    assert customer is not None
    history_claim = serving.last_claim_date(customer_id)
    agent_claim = store.last_dispute_date(customer_id) if store else None
    last_claim_date = max((d for d in (history_claim, agent_claim) if d is not None), default=None)
    return PolicyInputs(
        transaction=transaction,
        customer_country=customer.country,
        as_of_date=serving.as_of_date(),
        filed_on=filed_on or serving.as_of_date(),
        open_dispute_id=serving.open_complaint_id(customer_id, transaction_id),
        risk=serving.risk_signals(customer_id, transaction_id),
        last_claim_date=last_claim_date,
    )


def test_a_normal_unrecognized_charge_proceeds(serving: ServingDB) -> None:
    config = load_policy()
    decision = evaluate(config, _real_inputs(serving, MARIANA, "TXN-FX-0101"))
    assert decision.decision == DecisionType.PROCEED
    assert decision.sla_due_date == serving.as_of_date() + timedelta(days=config.sla_days)


def test_a_declined_transaction_is_ineligible(serving: ServingDB) -> None:
    decision = evaluate(load_policy(), _real_inputs(serving, DIEGO, "TXN-FX-0803"))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("DSP-ELIG-01",)


def test_a_pending_transaction_is_ineligible(serving: ServingDB) -> None:
    decision = evaluate(load_policy(), _real_inputs(serving, DIEGO, "TXN-FX-0804"))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("DSP-ELIG-01",)


def test_an_old_transaction_outside_the_window_is_ineligible(serving: ServingDB) -> None:
    decision = evaluate(load_policy(), _real_inputs(serving, DIEGO, "TXN-FX-0801"))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("DSP-WIN-01",)


def test_a_transaction_with_an_open_prior_complaint_is_ineligible(serving: ServingDB) -> None:
    decision = evaluate(load_policy(), _real_inputs(serving, VALENTINA, "TXN-FX-0701"))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("DSP-ELIG-02",)


def test_a_repeat_disputer_is_escalated_on_an_unrelated_clean_transaction(
    serving: ServingDB,
) -> None:
    decision = evaluate(load_policy(), _real_inputs(serving, VALENTINA, "TXN-FX-0702"))
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("DSP-ESC-02",)


def test_a_high_fraud_score_is_escalated(serving: ServingDB) -> None:
    decision = evaluate(load_policy(), _real_inputs(serving, CARLOS, "TXN-FX-0601"))
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("DSP-ESC-01",)


def test_the_real_sla_is_counted_from_filed_on_not_the_fixtures_as_of_date(
    serving: ServingDB,
) -> None:
    filed_on = serving.as_of_date() + timedelta(days=200)
    decision = evaluate(
        load_policy(), _real_inputs(serving, MARIANA, "TXN-FX-0101", filed_on=filed_on)
    )
    assert decision.decision == DecisionType.PROCEED
    assert decision.sla_due_date == filed_on + timedelta(days=30)


def test_a_customer_with_no_history_but_a_recent_agent_dispute_is_a_repeat_disputer(
    serving: ServingDB, ops_store: OpsStore
) -> None:
    # CUST-FX-002 (Andrés) has no dispute_history row at all: only the agent's own prior
    # dispute (T9 PR #44 review) should make the second one a repeat disputer.
    as_of = serving.as_of_date()
    first = DisputeCase(
        dispute_id="DSP-TEST-0001",
        transaction_id="TXN-FX-0201",
        reason=DisputeReason.UNRECOGNIZED,
        status=DisputeStatus.SUBMITTED,
        created_at=datetime.combine(as_of, datetime.min.time(), tzinfo=UTC),
        amount=Decimal("859.00"),
        currency="COP",
        idempotency_key="idem-dispute-0099",
        policy_version="test-v1",
    )
    ops_store.insert_dispute(ANDRES, first)

    without_agent_history = _real_inputs(serving, ANDRES, "TXN-FX-0202")
    assert without_agent_history.last_claim_date is None

    with_agent_history = _real_inputs(serving, ANDRES, "TXN-FX-0202", store=ops_store)
    decision = evaluate(load_policy(), with_agent_history)
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("DSP-ESC-02",)
