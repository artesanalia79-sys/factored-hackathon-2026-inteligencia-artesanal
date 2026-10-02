"""Read tools: cards, transactions and disputes of the session customer, and nothing else.

The customer comes from ``ctx.session``; an id from the arguments is only ever looked up
together with it, so "not yours" and "does not exist" raise the same ``NotFound``.
"""

from __future__ import annotations

from bankagent.contracts.enums import ProductStatus, ToolName
from bankagent.contracts.errors import NotFound
from bankagent.contracts.tools import (
    GetDisputeArgs,
    GetDisputeResult,
    GetTransactionArgs,
    GetTransactionResult,
    ListCardsArgs,
    ListCardsResult,
    SearchTransactionsArgs,
    SearchTransactionsResult,
    ToolContext,
)
from bankagent.tools.base import BaseTool

TRANSACTION_NOT_FOUND = "transaction not found"
DISPUTE_NOT_FOUND = "dispute not found"
CARD_NOT_FOUND = "card not found"


class ListCards(BaseTool[ListCardsArgs, ListCardsResult]):
    name = ToolName.LIST_CARDS

    def _run(self, ctx: ToolContext, args: ListCardsArgs) -> ListCardsResult:
        customer_id = ctx.session.customer_id
        cards = [
            # The serving DB is read-only: a block made through the agent lives in the ops
            # store and is shown on top of the core status. Only an active card changes; a
            # closed or suspended one keeps what the core says.
            card.model_copy(update={"product_status": ProductStatus.BLOCKED})
            if card.product_status == ProductStatus.ACTIVE
            and self._deps.store.get_card_block(customer_id, card.product_id) is not None
            else card
            for card in self._deps.serving.cards(customer_id)
        ]
        if not args.include_inactive:
            cards = [card for card in cards if card.product_status == ProductStatus.ACTIVE]
        return ListCardsResult(cards=tuple(cards))


class SearchTransactions(BaseTool[SearchTransactionsArgs, SearchTransactionsResult]):
    name = ToolName.SEARCH_TRANSACTIONS

    def _run(self, ctx: ToolContext, args: SearchTransactionsArgs) -> SearchTransactionsResult:
        # One extra row tells whether more matches exist than the caller asked for.
        found = self._deps.serving.search_transactions(
            ctx.session.customer_id, args, limit=args.limit + 1
        )
        return SearchTransactionsResult(
            transactions=tuple(found[: args.limit]), truncated=len(found) > args.limit
        )


class GetTransaction(BaseTool[GetTransactionArgs, GetTransactionResult]):
    name = ToolName.GET_TRANSACTION

    def _run(self, ctx: ToolContext, args: GetTransactionArgs) -> GetTransactionResult:
        customer_id = ctx.session.customer_id
        transaction = self._deps.serving.transaction(customer_id, args.transaction_id)
        if transaction is None:
            raise NotFound(TRANSACTION_NOT_FOUND)
        dispute = self._deps.store.get_dispute(customer_id, transaction_id=args.transaction_id)
        open_dispute_id = (
            dispute.dispute_id
            if dispute is not None
            # A claim opened before the agent existed (dispute_history) is an open case too.
            else self._deps.serving.open_complaint_id(customer_id, args.transaction_id)
        )
        return GetTransactionResult(transaction=transaction, open_dispute_id=open_dispute_id)


class GetDispute(BaseTool[GetDisputeArgs, GetDisputeResult]):
    """Disputes created through the agent. Prior complaints are not ``DisputeCase``s."""

    name = ToolName.GET_DISPUTE

    def _run(self, ctx: ToolContext, args: GetDisputeArgs) -> GetDisputeResult:
        dispute = self._deps.store.get_dispute(
            ctx.session.customer_id,
            dispute_id=args.dispute_id,
            transaction_id=args.transaction_id,
        )
        if dispute is None:
            raise NotFound(DISPUTE_NOT_FOUND)
        return GetDisputeResult(dispute=dispute)
