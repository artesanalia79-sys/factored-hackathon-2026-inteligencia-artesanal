"""Write tools on the fixture bank: BOLA, confirmation tokens, idempotency, read-back, policy.

`create_dispute` and `block_card` share one sequence, so most tests run on both (`WRITES`).
Fixtures (`desk`, `make_desk`, `altered_bank`) are in conftest.py.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from bankagent.contracts.base import Contract, args_hash
from bankagent.contracts.decisions import PolicyDecision
from bankagent.contracts.domain import CardBlockEvent, ConfirmationToken, DisputeCase
from bankagent.contracts.enums import (
    ActionType,
    DecisionType,
    DisputeReason,
    DisputeStatus,
    Intent,
    Language,
    Priority,
    Specialty,
    ToolName,
)
from bankagent.contracts.errors import (
    ConfirmationRequired,
    InvalidArguments,
    NotFound,
    ToolError,
    ToolUnavailable,
)
from bankagent.contracts.handoff import HandoffDraft, HandoffPacket, HandoffRouting, VerifiedFact
from bankagent.contracts.tools import BlockCardArgs, CreateDisputeArgs, CreateHandoffArgs
from bankagent.store.console import HandoffConsole
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB

if TYPE_CHECKING:
    from .conftest import Desk

MARIANA, ANDRES, CARLOS = "CUST-FX-001", "CUST-FX-002", "CUST-FX-006"

MakeDesk = Callable[..., "Desk"]
AlteredBank = Callable[..., ServingDB]


def _dispute(
    transaction_id: str = "TXN-FX-0101",
    key: str = "idem-dispute-0001",
    reason: DisputeReason = DisputeReason.UNRECOGNIZED,
) -> CreateDisputeArgs:
    return CreateDisputeArgs(transaction_id=transaction_id, reason=reason, idempotency_key=key)


def _block(
    product_id: str = "CARD-FX-011", key: str = "idem-block-0001", reason: str = "cargo extraño"
) -> BlockCardArgs:
    return BlockCardArgs(product_id=product_id, reason=reason, idempotency_key=key)


@dataclass(frozen=True)
class Write:
    """One confirmed write tool with the argument variants the shared tests need."""

    tool: ToolName
    action: ActionType
    table: str
    args: Contract  # Mariana's own target
    same_target_other_key: Contract
    other_target_same_key: Contract
    foreign: Contract  # Andrés's target
    missing: Contract

    def stored(self, result: Any) -> Any:
        return result.dispute if self.tool == ToolName.CREATE_DISPUTE else result.block


DISPUTE = Write(
    tool=ToolName.CREATE_DISPUTE,
    action=ActionType.CREATE_DISPUTE,
    table="disputes",
    args=_dispute(),
    same_target_other_key=_dispute(key="idem-dispute-0002"),
    other_target_same_key=_dispute("TXN-FX-0104"),
    foreign=_dispute("TXN-FX-0201"),
    missing=_dispute("TXN-FX-9999"),
)
BLOCK = Write(
    tool=ToolName.BLOCK_CARD,
    action=ActionType.BLOCK_CARD,
    table="card_blocks",
    args=_block(),
    same_target_other_key=_block(key="idem-block-0002"),
    other_target_same_key=_block("CARD-FX-012"),
    foreign=_block("CARD-FX-021"),
    missing=_block("CARD-FX-999"),
)
WRITES = pytest.mark.parametrize("write", [DISPUTE, BLOCK], ids=["create_dispute", "block_card"])
OTHER = {
    ToolName.CREATE_DISPUTE: ActionType.BLOCK_CARD,
    ToolName.BLOCK_CARD: ActionType.CREATE_DISPUTE,
}


def _refusal(desk: Desk, tool: ToolName, args: Contract, **ctx: Any) -> tuple[object, ...]:
    with pytest.raises(ToolError) as refused:
        desk.run(tool, args, **ctx)
    error = refused.value
    return type(error), error.code, error.message, str(error), error.retryable


def _nothing_written(desk: Desk) -> bool:
    return all(desk.store.count(t) == 0 for t in ("disputes", "card_blocks", "handoffs"))


# -- happy paths ----------------------------------------------------------------


def test_create_dispute_stores_a_verified_case_with_the_banks_facts(desk: Desk) -> None:
    token = desk.confirm(ToolName.CREATE_DISPUTE, DISPUTE.args)
    result = desk.run(ToolName.CREATE_DISPUTE, DISPUTE.args, token=token)
    assert (result.created, result.verified) == (True, True)
    assert result.dispute == DisputeCase(
        dispute_id="DSP-0001",
        transaction_id="TXN-FX-0101",
        reason=DisputeReason.UNRECOGNIZED,
        status=DisputeStatus.SUBMITTED,
        created_at=desk.now,
        amount=Decimal("2450.00"),
        currency="MXN",
        sla_due_date=date(2026, 7, 17),
        idempotency_key="idem-dispute-0001",
        policy_version="test-policy-v1",
        rule_ids=("DSP-ELIG-01", "DSP-WIN-02"),
    )
    assert desk.store.get_dispute(MARIANA, transaction_id="TXN-FX-0101") == result.dispute
    assert desk.store.count("disputes") == 1
    assert desk.token_is_spent(token) is True


def test_block_card_stores_a_verified_block_with_the_cards_last4(desk: Desk) -> None:
    token = desk.confirm(ToolName.BLOCK_CARD, BLOCK.args)
    result = desk.run(ToolName.BLOCK_CARD, BLOCK.args, token=token)
    assert (result.created, result.verified) == (True, True)
    assert result.block == CardBlockEvent(
        block_id="BLK-0001",
        product_id="CARD-FX-011",
        card_last4="4821",
        blocked_at=desk.now,
        reason="cargo extraño",
        idempotency_key="idem-block-0001",
    )
    assert desk.store.get_card_block(MARIANA, "CARD-FX-011") == result.block
    assert desk.token_is_spent(token) is True


def test_a_card_already_blocked_in_the_core_can_still_be_blocked(desk: Desk) -> None:
    result = desk.confirmed(ToolName.BLOCK_CARD, _block("CARD-FX-062"), CARLOS)
    assert (result.created, result.verified, result.block.card_last4) == (True, True, "1177")


# -- BOLA -------------------------------------------------------------------------


@WRITES
def test_another_customers_target_is_indistinguishable_from_a_missing_one(
    desk: Desk, write: Write
) -> None:
    # With a valid token for the exact call, without any token and without a policy decision.
    for make_ctx in (
        lambda args: {"token": desk.confirm(write.tool, args)},
        lambda args: {},
        lambda args: {"policy": None},
    ):
        foreign = _refusal(desk, write.tool, write.foreign, **make_ctx(write.foreign))
        missing = _refusal(desk, write.tool, write.missing, **make_ctx(write.missing))
        assert foreign == missing
    with_token = _refusal(
        desk, write.tool, write.foreign, token=desk.confirm(write.tool, write.foreign)
    )
    assert with_token[0] is NotFound
    assert _nothing_written(desk)
    # The target exists: its owner can act on it.
    assert desk.confirmed(write.tool, write.foreign, ANDRES).created is True
    assert desk.store.count(write.table) == 1
    # Mariana still cannot see or reuse what Andrés wrote.
    assert _refusal(
        desk, write.tool, write.foreign, token=desk.confirm(write.tool, write.foreign)
    ) == (with_token)


@WRITES
def test_a_refused_call_does_not_spend_its_token(desk: Desk, write: Write) -> None:
    token = desk.confirm(write.tool, write.foreign)
    with pytest.raises(NotFound):
        desk.run(write.tool, write.foreign, token=token)
    assert desk.token_is_spent(token) is False


# -- confirmation tokens ----------------------------------------------------------


def _save_token(desk: Desk, write: Write, **changes: Any) -> str:
    """A token bound exactly to Mariana's call, with single fields changed."""
    values: dict[str, Any] = {
        "token_id": "tok-crafted",
        "session_id": desk.session().session_id,
        "action": write.action,
        "args_hash": args_hash(write.args),
        "issued_at": desk.now - timedelta(seconds=30),
        "expires_at": desk.now + timedelta(minutes=2),
    }
    desk.store.save_confirmation_token(ConfirmationToken.model_validate({**values, **changes}))
    return "tok-crafted"


