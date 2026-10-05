"""Operational store on a temporary SQLite file: scoping, idempotency, single-use tokens."""

from __future__ import annotations

import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bankagent.contracts.domain import CardBlockEvent, ConfirmationToken, DisputeCase, Session
from bankagent.contracts.enums import (
    ActionType,
    ConversationState,
    DisputeReason,
    DisputeStatus,
    Intent,
    Language,
    Priority,
    Specialty,
    StepKind,
    StepOutcome,
    ToolName,
)
from bankagent.contracts.handoff import HandoffPacket, HandoffRouting
from bankagent.contracts.records import ExecutionRecord
from bankagent.store.console import HandoffConsole
from bankagent.store.ops import (
    SCHEMA_VERSION,
    CardAlreadyBlocked,
    DisputeAlreadyExists,
    IdempotencyConflict,
    LoginChallenge,
    OpsStore,
    OpsStoreError,
)
from bankagent.store.sqlite import Database

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
HASH = "a" * 64
TOKEN_ID = "tok-1"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[OpsStore]:
    with OpsStore(tmp_path / "runtime" / "ops.sqlite") as opened:
        yield opened


def _dispute(
    dispute_id: str = "DSP-1", key: str = "idem-key-0001", txn: str = "TXN-1"
) -> DisputeCase:
    return DisputeCase(
        dispute_id=dispute_id,
        transaction_id=txn,
        reason=DisputeReason.UNRECOGNIZED,
        status=DisputeStatus.SUBMITTED,
        created_at=NOW,
        amount=Decimal("2450"),
        currency="MXN",
        idempotency_key=key,
        policy_version="v1",
        rule_ids=("R-1", "R-2"),
    )


def _token(token_id: str = TOKEN_ID, **changes: object) -> ConfirmationToken:
    values: dict[str, object] = {
        "token_id": token_id,
        "session_id": "ses-1",
        "action": ActionType.CREATE_DISPUTE,
        "args_hash": HASH,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=2),
    }
    return ConfirmationToken.model_validate({**values, **changes})


def _consume(store: OpsStore, token_id: str = TOKEN_ID, **changes: Any) -> bool:
    values: dict[str, Any] = {
        "session_id": "ses-1",
        "action": ActionType.CREATE_DISPUTE,
        "args_hash": HASH,
        "now": NOW + timedelta(seconds=30),
    }
    values.update(changes)
    return store.consume_confirmation_token(token_id, **values)


def test_file_is_created_and_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "ops.sqlite"
    with OpsStore(path) as first:
        first.insert_dispute("CUST-A", _dispute())
    with OpsStore(path) as second:
        assert second.get_dispute("CUST-A", dispute_id="DSP-1") == _dispute()


def test_wrong_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ops.sqlite"
    with Database(path) as database:
        database.write("UPDATE schema_version SET version = ?", [SCHEMA_VERSION + 1])
    with pytest.raises(OpsStoreError, match="schema version"):
        OpsStore(path)


def test_file_from_the_previous_schema_is_refused(tmp_path: Path) -> None:
    # Schema 1 had no UNIQUE constraints on disputes / card blocks; such a file must not be
    # silently reused, or the constraints would be missing.
    path = tmp_path / "ops.sqlite"
    with Database(path) as database:
        database.write("UPDATE schema_version SET version = ?", [1])
    with pytest.raises(OpsStoreError, match="delete the file"):
        OpsStore(path)
    assert SCHEMA_VERSION == 2


def test_session_round_trip_and_revocation(store: OpsStore) -> None:
    session = Session(
        session_id="ses-1",
        customer_id="CUST-A",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        language=Language.PT,
    )
    store.save_session(session)
    assert store.get_session("ses-1") == session
    assert store.get_session("ses-unknown") is None
    assert store.revoke_session("ses-1", NOW) is True
    assert store.get_session("ses-1") is None
    assert store.revoke_session("ses-1", NOW) is False


def test_naive_datetimes_are_rejected(store: OpsStore) -> None:
    with pytest.raises(ValueError, match="naive"):
        store.revoke_session("ses-1", datetime(2026, 6, 17, 12, 0))


