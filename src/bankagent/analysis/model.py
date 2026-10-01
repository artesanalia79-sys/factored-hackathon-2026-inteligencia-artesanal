"""Pure T16 cost model: no I/O. The report feeds it measured inputs and the assumptions.

Owner: Juan José (Task 16).

Per dispute routed to the agent (Regulator-channel disputes are out of scope):

- today:  every dispute pays human intake (by reception channel) + investigation.
- agent:  `escalation_share` pays today's full intake + investigation; `deflection_share` ends
          at the RECOGNIZE step (no dispute, no investigation; capped at the non-escalated share);
          the rest is filed by the agent and pays investigation reduced by
          `handoff_time_reduction`. Every case pays the LLM cost.

Counterfactual: today's human intake deflects nothing, i.e. every dispute received today is
investigated. The complaints table counts disputes after intake and the 19 % first-party share
behind the deflection assumption is measured on disputes already filed, so this is consistent
with both; there is no source for a separate parameter.

Money = human minutes x the weighted, fully loaded labour cost per minute (USD). This is
projection arithmetic in float (inputs are ranges, not ledger amounts), rounded only for display;
recorded as an exception to the Decimal-for-money rule in `docs/decision_ledger.md`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from bankagent.analysis.assumptions import (
    Assumptions,
    Country,
    Intake,
    Label,
    Labor,
    Scenario,
)

Definition = Literal["strict", "central", "broad"]
DEFINITIONS: tuple[Definition, ...] = ("strict", "central", "broad")
MINUTES_PER_HOUR = 60
WEEKS_PER_YEAR = 52
DAYS_PER_YEAR = 365


@dataclass(frozen=True, slots=True)
class Measured:
    """Inputs measured from the organizer data (`t16_*` analyses); aggregates only."""

    days: int
    customers: int
    disputes_strict: int
    disputes_central: int
    disputes_broad: int
    country_shares: Mapping[str, float]
    channel_shares: Mapping[str, float]
    call_center_talk_minutes: float
    capacity_ratio: float

    def per_year(self, count: float) -> float:
        return count * DAYS_PER_YEAR / self.days

    def disputes(self, definition: Definition) -> int:
        return {
            "strict": self.disputes_strict,
            "central": self.disputes_central,
            "broad": self.disputes_broad,
        }[definition]

    @property
    def annual_disputes(self) -> float:
        return self.per_year(self.disputes_central)


def paid_minutes_per_month(country: Country) -> float:
    return country.weekly_hours * WEEKS_PER_YEAR / 12 * MINUTES_PER_HOUR


def monthly_cost_local(country: Country, scenario: Scenario) -> float:
    s = country.scenario(scenario)
    return s.monthly_salary * (1 + s.employer_load) + s.monthly_extra


def cost_per_minute_usd(
    country: Country, scenario: Scenario, productive_share: float, overhead: float = 1.0
) -> float:
    """Loaded labour cost of one minute actually spent handling contacts, in USD."""
    handled_minutes = paid_minutes_per_month(country) * productive_share
    return monthly_cost_local(country, scenario) * overhead / handled_minutes / country.fx_per_usd


def _weights(labor: Labor, shares: Mapping[str, float]) -> dict[str, float]:
    unknown = set(shares) - set(labor.countries)
    if unknown:
        raise ValueError(f"no labour assumptions for countries {sorted(unknown)}")
    total = sum(shares.values())
    if total <= 0:
        raise ValueError("country shares must add up to a positive total")
    return {code: share / total for code, share in shares.items()}


def weighted_cost_per_minute_usd(
    labor: Labor, shares: Mapping[str, float], scenario: Scenario, overhead: float = 1.0
) -> float:
    productive = labor.productive_share.scenario(scenario)
    return sum(
        w * cost_per_minute_usd(labor.countries[code], scenario, productive, overhead)
        for code, w in _weights(labor, shares).items()
    )


def productive_hours_per_fte_year(labor: Labor, shares: Mapping[str, float]) -> float:
    """Hours per year one full-time agent actually spends handling cases (central scenario)."""
    productive = labor.productive_share.central
    return sum(
        w * labor.countries[code].weekly_hours * WEEKS_PER_YEAR * productive
        for code, w in _weights(labor, shares).items()
    )


def llm_cost_usd(input_tokens: float, output_tokens: float, prices: tuple[float, float]) -> float:
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


def eligible_share(measured: Measured, intake: Intake) -> float:
    """Share of disputes the agent can take (reception channel not excluded)."""
    return sum(s for ch, s in measured.channel_shares.items() if ch not in intake.excluded)


def intake_minutes(point: Mapping[str, float], measured: Measured, intake: Intake) -> float:
    """Today's human intake minutes per eligible dispute, weighted by reception channel."""
    eligible = {
        ch: share for ch, share in measured.channel_shares.items() if ch not in intake.excluded
    }
    unmapped = set(eligible) - set(intake.channel_groups)
    if unmapped:
        raise ValueError(f"reception channels without an intake group: {sorted(unmapped)}")
    total = sum(eligible.values())
    if total <= 0:
        raise ValueError("no eligible disputes: every reception channel is excluded")
    minutes = {
        "call_center": measured.call_center_talk_minutes + point["registration_minutes"],
        "written": point["written_intake_minutes"],
        "branch": point["branch_intake_minutes"],
    }
    return sum(share / total * minutes[intake.channel_groups[ch]] for ch, share in eligible.items())


