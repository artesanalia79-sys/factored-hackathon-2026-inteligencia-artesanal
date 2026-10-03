"""Write tools: create a dispute, block a card, hand off to a human agent.

``create_dispute`` and ``block_card`` follow one sequence:

1. the policy decision in the context must allow the action;
2. a decision bound to a different target is refused: ``create_dispute`` checks the decision's
   transaction and ``block_card`` its card (a policy evaluated for A must not authorize, or
   stamp its SLA and rule ids onto, a write on B);
3. the target must be the session customer's (otherwise ``NotFound``);
4. ``create_dispute`` also refuses when the transaction already has an open case, agent-made or
   from before the agent existed (``InvalidArguments``, no token spent);
5. in one store transaction, the confirmation token is spent and the record is written, so a
   refused or failed write never burns the token and a spent token always has its record;
6. the record is read back; ``verified`` is true only if it equals what the tool reports.

The token is spent before the store looks for an existing record, so a call repeated with its
used token is refused; repeated with a fresh token it returns the stored record
(``created=False``, still one row). ``create_handoff`` needs no confirmation and no policy
decision: escalating must always be possible.

What the store's three refusals become, all without writing or spending the token:

- ``IdempotencyConflict`` (the key was used for a different request): a caller bug,
  ``InvalidArguments``.
- ``DisputeAlreadyExists`` / ``CardAlreadyBlocked`` (the same target under another key, e.g. two
  sessions racing; the policy's open-dispute rule stops the normal case earlier):
  ``InvalidArguments`` too. ``created=False`` is therefore always an exact replay, which is the
  only existing-state case the templates render.
"""

from __future__ import annotations

from bankagent.contracts.domain import CardBlockEvent, DisputeCase
from bankagent.contracts.enums import DisputeStatus, ToolName
from bankagent.contracts.errors import InvalidArguments, NotFound
from bankagent.contracts.handoff import HandoffPacket
from bankagent.contracts.tools import (
    BlockCardArgs,
    BlockCardResult,
    CreateDisputeArgs,
    CreateDisputeResult,
    CreateHandoffArgs,
    CreateHandoffResult,
    ToolContext,
)
from bankagent.store.ops import CardAlreadyBlocked, DisputeAlreadyExists, IdempotencyConflict
from bankagent.tools.base import BaseTool
from bankagent.tools.reads import CARD_NOT_FOUND, TRANSACTION_NOT_FOUND

# Stamped on a dispute written without a policy decision (LLM-only baseline of the evaluation).
UNSPECIFIED_POLICY_VERSION = "unspecified"
KEY_REUSED = "idempotency key already used for a different request"
ALREADY_DISPUTED = "this transaction already has a dispute"
ALREADY_BLOCKED = "this card is already blocked"
WRONG_TARGET = "the policy decision was evaluated for a different transaction"
WRONG_CARD = "the policy decision was evaluated for a different card"


