"""T16 cost model: hand-checked arithmetic, invariants and assumption validation (no data)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest
import yaml
from pydantic import ValidationError

from bankagent.analysis.assumptions import (
    DEFAULT_ASSUMPTIONS,
    EXTERNAL_LABELS,
    Assumptions,
    Country,
    Intake,
    Label,
    load_assumptions,
    load_prices,
)
from bankagent.analysis.model import (
    Measured,
    Swing,
    annual_scenarios,
    cost_per_minute_usd,
    eligible_share,
    intake_minutes,
    llm_cost_usd,
    tornado,
    unit_economics,
    weighted_cost_per_minute_usd,
)
from bankagent.analysis.svg import tornado_svg

INTAKE = Intake(
    channel_groups={"Call Center": "call_center", "Email": "written", "Branch": "branch"},
    excluded=("Regulator",),
)
MEASURED = Measured(
    days=365,
    customers=100_000,
    disputes_strict=800,
    disputes_central=1_000,
    disputes_broad=1_100,
    country_shares={"CO": 1.0},
    channel_shares={"Call Center": 0.5, "Email": 0.3, "Branch": 0.1, "Regulator": 0.1},
    call_center_talk_minutes=4.0,
    capacity_ratio=10.0,
)
POINT = {
    "cost_per_minute_usd": 0.2,
    "overhead_multiplier": 1.0,
    "investigation_minutes": 40.0,
    "deflection_share": 0.1,
    "handoff_time_reduction": 0.25,
    "escalation_share": 0.2,
    "registration_minutes": 2.0,
    "written_intake_minutes": 6.0,
    "branch_intake_minutes": 12.0,
    "llm_cost_per_case_usd": 0.01,
}


def _raw() -> dict[str, Any]:
    return yaml.safe_load(DEFAULT_ASSUMPTIONS.read_text(encoding="utf-8"))


def _country() -> Country:
    s = {"monthly_salary": 1000.0, "employer_load": 0.5, "monthly_extra": 100.0}
    return Country.model_validate(
        {
            "currency": "XXX",
            "fx_per_usd": 2.0,
            "weekly_hours": 40,
            "cheap": s,
            "central": s,
            "expensive": s,
            "sources": [{"title": "t", "url": "https://example.org"}],
        }
    )


def test_repo_assumptions_validate_and_external_values_are_sourced() -> None:
    a = load_assumptions()
    assert set(a.labor.countries) == {"MX", "CO", "AR"}
    for p in a.parameters.values():
        if p.label in EXTERNAL_LABELS:
            assert p.sources
    assert load_prices(a.llm.model) == (0.10, 0.50)


def test_cost_per_minute_matches_hand_calculation() -> None:
    # (1000 x 1.5 + 100) = 1600 local / month; 40 h x 52 / 12 x 60 = 10,400 paid min;
    # x 0.5 productive = 5,200 handled min; / FX 2 -> 1600 / 5200 / 2 per handled minute.
    country = _country()
    assert cost_per_minute_usd(country, "central", 0.5) == pytest.approx(1600 / 5200 / 2)
    assert cost_per_minute_usd(country, "central", 0.5, overhead=1.3) == pytest.approx(
        1.3 * 1600 / 5200 / 2
    )


def test_weighted_cost_uses_normalised_shares_and_rejects_unknown_countries() -> None:
    labor = load_assumptions().labor
    only_co = weighted_cost_per_minute_usd(labor, {"CO": 7.0}, "central")
    co = labor.countries["CO"]
    assert only_co == pytest.approx(
        cost_per_minute_usd(co, "central", labor.productive_share.central)
    )
    with pytest.raises(ValueError, match="BR"):
        weighted_cost_per_minute_usd(labor, {"BR": 1.0}, "central")


def test_intake_minutes_weights_eligible_channels_only() -> None:
    # Regulator is excluded; the rest renormalises to 0.5/0.9, 0.3/0.9, 0.1/0.9.
    expected = (0.5 * (4 + 2) + 0.3 * 6 + 0.1 * 12) / 0.9
    assert intake_minutes(POINT, MEASURED, INTAKE) == pytest.approx(expected)
    assert eligible_share(MEASURED, INTAKE) == pytest.approx(0.9)


def test_unmapped_reception_channel_is_rejected() -> None:
    measured = dataclasses.replace(MEASURED, channel_shares={"Fax": 1.0})
    with pytest.raises(ValueError, match="Fax"):
        intake_minutes(POINT, measured, INTAKE)


def test_unit_economics_hand_example() -> None:
    unit = unit_economics(POINT, MEASURED, INTAKE)
    intake = intake_minutes(POINT, MEASURED, INTAKE)
    today = intake + 40
    agent = 0.2 * today + (1 - 0.2 - 0.1) * 40 * (1 - 0.25)
    assert unit.today_minutes == pytest.approx(today)
    assert unit.agent_human_minutes == pytest.approx(agent)
    assert unit.today_usd == pytest.approx(today * 0.2)
    assert unit.agent_usd == pytest.approx(agent * 0.2 + 0.01)
    assert unit.savings_usd == pytest.approx((today - agent) * 0.2 - 0.01)


def test_deflection_only_happens_among_non_escalated_cases() -> None:
    unit = unit_economics(
        {**POINT, "escalation_share": 0.9, "deflection_share": 0.5}, MEASURED, INTAKE
    )
    assert unit.agent_human_minutes == pytest.approx(0.9 * unit.today_minutes)


def test_full_escalation_saves_nothing_but_costs_the_llm() -> None:
    unit = unit_economics({**POINT, "escalation_share": 1.0}, MEASURED, INTAKE)
    assert unit.human_minutes_saved == pytest.approx(0)
    assert unit.savings_usd == pytest.approx(-0.01)


@pytest.mark.parametrize(
    ("name", "lower", "higher", "savings_grow"),
    [
        ("deflection_share", 0.05, 0.25, True),
        ("handoff_time_reduction", 0.0, 0.3, True),
        ("investigation_minutes", 20, 60, True),
        ("cost_per_minute_usd", 0.1, 0.3, True),
        ("escalation_share", 0.1, 0.4, False),
        ("llm_cost_per_case_usd", 0.001, 0.02, False),
    ],
)
def test_savings_move_in_the_expected_direction(
    name: str, lower: float, higher: float, savings_grow: bool
) -> None:
    low = unit_economics({**POINT, name: lower}, MEASURED, INTAKE).savings_usd
    high = unit_economics({**POINT, name: higher}, MEASURED, INTAKE).savings_usd
    assert (high > low) is savings_grow


def test_tornado_is_sorted_by_span_and_skips_fixed_inputs() -> None:
    swing_map = {name: Swing(name, Label.TEAM, v, v, v) for name, v in POINT.items()}
    swing_map["deflection_share"] = Swing("deflection_share", Label.INDUSTRY, 0.05, 0.1, 0.25)
    swing_map["registration_minutes"] = Swing("registration_minutes", Label.TEAM, 1, 2, 3)
    bars = tornado(swing_map, lambda p: unit_economics(p, MEASURED, INTAKE).savings_usd)
    assert [b.swing.name for b in bars] == ["deflection_share", "registration_minutes"]
    assert bars[0].span >= bars[1].span
    svg = tornado_svg(bars, 1.0, "T <&>", "axis", lambda _n, v: f"{v:g}", {})
    root = ElementTree.fromstring(svg)  # noqa: S314 - our own generated markup
    assert root.tag.endswith("svg")
    assert "T &lt;&amp;&gt;" in svg


def test_annual_effect_scales_with_adoption_and_volume() -> None:
    a = load_assumptions()
    unit = unit_economics(POINT, MEASURED, INTAKE)
    rows = annual_scenarios(unit, MEASURED, a, hours_per_fte=1000)
    observed, declared, per_n = rows
    assert declared.disputes_per_year == pytest.approx(observed.disputes_per_year * 10)
    assert per_n.disputes_per_year == pytest.approx(1_000 / 100_000 * a.scale.per_customers)
    share = eligible_share(MEASURED, a.intake)
    assert observed.savings_usd[1.0] == pytest.approx(1_000 * share * unit.savings_usd)
    assert observed.savings_usd[0.5] == pytest.approx(observed.savings_usd[1.0] / 2)


def test_llm_cost_from_token_prices() -> None:
    assert llm_cost_usd(20_000, 2_000, (0.10, 0.50)) == pytest.approx(0.003)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("parameters", "investigation_minutes", "low"), 99, "low <= central <= high"),
        (("parameters", "deflection_share", "sources"), [], "needs at least one source"),
        (("parameters", "escalation_share", "high"), 1.5, "within"),
        (("parameters", "overhead_multiplier", "low"), 0.5, ">= 1"),
    ],
)
def test_invalid_assumptions_are_rejected(
    path: tuple[str, ...], value: object, message: str
) -> None:
    raw = _raw()
    node = raw
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValidationError, match=message):
        Assumptions.model_validate(raw)


def test_missing_parameter_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    del raw["parameters"]["investigation_minutes"]
    path = tmp_path / "a.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError, match="investigation_minutes"):
        load_assumptions(path)
