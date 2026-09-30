"""Render a Markdown evaluation report with per-slice tables and Wilson 95% intervals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from bankagent.contracts.enums import SystemVariant, UnsafeEvent
from bankagent.eval.gates import GateResult, GatesConfig
from bankagent.eval.metrics import (
    METRICS,
    SLICE_DIMENSIONS,
    SLICE_METRICS,
    Rate,
    SystemMetrics,
)
from bankagent.eval.scorer import ScoredRun

NOT_DEFINED = "not defined"


@dataclass(frozen=True, slots=True)
class ReportContext:
    suite_id: str
    generated_at: str
    cases_dir: str
    case_set_sha256: str
    n_cases: int
    repeats: int
    simulated: bool
    cost_assumptions: str


def fmt_rate(rate: Rate) -> str:
    if rate.n == 0:
        return f"{NOT_DEFINED} (n=0)"
    low, high = rate.interval or (0.0, 0.0)
    point = 100 * (rate.point or 0)
    return f"{rate.successes}/{rate.n} = {point:.1f}% [{100 * low:.1f}, {100 * high:.1f}]"


def fmt_usd(value: Decimal | None) -> str:
    return NOT_DEFINED if value is None else f"{value:.6f}"


def fmt_ms(value: float | None) -> str:
    return NOT_DEFINED if value is None else f"{value:.1f}"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def render(
    ctx: ReportContext,
    metrics: Mapping[SystemVariant, SystemMetrics],
    runs: Sequence[ScoredRun],
    gates: GatesConfig,
    gate_results: Sequence[GateResult],
) -> str:
    systems = [metrics[v] for v in SystemVariant if v in metrics]
    labels = [f"{m.variant.value} ({m.system_name})" for m in systems]
    out: list[str] = [f"# Evaluation report: {ctx.suite_id}", ""]
    if ctx.simulated:
        out += [
            "> **SIMULATED.** Both systems are scripted fakes (`bankagent.eval.fake`), not the "
            "real agent or the real LLM-only baseline. These numbers test the harness; they are "
            "not results.",
            "",
        ]
    out += [
        f"- Generated: {ctx.generated_at}",
        f"- Workload: `{ctx.cases_dir}`, {ctx.n_cases} cases x {ctx.repeats} repeats per system; "
        f"case set sha256 `{ctx.case_set_sha256[:16]}`",
        "- Unit of analysis: case (majority of repeats for good outcomes, any repeat for unsafe "
        "ones); Wilson 95% intervals in brackets, as percentages",
        f"- Cost assumptions: {ctx.cost_assumptions}",
        f"- Gates: `eval/gates.yaml` version {gates.version}, status **{gates.status}**",
        "",
        "## Headline metrics",
        "",
    ]
    rows = [
        [f"`{spec.name}`", spec.description, *(fmt_rate(m.rates[spec.name]) for m in systems)]
        for spec in METRICS
    ]
    out += _table(["Metric", "Definition", *labels], rows)
    out += ["", "## Unsafe events (cases with the event in any repeat)", ""]
    out += _table(
        ["Event", *labels],
        [
            [f"`{event.value}`", *(fmt_rate(m.event_rates[event]) for m in systems)]
            for event in UnsafeEvent
        ],
    )
    out += ["", "## Operating efficiency (per run and per turn)", ""]
    out += _table(
        ["Figure", *labels],
        [
            ["Runs", *(str(m.n_runs) for m in systems)],
            [
                "Turn latency p50 (ms, harness wall clock)",
                *(fmt_ms(m.latency_p50_ms) for m in systems),
            ],
            ["Turn latency p95 (ms, nearest rank)", *(fmt_ms(m.latency_p95_ms) for m in systems)],
            ["Total cost (USD)", *(fmt_usd(m.total_cost_usd) for m in systems)],
            [
                "Cost per attempted case run (USD)",
                *(fmt_usd(m.cost_per_attempted_usd) for m in systems),
            ],
            [
                "Cost per safe automated resolution (USD)",
                *(fmt_usd(m.cost_per_resolution_usd) for m in systems),
            ],
        ],
    )
    out += ["", "## Pooled per-run rates (descriptive only)", ""]
    out += _table(
        ["Rate", *labels],
        [[name, *(fmt_rate(m.pooled[name]) for m in systems)] for name in systems[0].pooled],
    )
    out += ["", "## Simulator coverage and diagnostics", ""]
    out += _table(
        ["Figure", *labels],
        [
            [
                "Agent questions the simulator could not classify",
                *(str(m.unclassified_questions) for m in systems),
            ],
            [
                "Runs with at least one unclassified question",
                *(str(m.runs_with_unclassified) for m in systems),
            ],
            [
                "Handoffs with trigger_rule_ids (diagnostic, proposed only)",
                *(fmt_rate(m.handoff_rule_ids) for m in systems),
            ],
        ],
    )
    for dimension in SLICE_DIMENSIONS:
        out += ["", f"## Slice: {dimension}", ""]
        rows = []
        values = sorted({value for m in systems for value in m.slices[dimension]})
        for value in values:
            for m in systems:
                rates = m.slices[dimension].get(value)
                if rates is None:
                    continue
                n = max(r.n for r in rates.values())
                rows.append(
                    [
                        value,
                        m.variant.value,
                        str(n),
                        *(fmt_rate(rates[name]) for name in SLICE_METRICS),
                    ]
                )
        out += _table([dimension, "system", "n cases", *SLICE_METRICS], rows)
    out += ["", "## Gates", ""]
    if gates.status != "frozen":
        out += ["Gates are a **draft**: not yet frozen by the team.", ""]
    if ctx.simulated or ctx.n_cases < gates.heldout.min_cases:
        out += [
            f"Informational only: this run has {ctx.n_cases} cases (the held-out plan needs "
            f">= {gates.heldout.min_cases}) or uses simulated systems.",
            "",
        ]
    out += _table(
        ["Gate", "System", "Metric", "Rule", "Observed", "n", "Status"],
        [
            [
                r.gate.id,
                r.gate.system.value,
                f"`{r.gate.metric}` {r.gate.statistic}",
                f"{r.gate.op} {_fmt_threshold(r)} (min n {r.gate.min_n})",
                "-" if r.observed is None else f"{r.observed:.4f}",
                "-" if r.n is None else str(r.n),
                r.status.value,
            ]
            for r in gate_results
        ],
    )
    out += ["", "## Per-case results", ""]
    by_key: dict[tuple[str, str], list[ScoredRun]] = {}
    for run in runs:
        by_key.setdefault((run.result.case_id, run.result.system.value), []).append(run)
    rows = []
    for (case_id, system), case_runs in sorted(by_key.items()):
        outcomes = ", ".join(r.result.final_outcome.value for r in case_runs)
        events = sorted({e.value for r in case_runs for e in r.result.unsafe_events})
        rows.append(
            [
                case_id,
                system,
                outcomes,
                str(sum(r.result.correct for r in case_runs)),
                ", ".join(events) or "-",
            ]
        )
    out += _table(
        ["Case", "System", "Final outcome per repeat", "Correct repeats", "Unsafe events"], rows
    )
    out += [
        "",
        "## Limitations",
        "",
        f"- n = {ctx.n_cases} cases. Zero observed failures in a small set does not establish zero "
        f"risk: the Wilson 95% upper bound for 0 unsafe cases out of {ctx.n_cases} is "
        f"{100 * (Rate(0, ctx.n_cases).upper or 0):.1f}%.",
        "- Slices with few cases have wide intervals; do not read disparities from them without "
        "more cases.",
        "- Offline evaluation with a scripted user; not a measured production improvement.",
        "",
    ]
    return "\n".join(out)


def _fmt_threshold(result: GateResult) -> str:
    if result.gate.compare_to is not None:
        base = "-" if result.threshold is None else f"{result.threshold:.4f}"
        return f"{result.gate.compare_to.value} ({base})"
    return f"{result.threshold:g}" if result.threshold is not None else "-"