class CreateDispute(BaseTool[CreateDisputeArgs, CreateDisputeResult]):
    name = ToolName.CREATE_DISPUTE

    def _run(self, ctx: ToolContext, args: CreateDisputeArgs) -> CreateDisputeResult:
        self._require_allowed_by_policy(ctx)
        policy = ctx.policy
        if policy is not None and policy.target_transaction_id not in (None, args.transaction_id):
            raise InvalidArguments(WRONG_TARGET)
        customer_id = ctx.session.customer_id
        store = self._deps.store
        transaction = self._deps.serving.transaction(customer_id, args.transaction_id)
        if transaction is None:
            raise NotFound(TRANSACTION_NOT_FOUND)
        # A dispute the agent already created on this transaction is caught below by
        # `DisputeAlreadyExists`; this catches a still-open case from before the agent existed.
        if self._deps.serving.open_complaint_id(customer_id, args.transaction_id) is not None:
            raise InvalidArguments(ALREADY_DISPUTED)
        token_id = self._require_token(ctx)
        dispute = DisputeCase(
            dispute_id=self._deps.new_id("DSP"),
            transaction_id=transaction.transaction_id,
            reason=args.reason,
            status=DisputeStatus.SUBMITTED,
            created_at=ctx.now,
            # Amount and currency are the bank's, never the caller's.
            amount=transaction.amount,
            currency=transaction.currency,
            sla_due_date=policy.sla_due_date if policy else None,
            idempotency_key=args.idempotency_key,
            policy_version=policy.policy_version if policy else UNSPECIFIED_POLICY_VERSION,
            rule_ids=policy.rule_ids if policy else (),
        )
        with store.transaction():
            self._consume_token(ctx, args, token_id)
            try:
                stored, created = store.insert_dispute(customer_id, dispute)
            except IdempotencyConflict as exc:
                raise InvalidArguments(KEY_REUSED) from exc
            except DisputeAlreadyExists as exc:
                raise InvalidArguments(ALREADY_DISPUTED) from exc
        verified = self._read_back(
            lambda: store.get_dispute(customer_id, transaction_id=args.transaction_id), stored
        )
        return CreateDisputeResult(dispute=stored, created=created, verified=verified)


class BlockCard(BaseTool[BlockCardArgs, BlockCardResult]):
    name = ToolName.BLOCK_CARD

    def _run(self, ctx: ToolContext, args: BlockCardArgs) -> BlockCardResult:
        self._require_allowed_by_policy(ctx)
        policy = ctx.policy
        if policy is not None and policy.target_product_id not in (None, args.product_id):
            raise InvalidArguments(WRONG_CARD)
        customer_id = ctx.session.customer_id
        store = self._deps.store
        cards = self._deps.serving.cards(customer_id, args.product_id)
        if not cards:
            raise NotFound(CARD_NOT_FOUND)
        token_id = self._require_token(ctx)
        block = CardBlockEvent(
            block_id=self._deps.new_id("BLK"),
            product_id=cards[0].product_id,
            card_last4=cards[0].card_last4,
            blocked_at=ctx.now,
            reason=args.reason,
            idempotency_key=args.idempotency_key,
        )
        with store.transaction():
            self._consume_token(ctx, args, token_id)
            try:
                stored, created = store.insert_card_block(customer_id, block)
            except IdempotencyConflict as exc:
                raise InvalidArguments(KEY_REUSED) from exc
            except CardAlreadyBlocked as exc:
                raise InvalidArguments(ALREADY_BLOCKED) from exc
        verified = self._read_back(
            lambda: store.get_card_block(customer_id, args.product_id), stored
        )
        return BlockCardResult(block=stored, created=created, verified=verified)


class CreateHandoff(BaseTool[CreateHandoffArgs, CreateHandoffResult]):
    """Stores the handoff packet. Identity and trace are set here, server-side.

    The routing is stored exactly as requested: choosing the specialty (and an agent, if any)
    is the caller's decision, and the templates only claim a handoff whose stored routing
    equals the requested one.
    """

    name = ToolName.CREATE_HANDOFF

    def _run(self, ctx: ToolContext, args: CreateHandoffArgs) -> CreateHandoffResult:
        customer_id = ctx.session.customer_id
        store = self._deps.store
        packet = HandoffPacket.model_validate(
            {
                **args.draft.model_dump(),
                "trace_id": ctx.trace_id,
                "handoff_id": self._deps.new_id("HND"),
                "created_at": ctx.now,
                "customer_id": customer_id,
            }
        )
        try:
            stored, created = store.insert_handoff(packet, args.idempotency_key)
        except IdempotencyConflict as exc:
            raise InvalidArguments(KEY_REUSED) from exc
        verified = self._read_back(
            lambda: store.get_handoff(customer_id, stored.handoff_id), stored
        )
        return CreateHandoffResult(
            handoff_id=stored.handoff_id, routing=stored.routing, created=created, verified=verified
        )