@WRITES
def test_a_token_bound_exactly_to_the_call_is_accepted(desk: Desk, write: Write) -> None:
    # Control for the mismatch cases below: the crafted token itself is good.
    result = desk.run(write.tool, write.args, token=_save_token(desk, write))
    assert (result.created, result.verified) == (True, True)


@WRITES
@pytest.mark.parametrize(
    "mismatch",
    [
        "other-session-same-customer",
        "other-customers-session",
        "other-action",
        "other-args",
        "expires-now",
        "expired-long-ago",
        "not-yet-issued",
        "already-used",
    ],
)
def test_a_token_that_is_not_exactly_for_this_call_is_refused(
    desk: Desk, write: Write, mismatch: str
) -> None:
    changes: dict[str, dict[str, Any]] = {
        "other-session-same-customer": {"session_id": "ses-other-device"},
        "other-customers-session": {"session_id": desk.session(ANDRES).session_id},
        "other-action": {"action": OTHER[write.tool]},
        "other-args": {"args_hash": args_hash(write.same_target_other_key)},
        "expires-now": {"expires_at": desk.now},
        "expired-long-ago": {
            "issued_at": desk.now - timedelta(days=2),
            "expires_at": desk.now - timedelta(days=1),
        },
        "not-yet-issued": {"issued_at": desk.now + timedelta(seconds=1)},
        "already-used": {"used_at": desk.now - timedelta(seconds=5)},
    }
    token = _save_token(desk, write, **changes[mismatch])
    with pytest.raises(ConfirmationRequired):
        desk.run(write.tool, write.args, token=token)
    assert _nothing_written(desk)


