"""Write tools: create a dispute, block a card, hand off to a human agent.

``create_dispute`` and ``block_card`` follow one sequence:

1. the policy decision in the context must allow the action;
2. the target must be the session customer's (otherwise ``NotFound``);
3. in one store transaction, the confirmation token is spent and the record is written, so a
   refused or failed write never burns the token and a spent token always has its record;
4. the record is read back; ``verified`` is true only if it equals what the tool reports.

The token is spent before the store looks for an existing record, so a call repeated with its
used token is refused; repeated with a fresh token it returns the stored record
(``created=False``, still one row). ``create_handoff`` needs no confirmation and no policy
decision: escalating must always be possible.

What the store's three refusals become:

- ``IdempotencyConflict`` (the key was used for a different request): a caller bug,
  ``InvalidArguments``; nothing is written and the token is not spent.
- ``DisputeAlreadyExists`` / ``CardAlreadyBlocked`` (the same target under another key, e.g. two
  sessions racing): the stored record with ``created=False``. The customer's goal already
  holds, and ``created`` tells the renderer not to say it was done now.
"""

from __future__ import annotations

import hashlib

from bankagent.contracts.domain import CardBlockEvent, DisputeCase
from bankagent.contracts.enums import DisputeStatus, Specialty, ToolName
from bankagent.contracts.errors import InvalidArguments, NotFound
from bankagent.contracts.handoff import HandoffPacket, HandoffRouting
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


class CreateDispute(BaseTool[CreateDisputeArgs, CreateDisputeResult]):
    name = ToolName.CREATE_DISPUTE

    def _run(self, ctx: ToolContext, args: CreateDisputeArgs) -> CreateDisputeResult:
        self._require_allowed_by_policy(ctx)
        customer_id = ctx.session.customer_id
        store = self._deps.store
        transaction = self._deps.serving.transaction(customer_id, args.transaction_id)
        if transaction is None:
            raise NotFound(TRANSACTION_NOT_FOUND)
        token_id = self._require_token(ctx)
        policy = ctx.policy
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
                stored, created = exc.existing, False
        verified = self._read_back(
            lambda: store.get_dispute(customer_id, transaction_id=args.transaction_id), stored
        )
        return CreateDisputeResult(dispute=stored, created=created, verified=verified)


class BlockCard(BaseTool[BlockCardArgs, BlockCardResult]):
    name = ToolName.BLOCK_CARD

    def _run(self, ctx: ToolContext, args: BlockCardArgs) -> BlockCardResult:
        self._require_allowed_by_policy(ctx)
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
                stored, created = exc.existing, False
        verified = self._read_back(
            lambda: store.get_card_block(customer_id, args.product_id), stored
        )
        return BlockCardResult(block=stored, created=created, verified=verified)


class CreateHandoff(BaseTool[CreateHandoffArgs, CreateHandoffResult]):
    """Stores the handoff packet. Identity, trace and the agent are set here, server-side."""

    name = ToolName.CREATE_HANDOFF

    def _run(self, ctx: ToolContext, args: CreateHandoffArgs) -> CreateHandoffResult:
        customer_id = ctx.session.customer_id
        store = self._deps.store
        packet = HandoffPacket.model_validate(
            {
                **args.draft.model_dump(),
                "trace_id": ctx.trace_id,
                "routing": self._route(customer_id, args),
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

    def _route(self, customer_id: str, args: CreateHandoffArgs) -> HandoffRouting:
        """Assign an active agent of the specialty who speaks the language.

        Falls back to the general specialty (the curated data has no cards agents) and then to
        no agent at all: the handoff still enters the queue. The choice depends only on the
        customer, the idempotency key and the agent directory, so a replay is assigned the same
        agent and handoffs spread over the candidates.
        """
        requested = args.draft.routing
        digest = hashlib.sha256(f"{customer_id}\x1f{args.idempotency_key}".encode()).digest()
        for specialty in dict.fromkeys((requested.specialty, Specialty.GENERAL)):
            agents = self._deps.serving.active_agents(specialty, requested.language)
            if agents:
                chosen = agents[int.from_bytes(digest[:8]) % len(agents)]
                return requested.model_copy(update={"agent_id": chosen})
        return requested.model_copy(update={"agent_id": None})
