"""Load ``eval/gates.yaml`` and evaluate it against computed metrics.

A gate reads one statistic of one metric for one system and compares it with a fixed threshold or
with the same statistic of another system. A gate whose denominator is below ``min_n`` is
``UNDERPOWERED``: it is reported, never counted as passed (the dev smoke run is always
underpowered).
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bankagent.contracts.enums import SystemVariant
from bankagent.eval.metrics import METRICS_BY_NAME, SystemMetrics

ROOT = Path(__file__).resolve().parents[3]
GATES_FILE = ROOT / "eval" / "gates.yaml"

Statistic = Literal["count", "point", "wilson_lower", "wilson_upper", "value"]
SCALAR_METRICS = frozenset(
    {
        "latency_p50_ms",
        "latency_p95_ms",
        "total_cost_usd",
        "cost_per_attempted_usd",
        "cost_per_resolution_usd",
    }
)
_OPS: dict[str, Callable[[float, float], bool]] = {
    "<=": operator.le,
    ">=": operator.ge,
    "<": operator.lt,
    ">": operator.gt,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Gate(_Strict):
    id: str
    title: str
    system: SystemVariant
    metric: str
    statistic: Statistic
    op: Literal["<=", ">=", "<", ">"]
    threshold: float | None = None
    compare_to: SystemVariant | None = None
    min_n: int = Field(default=1, ge=0)
    rationale: str

    @model_validator(mode="after")
    def _valid(self) -> Gate:
        if (self.threshold is None) == (self.compare_to is None):
            raise ValueError(f"{self.id}: set exactly one of threshold or compare_to")
        scalar = self.metric in SCALAR_METRICS
        if not scalar and self.metric not in METRICS_BY_NAME:
            raise ValueError(f"{self.id}: unknown metric {self.metric}")
        if scalar != (self.statistic == "value"):
            raise ValueError(f"{self.id}: scalar metrics use statistic 'value' and only they do")
        return self


class HeldoutPlan(_Strict):
    min_cases: int = Field(ge=1)
    repeats: int = Field(ge=1)
    min_automatable_cases: int = Field(ge=0)
    min_requires_escalation_cases: int = Field(ge=0)
    min_dialect_share: float = Field(ge=0, le=1)


class GatesConfig(_Strict):
    version: int
    status: Literal["draft", "frozen"]
    frozen_at: date | None = None
    confidence: float
    unit_of_analysis: Literal["case"]
    budget_usd_total: float = Field(gt=0)
    heldout: HeldoutPlan
    gates: tuple[Gate, ...]

    @model_validator(mode="after")
    def _frozen_has_date(self) -> GatesConfig:
        if (self.status == "frozen") != (self.frozen_at is not None):
            raise ValueError("frozen_at is set exactly when status is frozen")
        ids = [g.id for g in self.gates]
        if len(set(ids)) != len(ids):
            raise ValueError("gate ids must be unique")
        return self


class GateStatus(StrEnum):
    PASS = "PASS"  # noqa: S105 (a gate status, not a password)
    FAIL = "FAIL"
    UNDERPOWERED = "UNDERPOWERED"
    NOT_DEFINED = "NOT_DEFINED"


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: Gate
    status: GateStatus
    observed: float | None
    threshold: float | None
    n: int | None


def load_gates(path: Path = GATES_FILE) -> GatesConfig:
    with path.open(encoding="utf-8") as handle:
        return GatesConfig.model_validate(yaml.safe_load(handle))


def statistic(metrics: SystemMetrics, metric: str, stat: Statistic) -> tuple[float | None, int]:
    """(value, n) of one statistic; n is -1 for scalar metrics."""
    if stat == "value":
        return metrics.scalar(metric), -1
    rate = metrics.rates[metric]
    values: dict[str, float | None] = {
        "count": float(rate.successes),
        "point": rate.point,
        "wilson_lower": rate.lower,
        "wilson_upper": rate.upper,
    }
    return values[stat], rate.n


def evaluate(
    config: GatesConfig, metrics: Mapping[SystemVariant, SystemMetrics]
) -> list[GateResult]:
    results: list[GateResult] = []
    for gate in config.gates:
        system = metrics.get(gate.system)
        if system is None:
            results.append(GateResult(gate, GateStatus.NOT_DEFINED, None, gate.threshold, None))
            continue
        observed, n = statistic(system, gate.metric, gate.statistic)
        threshold = gate.threshold
        if gate.compare_to is not None:
            other = metrics.get(gate.compare_to)
            threshold = (
                statistic(other, gate.metric, gate.statistic)[0] if other is not None else None
            )
        if observed is None or threshold is None:
            status = GateStatus.NOT_DEFINED
        elif 0 <= n < gate.min_n:
            status = GateStatus.UNDERPOWERED
        else:
            status = GateStatus.PASS if _OPS[gate.op](observed, threshold) else GateStatus.FAIL
        results.append(GateResult(gate, status, observed, threshold, None if n < 0 else n))
    return results
