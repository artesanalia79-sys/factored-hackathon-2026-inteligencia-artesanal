"""Evaluation cases (inputs to the harness) and per-run results (outputs of the scorer)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from bankagent.contracts.base import Contract, Identifier
from bankagent.contracts.enums import (
    ActionType,
    Dialect,
    EvalCategory,
    EvalSplit,
    FaultInjection,
    Language,
    Outcome,
    Provenance,
    SystemVariant,
    UnsafeEvent,
)

CaseId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")]


class UserTurn(Contract):
    text: str = Field(min_length=1, max_length=2000)


class FactSheet(Contract):
    """Ground truth the scripted user answers from when the agent asks questions."""

    target_transaction_id: Identifier | None = None
    recognizes_charge: bool | None = None
    confirms_actions: bool = True
    wants_card_block: bool = False
    clarification_answers: dict[str, str] = Field(default_factory=dict)
    requests_human: bool = False


class EvalCase(Contract):
    case_id: CaseId
    split: EvalSplit
    category: EvalCategory
    language: Language
    dialect: Dialect
    customer_id: Identifier = Field(
        description="used by the harness to open the session; never shown to the model"
    )
    turns: tuple[UserTurn, ...] = Field(min_length=1)
    facts: FactSheet = Field(default_factory=FactSheet)
    fault_injections: tuple[FaultInjection, ...] = ()
    expected_outcome: Outcome
    acceptable_outcomes: tuple[Outcome, ...]
    expected_actions: tuple[ActionType, ...] = ()
    forbidden_events: tuple[UnsafeEvent, ...] = ()
    in_scope: bool = True
    requires_escalation: bool = False
    provenance: Provenance
    author: str = Field(min_length=1, max_length=64)
    reviewed_by: str | None = None
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _labels_consistent(self) -> EvalCase:
        if self.expected_outcome not in self.acceptable_outcomes:
            raise ValueError("expected_outcome must be one of acceptable_outcomes")
        if self.requires_escalation and Outcome.ESCALATED not in self.acceptable_outcomes:
            raise ValueError("requires_escalation needs 'escalated' in acceptable_outcomes")
        if self.reviewed_by is not None and self.reviewed_by == self.author:
            raise ValueError("a case cannot be reviewed by its own author")
        if self.dialect.value.split("-")[0] != self.language.value:
            raise ValueError("dialect does not match language")
        return self


class EvalResult(Contract):
    """Score of one system on one case in one repeat, computed from ExecutionRecords."""

    case_id: CaseId
    system: SystemVariant
    run_id: Identifier
    repeat_index: int = Field(ge=0)
    final_outcome: Outcome
    actions_taken: tuple[ActionType, ...] = ()
    verified_actions: tuple[ActionType, ...] = ()
    escalated: bool
    handoff_complete: bool | None = None
    unsafe_events: tuple[UnsafeEvent, ...] = ()
    turns_used: int = Field(ge=0)
    latencies_ms: tuple[float, ...] = ()
    cost_usd_total: Decimal = Field(default=Decimal("0"), ge=0)
    correct: bool
    safe_automated_resolution: bool
    automation_attempted: bool
    trace_ids: tuple[str, ...] = ()
    versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistency(self) -> EvalResult:
        if not set(self.verified_actions).issubset(self.actions_taken):
            raise ValueError("verified_actions must be a subset of actions_taken")
        if self.safe_automated_resolution and not (
            self.correct
            and self.final_outcome == Outcome.AUTOMATED_RESOLUTION
            and not self.unsafe_events
        ):
            raise ValueError(
                "safe_automated_resolution requires a correct automated resolution "
                "without unsafe events"
            )
        return self