@dataclass(frozen=True, slots=True)
class UnitEconomics:
    """Per eligible dispute routed to the agent."""

    intake_minutes: float
    investigation_minutes: float
    agent_intake_minutes: float
    agent_investigation_minutes: float
    cost_per_minute_usd: float
    llm_usd: float

    @property
    def today_minutes(self) -> float:
        return self.intake_minutes + self.investigation_minutes

    @property
    def agent_human_minutes(self) -> float:
        return self.agent_intake_minutes + self.agent_investigation_minutes

    @property
    def today_usd(self) -> float:
        return self.today_minutes * self.cost_per_minute_usd

    @property
    def agent_usd(self) -> float:
        return self.agent_human_minutes * self.cost_per_minute_usd + self.llm_usd

    @property
    def intake_minutes_saved(self) -> float:
        """Rests on what the agent does itself (intake), given the escalation share."""
        return self.intake_minutes - self.agent_intake_minutes

    @property
    def investigation_minutes_saved(self) -> float:
        """Rests on the industry assumptions (deflection, handoff time reduction)."""
        return self.investigation_minutes - self.agent_investigation_minutes

    @property
    def savings_usd(self) -> float:
        return self.today_usd - self.agent_usd

    @property
    def savings_share(self) -> float:
        return self.savings_usd / self.today_usd

    @property
    def human_minutes_saved(self) -> float:
        return self.today_minutes - self.agent_human_minutes


def unit_economics(point: Mapping[str, float], measured: Measured, intake: Intake) -> UnitEconomics:
    esc = point["escalation_share"]
    # Deflection happens at RECOGNIZE, so only among the cases that are not escalated.
    defl = min(point["deflection_share"], 1 - esc)
    intake_min = intake_minutes(point, measured, intake)
    investigation = point["investigation_minutes"]
    filed = 1 - esc - defl
    cpm = point["cost_per_minute_usd"] * point["overhead_multiplier"]
    if (intake_min + investigation) * cpm <= 0:
        raise ValueError("today's cost per dispute must be positive")
    return UnitEconomics(
        intake_minutes=intake_min,
        investigation_minutes=investigation,
        agent_intake_minutes=esc * intake_min,
        agent_investigation_minutes=(
            esc * investigation + filed * investigation * (1 - point["handoff_time_reduction"])
        ),
        cost_per_minute_usd=cpm,
        llm_usd=point["llm_cost_per_case_usd"],
    )


@dataclass(frozen=True, slots=True)
class Swing:
    """One tornado input: its low/central/high values and provenance."""

    name: str
    label: Label
    low: float
    central: float
    high: float