@WRITES
def test_missing_and_unknown_tokens_are_the_same_refusal(desk: Desk, write: Write) -> None:
    missing = _refusal(desk, write.tool, write.args)
    unknown = _refusal(desk, write.tool, write.args, token="tok-does-not-exist")
    crafted = _refusal(
        desk, write.tool, write.args, token=_save_token(desk, write, session_id="ses-other")
    )
    assert missing == unknown == crafted
    assert missing[0] is ConfirmationRequired
    assert _nothing_written(desk)


@WRITES
def test_a_token_for_other_arguments_is_refused_and_stays_valid_for_its_own(
    desk: Desk, write: Write
) -> None:
    token = desk.confirm(write.tool, write.args)
    for other in (write.same_target_other_key, write.other_target_same_key):
        with pytest.raises(ConfirmationRequired):
            desk.run(write.tool, other, token=token)
    assert _nothing_written(desk)
    assert desk.token_is_spent(token) is False
    assert desk.run(write.tool, write.args, token=token).created is True


def test_a_token_for_another_reason_is_refused(desk: Desk) -> None:
    token = desk.confirm(ToolName.CREATE_DISPUTE, _dispute(reason=DisputeReason.DUPLICATE))
    with pytest.raises(ConfirmationRequired):
        desk.run(ToolName.CREATE_DISPUTE, _dispute(), token=token)
    assert _nothing_written(desk)


@WRITES
def test_a_reused_token_is_refused(desk: Desk, write: Write) -> None:
    token = desk.confirm(write.tool, write.args)
    first = desk.run(write.tool, write.args, token=token)
    assert first.created is True
    with pytest.raises(ConfirmationRequired):
        desk.run(write.tool, write.args, token=token)
    assert desk.store.count(write.table) == 1


@WRITES
def test_an_issued_token_expires(desk: Desk, write: Write) -> None:
    token = desk.confirm(write.tool, write.args, ttl=timedelta(minutes=2))
    desk.now += timedelta(minutes=2)  # exactly at expiry
    with pytest.raises(ConfirmationRequired):
        desk.run(write.tool, write.args, token=token)
    assert _nothing_written(desk)
    desk.now -= timedelta(seconds=1)
    assert desk.run(write.tool, write.args, token=token).created is True


