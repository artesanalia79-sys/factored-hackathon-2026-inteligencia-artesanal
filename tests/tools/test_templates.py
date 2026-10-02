"""The response templates (T11) accept what the real tools return, and nothing else.

A template only states a fact or claims an action when it is handed a verified record with the
same arguments and a result that matches the request. These tests run the real tools through
`call_tool` and render the outcome, so the two sides cannot drift apart unnoticed.
Fixtures (`desk`) are in conftest.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from bankagent.contracts.base import Contract
from bankagent.contracts.enums import (
    ConversationState,
    DisputeReason,
    Intent,
    Language,
    Priority,
    Specialty,
    ToolErrorCode,
    ToolName,
)
from bankagent.contracts.handoff import HandoffDraft, HandoffRouting
from bankagent.contracts.tools import (
    BlockCardArgs,
    CreateDisputeArgs,
    CreateHandoffArgs,
    GetTransactionArgs,
    ListCardsArgs,
)
from bankagent.render.templates import (
    UnverifiedRenderError,
    render_blocked_card,
    render_confirmation,
    render_created_dispute,
    render_created_handoff,
    render_recognition,
)
from bankagent.tools import ToolCall, call_tool

if TYPE_CHECKING:
    from .conftest import Desk

READ = GetTransactionArgs(transaction_id="TXN-FX-0101")
DISPUTE = CreateDisputeArgs(
    transaction_id="TXN-FX-0101",
    reason=DisputeReason.UNRECOGNIZED,
    idempotency_key="idem-render-0001",
)
BLOCK = BlockCardArgs(
    product_id="CARD-FX-011", reason="cargo no reconocido", idempotency_key="idem-render-0002"
)
HANDOFF = CreateHandoffArgs(
    draft=HandoffDraft(
        trace_id="trace-1",
        language=Language.ES,
        request="cliente pide hablar con una persona",
        intent=Intent.HUMAN_REQUEST,
        policy_version="test-policy-v1",
        routing=HandoffRouting(
            specialty=Specialty.DISPUTES, language=Language.ES, priority=Priority.MEDIUM
        ),
    ),
    idempotency_key="idem-render-0003",
)


def _call(desk: Desk, tool: ToolName, args: Contract, *, confirm: bool = False) -> ToolCall[Any]:
    token = desk.confirm(tool, args) if confirm else None
    return call_tool(
        desk.tools[tool],
        desk.ctx(token=token),
        args,
        turn_index=0,
        step_index=0,
        state=ConversationState.ACT,
    )


@pytest.mark.parametrize("language", list(Language))
def test_a_transaction_read_can_be_shown_for_recognition_and_confirmation(
    desk: Desk, language: Language
) -> None:
    read = _call(desk, ToolName.GET_TRANSACTION, READ)
    shown = render_recognition(language, READ, read.unwrap(), read.record)
    assert "ELECTROMUNDO ONLINE" in shown
    assert "4821" in shown
    assert render_confirmation(language, DISPUTE, READ, read.unwrap(), read.record)


def test_a_card_read_can_be_shown_to_confirm_a_block(desk: Desk) -> None:
    cards = _call(desk, ToolName.LIST_CARDS, ListCardsArgs())
    text = render_confirmation(Language.ES, BLOCK, ListCardsArgs(), cards.unwrap(), cards.record)
    assert "4821" in text


@pytest.mark.parametrize("language", list(Language))
def test_a_created_dispute_and_its_exact_replay_are_rendered(
    desk: Desk, language: Language
) -> None:
    created = _call(desk, ToolName.CREATE_DISPUTE, DISPUTE, confirm=True)
    replay = _call(desk, ToolName.CREATE_DISPUTE, DISPUTE, confirm=True)
    first = render_created_dispute(language, DISPUTE, created.unwrap(), created.record)
    again = render_created_dispute(language, DISPUTE, replay.unwrap(), replay.record)
    assert created.unwrap().dispute.dispute_id in first
    assert created.unwrap().dispute.dispute_id in again
    assert first != again  # "created now" and "already registered" are different sentences


def test_a_blocked_card_and_its_exact_replay_are_rendered(desk: Desk) -> None:
    blocked = _call(desk, ToolName.BLOCK_CARD, BLOCK, confirm=True)
    replay = _call(desk, ToolName.BLOCK_CARD, BLOCK, confirm=True)
    first = render_blocked_card(Language.ES, BLOCK, blocked.unwrap(), blocked.record)
    again = render_blocked_card(Language.ES, BLOCK, replay.unwrap(), replay.record)
    assert "4821" in first
    assert "4821" in again
    assert first != again


@pytest.mark.parametrize("language", list(Language))
def test_a_created_handoff_is_rendered(desk: Desk, language: Language) -> None:
    # Regression: a tool that changed the routing (it used to assign an agent) made the
    # template refuse every escalation, because the stored routing was not the requested one.
    handoff = _call(desk, ToolName.CREATE_HANDOFF, HANDOFF)
    replay = _call(desk, ToolName.CREATE_HANDOFF, HANDOFF)
    assert handoff.unwrap().routing == HANDOFF.draft.routing
    assert handoff.unwrap().handoff_id in render_created_handoff(
        language, HANDOFF, handoff.unwrap(), handoff.record
    )
    assert handoff.unwrap().handoff_id in render_created_handoff(
        language, HANDOFF, replay.unwrap(), replay.record
    )


def test_a_transaction_already_disputed_under_another_key_never_reaches_a_template(
    desk: Desk,
) -> None:
    # Regression: this used to return the stored case with created=False, which the template
    # refuses (it is not the requested action). Now it is a tool error with a record.
    _call(desk, ToolName.CREATE_DISPUTE, DISPUTE, confirm=True)
    other_key = DISPUTE.model_copy(update={"idempotency_key": "idem-render-0009"})
    second = _call(desk, ToolName.CREATE_DISPUTE, other_key, confirm=True)
    assert second.result is None
    assert second.record.error_code == ToolErrorCode.INVALID_ARGUMENTS
    assert second.record.verified is False


def test_templates_refuse_a_failed_or_unverified_call(desk: Desk) -> None:
    foreign = GetTransactionArgs(transaction_id="TXN-FX-0201")
    refused = _call(desk, ToolName.GET_TRANSACTION, foreign)
    good = _call(desk, ToolName.GET_TRANSACTION, READ)
    with pytest.raises(UnverifiedRenderError):
        render_recognition(Language.ES, foreign, good.unwrap(), refused.record)
    unconfirmed = _call(desk, ToolName.CREATE_DISPUTE, DISPUTE)  # no token
    created = _call(desk, ToolName.CREATE_DISPUTE, DISPUTE, confirm=True)
    with pytest.raises(UnverifiedRenderError):
        render_created_dispute(Language.ES, DISPUTE, created.unwrap(), unconfirmed.record)