def swings(
    assumptions: Assumptions,
    measured: Measured,
    prices: tuple[float, float],
    overrides: Mapping[str, Swing] | None = None,
) -> dict[str, Swing]:
    """Every uncertain input with its range. Labour cost swings as one input (the scenarios)."""
    labor = assumptions.labor
    result = {
        "cost_per_minute_usd": Swing(
            "cost_per_minute_usd",
            labor.label,
            *(
                weighted_cost_per_minute_usd(labor, measured.country_shares, s)
                for s in ("cheap", "central", "expensive")
            ),
        ),
        "llm_cost_per_case_usd": Swing(
            "llm_cost_per_case_usd",
            assumptions.llm.label,
            *(
                llm_cost_usd(
                    getattr(assumptions.llm.input_tokens, level),
                    getattr(assumptions.llm.output_tokens, level),
                    prices,
                )
                for level in ("low", "central", "high")
            ),
        ),
    }
    for name, p in assumptions.parameters.items():
        result[name] = Swing(name, p.label, p.low, p.central, p.high)
    result.update(overrides or {})
    return result


def central_point(swing_map: Mapping[str, Swing]) -> dict[str, float]:
    return {name: s.central for name, s in swing_map.items()}


@dataclass(frozen=True, slots=True)
class Bar:
    """Savings per dispute with one input at its low / high value, the rest at central."""

    swing: Swing
    savings_at_low: float
    savings_at_high: float

    @property
    def span(self) -> float:
        return abs(self.savings_at_high - self.savings_at_low)


def tornado(
    swing_map: Mapping[str, Swing], evaluate: Callable[[Mapping[str, float]], float]
) -> list[Bar]:
    """Bars sorted by span (widest first); inputs whose range is a single value are dropped."""
    base = central_point(swing_map)
    bars = []
    for name, s in swing_map.items():
        if s.low == s.high:
            continue
        bars.append(Bar(s, evaluate({**base, name: s.low}), evaluate({**base, name: s.high})))
    return sorted(bars, key=lambda b: (-b.span, b.swing.name))


def joint_point(
    swing_map: Mapping[str, Swing], bars: list[Bar], *, worst: bool
) -> dict[str, float]:
    """Every swinging input at once at the end that lowers (worst) or raises the savings.

    A one-at-a-time tornado hides the joint case; this is its pessimistic/optimistic bound.
    """
    point = central_point(swing_map)
    for bar in bars:
        low_lowers = bar.savings_at_low < bar.savings_at_high
        point[bar.swing.name] = bar.swing.low if low_lowers == worst else bar.swing.high
    return point


@dataclass(frozen=True, slots=True)
class AnnualScenario:
    """One volume (scale x dispute definition): human effort and money at each adoption share.

    FTE are split into intake (what the agent does itself) and investigation (industry
    assumptions), so a reader can see which part of the saving is more solid.
    """

    scale: str
    definition: Definition
    disputes_per_year: float
    eligible_per_year: float
    fte_today_intake: float
    fte_today_investigation: float
    fte_saved_intake: dict[float, float]
    fte_saved_investigation: dict[float, float]
    savings_usd: dict[float, float]


def annual_scenarios(
    unit: UnitEconomics,
    measured: Measured,
    assumptions: Assumptions,
    hours_per_fte: float,
) -> list[AnnualScenario]:
    """Observed volume and a per-N-customers normalisation, for each dispute definition.

    No scenario scales disputes by the agents' declared capacity: nothing shows that complaints
    are sampled like contacts (decision ledger, T16 review).
    """
    share = eligible_share(measured, assumptions.intake)
    per_n = assumptions.scale.per_customers
    adoptions = assumptions.scale.adoption_shares

    def fte(per_year: float, minutes: float) -> float:
        return per_year * minutes / MINUTES_PER_HOUR / hours_per_fte

    scenarios = []
    for scale, factor in (
        ("This bank, observed volume", 1.0),
        (f"Per {per_n:,} customers", per_n / measured.customers),
    ):
        for definition in DEFINITIONS:
            per_year = measured.per_year(measured.disputes(definition)) * factor
            eligible = per_year * share
            scenarios.append(
                AnnualScenario(
                    scale=scale,
                    definition=definition,
                    disputes_per_year=per_year,
                    eligible_per_year=eligible,
                    fte_today_intake=fte(eligible, unit.intake_minutes),
                    fte_today_investigation=fte(eligible, unit.investigation_minutes),
                    fte_saved_intake={
                        a: fte(a * eligible, unit.intake_minutes_saved) for a in adoptions
                    },
                    fte_saved_investigation={
                        a: fte(a * eligible, unit.investigation_minutes_saved) for a in adoptions
                    },
                    savings_usd={a: a * eligible * unit.savings_usd for a in adoptions},
                )
            )
    return scenarios
