"""Load and validate ``policy/dispute_policy_v1.yaml``.

The file is data, not code: each rule names its ``rule_id``, what it checks (``kind`` and its
parameters) and its provenance. ``engine.py`` is the only place that reads ``kind`` and runs the
check; this module only parses and validates the shape.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[3]
POLICY_FILE = ROOT / "policy" / "dispute_policy_v1.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Provenance(_Strict):
    """Where a rule's parameters come from. ``synthetic`` is the default; flip it explicitly."""

    synthetic: bool = True
    source: str
    verified_by: str | None = None
    verified_on: date | None = None


class _RuleBase(_Strict):
    rule_id: str
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
    missing from the map fails closed (``no_open_case``-style ineligible), not a silent default.
    """

    kind: Literal["window"] = "window"
    window_days: dict[str, int]


class FraudScoreRule(_RuleBase):
    """Escalation: a disputed transaction at or above the threshold goes to a human, not ACT."""

    kind: Literal["fraud_score"] = "fraud_score"
    threshold: float = Field(ge=0.0, le=100.0)


class RepeatDisputerRule(_RuleBase):
    """Escalation: a customer with a recent prior claim goes to a human, not ACT."""

    kind: Literal["repeat_disputer"] = "repeat_disputer"
    window_days: int = Field(gt=0)


Rule = Annotated[
    ApprovedStatusRule | NoOpenCaseRule | WindowRule | FraudScoreRule | RepeatDisputerRule,
    Field(discriminator="kind"),
]

ELIGIBILITY_KINDS = ("approved_status", "no_open_case", "window")
ESCALATION_KINDS = ("fraud_score", "repeat_disputer")


class PolicyConfig(_Strict):
    """The whole policy file: a version, every rule in evaluation order, and the dispute SLA."""

    policy_version: str
    sla_days: int = Field(gt=0)
    rules: tuple[Rule, ...]

    def eligibility_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.kind in ELIGIBILITY_KINDS)

    def escalation_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.kind in ESCALATION_KINDS)


def load_policy(path: Path = POLICY_FILE) -> PolicyConfig:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    config = PolicyConfig.model_validate(data)
    ids = [rule.rule_id for rule in config.rules]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate rule_id in {sorted(ids)}")
    return config