@WRITES
def test_a_token_of_another_session_of_the_same_customer_is_refused(
    desk: Desk, write: Write
) -> None:
    phone = desk.session(MARIANA, "ses-phone")
    token = desk.confirm(write.tool, write.args, session=phone)
    with pytest.raises(ConfirmationRequired):
        desk.run(write.tool, write.args, token=token)  # the default (laptop) session
    assert _nothing_written(desk)
    assert desk.run(write.tool, write.args, token=token, session=phone).created is True


# -- idempotency ------------------------------------------------------------------


@WRITES
def test_a_replay_returns_the_same_record_and_writes_one_row(desk: Desk, write: Write) -> None:
    first = desk.confirmed(write.tool, write.args)
    desk.now += timedelta(seconds=40)
    second_token = desk.confirm(write.tool, write.args)
    second = desk.run(write.tool, write.args, token=second_token)
    assert (first.created, second.created) == (True, False)
    assert write.stored(second) == write.stored(first)
    assert second.verified is True
    assert desk.store.count(write.table) == 1
    assert desk.token_is_spent(second_token) is True


@WRITES
def test_a_key_reused_for_another_target_is_invalid_and_spends_nothing(
    desk: Desk, write: Write
) -> None:
    first = desk.confirmed(write.tool, write.args)
    token = desk.confirm(write.tool, write.other_target_same_key)
    with pytest.raises(InvalidArguments):
        desk.run(write.tool, write.other_target_same_key, token=token)
    assert desk.store.count(write.table) == 1
    assert desk.token_is_spent(token) is False
    assert write.stored(desk.confirmed(write.tool, write.args)) == write.stored(first)


def test_a_key_reused_for_another_reason_is_invalid(desk: Desk) -> None:
    desk.confirmed(ToolName.CREATE_DISPUTE, _dispute())
    other_reason = _dispute(reason=DisputeReason.NOT_RECEIVED)
    token = desk.confirm(ToolName.CREATE_DISPUTE, other_reason)
    with pytest.raises(InvalidArguments):
        desk.run(ToolName.CREATE_DISPUTE, other_reason, token=token)
    assert desk.store.count("disputes") == 1
    assert desk.token_is_spent(token) is False


@WRITES
def test_a_target_already_written_under_another_key_is_refused_and_spends_nothing(
    desk: Desk, write: Write
) -> None:
    first = desk.confirmed(write.tool, write.args)
    token = desk.confirm(write.tool, write.same_target_other_key)
    with pytest.raises(InvalidArguments) as refused:
        desk.run(write.tool, write.same_target_other_key, token=token)
    assert refused.value.message in {
        "this transaction already has a dispute",
        "this card is already blocked",
    }
    assert desk.store.count(write.table) == 1
    assert desk.token_is_spent(token) is False
    # `created=False` is only ever an exact replay of the original call.
    replay = desk.confirmed(write.tool, write.args)
    assert (replay.created, replay.verified) == (False, True)
    assert write.stored(replay) == write.stored(first)


def test_the_same_key_is_independent_per_customer(desk: Desk) -> None:
    mine = desk.confirmed(ToolName.CREATE_DISPUTE, _dispute())
    theirs = desk.confirmed(ToolName.CREATE_DISPUTE, _dispute("TXN-FX-0201"), ANDRES)
    assert (mine.created, theirs.created) == (True, True)
    assert mine.dispute.dispute_id != theirs.dispute.dispute_id
    assert desk.store.count("disputes") == 2


# -- concurrency ------------------------------------------------------------------


@WRITES
def test_concurrent_replays_create_one_row(desk: Desk, write: Write) -> None:
    tokens = [desk.confirm(write.tool, write.args) for _ in range(16)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda t: desk.run(write.tool, write.args, token=t), tokens))
    assert [r.created for r in results].count(True) == 1
    assert len({write.stored(r) for r in results}) == 1
    assert all(r.verified for r in results)
    assert desk.store.count(write.table) == 1


