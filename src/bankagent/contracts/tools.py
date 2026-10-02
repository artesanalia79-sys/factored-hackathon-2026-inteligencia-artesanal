"""Tool argument/result models, the tool registry and the ``Tool`` protocol.

Argument models are what the interpreter or orchestrator may fill in. None of them (at any
nesting level) has a ``customer_id`` field: identity always comes from ``ToolContext.session``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Protocol

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from bankagent.contracts.base import Contract, CurrencyCode, Identifier, Last4, NonNegativeMoney
from bankagent.contracts.decisions import PolicyDecision
from bankagent.contracts.domain import (
    CardBlockEvent,
    CardView,
    DisputeCase,
    Session,
    TransactionView,
)
from bankagent.contracts.enums import ActionType, DisputeReason, ToolName
from bankagent.contracts.handoff import HandoffDraft, HandoffRouting

IdempotencyKey = Annotated[str, StringConstraints(min_length=8, max_length=64)]


class ToolContext(Contract):
    """Server-side context of a tool call. Built by the orchestrator, never by the model.

    ``policy`` is the decision the write was allowed under. ``create_dispute`` and ``block_card``
    refuse an action the decision does not allow, and the dispute is stamped with its version,
    rule ids and SLA due date.
    """

    session: Session
    trace_id: Identifier
    now: AwareDatetime
    confirmation_token_id: Identifier | None = None
    policy: PolicyDecision | None = None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


class ListCardsArgs(Contract):
    include_inactive: bool = False


class ListCardsResult(Contract):
    cards: tuple[CardView, ...]


class SearchTransactionsArgs(Contract):
    amount_min: NonNegativeMoney | None = None
    amount_max: NonNegativeMoney | None = None
    currency: CurrencyCode | None = None
    merchant_query: str | None = Field(default=None, min_length=1, max_length=100)
    date_from: date | None = None
    date_to: date | None = None
    card_last4: Last4 | None = None
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def _ranges(self) -> SearchTransactionsArgs:
        if (
            self.amount_min is not None
            and self.amount_max is not None
            and self.amount_min > self.amount_max
        ):
            raise ValueError("amount_min must be <= amount_max")
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("date_from must be <= date_to")
        return self


class SearchTransactionsResult(Contract):
    transactions: tuple[TransactionView, ...]
    truncated: bool = False


class GetTransactionArgs(Contract):
    transaction_id: Identifier


class GetTransactionResult(Contract):
    transaction: TransactionView
    open_dispute_id: Identifier | None = None


class GetDisputeArgs(Contract):
    dispute_id: Identifier | None = None
    transaction_id: Identifier | None = None

    @model_validator(mode="after")
    def _one_key(self) -> GetDisputeArgs:
        if (self.dispute_id is None) == (self.transaction_id is None):
            raise ValueError("provide exactly one of dispute_id or transaction_id")
        return self


class GetDisputeResult(Contract):
    dispute: DisputeCase


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


class CreateDisputeArgs(Contract):
    transaction_id: Identifier
    reason: DisputeReason
    idempotency_key: IdempotencyKey


class CreateDisputeResult(Contract):
    dispute: DisputeCase
    created: bool = Field(description="False when an idempotent replay returned an existing case")
    verified: bool


class BlockCardArgs(Contract):
    product_id: Identifier
    reason: str = Field(min_length=1, max_length=200)
    idempotency_key: IdempotencyKey


class BlockCardResult(Contract):
    block: CardBlockEvent
    created: bool
    verified: bool


class CreateHandoffArgs(Contract):
    draft: HandoffDraft
    idempotency_key: IdempotencyKey


class CreateHandoffResult(Contract):
    handoff_id: Identifier
    routing: HandoffRouting
    created: bool
    verified: bool


# ---------------------------------------------------------------------------
# Registry and protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: ToolName
    args_model: type[Contract]
    result_model: type[Contract]
    description: str
    is_write: bool = False
    action: ActionType | None = None
    requires_confirmation: bool = False


TOOL_SPECS: dict[ToolName, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            ToolName.LIST_CARDS,
            ListCardsArgs,
            ListCardsResult,
            "List the session customer's cards (last4, type, status).",
        ),
        ToolSpec(
            ToolName.SEARCH_TRANSACTIONS,
            SearchTransactionsArgs,
            SearchTransactionsResult,
            "Search the session customer's transactions by amount, merchant, date or card.",
        ),
        ToolSpec(
            ToolName.GET_TRANSACTION,
            GetTransactionArgs,
            GetTransactionResult,
            "Get one of the session customer's transactions by id.",
        ),
        ToolSpec(
            ToolName.GET_DISPUTE,
            GetDisputeArgs,
            GetDisputeResult,
            "Get a dispute of the session customer by dispute id or transaction id.",
        ),
        ToolSpec(
            ToolName.CREATE_DISPUTE,
            CreateDisputeArgs,
            CreateDisputeResult,
            "Create a dispute for one transaction. Needs a confirmation token; "
            "verified by read-back.",
            is_write=True,
            action=ActionType.CREATE_DISPUTE,
            requires_confirmation=True,
        ),
        ToolSpec(
            ToolName.BLOCK_CARD,
            BlockCardArgs,
            BlockCardResult,
            "Block one of the session customer's cards. Needs a confirmation token.",
            is_write=True,
            action=ActionType.BLOCK_CARD,
            requires_confirmation=True,
        ),
        ToolSpec(
            ToolName.CREATE_HANDOFF,
            CreateHandoffArgs,
            CreateHandoffResult,
            "Create a structured handoff to a human agent.",
            is_write=True,
            action=ActionType.CREATE_HANDOFF,
        ),
    )
}


class Tool[ArgsT: Contract, ResultT: Contract](Protocol):
    """Every tool implementation satisfies this protocol (Task 8)."""

    @property
    def spec(self) -> ToolSpec: ...

    def run(self, ctx: ToolContext, args: ArgsT, /) -> ResultT: ...
