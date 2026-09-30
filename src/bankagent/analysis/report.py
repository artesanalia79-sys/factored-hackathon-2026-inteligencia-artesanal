"""Generate the T16 problem evidence and ROI (`uv run poe analysis`).

Runs the `t16_*` analyses in `data_pipeline/dbt/analyses/` on the warehouse (silver plus the two
gold baselines), feeds the measured inputs and `config/roi_assumptions.yaml` into
`bankagent.analysis.model`, and writes to `docs/evidence/`:

- `call_center_baseline.md`, `complaints_baseline.md`: offline measurements, aggregates only;
- `roi.md` and `roi_tornado.svg`: cost per dispute today vs. with the agent, FTE, annual
  scenarios and the tornado sensitivity, every number labelled with its provenance.

`--eval-results eval/runs/<suite>/results.jsonl` (T27) replaces the provisional escalation share
and LLM cost with the proposed system's measurements. Refuses to run unless the warehouse comes
from a full silver build whose manifest matches `data/bronze/_manifest.json` (when present).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

import duckdb

from bankagent.analysis.assumptions import (
    DEFAULT_ASSUMPTIONS,
    DEFAULT_PRICING,
    Assumptions,
    Label,
    Source,
    load_assumptions,
    load_prices,
)
from bankagent.analysis.model import (
    Measured,
    Swing,
    annual_scenarios,
    central_point,
    cost_per_minute_usd,
    eligible_share,
    monthly_cost_local,
    productive_hours_per_fte_year,
    swings,
    tornado,
    unit_economics,
)
from bankagent.analysis.svg import tornado_svg
from bankagent.contracts.enums import Outcome, SystemVariant
from bankagent.contracts.evaluation import EvalResult
from bankagent.ingest.manifest import sha256_file
from bankagent.silver.build import DEFAULT_WAREHOUSE, ROOT
from bankagent.silver.report import (
    EVIDENCE_DIR,
    IncompleteBuildError,
    build_metadata,
    markdown_table,
    render_analysis,
)
from bankagent.silver.verify import MANIFEST_NAME

DEFAULT_MANIFEST = ROOT / "data" / "bronze" / MANIFEST_NAME
REQUIRED_GOLD = ("gold_cc_contact_baseline", "gold_complaints_baseline")
HUMAN_OUTCOMES = frozenset(
    {Outcome.ESCALATED, Outcome.ABSTAINED, Outcome.INCOMPLETE, Outcome.FAILED}
)
TORNADO_FILE = "roi_tornado.svg"

ANALYSES = (
    "t16_dispute_definition",
    "t16_complaint_subcategories",
    "t16_disputes_by_year",
    "t16_disputes_by_country",
    "t16_disputes_by_segment",
    "t16_complaints_by_country",
    "t16_dispute_intake_channel",
    "t16_dispute_status",
    "t16_dispute_vs_other_complaints",
    "t16_call_complaint_linkage",
    "t16_complaint_money_fields",
    "t16_fraud_repeat_on_card",
    "t16_cc_by_reason",
    "t16_cc_transactional_by_channel",
    "t16_cc_transactional_by_segment",
    "t16_cc_by_country",
    "t16_cc_repeat_contacts",
    "t16_cc_intake_aht",
    "t16_agent_capacity",
)

NAMES = {
    "cost_per_minute_usd": "Labour cost per handled minute",
    "overhead_multiplier": "Overhead multiplier",
    "investigation_minutes": "Investigation minutes per dispute",
    "deflection_share": "Deflected at RECOGNIZE",
    "handoff_time_reduction": "Investigation time saved by the handoff",
    "escalation_share": "Escalated to a human",
    "registration_minutes": "Phone registration minutes",
    "written_intake_minutes": "Email/Web/App intake minutes",
    "branch_intake_minutes": "Branch intake minutes",
    "llm_cost_per_case_usd": "LLM cost per case",
}
SHARES = frozenset({"deflection_share", "handoff_time_reduction", "escalation_share"})
USD = frozenset({"cost_per_minute_usd", "llm_cost_per_case_usd"})


class EvidenceError(RuntimeError):
    """The warehouse, gold baselines or evaluation results cannot back the evidence."""


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    headers: list[str]
    rows: list[tuple[Any, ...]]

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.headers, row, strict=True)) for row in self.rows]

    def one(self) -> dict[str, Any]:
        if len(self.rows) != 1:
            raise EvidenceError(f"{self.name} returned {len(self.rows)} rows, expected 1")
        return self.records()[0]

    def markdown(self) -> str:
        rounded = [
            tuple(round(v, 4) if isinstance(v, float) else v for v in row) for row in self.rows
        ]
        return f"`data_pipeline/dbt/analyses/{self.name}.sql`\n\n" + markdown_table(
            self.headers, rounded
        )


def check_snapshot(con: duckdb.DuckDBPyConnection, manifest: Path) -> dict[str, str]:
    """Build metadata of a full silver build whose manifest is the local one; gold present."""
    metadata = build_metadata(con)
    if manifest.exists() and sha256_file(manifest) != metadata.get("manifest_sha256"):
        raise EvidenceError(
            f"{manifest} does not match the manifest the warehouse was built from; "
            "run `uv run poe dbt-build` and `uv run poe serving-build` first."
        )
    present = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'gold_%'"
        ).fetchall()
    }
    missing = [t for t in REQUIRED_GOLD if t not in present]
    if missing:
        raise EvidenceError(f"gold baselines missing {missing}; run `uv run poe serving-build`.")
    return metadata


def run_analyses(con: duckdb.DuckDBPyConnection) -> dict[str, Table]:
    tables = {}
    for name in ANALYSES:
        cursor = con.execute(render_analysis(name))
        headers = [d[0] for d in cursor.description or []]
        tables[name] = Table(name, headers, cursor.fetchall())
    return tables


def measured_inputs(tables: Mapping[str, Table]) -> Measured:
    definition = tables["t16_dispute_definition"].one()
    return Measured(
        days=int(definition["days"]),
        customers=int(definition["customers"]),
        disputes_strict=int(definition["disputes_strict"]),
        disputes_central=int(definition["disputes_central"]),
        disputes_broad=int(definition["disputes_broad"]),
        country_shares={
            r["country"]: float(r["share"])
            for r in tables["t16_disputes_by_country"].records()
            if r["country"] is not None
        },
        channel_shares={
            r["reception_channel"]: float(r["share"])
            for r in tables["t16_dispute_intake_channel"].records()
        },
        call_center_talk_minutes=float(tables["t16_cc_intake_aht"].one()["avg_duration_min"]),
        capacity_ratio=float(tables["t16_agent_capacity"].one()["declared_to_observed_ratio"]),
    )


def eval_overrides(path: Path, swing_map: Mapping[str, Swing]) -> tuple[dict[str, Swing], int]:
    """Escalation share and mean LLM cost of the proposed system from an evaluation run."""
    results = [
        EvalResult.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    proposed = [r for r in results if r.system == SystemVariant.PROPOSED]
    if not proposed:
        raise EvidenceError(f"{path} has no results for the proposed system")
    measured = {
        "escalation_share": fmean(
            float(r.escalated or r.final_outcome in HUMAN_OUTCOMES) for r in proposed
        ),
        "llm_cost_per_case_usd": fmean(float(r.cost_usd_total) for r in proposed),
    }
    overrides = {}
    for name, value in measured.items():
        s = swing_map[name]
        overrides[name] = Swing(
            name, Label.OFFLINE_EVAL, min(s.low, value), value, max(s.high, value)
        )
    return overrides, len(proposed)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def usd(value: float) -> str:
    return f"${value:,.2f}" if abs(value) >= 1 else f"${value:,.3f}"


def show(name: str, value: float) -> str:
    if name in SHARES:
        return f"{value:.0%}"
    if name in USD:
        return f"${value:.3f}" if value >= 0.01 else f"${value:.4f}"
    if name == "overhead_multiplier":
        return f"x{value:g}"
    return f"{value:g} min"


def _sources(sources: Sequence[Source]) -> list[str]:
    return [f"  - [{s.title}]({s.url})" for s in sources]


def _header(title: str, metadata: Mapping[str, str], measured: Measured | None) -> list[str]:
    lines = [
        f"# {title}",
        "",
        "Generated by `uv run poe analysis` (Task 16). Aggregates only: counts, rates and "
        "quantiles, never row-level data. Every table names the query that produced it "
        "(`data_pipeline/dbt/analyses/`), run on the warehouse of this snapshot:",
        "",
        f"- bronze manifest sha256 `{metadata.get('manifest_sha256', 'unknown')}`",
        f"- silver built at {metadata.get('built_at', 'unknown')} "
        f"(`data_mode={metadata.get('data_mode', 'unknown')}`)",
    ]
    if measured is not None:
        lines.append(f"- span: {measured.days:,} days of process dates")
    return lines


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def call_center_report(
    tables: Mapping[str, Table], metadata: Mapping[str, str], measured: Measured
) -> str:
    reasons = tables["t16_cc_by_reason"].records()
    total = sum(int(r["contacts"]) for r in reasons)
    trans = next((r for r in reasons if r["reason_category"] == "Transaccional"), None)
    capacity = tables["t16_agent_capacity"].one()
    parts = _header("Call-center baseline (offline measurement)", metadata, measured)
    parts += [
        "",
        "Label: every number here is **measured** from the organizer data (offline). Nothing is "
        "projected. The ROI model that uses these numbers is in `roi.md`.",
        "",
        "## Key facts",
        "",
        f"- {total:,} contacts, about {measured.per_year(total):,.0f} per year.",
    ]
    if trans is not None:
        parts.append(
            f"- The source has no dispute label: `contact_reason` has 6 values. `Transaccional` "
            f"({int(trans['contacts']) / total:.1%} of contacts) is the closest one and only an "
            f"upper bound for dispute contacts; its phone contacts last "
            f"{measured.call_center_talk_minutes:.2f} min on average (the talk time the ROI "
            "model uses for a dispute received by phone)."
        )

    def fcr_spread(name: str) -> float:
        rates = [float(r["fcr_rate"]) for r in tables[name].records()]
        return (max(rates) - min(rates)) * 100

    parts += [
        "- First-contact resolution differs by **reason** (spread "
        f"{fcr_spread('t16_cc_by_reason'):.1f} percentage points) but hardly by channel "
        f"({fcr_spread('t16_cc_transactional_by_channel'):.1f}), segment "
        f"({fcr_spread('t16_cc_transactional_by_segment'):.1f}) or country "
        f"({fcr_spread('t16_cc_by_country'):.1f}); durations behave the same way (tables below).",
        "- `was_resolved` (first-contact resolution) does not predict repeat contacts: resolved "
        "and unresolved contacts repeat at the same rate within 7 days.",
        f"- Agents declare {int(capacity['declared_monthly_interactions']):,} interactions per "
        f"month; the interactions table holds {float(capacity['observed_monthly_contacts']):,.0f}"
        f" per month (x{measured.capacity_ratio:.1f}). The table looks like a sample of the "
        "bank's volume, so `roi.md` reports both volume scenarios.",
        "- Complaints cannot be linked to contacts, not even by customer and date "
        "(`complaints_baseline.md`), so contacts per dispute is not measurable.",
    ]
    for title, name, note in (
        ("By contact reason", "t16_cc_by_reason", ""),
        (
            "Transactional contacts by channel",
            "t16_cc_transactional_by_channel",
            "Duration exists only for Phone, App and Web; wait time only for Phone.",
        ),
        ("Transactional contacts by customer segment", "t16_cc_transactional_by_segment", ""),
        (
            "By customer country (gold baseline)",
            "t16_cc_by_country",
            "Re-aggregated from `gold_cc_contact_baseline` (sums, not averages of averages).",
        ),
        (
            "Repeat contacts within 7 days",
            "t16_cc_repeat_contacts",
            "Share of contacts whose customer's next contact comes within 7 days (any reason, "
            "and same reason).",
        ),
        ("Declared capacity vs. observed volume", "t16_agent_capacity", ""),
    ):
        parts += ["", f"## {title}", ""]
        if note:
            parts += [note, ""]
        parts.append(tables[name].markdown())
    return "\n".join(parts) + "\n"


def complaints_report(
    tables: Mapping[str, Table], metadata: Mapping[str, str], measured: Measured
) -> str:
    status = {r["status"]: r for r in tables["t16_dispute_status"].records()}
    open_share = sum(float(status[s]["share"]) for s in ("Open", "In Process") if s in status)
    resolved = status.get("Resolved", {})
    main_channel = tables["t16_dispute_intake_channel"].records()[0]
    years = [int(r["disputes_central"]) for r in tables["t16_disputes_by_year"].records()]
    vs_other = {
        bool(r["is_dispute"]): r for r in tables["t16_dispute_vs_other_complaints"].records()
    }
    linkage = tables["t16_call_complaint_linkage"].records()
    gap_7d = max(abs(float(r["contact_prior_7d"]) - float(r["placebo_7d"])) for r in linkage)
    money = tables["t16_complaint_money_fields"].records()
    no_currency = sum(int(r["disputes"]) for r in money if r["currency"] == "(null)")
    fraud = tables["t16_fraud_repeat_on_card"].one()
    parts = _header("Complaints and disputes baseline (offline measurement)", metadata, measured)
    parts += [
        "",
        "Label: every number here is **measured** from the organizer data (offline). Nothing is "
        "projected. The ROI model that uses these numbers is in `roi.md`.",
        "",
        "## Dispute definition",
        "",
        "The source has no dispute label. `Cargo no reconocido` (unrecognized charge) is the only "
        "named subcategory of category `Transactions`; every category has exactly one named "
        "subcategory plus NULLs (`t16_complaint_subcategories.sql`). Three levels:",
        "",
        markdown_table(
            ["level", "definition", "disputes in snapshot", "per year"],
            [
                [
                    "strict",
                    "Cargo no reconocido, case type Complaint or Claim",
                    measured.disputes_strict,
                    round(measured.per_year(measured.disputes_strict)),
                ],
                [
                    "**central** (used by the ROI)",
                    "every Cargo no reconocido",
                    measured.disputes_central,
                    round(measured.annual_disputes),
                ],
                [
                    "broad",
                    "every Transactions complaint (incl. NULL subcategory)",
                    measured.disputes_broad,
                    round(measured.per_year(measured.disputes_broad)),
                ],
            ],
        ),
        "",
        f"The bank has {measured.customers:,} customers: about "
        f"{measured.annual_disputes / measured.customers * 100:.1f} central disputes per 100 "
        "customers per year.",
        "",
        tables["t16_dispute_definition"].markdown(),
        "",
        tables["t16_complaint_subcategories"].markdown(),
        "",
        "## Key facts",
        "",
        f"- Central disputes per snapshot year range from {min(years):,} to {max(years):,}, so "
        "the snapshot is annualised as its average rate.",
        f"- {open_share:.0%} of central disputes are still Open or In Process at the snapshot "
        f"date; resolved ones took a median of {resolved.get('median_resolution_days')} days. "
        f"Median first response in the main channel ({main_channel['reception_channel']}): "
        f"{float(main_channel['median_first_response_hours'] or 0):.0f} h.",
        "- Disputes behave like every other complaint: average resolution days "
        f"{float(vs_other[True]['avg_resolution_days']):.1f} vs "
        f"{float(vs_other[False]['avg_resolution_days']):.1f}, SLA breach "
        f"{float(vs_other[True]['sla_breach_rate']):.1%} vs "
        f"{float(vs_other[False]['sla_breach_rate']):.1%}. The source does not model "
        "dispute-specific handling, so handling effort comes from `config/roi_assumptions.yaml`.",
        "- Complaints and call-center contacts look independent: the share of complaints with a "
        "contact from the same customer in the 7 days before differs from a placebo window 90 "
        f"days earlier by at most {gap_7d * 100:.1f} percentage points, even for complaints "
        "received by the call center. Contacts per dispute cannot be measured.",
        "- `claimed_amount` cannot size the money at stake: "
        f"{no_currency / measured.disputes_central:.0%} of central disputes have no currency, "
        "and the median amount is about the same in MXN, "
        "COP, ARS and USD although their values differ by orders of magnitude.",
        f"- Fraud does not repeat on a card: {int(fraud['later_fraud_transactions']):,} of "
        f"{int(fraud['fraud_transactions']):,} fraud transactions follow an earlier fraud on the "
        f"same card, {int(fraud['later_fraud_within_37h']):,} within 37 h. A faster card block "
        "has no measurable loss avoided in this data (offline `is_fraud`, ADR 0003).",
    ]
    for title, name, note in (
        ("Volume per snapshot year", "t16_disputes_by_year", ""),
        ("Disputes by customer country", "t16_disputes_by_country", ""),
        ("Disputes by customer segment", "t16_disputes_by_segment", ""),
        (
            "Complaints by customer country (gold baseline)",
            "t16_complaints_by_country",
            "From `gold_complaints_baseline`; gold has no subcategory, so only the broad "
            "definition (`transactions_complaints`) is available there.",
        ),
        ("How disputes reach the bank", "t16_dispute_intake_channel", ""),
        ("Dispute status and elapsed times", "t16_dispute_status", ""),
        ("Disputes vs. other complaints", "t16_dispute_vs_other_complaints", ""),
        (
            "Complaints vs. call-center contacts (placebo test)",
            "t16_call_complaint_linkage",
            "`contact_prior_*`: share of complaints whose customer had a contact in the 1 or 7 "
            "days before the complaint; `placebo_*`: same window length 90 days earlier.",
        ),
        ("Money fields", "t16_complaint_money_fields", ""),
        ("Repeat fraud on a card (offline label)", "t16_fraud_repeat_on_card", ""),
    ):
        parts += ["", f"## {title}", ""]
        if note:
            parts += [note, ""]
        parts.append(tables[name].markdown())
    return "\n".join(parts) + "\n"


def roi_report(
    tables: Mapping[str, Table],
    metadata: Mapping[str, str],
    measured: Measured,
    assumptions: Assumptions,
    swing_map: Mapping[str, Swing],
    eval_note: str,
) -> tuple[str, str]:
    """Return (`roi.md`, `roi_tornado.svg`)."""
    intake = assumptions.intake
    point = central_point(swing_map)
    unit = unit_economics(point, measured, intake)
    bars = tornado(swing_map, lambda p: unit_economics(p, measured, intake).savings_usd)
    hours = productive_hours_per_fte_year(assumptions.labor, measured.country_shares)
    annual = annual_scenarios(unit, measured, assumptions, hours)
    labor = assumptions.labor
    esc = point["escalation_share"]
    defl = min(point["deflection_share"], 1 - esc)
    elig = eligible_share(measured, intake)

    parts = _header("ROI: cost per dispute today vs. with the agent", metadata, measured)
    parts += [
        f"- assumptions: `config/roi_assumptions.yaml` (version {assumptions.version}, as of "
        f"{assumptions.as_of}); LLM prices: `config/pricing.yaml`",
        f"- evaluation inputs: {eval_note}",
        "",
        "**Provenance labels.** `measured`: offline measurement on the organizer data. `market`: "
        "public LATAM salary, labour-law and FX data. `industry`: published dispute-operations "
        "studies (US/EU; no public LATAM figures were found). `team`: team estimate. "
        "`pending_eval`: provisional until the final evaluation (T27) measures it. "
        "`offline_eval`: measured by the evaluation harness on the held-out case mix. "
        "`projection`: a computed scenario, **not** a measured production result. Nothing in "
        "this document is a measured production saving.",
        "",
        "## Headline (central values, projection)",
        "",
        f"Per eligible dispute routed to the agent ({elig:.1%} of disputes; `Regulator` "
        "complaints stay out of scope). The agent is the **intake desk, not the investigator**: "
        "it authenticates, identifies the transaction, asks whether the customer recognizes it, "
        "files the dispute and blocks the card; the bank's analysts still investigate.",
        "",
        markdown_table(
            ["", "today", "with the agent", "change"],
            [
                [
                    "human minutes per dispute",
                    f"{unit.today_minutes:.1f}",
                    f"{unit.agent_human_minutes:.1f}",
                    f"{-unit.human_minutes_saved / unit.today_minutes:+.0%}",
                ],
                [
                    "cost per dispute (USD)",
                    usd(unit.today_usd),
                    usd(unit.agent_usd),
                    f"{-unit.savings_share:+.0%}",
                ],
                ["of which LLM (USD)", "-", show("llm_cost_per_case_usd", unit.llm_usd), ""],
            ],
        ),
        "",
        f"Savings: **{usd(unit.savings_usd)} per dispute ({unit.savings_share:.0%})**. Where "
        "they come from, per dispute:",
        "",
        f"- intake: {unit.intake_minutes:.1f} human minutes today (channel-weighted); with the "
        f"agent only the {esc:.0%} escalated cases need a human;",
        f"- deflection: {defl:.0%} of disputes end at RECOGNIZE and skip the "
        f"{unit.investigation_minutes:g}-minute investigation;",
        f"- handoff: filed disputes arrive prepared, saving "
        f"{point['handoff_time_reduction']:.0%} of the investigation time.",
        "",
        "## Model",
        "",
        "```",
        "today  = (intake + investigation) x cost_per_minute",
        "agent  = [ escalated x (intake + investigation)",
        "         + filed x investigation x (1 - handoff_time_reduction) ] x cost_per_minute",
        "         + llm_cost_per_case",
        "filed  = 1 - escalated - deflected      (deflected <= 1 - escalated)",
        "intake = sum over channels of share x minutes     (phone = measured talk + registration)",
        "cost_per_minute = (salary x (1 + employer_load) + extras) x overhead",
        "                  / (paid minutes per month x productive_share) / FX,",
        "                  weighted by the measured dispute share per country",
        "```",
        "",
        "Code: `src/bankagent/analysis/model.py` (unit-tested in `tests/analysis/`).",
        "",
        "## Measured inputs (`measured`)",
        "",
        markdown_table(
            ["input", "value", "query"],
            [
                [
                    "central disputes per year",
                    f"{measured.annual_disputes:,.0f}",
                    "t16_dispute_definition.sql",
                ],
                ["customers", f"{measured.customers:,}", "t16_dispute_definition.sql"],
                [
                    "dispute share by country",
                    ", ".join(f"{c} {s:.1%}" for c, s in measured.country_shares.items()),
                    "t16_disputes_by_country.sql",
                ],
                [
                    "reception channel mix",
                    ", ".join(f"{c} {s:.1%}" for c, s in measured.channel_shares.items()),
                    "t16_dispute_intake_channel.sql",
                ],
                [
                    "phone talk time of a Transactional contact",
                    f"{measured.call_center_talk_minutes:.2f} min",
                    "t16_cc_intake_aht.sql",
                ],
                [
                    "declared / observed contact volume",
                    f"x{measured.capacity_ratio:.1f}",
                    "t16_agent_capacity.sql",
                ],
            ],
        ),
        "",
        "## Labour cost per handled minute (`market`)",
        "",
        labor.description.strip(),
        "",
    ]
    rows = []
    for code, country in labor.countries.items():
        c = country.central
        rows.append(
            [
                code,
                f"{measured.country_shares.get(code, 0):.1%}",
                country.currency,
                f"{c.monthly_salary:,.0f}",
                f"{c.employer_load:.1%}",
                f"{monthly_cost_local(country, 'central'):,.0f}",
                f"{monthly_cost_local(country, 'central') / country.fx_per_usd:,.0f}",
                f"{country.weekly_hours:g}",
                *(
                    show(
                        "cost_per_minute_usd",
                        cost_per_minute_usd(country, s, labor.productive_share.scenario(s)),
                    )
                    for s in ("cheap", "central", "expensive")
                ),
            ]
        )
    cpm = swing_map["cost_per_minute_usd"]
    rows.append(
        [
            "**weighted**",
            "100%",
            "USD",
            "",
            "",
            "",
            "",
            "",
            show("cost_per_minute_usd", cpm.low),
            f"**{show('cost_per_minute_usd', cpm.central)}**",
            show("cost_per_minute_usd", cpm.high),
        ]
    )
    parts += [
        markdown_table(
            [
                "country",
                "dispute share",
                "currency",
                "salary (central)",
                "employer load",
                "loaded monthly (local)",
                "loaded monthly (USD)",
                "weekly hours",
                "USD/min cheap",
                "USD/min central",
                "USD/min expensive",
            ],
            rows,
        ),
        "",
        f"Productive share of paid time: cheap {labor.productive_share.cheap:.1%}, central "
        f"{labor.productive_share.central:.1%}, expensive {labor.productive_share.expensive:.1%}. "
        f"One full-time agent handles cases about {hours:,.0f} h per year (central, weighted).",
        "",
        "Country notes and sources:",
        "",
    ]
    for code, country in labor.countries.items():
        parts.append(f"- **{code}**: {country.note.strip()}")
        parts += _sources(country.sources)
    parts += ["- **Productive share**:", *_sources(labor.sources), ""]
    parts += [
        "## Other inputs",
        "",
        markdown_table(
            ["input", "low", "central", "high", "label", "unit"],
            [
                [
                    NAMES.get(name, name),
                    show(name, s.low),
                    show(name, s.central),
                    show(name, s.high),
                    s.label.value,
                    assumptions.parameters[name].unit
                    if name in assumptions.parameters
                    else "USD per case",
                ]
                for name, s in swing_map.items()
                if name != "cost_per_minute_usd"
            ],
        ),
        "",
    ]
    for name, p in assumptions.parameters.items():
        parts.append(f"- **{NAMES.get(name, name)}**: {p.description.strip()}")
        if p.note:
            parts.append(f"  {p.note.strip()}")
        parts += _sources(p.sources)
    parts += [
        f"- **{NAMES['llm_cost_per_case_usd']}**: {assumptions.llm.description.strip()} Model "
        f"`{assumptions.llm.model}`; tokens per case {assumptions.llm.input_tokens.low:,.0f} / "
        f"{assumptions.llm.input_tokens.central:,.0f} / {assumptions.llm.input_tokens.high:,.0f} "
        "input and "
        f"{assumptions.llm.output_tokens.low:,.0f} / {assumptions.llm.output_tokens.central:,.0f}"
        f" / {assumptions.llm.output_tokens.high:,.0f} output (low / central / high).",
        "",
        "## Sensitivity (tornado)",
        "",
        "Savings per dispute (USD) when one input moves to its low or high value and every other "
        "input stays central. Widest bar first.",
        "",
        f"![Tornado: savings per dispute]({TORNADO_FILE})",
        "",
        markdown_table(
            ["input", "low -> savings", "high -> savings", "span", "label"],
            [
                [
                    NAMES.get(b.swing.name, b.swing.name),
                    f"{show(b.swing.name, b.swing.low)} -> {usd(b.savings_at_low)}",
                    f"{show(b.swing.name, b.swing.high)} -> {usd(b.savings_at_high)}",
                    usd(b.span),
                    b.swing.label.value,
                ]
                for b in bars
            ],
        ),
        "",
        "## Annual effect (`projection`)",
        "",
        "Adoption is the share of eligible disputes that start in the agent channel; it is not "
        "assumed, each value is shown. The observed volume is small because the bank is small "
        f"({measured.customers:,} customers); the per-customer row lets any bank size apply it. "
        "The declared-capacity row scales the observed volume by what the agents declare "
        "(`call_center_baseline.md`).",
        "",
    ]
    adoptions = assumptions.scale.adoption_shares
    parts += [
        markdown_table(
            [
                "volume scenario",
                "disputes / year",
                "eligible / year",
                "FTE on disputes today",
                *(f"FTE saved @ {a:.0%}" for a in adoptions),
            ],
            [
                [
                    s.name,
                    f"{s.disputes_per_year:,.0f}",
                    f"{s.eligible_per_year:,.0f}",
                    f"{s.fte_today:,.1f}",
                    *(f"{s.fte_saved[a]:,.1f}" for a in adoptions),
                ]
                for s in annual
            ],
        ),
        "",
        markdown_table(
            ["volume scenario", *(f"USD saved / year @ {a:.0%}" for a in adoptions)],
            [[s.name, *(f"{s.savings_usd[a]:,.0f}" for a in adoptions)] for s in annual],
        ),
        "",
        "## What this does not claim",
        "",
        "- No measured production saving: every saving above is a projection from measured "
        "volumes and labelled assumptions. The final evaluation (T27) reports cost per attempted "
        "case and per safe automated resolution separately.",
        "- The agent does not investigate disputes; investigation effort only drops through "
        "deflection and the prepared handoff.",
        "- Contacts per dispute are not modelled: complaints and contacts are independent in the "
        "source (`complaints_baseline.md`), so repeat calls about a dispute are left out, which "
        "understates today's cost.",
        "- No fraud loss avoided by a faster card block: fraud does not repeat on a card in this "
        "data (`t16_fraud_repeat_on_card.sql`).",
        "- Not counted: platform hosting, build and maintenance, change management, card-network "
        "fees and chargeback outcomes, and the money at stake (`claimed_amount` is unusable).",
        "- Labour costs are for outsourced contact centers; bank staff cost more. Industry "
        "figures are US/EU; LATAM dispute operations may differ.",
        "",
        "## Reproduce",
        "",
        "```",
        "uv run poe analysis                                   # provisional evaluation inputs",
        "uv run poe analysis --eval-results eval/runs/<suite>/results.jsonl   # after T27",
        "```",
    ]
    svg = tornado_svg(
        bars,
        base=unit.savings_usd,
        title="Savings per dispute (USD): one input at a time, others central",
        axis_label="Savings per eligible dispute routed to the agent (USD)",
        display=show,
        names=NAMES,
    )
    return "\n".join(parts) + "\n", svg


def generate(
    con: duckdb.DuckDBPyConnection,
    assumptions: Assumptions,
    prices: tuple[float, float],
    manifest: Path,
    eval_results: Path | None = None,
) -> dict[str, str]:
    """All evidence files as {filename: text}."""
    metadata = check_snapshot(con, manifest)
    tables = run_analyses(con)
    measured = measured_inputs(tables)
    swing_map = swings(assumptions, measured, prices)
    eval_note = "provisional (`pending_eval`); the final evaluation (T27) has not run"
    if eval_results is not None:
        overrides, n = eval_overrides(eval_results, swing_map)
        swing_map = {**swing_map, **overrides}
        eval_note = f"`{eval_results.name}` from `{eval_results.parent}` ({n:,} proposed runs)"
    roi, svg = roi_report(tables, metadata, measured, assumptions, swing_map, eval_note)
    return {
        "call_center_baseline.md": call_center_report(tables, metadata, measured),
        "complaints_baseline.md": complaints_report(tables, metadata, measured),
        "roi.md": roi,
        TORNADO_FILE: svg,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=DEFAULT_WAREHOUSE)
    parser.add_argument("--out-dir", type=Path, default=EVIDENCE_DIR)
    parser.add_argument("--assumptions", type=Path, default=DEFAULT_ASSUMPTIONS)
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--eval-results", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.warehouse.exists():
        print(
            f"ERROR: {args.warehouse} not found; run `uv run poe dbt-build` and "
            "`uv run poe serving-build`.",
            file=sys.stderr,
        )
        return 1
    assumptions = load_assumptions(args.assumptions)
    prices = load_prices(assumptions.llm.model, args.pricing)
    con = duckdb.connect(str(args.warehouse), read_only=True)
    try:
        outputs = generate(con, assumptions, prices, args.manifest, args.eval_results)
    except (IncompleteBuildError, EvidenceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for filename, text in outputs.items():
        (args.out_dir / filename).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out_dir / filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