def test_confirmation_token_is_single_use(store: OpsStore) -> None:
    store.save_confirmation_token(_token())
    assert _consume(store) is True
    assert _consume(store) is False
    stored = store.get_confirmation_token("tok-1", "ses-1")
    assert stored is not None
    assert stored.used_at == NOW + timedelta(seconds=30)
    # Another session cannot even see the token.
    assert store.get_confirmation_token("tok-1", "ses-other") is None


@pytest.mark.parametrize(
    "mismatch",
    [
        {"session_id": "ses-other"},
        {"action": ActionType.BLOCK_CARD},
        {"args_hash": "b" * 64},
        {"now": NOW + timedelta(minutes=2)},  # exactly at expiry
        {"now": NOW - timedelta(seconds=1)},  # before issue
    ],
)
def test_confirmation_token_only_works_for_its_exact_binding(
    store: OpsStore, mismatch: dict[str, Any]
) -> None:
    store.save_confirmation_token(_token())
    assert _consume(store, **mismatch) is False
    assert _consume(store, "tok-missing") is False
    # A rejected attempt does not burn the token.
    assert _consume(store) is True


def test_dispute_is_idempotent_and_scoped_by_customer(store: OpsStore) -> None:
    first, created = store.insert_dispute("CUST-A", _dispute())
    assert created is True
    replay, created_again = store.insert_dispute("CUST-A", _dispute(dispute_id="DSP-2"))
    assert created_again is False
    assert replay == first
    assert store.count("disputes") == 1
    # Same key from another customer is a different dispute.
    _, other_created = store.insert_dispute("CUST-B", _dispute(dispute_id="DSP-3"))
    assert other_created is True
    # BOLA: another customer's dispute looks exactly like a missing one.
    assert store.get_dispute("CUST-B", dispute_id="DSP-1") is None
    assert store.get_dispute("CUST-B", dispute_id="DSP-missing") is None
    assert store.get_dispute("CUST-A", transaction_id="TXN-1") == first
    assert store.get_dispute("CUST-A", transaction_id="TXN-9") is None
    with pytest.raises(ValueError, match="exactly one"):
        store.get_dispute("CUST-A")


def _block(
    block_id: str = "BLK-1", key: str = "idem-key-0002", product_id: str = "CARD-1"
) -> CardBlockEvent:
    return CardBlockEvent(
        block_id=block_id,
        product_id=product_id,
        card_last4="4242",
        blocked_at=NOW,
        reason="unrecognized charge",
        idempotency_key=key,
    )


def test_card_block_is_idempotent_and_scoped(store: OpsStore) -> None:
    block = _block()
    assert store.insert_card_block("CUST-A", block) == (block, True)
    assert store.insert_card_block("CUST-A", block) == (block, False)
    assert store.get_card_block("CUST-A", "CARD-1") == block
    assert store.get_card_block("CUST-B", "CARD-1") is None


def _packet(handoff_id: str, minutes: int = 0, **changes: Any) -> HandoffPacket:
    values: dict[str, Any] = {
        "handoff_id": handoff_id,
        "created_at": NOW + timedelta(minutes=minutes),
        "customer_id": "CUST-A",
        "trace_id": "trace-1",
        "language": Language.ES,
        "request": "cliente pide hablar con una persona",
        "intent": Intent.HUMAN_REQUEST,
        "policy_version": "v1",
        "routing": HandoffRouting(
            specialty=Specialty.DISPUTES, language=Language.ES, priority=Priority.MEDIUM
        ),
    }
    return HandoffPacket.model_validate({**values, **changes})


def test_handoff_replay_and_customer_scoped_read(store: OpsStore) -> None:
    first, created = store.insert_handoff(_packet("HND-1"), "idem-key-0003")
    assert created is True
    # A replay mints a new id and timestamp but carries the same content.
    assert store.insert_handoff(_packet("HND-9", 5), "idem-key-0003") == (first, False)
    assert store.count("handoffs") == 1
    assert store.get_handoff("CUST-A", "HND-1") == first
    assert store.get_handoff("CUST-B", "HND-1") is None  # BOLA: looks like a missing one
    assert store.get_handoff("CUST-A", "HND-missing") is None


