"""Outputs of the interpreter, the router and the policy engine."""

from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

from bankagent.contracts.base import (
    Contract,
    CurrencyCode,
    Identifier,
    Last4,
    NonNegativeMoney,
    Probability,
)
from bankagent.contracts.enums import (
    CONFIRMED_WRITE_ACTIONS,
    ActionType,
    DecisionType,
    Dialect,
    DialogueAct,
    Intent,
    Language,
)


class DisputeSlots(Contract):
    """Facts the customer mentioned. Unverified until matched against a real transaction."""

    amount: NonNegativeMoney | None = None
    currency: CurrencyCode | None = None
    merchant_query: str | None = Field(default=None, max_length=100)
    card_last4: Last4 | None = None
    date_text: str | None = Field(default=None, max_length=100)
    transaction_ref: str | None = Field(default=None, max_length=64)
    card_block_requested: bool = False


class InterpretationResult(Contract):
    """What the interpreter (LLM or keyword stub) understood from one customer message."""

    intent: Intent
    dialogue_act: DialogueAct
    slots: DisputeSlots = Field(default_factory=DisputeSlots)
    language: Language
    dialect: Dialect | None = None
    confidence: Probability
    injection_suspected: bool = False
    model: str
    prompt_version: str


class RouterResult(Contract):
    """Learned router output with a split-conformal prediction set.

    The router abstains whenever the prediction set is not a singleton.
    """

    prediction_set: tuple[Intent, ...]
    probabilities: dict[Intent, Probability] = Field(default_factory=dict)
    abstain: bool
    alpha: float = Field(gt=0.0, lt=1.0)
    q_hat: float
    model_version: str

    @model_validator(mode="after")
    def _abstain_iff_not_singleton(self) -> RouterResult:
        if self.abstain != (len(self.prediction_set) != 1):
            raise ValueError(
                "abstain must be True exactly when the prediction set is not a singleton"
            )
        return self

    @property
    def intent(self) -> Intent | None:
        return None if self.abstain else self.prediction_set[0]


class PolicyDecision(Contract):
    """Deterministic decision of the policy engine for one request.

    ``target_transaction_id`` is the transaction this decision was evaluated for, when it was
    evaluated against one (``None`` for a baseline/ad hoc decision with no bound target). A write
    tool that allows ``create_dispute`` must refuse a call whose ``args.transaction_id`` differs
    from it: otherwise a decision computed for transaction A would authorize, and stamp its SLA
    and rule ids onto, a dispute on transaction B (T8 PR #43 review; T9 PR #44 review).
    """

    decision: DecisionType
    rule_ids: tuple[str, ...] = ()
    explanation_keys: tuple[str, ...] = ()
    required_slots: tuple[str, ...] = ()
    allowed_actions: tuple[ActionType, ...] = ()
    requires_confirmation: bool = False
    escalation_triggers: tuple[str, ...] = ()
    sla_due_date: date | None = None
    target_transaction_id: Identifier | None = None
    policy_version: str

    @model_validator(mode="after")
    def _write_invariants(self) -> PolicyDecision:
        writes = CONFIRMED_WRITE_ACTIONS.intersection(self.allowed_actions)
        if self.decision != DecisionType.PROCEED and writes:
            raise ValueError(
                f"decision '{self.decision}' cannot allow write actions {sorted(writes)}"
            )
        if writes and not self.requires_confirmation:
            raise ValueError("write actions always require confirmation")
        return self