@WRITES
def test_one_token_fired_in_parallel_is_honoured_once(desk: Desk, write: Write) -> None:
    token = desk.confirm(write.tool, write.args)

    def attempt(_: int) -> str:
        try:
            desk.run(write.tool, write.args, token=token)
        except ConfirmationRequired:
            return "refused"
        return "done"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(16)))
    assert outcomes.count("done") == 1
    assert outcomes.count("refused") == 15
    assert desk.store.count(write.table) == 1


def test_concurrent_disputes_under_different_keys_create_one_case(desk: Desk) -> None:
    calls = [_dispute(key=f"idem-race-{i:04d}") for i in range(16)]
    tokens = [desk.confirm(ToolName.CREATE_DISPUTE, args) for args in calls]

    def attempt(pair: tuple[CreateDisputeArgs, str]) -> str:
        try:
            result = desk.run(ToolName.CREATE_DISPUTE, pair[0], token=pair[1])
        except InvalidArguments:
            return "already disputed"
        assert (result.created, result.verified) == (True, True)
        return "created"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, zip(calls, tokens, strict=True)))
    assert outcomes.count("created") == 1
    assert outcomes.count("already disputed") == 15
    assert desk.store.count("disputes") == 1
    # Only the winner's confirmation was spent.
    assert [desk.token_is_spent(token) for token in tokens].count(True) == 1


# -- read-back and store failures -------------------------------------------------


