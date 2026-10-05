"""Portable, read-only dev replay. No session, tool arguments or customer identity."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from bankagent.contracts.base import Contract, Sha256Hex
from bankagent.contracts.enums import ConversationState, StepKind, StepOutcome, ToolName
from bankagent.contracts.evaluation import EvalResult


class ComparisonStep(Contract):
    step: StepKind
    state: ConversationState
    tool: ToolName | None
    outcome: StepOutcome
    verified: bool
    rule_ids: tuple[str, ...]
    model: str | None

    @model_validator(mode="after")
    def _verified_success(self) -> ComparisonStep:
        if self.verified and self.outcome != StepOutcome.SUCCESS:
            raise ValueError("only successful steps can be verified")
        return self


class ComparisonTurn(Contract):
    user_text: str
    reply_text: str
    steps: tuple[ComparisonStep, ...]


class ComparisonRun(Contract):
    system_name: str
    result: EvalResult
    turns: tuple[ComparisonTurn, ...]

    @model_validator(mode="after")
    def _turn_count(self) -> ComparisonRun:
        if len(self.turns) != self.result.turns_used:
            raise ValueError("turn count must match scored result")
        return self


class ComparisonBundle(Contract):
    schema_version: Literal[1]
    suite_id: str
    case_set_sha256: Sha256Hex
    simulated: bool
    cost_assumptions: str
    runs: tuple[ComparisonRun, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_runs(self) -> ComparisonBundle:
        keys = [(r.result.case_id, r.result.repeat_index, r.result.system) for r in self.runs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate case/repeat/system in comparison")
        return self
