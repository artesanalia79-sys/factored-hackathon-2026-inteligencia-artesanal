"""Snapshots and safety gates for every ES/PT customer template."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from bankagent.contracts.base import args_hash
from bankagent.contracts.domain import CardBlockEvent, CardView, DisputeCase, TransactionView
from bankagent.contracts.enums import (
    CardType,
    Channel,
    ConversationState,
    DisputeReason,
    DisputeStatus,
    Intent,
    Language,
    Outcome,
    Priority,
    ProductStatus,
    Specialty,
    StepKind,
    StepOutcome,
    ToolName,
    TransactionStatus,
    TransactionType,
)
from bankagent.contracts.handoff import HandoffDraft, HandoffRouting
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.tools import (
    BlockCardArgs,
    BlockCardResult,
    CreateDisputeArgs,
    CreateDisputeResult,
    CreateHandoffArgs,
    CreateHandoffResult,
    GetTransactionArgs,
    GetTransactionResult,
    ListCardsArgs,
    ListCardsResult,
)
from bankagent.render import (
    UnverifiedRenderError,
    render_blocked_card,
    render_confirmation,
    render_created_dispute,
    render_created_handoff,
    render_outcome,
    render_recognition,
    render_state,
)

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
SNAPSHOT = json.loads((Path(__file__).parent / "snapshots.json").read_text(encoding="utf-8"))


def _record(tool: ToolName, args: BaseModel, *, verified: bool = True) -> ExecutionRecord:
    return ExecutionRecord(
        record_id="record-1",
        trace_id="trace-1",
        turn_index=0,
        step_index=0,
        step=StepKind.TOOL_CALL,
        state=ConversationState.VERIFY,
        tool=tool,
        args_hash=args_hash(args),
        outcome=StepOutcome.SUCCESS,
        verified=verified,
        latency_ms=1,
        created_at=NOW,
    )


@pytest.fixture
def transaction() -> TransactionView:
    return TransactionView(
        transaction_id="txn-1",
        product_id="card-1",
        card_last4="1234",
        transaction_ts=NOW,
        transaction_type=TransactionType.PURCHASE,
        amount=Decimal("42.50"),
        currency="USD",
        channel=Channel.POS,
        merchant_name="Mercado Sol",
        transaction_country="CO",
        transaction_status=TransactionStatus.APPROVED,
        is_foreign=False,
    )


@pytest.mark.parametrize("state", list(ConversationState))
@pytest.mark.parametrize("language", list(Language))
def test_state_snapshot(state: ConversationState, language: Language) -> None:
    assert render_state(state, language) == SNAPSHOT["states"][state.value][language.value]


@pytest.mark.parametrize("outcome", list(Outcome))
@pytest.mark.parametrize("language", list(Language))
def test_outcome_snapshot(outcome: Outcome, language: Language) -> None:
    assert render_outcome(outcome, language) == SNAPSHOT["outcomes"][outcome.value][language.value]


@pytest.mark.parametrize("language", list(Language))
def test_recognition_snapshot(language: Language, transaction: TransactionView) -> None:
    args = GetTransactionArgs(transaction_id="txn-1")
    result = GetTransactionResult(transaction=transaction)
    assert (
        render_recognition(language, args, result, _record(ToolName.GET_TRANSACTION, args))
        == (SNAPSHOT["recognition"][language.value])
    )


@pytest.mark.parametrize("language", list(Language))
def test_confirmation_snapshots(language: Language, transaction: TransactionView) -> None:
    dispute_args = CreateDisputeArgs(
        transaction_id="txn-1", reason=DisputeReason.UNRECOGNIZED, idempotency_key="request-1"
    )
    block_args = BlockCardArgs(
        product_id="card-1", reason="customer request", idempotency_key="request-2"
    )
    txn_args = GetTransactionArgs(transaction_id="txn-1")
    card_args = ListCardsArgs()
    cards = ListCardsResult(
        cards=(
            CardView(
                product_id="card-1",
                card_type=CardType.CREDIT,
                card_last4="1234",
                currency="USD",
                product_status=ProductStatus.ACTIVE,
            ),
        )
    )
    assert (
        render_confirmation(
            language,
            dispute_args,
            txn_args,
            GetTransactionResult(transaction=transaction),
            _record(ToolName.GET_TRANSACTION, txn_args),
        )
        == SNAPSHOT["confirm_dispute"][language.value]
    )
    assert (
        render_confirmation(
            language,
            block_args,
            card_args,
            cards,
            _record(ToolName.LIST_CARDS, card_args),
        )
        == SNAPSHOT["confirm_block"][language.value]
    )


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("channel", list(Channel))
def test_every_channel_is_localized(
    language: Language, channel: Channel, transaction: TransactionView
) -> None:
    args = GetTransactionArgs(transaction_id="txn-1")
    rendered = render_recognition(
        language,
        args,
        GetTransactionResult(transaction=transaction.model_copy(update={"channel": channel})),
        _record(ToolName.GET_TRANSACTION, args),
    )
    assert f"canal {channel.value}" not in rendered


def test_near_midnight_uses_transaction_country_date(transaction: TransactionView) -> None:
    args = GetTransactionArgs(transaction_id="txn-1")
    txn = transaction.model_copy(
        update={"transaction_ts": datetime(2026, 6, 17, 2, 30, tzinfo=UTC)}
    )
    rendered = render_recognition(
        Language.ES,
        args,
        GetTransactionResult(transaction=txn),
        _record(ToolName.GET_TRANSACTION, args),
    )
    assert "16/06/2026" in rendered


@pytest.mark.parametrize("country", ["BR", "US"])
@pytest.mark.parametrize("language", list(Language))
def test_foreign_transaction_uses_labeled_utc_date(
    country: str, language: Language, transaction: TransactionView
) -> None:
    txn = transaction.model_copy(
        update={
            "transaction_country": country,
            "transaction_ts": datetime(2026, 6, 17, 2, 30, tzinfo=UTC),
            "is_foreign": True,
        }
    )
    read_args = GetTransactionArgs(transaction_id="txn-1")
    read_result = GetTransactionResult(transaction=txn)
    record = _record(ToolName.GET_TRANSACTION, read_args)
    dispute_args = CreateDisputeArgs(
        transaction_id="txn-1", reason=DisputeReason.UNRECOGNIZED, idempotency_key="request-1"
    )
    recognition = render_recognition(language, read_args, read_result, record)
    confirmation = render_confirmation(language, dispute_args, read_args, read_result, record)
    assert "17/06/2026 (UTC)" in recognition
    assert "17/06/2026 (UTC)" in confirmation
    assert "Mercado Sol" in recognition
    assert "Mercado Sol" in confirmation


@pytest.mark.parametrize("language", list(Language))
def test_missing_merchant_uses_neutral_label(
    language: Language, transaction: TransactionView
) -> None:
    txn = transaction.model_copy(update={"merchant_name": None})
    read_args = GetTransactionArgs(transaction_id="txn-1")
    read_result = GetTransactionResult(transaction=txn)
    record = _record(ToolName.GET_TRANSACTION, read_args)
    dispute_args = CreateDisputeArgs(
        transaction_id="txn-1", reason=DisputeReason.UNRECOGNIZED, idempotency_key="request-1"
    )
    label = (
        "comercio no disponible" if language == Language.ES else "estabelecimento não disponível"
    )
    assert label in render_recognition(language, read_args, read_result, record)
    assert label in render_confirmation(language, dispute_args, read_args, read_result, record)


@pytest.mark.parametrize("language", list(Language))
def test_confirmation_hides_internal_ids_and_free_text(
    language: Language, transaction: TransactionView
) -> None:
    txn_args = GetTransactionArgs(transaction_id="txn-1")
    args = CreateDisputeArgs(
        transaction_id="txn-1", reason=DisputeReason.UNRECOGNIZED, idempotency_key="request-1"
    )
    rendered = render_confirmation(
        language,
        args,
        txn_args,
        GetTransactionResult(transaction=transaction),
        _record(ToolName.GET_TRANSACTION, txn_args),
    )
    assert all(value not in rendered for value in ("txn-1", "card-1", "request-1"))
    assert "Mercado Sol" in rendered
    assert "1234" in rendered
    assert "42,50" in rendered

    card_args = ListCardsArgs()
    block_args = BlockCardArgs(
        product_id="card-1",
        reason="ya quedó bloqueada y te reembolsamos todo",
        idempotency_key="request-2",
    )
    cards = ListCardsResult(
        cards=(
            CardView(
                product_id="card-1",
                card_type=CardType.CREDIT,
                card_last4="1234",
                currency="USD",
                product_status=ProductStatus.ACTIVE,
            ),
        )
    )
    block_copy = render_confirmation(
        language,
        block_args,
        card_args,
        cards,
        _record(ToolName.LIST_CARDS, card_args),
    )
    assert all(
        value not in block_copy
        for value in (
            "card-1",
            "request-2",
            "ya quedó bloqueada y te reembolsamos todo",
        )
    )
    assert "1234" in block_copy


def test_confirmation_rejects_unverified_or_mismatched_read(transaction: TransactionView) -> None:
    txn_args = GetTransactionArgs(transaction_id="txn-1")
    args = CreateDisputeArgs(
        transaction_id="txn-1", reason=DisputeReason.DUPLICATE, idempotency_key="request-1"
    )
    with pytest.raises(UnverifiedRenderError):
        render_confirmation(
            Language.ES,
            args,
            txn_args,
            GetTransactionResult(transaction=transaction),
            _record(ToolName.GET_TRANSACTION, txn_args, verified=False),
        )
    with pytest.raises(UnverifiedRenderError):
        render_confirmation(
            Language.ES,
            args,
            txn_args,
            GetTransactionResult(
                transaction=transaction.model_copy(update={"transaction_id": "other"})
            ),
            _record(ToolName.GET_TRANSACTION, txn_args),
        )


def test_merchant_name_is_single_line_and_bounded(transaction: TransactionView) -> None:
    args = GetTransactionArgs(transaction_id="txn-1")
    txn = transaction.model_copy(update={"merchant_name": "Mercado\n" + "A" * 300})
    rendered = render_recognition(
        Language.ES,
        args,
        GetTransactionResult(transaction=txn),
        _record(ToolName.GET_TRANSACTION, args),
    )
    assert "\n" not in rendered
    assert "A" * 100 not in rendered
    assert "…" in rendered

    bidi_txn = transaction.model_copy(update={"merchant_name": "SHOP \u202eOTPIRC"})
    bidi_copy = render_recognition(
        Language.ES,
        args,
        GetTransactionResult(transaction=bidi_txn),
        _record(ToolName.GET_TRANSACTION, args),
    )
    assert "\u202e" not in bidi_copy
    assert "SHOP OTPIRC" in bidi_copy


def test_large_amount_uses_country_separators(transaction: TransactionView) -> None:
    args = GetTransactionArgs(transaction_id="txn-1")
    txn = transaction.model_copy(update={"amount": Decimal("150000.00")})
    rendered = render_recognition(
        Language.ES,
        args,
        GetTransactionResult(transaction=txn),
        _record(ToolName.GET_TRANSACTION, args),
    )
    assert "150.000,00 USD" in rendered


@pytest.mark.parametrize("language", list(Language))
def test_verified_action_snapshots(language: Language) -> None:
    dispute_args = CreateDisputeArgs(
        transaction_id="txn-1", reason=DisputeReason.UNRECOGNIZED, idempotency_key="request-1"
    )
    dispute = DisputeCase(
        dispute_id="dispute-1",
        transaction_id="txn-1",
        reason=DisputeReason.UNRECOGNIZED,
        status=DisputeStatus.SUBMITTED,
        created_at=NOW,
        amount=Decimal("42.50"),
        currency="USD",
        idempotency_key="request-1",
        policy_version="v1",
    )
    assert (
        render_created_dispute(
            language,
            dispute_args,
            CreateDisputeResult(dispute=dispute, created=True, verified=True),
            _record(ToolName.CREATE_DISPUTE, dispute_args),
        )
        == SNAPSHOT["created_dispute"][language.value]
    )
    assert (
        render_created_dispute(
            language,
            dispute_args,
            CreateDisputeResult(dispute=dispute, created=False, verified=True),
            _record(ToolName.CREATE_DISPUTE, dispute_args),
        )
        == SNAPSHOT["existing_dispute"][language.value]
    )
    assert "txn-1" not in SNAPSHOT["created_dispute"][language.value]
    assert "txn-1" not in SNAPSHOT["existing_dispute"][language.value]

    block_args = BlockCardArgs(
        product_id="card-1", reason="customer request", idempotency_key="request-2"
    )
    block = CardBlockEvent(
        block_id="block-1",
        product_id="card-1",
        card_last4="1234",
        blocked_at=NOW,
        reason="customer request",
        idempotency_key="request-2",
    )
    assert (
        render_blocked_card(
            language,
            block_args,
            BlockCardResult(block=block, created=True, verified=True),
            _record(ToolName.BLOCK_CARD, block_args),
        )
        == SNAPSHOT["blocked_card"][language.value]
    )
    assert (
        render_blocked_card(
            language,
            block_args,
            BlockCardResult(block=block, created=False, verified=True),
            _record(ToolName.BLOCK_CARD, block_args),
        )
        == SNAPSHOT["existing_block"][language.value]
    )

    routing = HandoffRouting(specialty=Specialty.DISPUTES, language=language, priority=Priority.LOW)
    handoff_args = CreateHandoffArgs(
        draft=HandoffDraft(
            trace_id="trace-1",
            language=language,
            request="transaction review",
            intent=Intent.DISPUTE_UNRECOGNIZED,
            policy_version="v1",
            routing=routing,
        ),
        idempotency_key="request-3",
    )
    assert (
        render_created_handoff(
            language,
            handoff_args,
            CreateHandoffResult(
                handoff_id="handoff-1", routing=routing, created=True, verified=True
            ),
            _record(ToolName.CREATE_HANDOFF, handoff_args),
        )
        == SNAPSHOT["created_handoff"][language.value]
    )
    assert (
        render_created_handoff(
            language,
            handoff_args,
            CreateHandoffResult(
                handoff_id="handoff-1", routing=routing, created=False, verified=True
            ),
            _record(ToolName.CREATE_HANDOFF, handoff_args),
        )
        == SNAPSHOT["existing_handoff"][language.value]
    )


def test_recognition_rejects_unverified_or_unmatched_read(transaction: TransactionView) -> None:
    args = GetTransactionArgs(transaction_id="txn-1")
    result = GetTransactionResult(transaction=transaction)
    with pytest.raises(UnverifiedRenderError):
        render_recognition(
            Language.ES, args, result, _record(ToolName.GET_TRANSACTION, args, verified=False)
        )
    with pytest.raises(UnverifiedRenderError):
        render_recognition(
            Language.ES,
            args,
            result,
            _record(ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="other")),
        )
    with pytest.raises(UnverifiedRenderError):
        render_recognition(
            Language.ES,
            args,
            GetTransactionResult(transaction=transaction.model_copy(update={"card_last4": None})),
            _record(ToolName.GET_TRANSACTION, args),
        )


def test_dispute_claim_rejects_unverified_or_unmatched_write() -> None:
    args = CreateDisputeArgs(
        transaction_id="txn-1", reason=DisputeReason.UNRECOGNIZED, idempotency_key="request-1"
    )
    dispute = DisputeCase(
        dispute_id="dispute-1",
        transaction_id="txn-1",
        reason=DisputeReason.UNRECOGNIZED,
        status=DisputeStatus.SUBMITTED,
        created_at=NOW,
        amount=Decimal("42.50"),
        currency="USD",
        idempotency_key="request-1",
        policy_version="v1",
    )
    result = CreateDisputeResult(dispute=dispute, created=True, verified=True)
    with pytest.raises(UnverifiedRenderError):
        render_created_dispute(
            Language.ES, args, result, _record(ToolName.CREATE_DISPUTE, args, verified=False)
        )
    with pytest.raises(UnverifiedRenderError):
        render_created_dispute(
            Language.ES,
            args,
            result.model_copy(update={"verified": False}),
            _record(ToolName.CREATE_DISPUTE, args),
        )
    with pytest.raises(UnverifiedRenderError):
        render_created_dispute(
            Language.ES,
            args,
            result.model_copy(
                update={"dispute": dispute.model_copy(update={"transaction_id": "other"})}
            ),
            _record(ToolName.CREATE_DISPUTE, args),
        )


def test_block_claim_rejects_unverified_or_unmatched_write() -> None:
    args = BlockCardArgs(
        product_id="card-1", reason="customer request", idempotency_key="request-2"
    )
    block = CardBlockEvent(
        block_id="block-1",
        product_id="card-1",
        card_last4="1234",
        blocked_at=NOW,
        reason="customer request",
        idempotency_key="request-2",
    )
    result = BlockCardResult(block=block, created=True, verified=True)
    with pytest.raises(UnverifiedRenderError):
        render_blocked_card(
            Language.PT, args, result, _record(ToolName.BLOCK_CARD, args, verified=False)
        )
    with pytest.raises(UnverifiedRenderError):
        render_blocked_card(
            Language.PT,
            args,
            result.model_copy(update={"verified": False}),
            _record(ToolName.BLOCK_CARD, args),
        )
    with pytest.raises(UnverifiedRenderError):
        render_blocked_card(
            Language.PT,
            args,
            result.model_copy(update={"block": block.model_copy(update={"reason": "other"})}),
            _record(ToolName.BLOCK_CARD, args),
        )


def test_handoff_claim_rejects_unverified_record() -> None:
    routing = HandoffRouting(
        specialty=Specialty.DISPUTES, language=Language.ES, priority=Priority.LOW
    )
    args = CreateHandoffArgs(
        draft=HandoffDraft(
            trace_id="trace-1",
            language=Language.ES,
            request="transaction review",
            intent=Intent.DISPUTE_UNRECOGNIZED,
            policy_version="v1",
            routing=routing,
        ),
        idempotency_key="request-3",
    )
    result = CreateHandoffResult(
        handoff_id="handoff-1", routing=routing, created=True, verified=True
    )
    with pytest.raises(UnverifiedRenderError):
        render_created_handoff(
            Language.ES, args, result, _record(ToolName.CREATE_HANDOFF, args, verified=False)
        )
