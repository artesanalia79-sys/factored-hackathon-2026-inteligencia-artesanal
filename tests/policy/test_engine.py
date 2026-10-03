"""The policy engine on hand-built inputs: every rule, at and one day past each boundary.

These tests build a throwaway `PolicyConfig` so the boundary assertions do not depend on the
exact (synthetic, pending verification) numbers in the committed `dispute_policy_v1.yaml`.
`test_inputs.py` runs the real file on the real fixture bank.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from bankagent.contracts.domain import CardView, TransactionRiskSignals, TransactionView
from bankagent.contracts.enums import (
    ActionType,
    CardType,
    Channel,
    DecisionType,
    ProductStatus,
    TransactionStatus,
    TransactionType,
)
from bankagent.policy.engine import PolicyInputs, evaluate
from bankagent.policy.schema import (
    ApprovedStatusRule,
    CardBlockableRule,
    DqFlagRule,
    FraudScoreRule,
    NoOpenCaseRule,
    PolicyConfig,
    Provenance,
    RepeatDisputerRule,
    WindowRule,
)

AS_OF = date(2026, 6, 17)
TXN, CARD = "TXN-TEST-0001", "CARD-TEST-01"
E1, E2, W1 = "DSP-E-01", "DSP-E-02", "DSP-W-01"
F1, R1, Q1, A1 = "DSP-F-01", "DSP-R-01", "DSP-Q-01", "DSP-A-01"
ALL_RULES = (E1, E2, W1, F1, R1, Q1, A1)


def _txn(
    *,
    status: TransactionStatus = TransactionStatus.APPROVED,
    ts: datetime = datetime(2026, 6, 1, tzinfo=UTC),
) -> TransactionView:
    return TransactionView(
        transaction_id=TXN,
        product_id=CARD,
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


def _card(*, product_id: str = CARD, status: ProductStatus = ProductStatus.ACTIVE) -> CardView:
    return CardView(
        product_id=product_id,
        card_type=CardType.CREDIT,
        card_last4="1234",
        currency="ARS",
        product_status=status,
    )


def _config() -> PolicyConfig:
    p = Provenance(source="test")
    return PolicyConfig(
        policy_version="test-v1",
        sla_days=30,
        rules=(
            ApprovedStatusRule(
                rule_id=E1, description="d", explanation_key="k.approved", provenance=p
            ),
            NoOpenCaseRule(rule_id=E2, description="d", explanation_key="k.open", provenance=p),
            WindowRule(
                rule_id=W1,
                description="d",
                explanation_key="k.window",
                window_days={"AR": 90, "MX": 90, "CO": 90},
                provenance=p,
            ),
            FraudScoreRule(
                rule_id=F1, description="d", explanation_key="k.fraud", threshold=30.0, provenance=p
            ),
            RepeatDisputerRule(
                rule_id=R1,
                description="d",
                explanation_key="k.repeat",
                window_days=90,
                provenance=p,
            ),
            DqFlagRule(
                rule_id=Q1,
                description="d",
                explanation_key="k.dq",
                flags=("dq_txn_before_product_opening",),
                provenance=p,
            ),
            CardBlockableRule(
                rule_id=A1,
                description="d",
                explanation_key="k.card",
                statuses=(ProductStatus.ACTIVE,),
                provenance=p,
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
        "card": _card(),
        "last_history_dispute_date": None,
        "last_agent_dispute_date": None,
    }
    base.update(overrides)
    return PolicyInputs(**base)  # type: ignore[arg-type]


def _risk(*, score: float | None = None, flags: tuple[str, ...] = ()) -> TransactionRiskSignals:
    return TransactionRiskSignals(transaction_id=TXN, fraud_score=score, dq_flags=flags)


# -- proceed --------------------------------------------------------------------------------


def test_a_clean_case_proceeds_with_both_writes_and_names_every_rule_it_checked() -> None:
    decision = evaluate(_config(), _inputs())
    assert decision.decision == DecisionType.PROCEED
    assert decision.allowed_actions == (ActionType.CREATE_DISPUTE, ActionType.BLOCK_CARD)
    assert decision.rule_ids == ALL_RULES
    assert decision.explanation_keys == ()
    assert decision.requires_confirmation is True
    assert decision.sla_due_date == AS_OF + timedelta(days=30)
    assert decision.policy_version == "test-v1"
    assert (decision.target_transaction_id, decision.target_product_id) == (TXN, CARD)


def test_the_sla_is_counted_from_filed_on_not_from_as_of_date() -> None:
    filed_on = AS_OF + timedelta(days=120)
    decision = evaluate(_config(), _inputs(filed_on=filed_on))
    assert decision.sla_due_date == filed_on + timedelta(days=30)


def test_a_card_that_is_not_active_still_gets_a_dispute_but_no_block() -> None:
    for status in (ProductStatus.BLOCKED, ProductStatus.CLOSED, ProductStatus.SUSPENDED):
        decision = evaluate(_config(), _inputs(card=_card(status=status)))
        assert decision.decision == DecisionType.PROCEED
        assert decision.allowed_actions == (ActionType.CREATE_DISPUTE,)
        assert decision.explanation_keys == ("k.card",)
        assert A1 in decision.rule_ids


def test_no_block_without_the_transactions_own_card() -> None:
    for card in (None, _card(product_id="CARD-OTHER-01")):
        decision = evaluate(_config(), _inputs(card=card))
        assert decision.allowed_actions == (ActionType.CREATE_DISPUTE,)


# -- eligibility ----------------------------------------------------------------------------


def test_every_non_approved_status_is_ineligible() -> None:
    for status in (
        TransactionStatus.DECLINED,
        TransactionStatus.PENDING,
        TransactionStatus.REVERSED,
    ):
        decision = evaluate(_config(), _inputs(transaction=_txn(status=status)))
        assert decision.decision == DecisionType.INELIGIBLE
        assert decision.rule_ids == (E1,)
        assert decision.explanation_keys == ("k.approved",)
        assert decision.allowed_actions == ()
        assert decision.policy_version == "test-v1"
        assert (decision.target_transaction_id, decision.target_product_id) == (TXN, CARD)


def test_an_open_case_is_ineligible_even_though_status_and_window_pass() -> None:
    decision = evaluate(_config(), _inputs(open_dispute_id="CMP-EXISTING"))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == (E2,)
    assert decision.explanation_keys == ("k.open",)


def test_a_transaction_exactly_at_the_window_boundary_still_proceeds() -> None:
    ts = datetime.combine(AS_OF - timedelta(days=90), datetime.min.time(), tzinfo=UTC)
    assert evaluate(_config(), _inputs(transaction=_txn(ts=ts))).decision == DecisionType.PROCEED


def test_a_transaction_one_day_past_the_window_is_ineligible() -> None:
    ts = datetime.combine(AS_OF - timedelta(days=91), datetime.min.time(), tzinfo=UTC)
    decision = evaluate(_config(), _inputs(transaction=_txn(ts=ts)))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == (W1,)
    assert decision.explanation_keys == ("k.window",)


def test_the_window_compares_to_as_of_date_not_to_the_filing_day() -> None:
    ts = datetime.combine(AS_OF - timedelta(days=10), datetime.min.time(), tzinfo=UTC)
    late_filing = _inputs(transaction=_txn(ts=ts), filed_on=AS_OF + timedelta(days=200))
    assert evaluate(_config(), late_filing).decision == DecisionType.PROCEED


def test_a_country_with_no_window_fails_closed() -> None:
    decision = evaluate(_config(), _inputs(customer_country="BR"))
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == (W1,)


def test_eligibility_short_circuits_before_any_escalation_check() -> None:
    decision = evaluate(
        _config(),
        _inputs(transaction=_txn(status=TransactionStatus.DECLINED), risk=_risk(score=99.0)),
    )
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == (E1,)


# -- escalation -----------------------------------------------------------------------------


def test_fraud_score_at_the_threshold_escalates() -> None:
    decision = evaluate(_config(), _inputs(risk=_risk(score=30.0)))
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == (F1,)
    assert decision.explanation_keys == ("k.fraud",)
    assert decision.escalation_triggers == ("fraud_score",)
    assert decision.allowed_actions == ()
    assert decision.policy_version == "test-v1"
    assert (decision.target_transaction_id, decision.target_product_id) == (TXN, CARD)


def test_fraud_score_just_below_the_threshold_proceeds() -> None:
    assert evaluate(_config(), _inputs(risk=_risk(score=29.99))).decision == DecisionType.PROCEED


def test_a_missing_or_null_fraud_score_does_not_escalate() -> None:
    for risk in (None, _risk(score=None)):
        assert evaluate(_config(), _inputs(risk=risk)).decision == DecisionType.PROCEED


def test_a_history_dispute_exactly_at_the_window_boundary_escalates() -> None:
    decision = evaluate(_config(), _inputs(last_history_dispute_date=AS_OF - timedelta(days=90)))
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == (R1,)
    assert decision.explanation_keys == ("k.repeat",)


def test_a_history_dispute_one_day_past_the_window_proceeds() -> None:
    old = _inputs(last_history_dispute_date=AS_OF - timedelta(days=91))
    assert evaluate(_config(), old).decision == DecisionType.PROCEED


def test_a_history_dispute_is_counted_from_as_of_date_not_from_the_filing_day() -> None:
    # The snapshot's history is as old as the snapshot, not as old as today: a dispute 10 days
    # before as_of_date still counts when the customer files 200 days after the build.
    inputs = _inputs(
        filed_on=AS_OF + timedelta(days=200), last_history_dispute_date=AS_OF - timedelta(days=10)
    )
    assert evaluate(_config(), inputs).decision == DecisionType.ESCALATE


def test_a_history_dispute_newer_than_the_snapshot_still_counts() -> None:
    newer = _inputs(last_history_dispute_date=AS_OF + timedelta(days=1))
    assert evaluate(_config(), newer).decision == DecisionType.ESCALATE


def test_an_agent_dispute_is_counted_from_the_filing_day_at_both_boundaries() -> None:
    filed_on = AS_OF + timedelta(days=400)
    at_limit = _inputs(filed_on=filed_on, last_agent_dispute_date=filed_on - timedelta(days=90))
    past = _inputs(filed_on=filed_on, last_agent_dispute_date=filed_on - timedelta(days=91))
    assert evaluate(_config(), at_limit).decision == DecisionType.ESCALATE
    assert evaluate(_config(), past).decision == DecisionType.PROCEED


def test_an_agent_dispute_a_year_old_does_not_count_just_because_the_snapshot_is_older() -> None:
    # PR #44 review: compared to the frozen as_of_date, every agent dispute filed after it had a
    # negative age and counted forever.
    inputs = _inputs(filed_on=date(2028, 6, 1), last_agent_dispute_date=date(2027, 6, 1))
    assert evaluate(_config(), inputs).decision == DecisionType.PROCEED


def test_a_listed_dq_flag_escalates_and_an_unlisted_one_does_not() -> None:
    flagged = evaluate(_config(), _inputs(risk=_risk(flags=("dq_txn_before_product_opening",))))
    assert flagged.decision == DecisionType.ESCALATE
    assert flagged.rule_ids == (Q1,)
    assert flagged.explanation_keys == ("k.dq",)
    assert flagged.escalation_triggers == ("dq_flag",)
    duplicate = _inputs(risk=_risk(flags=("dq_possible_duplicate", "dq_txn_after_card_expiry")))
    assert evaluate(_config(), duplicate).decision == DecisionType.PROCEED


def test_every_escalation_trigger_combines_in_rule_order() -> None:
    risk = _risk(score=50.0, flags=("dq_txn_before_product_opening",))
    inputs = _inputs(risk=risk, last_agent_dispute_date=AS_OF)
    decision = evaluate(_config(), inputs)
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == (F1, R1, Q1)
    assert decision.explanation_keys == ("k.fraud", "k.repeat", "k.dq")
    assert decision.escalation_triggers == ("fraud_score", "repeat_disputer", "dq_flag")
