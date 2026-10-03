"""The real policy file on the real fixture bank, through `build_inputs` (what T13 calls).

One scenario per rule on the committed personas, the dev cases that reach the policy, and, on a
writable copy of the bank, the cases the personas do not cover (other complaint categories, a
card that is not Active, a dq-flagged charge). Also the scoping of every policy-only read.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

from bankagent.contracts.decisions import PolicyDecision
from bankagent.contracts.domain import DisputeCase
from bankagent.contracts.enums import ActionType, DecisionType, DisputeReason, DisputeStatus
from bankagent.policy import build_inputs, evaluate, load_policy
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB

MARIANA, ANDRES, RAFAEL = "CUST-FX-001", "CUST-FX-002", "CUST-FX-004"
CARLOS, VALENTINA, DIEGO = "CUST-FX-006", "CUST-FX-007", "CUST-FX-008"
ANDRES_CLEAN_TXN = "TXN-FX-BG-021-01"  # background, Approved, in window, no flags, low score


def _decide(
    serving: ServingDB,
    customer_id: str,
    transaction_id: str,
    *,
    store: OpsStore | None = None,
    filed_on: date | None = None,
) -> PolicyDecision:
    inputs = build_inputs(
        serving, store, customer_id, transaction_id, filed_on=filed_on or serving.as_of_date()
    )
    assert inputs is not None
    return evaluate(load_policy(), inputs)


def _agent_dispute(transaction_id: str, created_at: datetime, key: str) -> DisputeCase:
    return DisputeCase(
        dispute_id=f"DSP-TEST-{key[-4:]}",
        transaction_id=transaction_id,
        reason=DisputeReason.UNRECOGNIZED,
        status=DisputeStatus.SUBMITTED,
        created_at=created_at,
        amount=Decimal("1.00"),
        currency="COP",
        idempotency_key=key,
        policy_version="test-v1",
    )


def _add_history(
    bank: Path, customer_id: str, created_at: str, case_type: str, category: str
) -> None:
    with duckdb.connect(str(bank)) as con:
        n = con.execute("SELECT count(*) FROM dispute_history").fetchone()
        assert n is not None
        con.execute(
            "INSERT INTO dispute_history (complaint_id, customer_id, created_at, case_type, "
            "category, subcategory, status, claimed_amount, currency, related_transaction_id, "
            "resolution_days) VALUES (?, ?, ?, ?, ?, NULL, 'Resolved', NULL, NULL, NULL, 5)",
            [f"CMP-TEST-{n[0]:03d}", customer_id, created_at, case_type, category],
        )


def _days_before_as_of(serving: ServingDB, days: int) -> str:
    return f"{serving.as_of_date() - timedelta(days=days)}T10:00:00"


# -- the committed personas, one scenario per rule ------------------------------------------


def test_a_foreign_or_missing_transaction_builds_no_inputs(serving: ServingDB) -> None:
    as_of = serving.as_of_date()
    assert build_inputs(serving, None, MARIANA, "TXN-FX-0601", filed_on=as_of) is None
    assert build_inputs(serving, None, MARIANA, "TXN-DOES-NOT-EXIST", filed_on=as_of) is None


def test_a_normal_unrecognized_charge_proceeds_with_both_writes(serving: ServingDB) -> None:
    filed_on = serving.as_of_date() + timedelta(days=200)
    decision = _decide(serving, MARIANA, "TXN-FX-0101", filed_on=filed_on)
    assert decision.decision == DecisionType.PROCEED
    assert decision.allowed_actions == (ActionType.CREATE_DISPUTE, ActionType.BLOCK_CARD)
    assert decision.sla_due_date == filed_on + timedelta(days=load_policy().sla_days)
    assert decision.policy_version == load_policy().policy_version
    assert decision.target_transaction_id == "TXN-FX-0101"
    assert decision.target_product_id == "CARD-FX-011"


def test_dev_case_portuguese_purchase_allows_the_card_block(serving: ServingDB) -> None:
    # dev-normal-pt-br-001 expects create_dispute and block_card on TXN-FX-0401.
    decision = _decide(serving, RAFAEL, "TXN-FX-0401")
    assert decision.allowed_actions == (ActionType.CREATE_DISPUTE, ActionType.BLOCK_CARD)


def test_dev_case_duplicate_charge_proceeds_despite_its_dq_flag(serving: ServingDB) -> None:
    # dev-normal-es-co-001 expects automated_resolution on TXN-FX-0202 (dq_possible_duplicate).
    risk = serving.risk_signals(ANDRES, "TXN-FX-0202")
    assert risk is not None
    assert "dq_possible_duplicate" in risk.dq_flags
    assert _decide(serving, ANDRES, "TXN-FX-0202").decision == DecisionType.PROCEED


def test_a_declined_and_a_pending_transaction_are_ineligible(serving: ServingDB) -> None:
    for transaction_id in ("TXN-FX-0803", "TXN-FX-0804"):
        decision = _decide(serving, DIEGO, transaction_id)
        assert decision.decision == DecisionType.INELIGIBLE
        assert decision.rule_ids == ("DSP-ELIG-01",)
        assert decision.explanation_keys == ("dispute.not_settled",)


def test_an_old_transaction_outside_the_window_is_ineligible(serving: ServingDB) -> None:
    decision = _decide(serving, DIEGO, "TXN-FX-0801")
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("DSP-WIN-01",)


def test_a_transaction_with_an_open_prior_complaint_is_ineligible(serving: ServingDB) -> None:
    decision = _decide(serving, VALENTINA, "TXN-FX-0701")
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("DSP-ELIG-02",)


def test_an_agent_made_dispute_makes_its_transaction_ineligible(
    serving: ServingDB, ops_store: OpsStore
) -> None:
    created = datetime(2026, 6, 17, 9, tzinfo=UTC)
    ops_store.insert_dispute(MARIANA, _agent_dispute("TXN-FX-0101", created, "idem-own-0001"))
    decision = _decide(serving, MARIANA, "TXN-FX-0101", store=ops_store)
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("DSP-ELIG-02",)


def test_a_repeat_disputer_is_escalated_on_another_of_her_transactions(
    serving: ServingDB,
) -> None:
    # TXN-FX-0702 also carries dq_txn_after_card_expiry, which deliberately does not escalate.
    decision = _decide(serving, VALENTINA, "TXN-FX-0702")
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("DSP-ESC-02",)
    assert decision.explanation_keys == ("dispute.repeat_disputer",)


def test_a_high_fraud_score_is_escalated(serving: ServingDB) -> None:
    decision = _decide(serving, CARLOS, "TXN-FX-0601")
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("DSP-ESC-01",)
    assert decision.explanation_keys == ("dispute.fraud_risk",)


# -- what the repeat-disputer rule counts as a prior dispute --------------------------------


def test_a_recent_claim_about_fees_is_not_a_prior_dispute(bank_copy: Path) -> None:
    serving = ServingDB(bank_copy)
    _add_history(bank_copy, ANDRES, _days_before_as_of(serving, 10), "Claim", "Fees")
    _add_history(bank_copy, ANDRES, _days_before_as_of(serving, 10), "Complaint", "Service")
    # Not every case about a transaction is a dispute: a Request or Suggestion is not.
    _add_history(bank_copy, ANDRES, _days_before_as_of(serving, 10), "Request", "Transactions")
    _add_history(bank_copy, ANDRES, _days_before_as_of(serving, 10), "Suggestion", "Transactions")
    assert _decide(serving, ANDRES, ANDRES_CLEAN_TXN).decision == DecisionType.PROCEED


def test_a_recent_transactions_complaint_is_a_prior_dispute(bank_copy: Path) -> None:
    serving = ServingDB(bank_copy)
    _add_history(bank_copy, ANDRES, _days_before_as_of(serving, 10), "Complaint", "Transactions")
    decision = _decide(serving, ANDRES, ANDRES_CLEAN_TXN)
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("DSP-ESC-02",)


def test_the_newest_prior_dispute_is_the_one_that_counts(bank_copy: Path) -> None:
    serving = ServingDB(bank_copy)
    _add_history(bank_copy, ANDRES, _days_before_as_of(serving, 200), "Claim", "Transactions")
    assert _decide(serving, ANDRES, ANDRES_CLEAN_TXN).decision == DecisionType.PROCEED
    _add_history(bank_copy, ANDRES, _days_before_as_of(serving, 10), "Claim", "Transactions")
    assert _decide(serving, ANDRES, ANDRES_CLEAN_TXN).decision == DecisionType.ESCALATE


def test_an_earlier_agent_dispute_makes_the_next_one_a_repeat(
    serving: ServingDB, ops_store: OpsStore
) -> None:
    filed_on = date(2026, 10, 3)
    created = datetime(2026, 10, 3, 9, tzinfo=UTC)
    ops_store.insert_dispute(ANDRES, _agent_dispute("TXN-FX-0201", created, "idem-first-0001"))
    without_store = _decide(serving, ANDRES, "TXN-FX-0202", filed_on=filed_on)
    assert without_store.decision == DecisionType.PROCEED
    decision = _decide(serving, ANDRES, "TXN-FX-0202", store=ops_store, filed_on=filed_on)
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("DSP-ESC-02",)


def test_an_agent_dispute_from_a_year_before_the_filing_day_does_not_count(
    serving: ServingDB, ops_store: OpsStore
) -> None:
    created = datetime(2027, 6, 1, 9, tzinfo=UTC)
    ops_store.insert_dispute(ANDRES, _agent_dispute("TXN-FX-0201", created, "idem-old-0001"))
    later = _decide(serving, ANDRES, "TXN-FX-0202", store=ops_store, filed_on=date(2028, 6, 1))
    assert later.decision == DecisionType.PROCEED
    soon = _decide(serving, ANDRES, "TXN-FX-0202", store=ops_store, filed_on=date(2027, 6, 2))
    assert soon.decision == DecisionType.ESCALATE


def test_the_newest_agent_dispute_is_the_one_that_counts(
    serving: ServingDB, ops_store: OpsStore
) -> None:
    old = datetime(2026, 1, 5, 9, tzinfo=UTC)
    recent = datetime(2026, 10, 1, 9, tzinfo=UTC)
    ops_store.insert_dispute(ANDRES, _agent_dispute("TXN-FX-0201", old, "idem-older-0011"))
    ops_store.insert_dispute(ANDRES, _agent_dispute("TXN-FX-0202", recent, "idem-newer-0012"))
    assert ops_store.last_dispute_date(ANDRES) == recent.date()
    decision = _decide(
        serving, ANDRES, ANDRES_CLEAN_TXN, store=ops_store, filed_on=date(2026, 10, 3)
    )
    assert decision.decision == DecisionType.ESCALATE


def test_another_customers_agent_dispute_does_not_count(
    serving: ServingDB, ops_store: OpsStore
) -> None:
    created = datetime(2026, 6, 17, 9, tzinfo=UTC)
    ops_store.insert_dispute(MARIANA, _agent_dispute("TXN-FX-0101", created, "idem-other-0001"))
    assert ops_store.last_dispute_date(ANDRES) is None
    assert ops_store.last_dispute_date(MARIANA) == created.date()
    decision = _decide(serving, ANDRES, ANDRES_CLEAN_TXN, store=ops_store)
    assert decision.decision == DecisionType.PROCEED


# -- data quality and the card ---------------------------------------------------------------


def test_a_charge_dated_before_its_card_was_opened_is_escalated(bank_copy: Path) -> None:
    with duckdb.connect(str(bank_copy)) as con:
        con.execute(
            "UPDATE transactions_enriched SET dq_flags = ['dq_txn_before_product_opening'] "
            "WHERE transaction_id = 'TXN-FX-0101'"
        )
    decision = _decide(ServingDB(bank_copy), MARIANA, "TXN-FX-0101")
    assert decision.decision == DecisionType.ESCALATE
    assert decision.rule_ids == ("DSP-ESC-03",)
    assert decision.explanation_keys == ("dispute.data_inconsistent",)


def test_a_card_the_core_has_blocked_gets_a_dispute_but_no_block(bank_copy: Path) -> None:
    with duckdb.connect(str(bank_copy)) as con:
        con.execute(
            "UPDATE customer_cards SET product_status = 'Blocked' WHERE product_id = 'CARD-FX-011'"
        )
    decision = _decide(ServingDB(bank_copy), MARIANA, "TXN-FX-0101")
    assert decision.decision == DecisionType.PROCEED
    assert decision.allowed_actions == (ActionType.CREATE_DISPUTE,)
    assert decision.explanation_keys == ("dispute.card_not_blockable",)


# -- the policy-only reads are scoped to the customer ---------------------------------------


def test_risk_signals_are_scoped_to_the_customer(serving: ServingDB) -> None:
    own = serving.risk_signals(CARLOS, "TXN-FX-0601")
    assert own is not None
    assert own.fraud_score == 78.5
    assert serving.risk_signals(MARIANA, "TXN-FX-0601") is None


def test_the_last_history_dispute_is_scoped_to_the_customer(bank_copy: Path) -> None:
    serving = ServingDB(bank_copy)
    _add_history(bank_copy, MARIANA, _days_before_as_of(serving, 5), "Claim", "Transactions")
    assert serving.last_dispute_date(MARIANA) == serving.as_of_date() - timedelta(days=5)
    assert serving.last_dispute_date(ANDRES) is None