class FaultyStore(OpsStore):
    """An ops store whose read-back or insert can be made to fail."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.read_back: str | None = None
        self.fail_insert = False

    def _faulty(self, stored: Any, changed: dict[str, Any]) -> Any:
        if self.read_back == "missing":
            return None
        if self.read_back == "altered":
            return stored.model_copy(update=changed)
        if self.read_back == "locked":
            raise sqlite3.OperationalError("database is locked")
        if self.read_back == "corrupt":
            return type(stored).model_validate({})
        return stored

    def get_dispute(
        self,
        customer_id: str,
        *,
        dispute_id: str | None = None,
        transaction_id: str | None = None,
    ) -> DisputeCase | None:
        stored = super().get_dispute(
            customer_id, dispute_id=dispute_id, transaction_id=transaction_id
        )
        return self._faulty(stored, {"amount": Decimal("1.00")})

    def get_card_block(self, customer_id: str, product_id: str) -> CardBlockEvent | None:
        stored = super().get_card_block(customer_id, product_id)
        return self._faulty(stored, {"card_last4": "0000"})

    def get_handoff(self, customer_id: str, handoff_id: str) -> HandoffPacket | None:
        stored = super().get_handoff(customer_id, handoff_id)
        return self._faulty(stored, {"request": "otra cosa"})

    def _maybe_fail(self) -> None:
        if self.fail_insert:
            raise sqlite3.OperationalError("disk I/O error")

    def insert_dispute(self, customer_id: str, dispute: DisputeCase) -> tuple[DisputeCase, bool]:
        self._maybe_fail()
        return super().insert_dispute(customer_id, dispute)

    def insert_card_block(
        self, customer_id: str, block: CardBlockEvent
    ) -> tuple[CardBlockEvent, bool]:
        self._maybe_fail()
        return super().insert_card_block(customer_id, block)

    def insert_handoff(
        self, packet: HandoffPacket, idempotency_key: str
    ) -> tuple[HandoffPacket, bool]:
        self._maybe_fail()
        return super().insert_handoff(packet, idempotency_key)


@pytest.fixture
def faulty(tmp_path: Path, make_desk: MakeDesk) -> Desk:
    return make_desk(ops=FaultyStore(tmp_path / "faulty.sqlite"))


def _faults(desk: Desk) -> FaultyStore:
    assert isinstance(desk.store, FaultyStore)
    return desk.store


READ_BACK_FAULTS = pytest.mark.parametrize("fault", ["missing", "altered", "locked", "corrupt"])


@WRITES
@READ_BACK_FAULTS
def test_a_read_back_that_does_not_match_is_not_verified(
    faulty: Desk, write: Write, fault: str
) -> None:
    token = faulty.confirm(write.tool, write.args)
    _faults(faulty).read_back = fault
    result = faulty.run(write.tool, write.args, token=token)
    # The write happened and is reported, but nothing may claim it as done.
    assert (result.created, result.verified) == (True, False)
    assert faulty.store.count(write.table) == 1
    _faults(faulty).read_back = None
    assert faulty.confirmed(write.tool, write.args).verified is True


@READ_BACK_FAULTS
def test_a_handoff_read_back_that_does_not_match_is_not_verified(faulty: Desk, fault: str) -> None:
    _faults(faulty).read_back = fault
    result = faulty.run(ToolName.CREATE_HANDOFF, _handoff())
    assert (result.created, result.verified) == (True, False)
    assert faulty.store.count("handoffs") == 1


@WRITES
def test_a_failed_write_is_unavailable_and_keeps_the_token(faulty: Desk, write: Write) -> None:
    token = faulty.confirm(write.tool, write.args)
    _faults(faulty).fail_insert = True
    with pytest.raises(ToolUnavailable) as refused:
        faulty.run(write.tool, write.args, token=token)
    assert refused.value.retryable is True
    assert "disk" not in refused.value.message
    assert _nothing_written(faulty)
    assert faulty.token_is_spent(token) is False
    # The retry with the same token goes through once the store is back.
    _faults(faulty).fail_insert = False
    result = faulty.run(write.tool, write.args, token=token)
    assert (result.created, result.verified) == (True, True)


# -- policy decision in the context -----------------------------------------------

ESCALATE = PolicyDecision(
    decision=DecisionType.ESCALATE,
    rule_ids=("DSP-AMT-01",),
    allowed_actions=(ActionType.CREATE_HANDOFF,),
    escalation_triggers=("high_amount",),
    policy_version="test-policy-v1",
)


def _only(action: ActionType) -> PolicyDecision:
    return PolicyDecision(
        decision=DecisionType.PROCEED,
        allowed_actions=(action,),
        requires_confirmation=True,
        policy_version="test-policy-v1",
    )


@WRITES
def test_a_write_the_policy_decision_does_not_allow_is_refused(desk: Desk, write: Write) -> None:
    token = desk.confirm(write.tool, write.args)
    for policy in (None, ESCALATE, _only(OTHER[write.tool])):
        with pytest.raises(InvalidArguments):
            desk.run(write.tool, write.args, token=token, policy=policy)
    assert _nothing_written(desk)
    assert desk.token_is_spent(token) is False
    assert desk.run(write.tool, write.args, token=token, policy=_only(write.action)).created


def test_without_a_policy_decision_the_dispute_carries_no_sla_or_rules(desk: Desk) -> None:
    bare = _only(ActionType.CREATE_DISPUTE)
    token = desk.confirm(ToolName.CREATE_DISPUTE, _dispute())
    dispute = desk.run(ToolName.CREATE_DISPUTE, _dispute(), token=token, policy=bare).dispute
    assert (dispute.sla_due_date, dispute.rule_ids) == (None, ())
    assert dispute.policy_version == "test-policy-v1"


@WRITES
def test_the_baseline_wiring_writes_without_a_policy_decision(
    make_desk: MakeDesk, write: Write
) -> None:
    desk = make_desk(require_policy=False)
    result = desk.confirmed(write.tool, write.args, policy=None)
    assert (result.created, result.verified) == (True, True)
    if write.tool == ToolName.CREATE_DISPUTE:
        assert result.dispute.policy_version == "unspecified"
        assert (result.dispute.sla_due_date, result.dispute.rule_ids) == (None, ())
    # A decision that forbids the action is still honoured, and so is the token.
    with pytest.raises(InvalidArguments):
        desk.confirmed(write.tool, write.same_target_other_key, policy=ESCALATE)
    with pytest.raises(ConfirmationRequired):
        desk.run(write.tool, write.same_target_other_key, policy=None)


# -- create_handoff ---------------------------------------------------------------


def _handoff(
    key: str = "idem-handoff-0001",
    specialty: Specialty = Specialty.DISPUTES,
    language: Language = Language.ES,
    **draft: Any,
) -> CreateHandoffArgs:
    values: dict[str, Any] = {
        "trace_id": "trace-1",
        "language": language,
        "request": "cliente no reconoce un cargo de monto alto",
        "intent": Intent.DISPUTE_UNRECOGNIZED,
        "verified_facts": (
            VerifiedFact(
                key="transaction",
                value="LUXURY WATCHES INTL 9800000.00 COP",
                source="get_transaction",
                ref="TXN-FX-0601",
            ),
        ),
        "trigger_rule_ids": ("DSP-AMT-01",),
        "policy_version": "test-policy-v1",
        "routing": HandoffRouting(specialty=specialty, language=language, priority=Priority.HIGH),
    }
    return CreateHandoffArgs(
        draft=HandoffDraft.model_validate({**values, **draft}), idempotency_key=key
    )


def test_create_handoff_stores_a_verified_packet_with_the_session_identity(desk: Desk) -> None:
    args = _handoff(language=Language.PT)
    # No confirmation token and no policy decision: escalating must always be possible.
    result = desk.run(ToolName.CREATE_HANDOFF, args, CARLOS, policy=None)
    assert (result.handoff_id, result.created, result.verified) == ("HND-0001", True, True)
    assert result.routing == args.draft.routing  # stored exactly as requested
    stored = desk.store.get_handoff(CARLOS, "HND-0001")
    assert stored is not None
    assert (stored.customer_id, stored.created_at) == (CARLOS, desk.now)
    assert stored.model_dump(exclude={"handoff_id", "created_at", "customer_id"}) == (
        args.draft.model_dump()
    )
    # BOLA: nobody else reads it through the customer-facing store.
    assert desk.store.get_handoff(MARIANA, "HND-0001") is None


def test_the_trace_is_set_by_the_server_and_the_routing_is_kept_as_requested(desk: Desk) -> None:
    named = HandoffRouting(
        specialty=Specialty.CARDS, language=Language.PT, priority=Priority.LOW, agent_id="AGT-FX-06"
    )
    args = _handoff(trace_id="trace-of-someone-else", routing=named)
    result = desk.run(ToolName.CREATE_HANDOFF, args, trace_id="trace-real")
    assert result.routing == named
    stored = desk.store.get_handoff(MARIANA, result.handoff_id)
    assert stored is not None
    assert (stored.trace_id, stored.routing) == ("trace-real", named)


def test_a_handoff_replay_returns_the_same_handoff(desk: Desk) -> None:
    first = desk.run(ToolName.CREATE_HANDOFF, _handoff())
    desk.now += timedelta(seconds=30)
    second = desk.run(ToolName.CREATE_HANDOFF, _handoff())
    assert (first.created, second.created) == (True, False)
    assert (second.handoff_id, second.routing, second.verified) == (
        first.handoff_id,
        first.routing,
        True,
    )
    assert desk.store.count("handoffs") == 1
    # The same key from another customer is another handoff.
    assert desk.run(ToolName.CREATE_HANDOFF, _handoff(), ANDRES).created is True
    assert len(HandoffConsole(desk.store.database).list_handoffs()) == 2


def test_a_handoff_key_reused_for_other_content_is_invalid(desk: Desk) -> None:
    desk.run(ToolName.CREATE_HANDOFF, _handoff())
    with pytest.raises(InvalidArguments):
        desk.run(ToolName.CREATE_HANDOFF, _handoff(request="otra solicitud distinta"))
    with pytest.raises(InvalidArguments):
        desk.run(ToolName.CREATE_HANDOFF, _handoff(), trace_id="trace-2")
    assert desk.store.count("handoffs") == 1


def test_a_failed_handoff_write_is_unavailable(faulty: Desk) -> None:
    _faults(faulty).fail_insert = True
    with pytest.raises(ToolUnavailable):
        faulty.run(ToolName.CREATE_HANDOFF, _handoff())
    assert _nothing_written(faulty)
