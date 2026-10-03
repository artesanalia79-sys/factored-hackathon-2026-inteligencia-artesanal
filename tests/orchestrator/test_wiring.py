"""The T13 policy evaluator over the real policy file, fixture bank and ops store.

`build_policy_evaluator` must hand the engine what `bankagent.policy.build_inputs` resolves: the
transaction's card, and the bank's dispute history and the agent's own disputes on their two
clocks (`as_of_date` and the filing day from the agent's clock). The rule-by-rule scenarios live
in `tests/policy/test_inputs.py`; these tests check the wiring.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from bankagent.contracts.domain import DisputeCase, Session, TransactionRiskSignals
from bankagent.contracts.enums import ActionType, DecisionType, DisputeReason, DisputeStatus
from bankagent.contracts.errors import ToolUnavailable
from bankagent.contracts.tools import GetTransactionResult
from bankagent.fixtures.builder import build
from bankagent.orchestrator.wiring import build_policy_evaluator
from bankagent.policy import load_policy
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB

MARIANA, VALENTINA = "CUST-FX-001", "CUST-FX-007"
AS_OF = datetime(2026, 6, 17, 12, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(scope="module")
def bank(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("t13-wiring") / "bank_fixture.duckdb"
    _, problems = build(out=path)
    assert not problems
    return path


@pytest.fixture
def store(tmp_path: Path) -> Iterator[OpsStore]:
    with OpsStore(tmp_path / "ops.sqlite") as ops:
        yield ops


def _session(customer_id: str, now: datetime) -> Session:
    return Session(
        session_id=f"ses-{customer_id}",
        customer_id=customer_id,
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=10),
    )


def _read(serving: ServingDB, customer_id: str, transaction_id: str) -> GetTransactionResult:
    transaction = serving.transaction(customer_id, transaction_id)
    assert transaction is not None
    return GetTransactionResult(transaction=transaction)


def test_proceed_is_bound_to_the_transaction_and_its_active_card(
    bank: Path, store: OpsStore
) -> None:
    serving = ServingDB(bank)
    filed = datetime(2026, 10, 2, 15, tzinfo=UTC)  # the real filing day, after as_of_date
    decide = build_policy_evaluator(serving, store, clock=Clock(filed))
    decision = decide(
        _session(MARIANA, filed), _read(serving, MARIANA, "TXN-FX-0101"), DisputeReason.UNRECOGNIZED
    )
    assert decision.decision == DecisionType.PROCEED
    assert decision.policy_version == load_policy().policy_version
    assert decision.target_transaction_id == "TXN-FX-0101"
    assert decision.target_product_id == "CARD-FX-011"
    assert ActionType.BLOCK_CARD in decision.allowed_actions  # CARD-FX-011 is Active
    assert decision.sla_due_date == filed.date() + timedelta(days=load_policy().sla_days)


def test_history_and_open_case_come_from_the_bank(bank: Path, store: OpsStore) -> None:
    serving = ServingDB(bank)
    decide = build_policy_evaluator(serving, store, clock=Clock(AS_OF))
    session = _session(VALENTINA, AS_OF)
    open_claim = decide(
        session, _read(serving, VALENTINA, "TXN-FX-0701"), DisputeReason.UNRECOGNIZED
    )
    assert open_claim.decision == DecisionType.INELIGIBLE
    assert open_claim.rule_ids == ("DSP-ELIG-02",)
    repeat = decide(session, _read(serving, VALENTINA, "TXN-FX-0702"), DisputeReason.UNRECOGNIZED)
    assert repeat.decision == DecisionType.ESCALATE
    assert repeat.rule_ids == ("DSP-ESC-02",)


@pytest.mark.parametrize(("days_later", "expected"), [(10, "escalate"), (151, "proceed")])
def test_an_agent_dispute_counts_against_the_filing_day_not_as_of_date(
    bank: Path, store: OpsStore, days_later: int, expected: str
) -> None:
    # An agent dispute is stamped with real time, after the frozen as_of_date. Compared with
    # as_of_date (one merged date) it would sit "in the future" and escalate forever.
    first_filed = datetime(2027, 1, 4, 9, tzinfo=UTC)
    store.insert_dispute(
        MARIANA,
        DisputeCase(
            dispute_id="DSP-WIRING-1",
            transaction_id="TXN-FX-0101",
            reason=DisputeReason.UNRECOGNIZED,
            status=DisputeStatus.SUBMITTED,
            created_at=first_filed,
            amount=Decimal("2450.00"),
            currency="MXN",
            idempotency_key="idem-wiring-1",
            policy_version="test-v1",
        ),
    )
    now = first_filed + timedelta(days=days_later)
    serving = ServingDB(bank)
    decide = build_policy_evaluator(serving, store, clock=Clock(now))
    decision = decide(
        _session(MARIANA, now), _read(serving, MARIANA, "TXN-FX-0104"), DisputeReason.UNRECOGNIZED
    )
    assert decision.decision == expected
    if expected == "escalate":
        assert decision.rule_ids == ("DSP-ESC-02",)


def test_the_agents_own_dispute_closes_the_transaction(bank: Path, store: OpsStore) -> None:
    store.insert_dispute(
        MARIANA,
        DisputeCase(
            dispute_id="DSP-WIRING-2",
            transaction_id="TXN-FX-0101",
            reason=DisputeReason.UNRECOGNIZED,
            status=DisputeStatus.SUBMITTED,
            created_at=AS_OF,
            amount=Decimal("2450.00"),
            currency="MXN",
            idempotency_key="idem-wiring-2",
            policy_version="test-v1",
        ),
    )
    serving = ServingDB(bank)
    decide = build_policy_evaluator(serving, store, clock=Clock(AS_OF))
    decision = decide(
        _session(MARIANA, AS_OF), _read(serving, MARIANA, "TXN-FX-0101"), DisputeReason.UNRECOGNIZED
    )
    assert decision.decision == DecisionType.INELIGIBLE
    assert decision.rule_ids == ("DSP-ELIG-02",)


def test_another_customers_transaction_is_refused(bank: Path, store: OpsStore) -> None:
    serving = ServingDB(bank)
    decide = build_policy_evaluator(serving, store, clock=Clock(AS_OF))
    foreign = _read(serving, "CUST-FX-006", "TXN-FX-0601")
    with pytest.raises(ToolUnavailable):
        decide(_session(MARIANA, AS_OF), foreign, DisputeReason.UNRECOGNIZED)


class BrokenServing(ServingDB):
    def __init__(self, path: Path, error: Exception) -> None:
        super().__init__(path)
        self.error = error

    def risk_signals(self, customer_id: str, transaction_id: str) -> TransactionRiskSignals | None:
        raise self.error


def test_a_database_error_becomes_unavailable_without_its_cause(
    bank: Path, store: OpsStore
) -> None:
    serving = BrokenServing(bank, duckdb.Error("row TXN-FX-0101 fraud_score=12.4"))
    decide = build_policy_evaluator(serving, store, clock=Clock(AS_OF))
    with pytest.raises(ToolUnavailable) as raised:
        decide(
            _session(MARIANA, AS_OF),
            _read(serving, MARIANA, "TXN-FX-0101"),
            DisputeReason.UNRECOGNIZED,
        )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_a_programming_error_is_not_disguised_as_unavailable(bank: Path, store: OpsStore) -> None:
    # The PR's first version caught every exception, so the ServingDB method renamed in PR #47
    # turned every dispute into a polite "try again later" at runtime instead of an error.
    serving = BrokenServing(bank, AttributeError("no such method"))
    decide = build_policy_evaluator(serving, store, clock=Clock(AS_OF))
    with pytest.raises(AttributeError):
        decide(
            _session(MARIANA, AS_OF),
            _read(serving, MARIANA, "TXN-FX-0101"),
            DisputeReason.UNRECOGNIZED,
        )
