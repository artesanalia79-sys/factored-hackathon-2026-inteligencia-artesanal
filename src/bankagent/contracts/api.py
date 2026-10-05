"""HTTP wire models of the customer API (`/api/auth/*`, `/api/chat/turn`).

The web UI (T14) generates its TypeScript types from the exported schemas, so these shapes are
the contract between the API and the UI. Requests forbid unknown fields: a body carrying
``customer_id`` is rejected with 422 before it reaches any logic. No response carries
``customer_id``.
"""

from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from bankagent.contracts.base import Contract, Identifier, Last4
from bankagent.contracts.enums import CONFIRMED_WRITE_ACTIONS, ActionType, Language

# --- authentication --------------------------------------------------------------------------


class LoginRequest(Contract):
    persona_id: str = Field(min_length=1, max_length=64)
    access_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "the code shared with the people invited to a public demo (DEMO_ACCESS_CODE, T15); "
            "a deployment that sets one refuses a login without it with 403 access_code_required"
        ),
    )


class VerifyRequest(Contract):
    challenge_id: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=12)


class PersonaResponse(Contract):
    """A demo persona: an opaque id (a keyed hash), never the customer id."""

    persona_id: str
    first_name: str
    country: str
    language: Language | None


class LoginResponse(Contract):
    challenge_id: str
    expires_at: AwareDatetime
    delivery: str = "mock"
    mock_otp: str | None = Field(
        default=None,
        description="the code itself, only with AUTH_EXPOSE_MOCK_OTP on synthetic data",
    )


class VerifyResponse(Contract):
    token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a credential
    expires_at: AwareDatetime
    first_name: str
    language: Language | None


class SessionResponse(Contract):
    expires_at: AwareDatetime
    language: Language | None


# --- chat ------------------------------------------------------------------------------------


class ChatTurnRequest(Contract):
    text: str = Field(min_length=1, max_length=2000)
    language: Language | None = Field(
        default=None,
        description="preferred response language; omitted requests follow the customer's message",
    )


class ConfirmationView(Contract):
    """The write a confirmation question asks about, as the question states it.

    Built from the same verified read as the question (``bankagent.render``), and the values are
    the question's own display strings in the conversation's language: the UI labels them and
    never formats or derives them, so the panel cannot show other facts than the question. A
    dispute shows the reason and the charge (merchant, date, channel, amount, card ending); a
    card block shows the card ending only.
    """

    action: ActionType
    card_last4: Last4
    reason: str | None = Field(default=None, max_length=64)
    merchant: str | None = Field(default=None, max_length=64)
    date: str | None = Field(default=None, max_length=32)
    channel: str | None = Field(default=None, max_length=32)
    amount: str | None = Field(default=None, max_length=48)

    @model_validator(mode="after")
    def _facts_match_action(self) -> ConfirmationView:
        if self.action not in CONFIRMED_WRITE_ACTIONS:
            raise ValueError("only a confirmed write action is asked for confirmation")
        charge = (self.reason, self.merchant, self.date, self.channel, self.amount)
        if self.action == ActionType.CREATE_DISPUTE and any(value is None for value in charge):
            raise ValueError("a dispute confirmation shows the reason and the whole charge")
        if self.action == ActionType.BLOCK_CARD and any(value is not None for value in charge):
            raise ValueError("a card block confirmation shows the card ending only")
        return self


class ChatTurnResponse(Contract):
    """One agent reply.

    ``claimed_actions`` lists only writes verified by read-back. ``confirmation`` is set exactly
    when the reply asks the customer to confirm a write; the customer answers in the chat ("Sí"
    or "No"), and the token for the write is issued at that yes, server side. ``language`` is the
    language the agent replied in, which follows the requested preference when provided, or the
    customer's message otherwise. ``disputed_transaction_id`` is set exactly when
    ``claimed_actions`` includes ``create_dispute``: the disputed transaction's own id, already
    shown to the customer as "Ref. <id>" in the Transactions panel, so the UI can mark it there.
    ``blocked_product_id`` is the same for ``block_card``: the blocked card's product id, which
    each of its movements carries in that panel (``TransactionView.product_id``); not the card
    ending, which two cards of one customer can share. Unlike ``ConfirmationView`` (the
    question's own facts, nothing more), these fields are about a write already verified, not
    about what a question says, and neither may be set without its claim.
    """

    reply_text: str
    ended: bool
    claimed_actions: tuple[ActionType, ...]
    language: Language
    confirmation: ConfirmationView | None = None
    disputed_transaction_id: Identifier | None = None
    blocked_product_id: Identifier | None = None

    @model_validator(mode="after")
    def _marks_only_a_claimed_write(self) -> ChatTurnResponse:
        if (
            self.disputed_transaction_id is not None
            and ActionType.CREATE_DISPUTE not in self.claimed_actions
        ):
            raise ValueError("disputed_transaction_id needs a claimed create_dispute")
        if (
            self.blocked_product_id is not None
            and ActionType.BLOCK_CARD not in self.claimed_actions
        ):
            raise ValueError("blocked_product_id needs a claimed block_card")
        return self