def test_console_reads_are_unscoped_and_live_outside_the_store(store: OpsStore) -> None:
    store.insert_handoff(_packet("HND-1"), "idem-key-0003")
    store.insert_handoff(_packet("HND-2", 1, customer_id="CUST-B"), "idem-key-0004")
    console = HandoffConsole(store.database)
    assert [h.handoff_id for h in console.list_handoffs()] == ["HND-2", "HND-1"]
    assert [h.handoff_id for h in console.list_handoffs(limit=1)] == ["HND-2"]
    found = console.get_handoff("HND-2")
    assert found is not None
    assert found.customer_id == "CUST-B"
    assert console.get_handoff("HND-missing") is None
    # The customer-facing store has no unscoped handoff read at all.
    assert not hasattr(store, "list_handoffs")


def test_console_lists_disputes_and_card_blocks_across_customers(store: OpsStore) -> None:
    older = _dispute("DSP-1", "idem-key-0005", "TXN-1")
    newer = _dispute("DSP-2", "idem-key-0006", "TXN-2").model_copy(
        update={"created_at": NOW + timedelta(minutes=1)}
    )
    store.insert_dispute("CUST-A", older)
    store.insert_dispute("CUST-B", newer)
    store.insert_card_block("CUST-A", _block("BLK-1", "idem-key-0007", "CARD-1"))
    store.insert_card_block(
        "CUST-B",
        _block("BLK-2", "idem-key-0008", "CARD-2").model_copy(
            update={"blocked_at": NOW + timedelta(minutes=1)}
        ),
    )
    console = HandoffConsole(store.database)
    disputes = console.list_disputes()
    assert [(customer_id, case.dispute_id) for customer_id, case in disputes] == [
        ("CUST-B", "DSP-2"),
        ("CUST-A", "DSP-1"),
    ]
    blocks = console.list_card_blocks()
    assert [(customer_id, event.block_id) for customer_id, event in blocks] == [
        ("CUST-B", "BLK-2"),
        ("CUST-A", "BLK-1"),
    ]
    assert [(c, e.block_id) for c, e in console.list_card_blocks(limit=1)] == [("CUST-B", "BLK-2")]
    assert [(c, d.dispute_id) for c, d in console.list_disputes(limit=1)] == [("CUST-B", "DSP-2")]


def test_console_lists_the_execution_trace_of_one_handoff(store: OpsStore) -> None:
    store.append_records(
        [
            ExecutionRecord(
                record_id="rec-1",
                trace_id="trace-1",
                turn_index=0,
                step_index=0,
                step=StepKind.TOOL_CALL,
                state=ConversationState.IDENTIFY_TXN,
                tool=ToolName.CREATE_HANDOFF,
                outcome=StepOutcome.SUCCESS,
                verified=True,
                latency_ms=5.0,
                created_at=NOW,
            )
        ]
    )
    later = [
        ExecutionRecord(
            record_id=f"rec-{turn}-{step}",
            trace_id="trace-1",
            turn_index=turn,
            step_index=step,
            step=StepKind.RENDER,
            state=ConversationState.RESPOND,
            outcome=StepOutcome.SUCCESS,
            latency_ms=1.0,
            created_at=NOW,
        )
        # Stored out of order on purpose: the trace reads in turn and step order.
        for turn, step in ((1, 1), (1, 0), (0, 1))
    ]
    store.append_records(later)
    console = HandoffConsole(store.database)
    records = console.list_records("trace-1")
    assert [r.record_id for r in records] == ["rec-1", "rec-0-1", "rec-1-0", "rec-1-1"]
    assert console.list_records("trace-missing") == []


def test_tools_layer_never_imports_the_console() -> None:
    tools = Path(__file__).resolve().parents[2] / "src" / "bankagent" / "tools"
    offenders = [
        path.name
        for path in tools.rglob("*.py")
        if re.search(r"bankagent\.store\.console|import console", path.read_text("utf-8"))
    ]
    assert offenders == []


# -- idempotency conflicts and single dispute / block -------------------------


def test_idempotency_key_reused_for_another_dispute_is_a_conflict(store: OpsStore) -> None:
    store.insert_dispute("CUST-A", _dispute())
    with pytest.raises(IdempotencyConflict):
        store.insert_dispute("CUST-A", _dispute(dispute_id="DSP-2", txn="TXN-2"))
    other_reason = _dispute(dispute_id="DSP-3").model_copy(
        update={"reason": DisputeReason.NOT_RECEIVED}
    )
    with pytest.raises(IdempotencyConflict):
        store.insert_dispute("CUST-A", other_reason)
    assert store.count("disputes") == 1
    assert store.insert_dispute("CUST-A", _dispute(dispute_id="DSP-4")) == (_dispute(), False)


