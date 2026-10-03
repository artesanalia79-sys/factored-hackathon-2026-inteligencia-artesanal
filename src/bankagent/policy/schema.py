"""Load and validate ``policy/dispute_policy_v1.yaml``.

The file is data, not code: each rule names its ``rule_id``, what it checks (``kind`` and its
parameters) and its provenance. ``engine.py`` is the only place that reads ``kind`` and runs the
check; this module only parses and validates the shape: one rule of every kind, unique
``DSP-<AREA>-<NN>`` ids, positive windows, and a provenance that says how it was checked.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bankagent.contracts.enums import ProductStatus

ROOT = Path(__file__).resolve().parents[3]
POLICY_FILE = ROOT / "policy" / "dispute_policy_v1.yaml"

RULE_ID_PATTERN = r"^DSP-[A-Z]+-\d{2}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Provenance(_Strict):
    """Where a rule's parameters come from. ``synthetic`` is the default; flip it explicitly.

    Three labels, printed by ``poe policy-explain``:

    - ``synthetic: true``: a placeholder nobody has checked against a primary source.
    - ``internal: true``: a system invariant (what the bank's own records allow), not a
      regulatory or business parameter, so there is no external source to verify.
    - neither: verified, which needs ``verified_by`` and ``verified_on``.
    """

    synthetic: bool = True
    internal: bool = False
    source: str
    verified_by: str | None = None
    verified_on: date | None = None

    @model_validator(mode="after")
    def _label_is_backed(self) -> Provenance:
        if self.internal and self.synthetic:
            raise ValueError("an internal rule is not synthetic; set synthetic: false")
        verified = not self.synthetic and not self.internal
        if verified and (self.verified_by is None or self.verified_on is None):
            raise ValueError("synthetic: false needs verified_by and verified_on (or internal)")
        return self

    @property
    def label(self) -> str:
        if self.synthetic:
            return "synthetic"
        return "internal" if self.internal else "verified"


class _RuleBase(_Strict):
    rule_id: Annotated[str, Field(pattern=RULE_ID_PATTERN)]
    description: str
    explanation_key: str
    provenance: Provenance
    todo: str | None = None


class ApprovedStatusRule(_RuleBase):
    """Eligibility: only a settled (``Approved``) transaction can be disputed."""

    kind: Literal["approved_status"] = "approved_status"


class NoOpenCaseRule(_RuleBase):
    """Eligibility: a transaction with an open case (agent-made or prior) cannot be disputed
    again."""

    kind: Literal["no_open_case"] = "no_open_case"


class WindowRule(_RuleBase):
    """Eligibility: the dispute must be filed within the customer's country window.

    ``window_days`` keys are the 2-letter country codes of ``CustomerProfile.country``. A country
    missing from the map fails closed (ineligible), not a silent default.
    """

    kind: Literal["window"] = "window"
    window_days: dict[str, Annotated[int, Field(gt=0)]] = Field(min_length=1)


class FraudScoreRule(_RuleBase):
    """Escalation: a disputed transaction at or above the threshold goes to a human, not ACT."""

    kind: Literal["fraud_score"] = "fraud_score"
    threshold: float = Field(ge=0.0, le=100.0)


class RepeatDisputerRule(_RuleBase):
    """Escalation: a customer with a recent prior dispute goes to a human, not ACT."""

    kind: Literal["repeat_disputer"] = "repeat_disputer"
    window_days: int = Field(gt=0)


class DqFlagRule(_RuleBase):
    """Escalation: a transaction carrying one of these data-quality flags goes to a human."""

    kind: Literal["dq_flag"] = "dq_flag"
    flags: tuple[str, ...] = Field(min_length=1)


class CardBlockableRule(_RuleBase):
    """Action: on ``proceed``, ``block_card`` is allowed only for the transaction's own card and
    only while the core reports one of these statuses."""

    kind: Literal["card_blockable"] = "card_blockable"
    statuses: tuple[ProductStatus, ...] = Field(min_length=1)


Rule = Annotated[
    ApprovedStatusRule
    | NoOpenCaseRule
    | WindowRule
    | FraudScoreRule
    | RepeatDisputerRule
    | DqFlagRule
    | CardBlockableRule,
    Field(discriminator="kind"),
]

ELIGIBILITY_KINDS = ("approved_status", "no_open_case", "window")
ESCALATION_KINDS = ("fraud_score", "repeat_disputer", "dq_flag")
ACTION_KINDS = ("card_blockable",)


class PolicyConfig(_Strict):
    """The whole policy file: a version, every rule in evaluation order, and the dispute SLA."""

    policy_version: str
    sla_days: int = Field(gt=0)
    rules: tuple[Rule, ...]

    @model_validator(mode="after")
    def _one_rule_per_kind(self) -> PolicyConfig:
        ids = [rule.rule_id for rule in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate rule_id in {sorted(ids)}")
        kinds = sorted(rule.kind for rule in self.rules)
        expected = sorted(ELIGIBILITY_KINDS + ESCALATION_KINDS + ACTION_KINDS)
        if kinds != expected:
            # A missing kind would silently pass every case it was meant to stop.
            raise ValueError(f"need exactly one rule of each kind {expected}, got {kinds}")
        return self

    def eligibility_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.kind in ELIGIBILITY_KINDS)

    def escalation_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.kind in ESCALATION_KINDS)

    def action_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.kind in ACTION_KINDS)


def load_policy(path: Path = POLICY_FILE) -> PolicyConfig:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return PolicyConfig.model_validate(data)
