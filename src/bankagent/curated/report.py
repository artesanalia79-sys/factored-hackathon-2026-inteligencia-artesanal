"""The committed evidence of a curated end-to-end run, and the transcripts that stay local (T19).

``render_report`` writes counts, step names and rule ids only. ``leaks`` then looks for every
value of every case in that text (ids, merchants, amounts) and for the shapes a row value has
in the agent's copy (a card ending, a date); the caller refuses to write a report that holds
one. The transcripts hold the real conversations: they go under ``data/``, which git ignores.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from bankagent.contracts.serving import SERVING_CONTRACT_VERSION
from bankagent.curated.cases import Scenario
from bankagent.curated.run import CaseResult, Reply, RunResult, Turn, spoken_amount

# What each scenario shows, for the reader of the report.
SCENARIOS: dict[Scenario, tuple[str, str]] = {
    Scenario.ESCALATE_FRAUD: (
        "Fraud signal",
        "The charge's `fraud_score` is at or above the policy threshold: no dispute is "
        "created, the case goes to the fraud team.",
    ),
    Scenario.ESCALATE_REPEAT: (
        "Repeat disputer",
        "The customer has a dispute in the bank's history inside the policy window: the case "
        "goes to a person.",
    ),
    Scenario.ESCALATE_DATA: (
        "Inconsistent record",
        "The charge is dated before its card was opened: the case goes to a person.",
    ),
    Scenario.CHOOSE_AMONG_MATCHES: (
        "Two charges of the same amount",
        "The agent lists them, the customer picks one by its position, and the policy decides "
        "on that one.",
    ),
    Scenario.DISPUTE_CARD_NOT_ACTIVE: (
        "Dispute, card not active",
        "Eligible charge on a card the core no longer reports active: the dispute is created "
        "and no card block is offered.",
    ),
    Scenario.DISPUTE_BLOCK_DECLINED: (
        "Dispute, card block declined",
        "Eligible charge: the customer confirms the dispute and says no to the card block.",
    ),
    Scenario.DISPUTE_BLOCK_ACCEPTED: (
        "Dispute and card block",
        "Eligible charge: the customer confirms the dispute, then confirms the card block.",
    ),
    Scenario.ALREADY_DISPUTED: (
        "Same charge again",
        "After a dispute, a new session of the same customer asks for the same charge: "
        "refused, one dispute stays.",
    ),
    Scenario.RECOGNIZED: (
        "Charge recognized",
        "Shown the charge, the customer recognizes it: nothing is filed and no policy runs.",
    ),
    Scenario.INELIGIBLE_WINDOW: (
        "Out of the window",
        "The charge is older than the dispute window, counted from the serving DB's "
        "`as_of_date`: refused with that reason.",
    ),
    Scenario.INELIGIBLE_STATUS: (
        "Not a settled charge",
        "The transaction is pending, declined or reversed: refused with that reason.",
    ),
    Scenario.OTHER_CUSTOMERS_CHARGE: (
        "Another customer's charge",
        "The customer describes a charge that belongs to someone else: it is never found, "
        "and the agent stops after two requests for more data.",
    ),
}

REPLIES: dict[Reply, str] = {
    Reply.RECOGNIZE: "shows the charge and asks whether the customer recognizes it",
    Reply.CHOOSE: "lists the matching charges and asks which one",
    Reply.CLARIFY: "asks for the merchant or the exact amount",
    Reply.CONFIRM_DISPUTE: "asks to confirm the dispute",
    Reply.DISPUTED_OFFER_BLOCK: "claims the dispute and asks to confirm a card block",
    Reply.DISPUTED: "claims the dispute and ends",
    Reply.BLOCKED: "claims the card block and ends",
    Reply.BLOCK_DECLINED: "says the card will not be blocked and ends",
    Reply.ESCALATED: "claims the handoff to a person and ends",
    Reply.INELIGIBLE: "refuses with the reason of the rule that failed and ends",
    Reply.DEFLECTED: "thanks the customer for recognizing the charge and ends",
    Reply.ABSTAINED: "says it cannot continue safely and ends",
    Reply.OTHER: "something the script does not know",
}

NO_ROW = "No row of this serving DB fits the scenario, so it did not run."
_CARD_ENDING = re.compile(r"terminada en \d")
_LOCAL_DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")


class ReportLeak(RuntimeError):
    """The report text holds a value of a row. It must not be written."""


def leaks(text: str, cases: Sequence[CaseResult]) -> list[str]:
    """Which kinds of row value appear in ``text``. Names the kind only, never the value."""
    found: set[str] = set()
    for result in cases:
        case, charge = result.case, result.case.charge
        values = {
            "a customer id": (case.customer_id, charge.customer_id),
            "a transaction id": (charge.transaction_id,),
            "a card id": (charge.product_id,),
            "a merchant": (charge.merchant_name,) if charge.merchant_name else (),
            "an amount": (
                spoken_amount(charge.amount, charge.country),
                f"{charge.amount:,.2f}",
                str(charge.amount),
            ),
        }
        found.update(
            kind for kind, group in values.items() if any(value in text for value in group)
        )
    if _CARD_ENDING.search(text):
        found.add("a card ending")
    if _LOCAL_DATE.search(text):
        found.add("a transaction date")
    return sorted(found)


def _count(turns: Sequence[Turn], kind: Reply) -> int:
    return sum(turn.kind == kind for turn in turns)


def _result_table(result: RunResult, asked: Sequence[Scenario]) -> list[str]:
    lines = [
        "| Scenario | What happens | Cases | Passed | Sessions | Disputes | Card blocks "
        "| Handoffs |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for scenario in asked:
        cases = [case for case in result.cases if case.case.scenario == scenario]
        turns = [turn for case in cases for turn in case.turns]
        title, shows = SCENARIOS[scenario]
        disputes = _count(turns, Reply.DISPUTED) + _count(turns, Reply.DISPUTED_OFFER_BLOCK)
        lines.append(
            f"| {title} (`{scenario.value}`) | {shows} | {len(cases)} "
            f"| {sum(case.passed for case in cases)} "
            f"| {len({(id(case), turn.session) for case in cases for turn in case.turns})} "
            f"| {disputes} | {_count(turns, Reply.BLOCKED)} | {_count(turns, Reply.ESCALATED)} |"
        )
    return lines


def _trace(result: CaseResult) -> list[str]:
    lines = ["| Session | Customer types | The agent | Steps recorded |", "|---|---|---|---|"]
    for turn in result.turns:
        steps = "; ".join(turn.steps) or "none"
        lines.append(f"| {turn.session} | `{turn.shown}` | {REPLIES[turn.kind]} | {steps} |")
    return lines


def fingerprint(cases: Sequence[CaseResult]) -> str:
    """A digest of which rows the cases are: the same serving DB and seed give the same one."""
    picked = "\n".join(
        f"{case.case.case_id}:{case.case.customer_id}:{case.case.charge.transaction_id}"
        for case in cases
    )
    return hashlib.sha256(picked.encode("utf-8")).hexdigest()[:16]


def _mix(result: RunResult) -> str:
    countries = Counter(case.case.charge.country for case in result.cases)
    currencies = Counter(case.case.charge.currency for case in result.cases)
    kinds = Counter(case.case.charge.transaction_type for case in result.cases)

    def listed(counter: Counter[str]) -> str:
        return ", ".join(f"{name} {count}" for name, count in sorted(counter.items()))

    return (
        f"customer country: {listed(countries)}; currency: {listed(currencies)}; "
        f"transaction type: {listed(kinds)}"
    )


def render_report(
    result: RunResult,
    *,
    asked: Sequence[Scenario],
    per_scenario: int,
    seed: str,
    policy_version: str,
    run_on: date,
) -> str:
    """The evidence file. Raises ``ReportLeak`` instead of returning text with a row value."""
    meta = result.metadata
    passed = sum(case.passed for case in result.cases)
    sessions = len({(id(case), turn.session) for case in result.cases for turn in case.turns})
    turns = sum(len(case.turns) for case in result.cases)
    failed = [case for case in result.cases if not case.passed]
    lines = [
        "# The agent on the curated serving DB, end to end",
        "",
        "Generated by `uv run poe curated-e2e` (Task 19). Counts, step names and rule ids only: "
        "no id, name, merchant, amount, date or card of any row. The conversations themselves "
        "stay in the run's work folder, under `data/` (which git ignores) unless a folder "
        "outside the repository is given. Local only: the public service runs on the "
        "synthetic fixture bank.",
        "",
        f"- serving DB: `data_mode={meta.get('data_mode')}`, "
        f"`as_of_date={meta.get('as_of_date')}`, built at {meta.get('built_at')}, serving "
        f"contract {meta.get('contract_version')}",
        f"- source: {meta.get('source')}",
        f"- `validate_serving_db()`: {len(result.contract_problems)} problems against serving "
        f"contract {SERVING_CONTRACT_VERSION} (the app checks it at start and refuses a file "
        "that has any)",
        f"- every served row fits the view the runtime reads it into: "
        f"{result.row_check.transactions:,} transactions ({result.row_check.transaction_shapes:,} "
        f"distinct combinations of the constrained values) and {result.row_check.cards:,} cards "
        f"({result.row_check.card_shapes:,}), {len(result.row_check.problems)} problems "
        "(`bankagent.curated.rows`)",
        f"- policy `{policy_version}`; interpreter: the keyword rules (`LLM_PROVIDER=stub`, 0 "
        "USD, no text leaves the machine)",
        f"- run on {run_on.isoformat()}: seed `{seed}`, {per_scenario} cases per scenario, "
        f"{result.seconds:.0f} s",
        "",
        "## Result",
        "",
        f"**{passed} of {len(result.cases)} cases passed** ({sessions} sessions, {turns} "
        f"turns over HTTP). Service checks: "
        f"{'all held' if not result.probes else '; '.join(result.probes)}.",
        "",
        *_result_table(result, asked),
        "",
        f"The cases: {_mix(result)}. One customer per case, picked from the active customers "
        f"with card transactions. Fingerprint of the picked rows: `{fingerprint(result.cases)}` "
        "(the same serving DB and seed pick the same cases).",
        "",
    ]
    broken = (*result.contract_problems, *result.row_check.problems)
    if failed or broken:
        lines += ["### Failures", ""]
        lines += [f"- serving DB: {problem}" for problem in broken]
        lines += [f"- `{case.case.case_id}`: {'; '.join(case.failures)}" for case in failed]
        lines.append("")
    lines += [
        "## What ran",
        "",
        "- The production app, `create_default_app`, the same factory `poe serve` and the "
        "image run, with `DATA_MODE=curated`, a new ops store and a signing key made for the "
        "run. Requests go over HTTP to `/api/chat/turn` (in process, FastAPI's test client).",
        "- Sessions are opened in code, as the evaluation harness does. On organizer data the "
        "login never returns its code and no flag was added to get one "
        "(`docs/decision_ledger.md`, T19). A session is a stored row and a signed token, so "
        "every turn passes the same authentication as a logged-in customer's; only `Active` "
        "customers get one, the ones the login accepts.",
        "- What each case must end in is worked out without the policy engine: its own SQL on "
        "the serving DB plus the parameters of `policy/dispute_policy_v1.yaml` "
        "(`bankagent.curated.cases`).",
        "",
        "## What is checked",
        "",
        "For every session:",
        "",
        "- each reply is the one the script expects, in Spanish, and the conversation ends "
        "where it should;",
        "- the policy step names exactly the expected rules, or does not run where none is due;",
        "- every write (dispute, card block) is verified by its read-back, ran in a turn "
        "where the customer said yes, and follows a confirmation of its exact arguments;",
        "- every action a reply claims has a verified step in that same turn;",
        "- every message was interpreted by the keyword rules, no step failed and none cost "
        "anything;",
        "- the ops store gained exactly the expected disputes, blocks, handoffs and "
        "confirmation tokens, read by a second connection: the dispute carries the bank's "
        "amount, the policy version, the rules checked and the SLA date; the handoff carries "
        "the expected team and rule ids;",
        "- no reply shows a customer, transaction or card id, and nothing of another "
        "customer's charge;",
        "- `GET /api/chat/transactions` returns the customer's own newest transactions, in "
        "order, and nothing else.",
        "",
        "Once per run: `/ready` answers; a turn with no token, with a token signed by "
        "another key, or with a signed token of a session that was never stored gets 401; a "
        "login on this data returns no code.",
        "",
        "## Served tables",
        "",
        "| Table | Rows |",
        "|---|---|",
        *[f"| `{table}` | {rows:,} |" for table, rows in result.rows.items()],
        "",
        "## One case of each scenario, step by step",
        "",
        "The customer's words are shown with the values masked. A step reads: step or tool, "
        "outcome, `verified` when a read or a write was checked, and the policy rule ids.",
        "",
    ]
    ran = {case.case.scenario for case in result.cases}
    for scenario in asked:
        first = next((case for case in result.cases if case.case.scenario == scenario), None)
        lines += [f"### {SCENARIOS[scenario][0]} (`{scenario.value}`)", ""]
        lines += _trace(first) if first is not None else [NO_ROW]
        lines.append("")
    lines += ["## Not covered", ""]
    empty = [scenario for scenario in asked if scenario not in ran]
    if empty:
        names = ", ".join(f"`{scenario.value}`" for scenario in empty)
        lines.append(f"- {names}: no row of this serving DB fits, so nothing ran.")
    if Scenario.DISPUTE_CARD_NOT_ACTIVE in empty:
        lines.append(
            "- In the organizer data only cards the core reports `Active` have transactions "
            "(`docs/evidence/data_audit.md`, B2), so a dispute on a card that is no longer "
            "active cannot be shown on it. `tests/curated` runs that scenario on synthetic "
            "rows."
        )
    lines += [
        "- Portuguese: the scripted customer writes Spanish only. The organizer data has no "
        "customer language, so every customer is served as Spanish (`docs/limitations.md`, "
        "Data); the fixture bank's tests cover Portuguese.",
        "- The real model: only the keyword interpreter ran. A model run would send the "
        "customers' words, with amounts and merchants of organizer data, to an external "
        "service.",
        "- The duplicate-charge and purchase-not-received reasons, and the web UI: the UI "
        "needs a login, which organizer data does not allow.",
        "",
        "## Reproduce",
        "",
        "```",
        "uv sync --group data",
        "uv run poe ingest && uv run poe dbt-build && uv run poe serving-build",
        f"uv run poe curated-e2e --seed {seed} --per-scenario {per_scenario}",
        "```",
        "",
    ]
    text = "\n".join(lines)
    found = leaks(text, result.cases)
    if found:
        raise ReportLeak("the report would show " + ", ".join(found))
    return text


def write_transcripts(result: RunResult, path: Path) -> None:
    """Every conversation as it happened, one case per line. Organizer data: local only."""
    with path.open("w", encoding="utf-8") as handle:
        for case in result.cases:
            charge = case.case.charge
            handle.write(
                json.dumps(
                    {
                        "case_id": case.case.case_id,
                        "scenario": case.case.scenario.value,
                        "passed": case.passed,
                        "failures": case.failures,
                        "session_customer": case.case.customer_id,
                        "charge_customer": charge.customer_id,
                        "transaction_id": charge.transaction_id,
                        "product_id": charge.product_id,
                        "expected_decision": case.case.expected.decision,
                        "expected_rule_ids": case.case.expected.rule_ids,
                        "seconds": round(case.seconds, 3),
                        "turns": [
                            {
                                "session": turn.session,
                                "customer": turn.says,
                                "agent": turn.reply,
                                "kind": turn.kind.value,
                                "expected": turn.expects.value,
                                "claimed": turn.claimed,
                                "ended": turn.ended,
                                "steps": turn.steps,
                            }
                            for turn in case.turns
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
