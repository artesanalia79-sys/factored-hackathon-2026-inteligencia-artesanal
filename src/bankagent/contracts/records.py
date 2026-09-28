"""Execution records: the audit trail every step emits and the evaluation scores from."""

from __future__ import annotations

from decimal import Decimal

from pydantic import AwareDatetime, Field, model_validator

from bankagent.contracts.base import Contract, Identifier, Sha256Hex
from bankagent.contracts.enums import (
    ConversationState,
    StepKind,
    StepOutcome,
    ToolErrorCode,
    ToolName,
)


class ExecutionRecord(Contract):
    """One step of one turn. Never contains raw PII, prompts or secrets."""

    record_id: Identifier
    trace_id: Identifier
    session_id: Identifier | None = None
    turn_index: int = Field(ge=0)
    step_index: int = Field(ge=0)
    step: StepKind
    state: ConversationState
    tool: ToolName | None = None
    args_hash: Sha256Hex | None = None
    outcome: StepOutcome
    verified: bool = False
    rule_ids: tuple[str, ...] = ()
    latency_ms: float = Field(ge=0.0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=8)
    model: str | None = None
    prompt_version: str | None = None
    error_code: ToolErrorCode | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _consistency(self) -> ExecutionRecord:
        if self.verified and self.outcome != StepOutcome.SUCCESS:
            raise ValueError("only successful steps can be verified")
        if self.step == StepKind.TOOL_CALL and self.tool is None:
            raise ValueError("tool_call records must name the tool")
        if self.error_code is not None and self.outcome == StepOutcome.SUCCESS:
            raise ValueError("successful steps cannot carry an error code")
        return self
