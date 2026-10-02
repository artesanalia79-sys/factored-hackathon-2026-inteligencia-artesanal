"""Load and validate the T16 ROI assumptions (`config/roi_assumptions.yaml`).

Only values that the organizer data cannot measure live in that file, each with a range, a
label (market, industry, team, pending_eval) and its sources. Validation fails loudly on an
unordered range, a missing required parameter or an externally sourced value without sources.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ASSUMPTIONS = ROOT / "config" / "roi_assumptions.yaml"
DEFAULT_PRICING = ROOT / "config" / "pricing.yaml"

Scenario = Literal["cheap", "central", "expensive"]
SCENARIOS: tuple[Scenario, ...] = ("cheap", "central", "expensive")
IntakeGroup = Literal["call_center", "written", "branch"]

REQUIRED_PARAMETERS = frozenset(
    {
        "overhead_multiplier",
        "investigation_minutes",
        "deflection_share",
        "handoff_time_reduction",
        "escalation_share",
        "registration_minutes",
        "written_intake_minutes",
        "branch_intake_minutes",
    }
)
SHARE_PARAMETERS = frozenset({"deflection_share", "handoff_time_reduction", "escalation_share"})


class Label(StrEnum):
    """Provenance of a number; printed next to it in the evidence."""

    MEASURED = "measured"  # organizer data, offline measurement
    MARKET = "market"  # public LATAM salary, labour-law and FX data
    INDUSTRY = "industry"  # published dispute-operations studies
    TEAM = "team"  # team estimate without an external source
    PENDING_EVAL = "pending_eval"  # provisional until the T27 evaluation measures it
    OFFLINE_EVAL = "offline_eval"  # measured by the evaluation harness (held-out case mix)


EXTERNAL_LABELS = frozenset({Label.MARKET, Label.INDUSTRY})


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Source(_Model):
    title: str
    url: str


class Range(_Model):
    low: float
    central: float
    high: float

    @model_validator(mode="after")
    def _ordered(self) -> Range:
        if not self.low <= self.central <= self.high:
            raise ValueError(f"range must satisfy low <= central <= high, got {self}")
        return self


class Parameter(Range):
    description: str
    unit: str
    label: Label
    note: str = ""
    sources: tuple[Source, ...] = ()

    @model_validator(mode="after")
    def _sourced(self) -> Parameter:
        if self.label in EXTERNAL_LABELS and not self.sources:
            raise ValueError(f"a {self.label.value} parameter needs at least one source")
        return self

    def with_central(self, value: float, label: Label, note: str) -> Parameter:
        """Replace the central value (e.g. with an evaluation measurement), widening the range."""
        return self.model_copy(
            update={
                "low": min(self.low, value),
                "central": value,
                "high": max(self.high, value),
                "label": label,
                "note": note,
            }
        )


class LaborScenario(_Model):
    monthly_salary: float = Field(gt=0)
    employer_load: float = Field(ge=0)
    monthly_extra: float = Field(ge=0)


class Country(_Model):
    currency: str
    fx_per_usd: float = Field(gt=0)
    weekly_hours: float = Field(gt=0, le=60)
    cheap: LaborScenario
    central: LaborScenario
    expensive: LaborScenario
    note: str = ""
    sources: tuple[Source, ...] = Field(min_length=1)

    def scenario(self, name: Scenario) -> LaborScenario:
        return {"cheap": self.cheap, "central": self.central, "expensive": self.expensive}[name]


class ProductiveShare(_Model):
    cheap: float = Field(gt=0, le=1)
    central: float = Field(gt=0, le=1)
    expensive: float = Field(gt=0, le=1)

    def scenario(self, name: Scenario) -> float:
        return {"cheap": self.cheap, "central": self.central, "expensive": self.expensive}[name]


class Labor(_Model):
    description: str
    label: Label
    productive_share: ProductiveShare
    sources: tuple[Source, ...] = Field(min_length=1)
    countries: dict[str, Country] = Field(min_length=1)


class Llm(_Model):
    description: str
    label: Label
    model: str
    input_tokens: Range
    output_tokens: Range


class Intake(_Model):
    channel_groups: dict[str, IntakeGroup]
    excluded: tuple[str, ...] = ()


class Scale(_Model):
    adoption_shares: tuple[float, ...] = Field(min_length=1)
    per_customers: int = Field(gt=0)


class Assumptions(_Model):
    version: int
    as_of: date
    labor: Labor
    parameters: dict[str, Parameter]
    llm: Llm
    intake: Intake
    scale: Scale

    @model_validator(mode="after")
    def _complete(self) -> Assumptions:
        missing = REQUIRED_PARAMETERS - self.parameters.keys()
        if missing:
            raise ValueError(f"missing parameters: {sorted(missing)}")
        for name in SHARE_PARAMETERS:
            p = self.parameters[name]
            if not (p.low >= 0 and p.high <= 1):
                raise ValueError(f"{name} must stay within [0, 1]")
        esc, defl = self.parameters["escalation_share"], self.parameters["deflection_share"]
        if esc.high + defl.high > 1:
            raise ValueError("escalation_share.high + deflection_share.high must not exceed 1")
        if self.parameters["overhead_multiplier"].low < 1:
            raise ValueError("overhead_multiplier must be >= 1")
        return self

    def value(self, name: str) -> float:
        return self.parameters[name].central


def load_assumptions(path: Path = DEFAULT_ASSUMPTIONS) -> Assumptions:
    return Assumptions.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_prices(model: str, path: Path = DEFAULT_PRICING) -> tuple[float, float]:
    """(input, output) USD per million tokens of `model` from `config/pricing.yaml`."""
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    prices = config["models"][model]
    return float(prices["input_per_million_usd"]), float(prices["output_per_million_usd"])
