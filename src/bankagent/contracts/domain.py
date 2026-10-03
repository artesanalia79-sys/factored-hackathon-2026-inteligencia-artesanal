"""Bank domain views returned by tools, plus sessions and confirmation tokens.

Views are what the agent may see. They deliberately exclude fraud labels and personal data;
fraud signals travel separately in ``TransactionRiskSignals`` and are read by policy only.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import AwareDatetime, Field, model_validator

from bankagent.contracts.base import (
    Contract,
    CountryCode,
    CurrencyCode,
    Identifier,
    Last4,
    Money,
    Sha256Hex,
)
from bankagent.contracts.enums import (
    ActionType,
    CardType,
    Channel,
    DisputeReason,
    DisputeStatus,
    Language,
    ProductStatus,
    TransactionStatus,
    TransactionType,
)


class Session(Contract):
    """Server-side authenticated session.

    ``customer_id`` never leaves the server: it is not sent to the LLM, not accepted from the
    client and not present in any tool argument model.
    """

    session_id: Identifier
    customer_id: Identifier
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    language: Language | None = None

    @model_validator(mode="after")
    def _expires_after_issue(self) -> Session:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self

    def is_active(self, now: datetime) -> bool:
        return self.issued_at <= now < self.expires_at


class CardView(Contract):
    product_id: Identifier
    card_type: CardType
    card_last4: Last4
    currency: CurrencyCode
    product_status: ProductStatus
    expiration_date: date | None = None


class TransactionView(Contract):
    """A transaction as the agent may see it. No fraud label or score by design (ADR 0003)."""

    transaction_id: Identifier
    product_id: Identifier
    card_last4: Last4 | None = None
    transaction_ts: AwareDatetime
    transaction_type: TransactionType
    transaction_category: str | None = None
    amount: Money
    currency: CurrencyCode
    amount_usd: Money | None = None
    channel: Channel
    merchant_name: str | None = None
    merchant_category: str | None = None
    transaction_country: CountryCode
    transaction_city: str | None = None
    transaction_status: TransactionStatus
    is_foreign: bool


class TransactionRiskSignals(Contract):
    """Risk inputs for the policy engine only. Never rendered or sent to the LLM.

    ``dq_flags`` are the serving table's data-quality flags for this transaction (ADR 0003: the
    views never carry them; only the policy reads them).
    """

    transaction_id: Identifier
    fraud_score: float | None = Field(default=None, ge=0.0, le=100.0)
    dq_flags: tuple[str, ...] = ()


class DisputeCase(Contract):
    dispute_id: Identifier
    transaction_id: Identifier
    reason: DisputeReason
    status: DisputeStatus
    created_at: AwareDatetime
    amount: Money
    currency: CurrencyCode
    sla_due_date: date | None = None
    idempotency_key: Identifier
    policy_version: str
    rule_ids: tuple[str, ...] = ()


class CardBlockEvent(Contract):
    block_id: Identifier
    product_id: Identifier
    card_last4: Last4
    blocked_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=200)
    idempotency_key: Identifier


class ConfirmationToken(Contract):
    """Single-use permission for one exact write, bound to the session and the argument hash."""

    token_id: Identifier
    session_id: Identifier
    action: ActionType
    args_hash: Sha256Hex
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    used_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _valid_times(self) -> ConfirmationToken:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if self.used_at is not None and self.used_at < self.issued_at:
            raise ValueError("used_at cannot be before issued_at")
        return self

    def is_usable_for(
        self, *, session_id: str, action: ActionType, args_hash: str, now: datetime
    ) -> bool:
        return (
            self.used_at is None
            and self.session_id == session_id
            and self.action == action
            and self.args_hash == args_hash
            and self.issued_at <= now < self.expires_at
        )