def test_idempotency_key_reused_for_another_card_is_a_conflict(store: OpsStore) -> None:
    store.insert_card_block("CUST-A", _block())
    with pytest.raises(IdempotencyConflict):
        store.insert_card_block("CUST-A", _block(block_id="BLK-2", product_id="CARD-2"))
    assert store.count("card_blocks") == 1
    assert store.insert_card_block("CUST-A", _block(block_id="BLK-3")) == (_block(), False)


def test_idempotency_key_reused_for_another_handoff_is_a_conflict(store: OpsStore) -> None:
    first, _ = store.insert_handoff(_packet("HND-1"), "idem-key-0003")
    with pytest.raises(IdempotencyConflict):
        store.insert_handoff(_packet("HND-2", request="otra solicitud"), "idem-key-0003")
    assert store.count("handoffs") == 1
    assert store.insert_handoff(_packet("HND-3", 9), "idem-key-0003") == (first, False)


def test_second_dispute_on_a_transaction_is_refused(store: OpsStore) -> None:
    first, _ = store.insert_dispute("CUST-A", _dispute())
    with pytest.raises(DisputeAlreadyExists) as refused:
        store.insert_dispute("CUST-A", _dispute(dispute_id="DSP-2", key="idem-key-9999"))
    assert refused.value.existing == first
    assert store.count("disputes") == 1
    # Another transaction of the same customer is fine.
    store.insert_dispute("CUST-A", _dispute(dispute_id="DSP-5", key="idem-key-5555", txn="TXN-5"))
    assert store.count("disputes") == 2


def test_concurrent_disputes_on_one_transaction_create_a_single_row(store: OpsStore) -> None:
    def attempt(i: int) -> str:
        try:
            store.insert_dispute("CUST-A", _dispute(dispute_id=f"DSP-{i}", key=f"idem-key-{i:04d}"))
        except DisputeAlreadyExists:
            return "exists"
        return "created"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(40)))
    assert outcomes.count("created") == 1
    assert store.count("disputes") == 1


def test_second_block_on_a_card_is_refused(store: OpsStore) -> None:
    first, _ = store.insert_card_block("CUST-A", _block())
    with pytest.raises(CardAlreadyBlocked) as refused:
        store.insert_card_block("CUST-A", _block(block_id="BLK-2", key="idem-key-8888"))
    assert refused.value.existing == first
    assert store.count("card_blocks") == 1


# -- several connections on one file (uvicorn workers) -----------------------


def test_two_connections_share_one_file_and_keep_the_guarantees(tmp_path: Path) -> None:
    path = tmp_path / "ops.sqlite"
    with OpsStore(path) as first, OpsStore(path) as second:
        first.save_confirmation_token(_token())
        stores = [first, second]

        def consume(i: int) -> bool:
            return _consume(stores[i % 2])

        def dispute(i: int) -> str:
            try:
                stores[i % 2].insert_dispute(
                    "CUST-A", _dispute(dispute_id=f"DSP-{i}", key=f"idem-key-{i:04d}")
                )
            except DisputeAlreadyExists:
                return "exists"
            return "created"

        with ThreadPoolExecutor(max_workers=8) as pool:
            consumed = list(pool.map(consume, range(20)))
            disputes = list(pool.map(dispute, range(20)))
        assert sum(consumed) == 1  # single use across connections
        assert disputes.count("created") == 1
        assert second.count("disputes") == first.count("disputes") == 1
        journal = first.database.one("PRAGMA journal_mode")
        assert journal is not None
        assert journal[0] == "wal"


def test_execution_records_are_listed_in_step_order(store: OpsStore) -> None:
    def record(step_index: int, turn_index: int = 0) -> ExecutionRecord:
        return ExecutionRecord(
            record_id=f"rec-{turn_index}-{step_index}",
            trace_id="trace-1",
            session_id="ses-1",
            turn_index=turn_index,
            step_index=step_index,
            step=StepKind.AUTHENTICATE,
            state=ConversationState.AUTH,
            outcome=StepOutcome.SUCCESS,
            latency_ms=1.5,
            cost_usd=Decimal("0.00012345"),
            created_at=NOW,
        )

    store.append_records([record(1, 1), record(1), record(0)])
    listed = store.list_records("trace-1")
    assert [(r.turn_index, r.step_index) for r in listed] == [(0, 0), (0, 1), (1, 1)]
    assert listed[0] == record(0)
    assert store.list_records("trace-other") == []


def test_transaction_rolls_back_every_write_on_error(store: OpsStore) -> None:
    store.save_confirmation_token(_token())

    def failing_write() -> None:
        with store.transaction():
            assert _consume(store) is True
            store.insert_dispute("CUST-A", _dispute())
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        failing_write()
    assert store.count("disputes") == 0
    assert _consume(store) is True  # the token was not burned by the failed write


def test_values_are_parameters_never_sql(store: OpsStore) -> None:
    hostile = "x'; DROP TABLE disputes; --"
    store.insert_dispute(hostile, _dispute(txn=hostile))
    assert store.get_dispute(hostile, transaction_id=hostile) is not None
    assert store.count("disputes") == 1


def _challenge(max_attempts: int, challenge_id: str = "chl-1") -> LoginChallenge:
    return LoginChallenge(
        challenge_id, "CUST-A", HASH, NOW, NOW + timedelta(minutes=5), max_attempts=max_attempts
    )


def test_attempts_are_reserved_atomically_up_to_the_limit(store: OpsStore) -> None:
    store.create_challenge(_challenge(2))
    assert [store.reserve_attempt("chl-1", NOW) for _ in range(4)] == [True, True, False, False]
    challenge = store.get_challenge("chl-1")
    assert challenge is not None
    assert challenge.attempts == 2
    assert store.reserve_attempt("chl-missing", NOW) is False


def test_concurrent_guesses_cannot_exceed_the_attempt_limit(store: OpsStore) -> None:
    store.create_challenge(_challenge(3))
    with ThreadPoolExecutor(max_workers=8) as pool:
        granted = list(pool.map(lambda _: store.reserve_attempt("chl-1", NOW), range(40)))
    assert sum(granted) == 3


def test_reserve_and_consume_respect_the_validity_window(store: OpsStore) -> None:
    store.create_challenge(_challenge(3))
    assert store.reserve_attempt("chl-1", NOW + timedelta(minutes=5)) is False  # expired
    assert store.reserve_attempt("chl-1", NOW - timedelta(seconds=1)) is False  # before issue
    assert store.consume_challenge("chl-1", NOW + timedelta(minutes=5)) is False
    assert store.consume_challenge("chl-1", NOW) is True
    assert store.consume_challenge("chl-1", NOW) is False
    assert store.reserve_attempt("chl-1", NOW) is False  # used challenges take no more guesses


def test_login_failures_are_listed_per_customer_and_forgotten_one_by_one(
    store: OpsStore,
) -> None:
    store.record_login_failure("CUST-A", NOW)
    second = store.record_login_failure("CUST-A", NOW + timedelta(seconds=5))
    store.record_login_failure("CUST-B", NOW)
    assert store.failures_since("CUST-A", NOW) == [NOW, NOW + timedelta(seconds=5)]
    assert store.failures_since("CUST-A", NOW + timedelta(seconds=1)) == [
        NOW + timedelta(seconds=5)
    ]
    store.forget_login_failure(second)
    assert store.failures_since("CUST-A", NOW) == [NOW]  # the earlier failure still counts
    assert store.failures_since("CUST-B", NOW) == [NOW]


def test_purge_removes_only_what_is_older_than_the_cutoff(store: OpsStore) -> None:
    store.create_challenge(_challenge(3, "chl-old"))  # expires at NOW + 5 min
    store.create_challenge(
        LoginChallenge(
            "chl-new", "CUST-A", HASH, NOW + timedelta(hours=1), NOW + timedelta(hours=2), 3
        )
    )
    store.record_login_failure("CUST-A", NOW)
    store.record_login_failure("CUST-A", NOW + timedelta(hours=1))
    store.purge_login_data(NOW + timedelta(minutes=30))
    assert store.get_challenge("chl-old") is None
    assert store.get_challenge("chl-new") is not None
    assert store.failures_since("CUST-A", NOW) == [NOW + timedelta(hours=1)]
