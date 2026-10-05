"""The committed evidence of a curated run holds no value of any row (T19)."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pytest

from bankagent.curated import report as report_module
from bankagent.curated.cases import Scenario, pick_cases
from bankagent.curated.report import (
    NO_ROW,
    REPLIES,
    SCENARIOS,
    ReportLeak,
    fingerprint,
    leaks,
    render_report,
    write_transcripts,
)
from bankagent.curated.run import Reply, RunResult, run_cases, spoken_amount
from bankagent.policy.schema import PolicyConfig

RUN_ON = date(2026, 10, 4)


@pytest.fixture(scope="module")
def result(
    bank: Path, policy: PolicyConfig, as_of: date, tmp_path_factory: pytest.TempPathFactory
) -> RunResult:
    cases = pick_cases(bank, policy, as_of, per_scenario=2, seed="19")
    # The app installs process-wide log redaction. This fixture outlives a test, so the
    # per-test restore of tests/conftest.py would take the redacting factory for the original.
    factory, make_record = logging.getLogRecordFactory(), logging.Logger.makeRecord
    try:
        return run_cases(bank, tmp_path_factory.mktemp("report-run"), policy, cases)
    finally:
        logging.setLogRecordFactory(factory)
        logging.Logger.makeRecord = make_record


def _report(result: RunResult, **options: object) -> str:
    settings = {
        "asked": tuple(Scenario),
        "per_scenario": 2,
        "seed": "19",
        "policy_version": "dispute-v1.1",
        "run_on": RUN_ON,
        **options,
    }
    return render_report(result, **settings)  # type: ignore[arg-type]


def test_the_report_says_what_ran_and_how_it_ended(result: RunResult) -> None:
    assert [(case.case.case_id, case.failures) for case in result.cases if not case.passed] == []
    assert result.passed
    text = _report(result)
    assert f"**{len(result.cases)} of {len(result.cases)} cases passed**" in text
    assert "Service checks: all held." in text
    assert "`data_mode=curated`, `as_of_date=2026-06-17`" in text
    assert "`validate_serving_db()`: 0 problems" in text
    assert "40 transactions" in text
    assert "33 cards" in text
    assert "run on 2026-10-04: seed `19`, 2 cases per scenario" in text
    assert "| `transactions_enriched` | 40 |" in text
    assert "### Failures" not in text
    for scenario, (title, _) in SCENARIOS.items():
        assert f"### {title} (`{scenario.value}`)" in text
        assert f"| {title} (`{scenario.value}`) |" in text
    assert NO_ROW not in text  # the synthetic bank has a row for every scenario
    assert "no row of this serving DB fits" not in text
    # The scripted words are shown masked, next to what the agent did and recorded.
    assert "`Buenas tardes, tengo un cargo de <importe> <moneda> en <comercio> que" in text
    assert "create_dispute success verified" in text
    assert "policy blocked DSP-ESC-01" in text
    assert set(SCENARIOS) == set(Scenario)
    assert set(REPLIES) == set(Reply)


def test_no_value_of_any_row_of_the_bank_is_in_the_report(bank: Path, result: RunResult) -> None:
    text = _report(result)
    with duckdb.connect(str(bank), read_only=True) as con:
        ids = con.execute(
            "SELECT customer_id FROM customer_profile_min UNION ALL "
            "SELECT product_id FROM customer_cards UNION ALL "
            "SELECT transaction_id FROM transactions_enriched UNION ALL "
            "SELECT complaint_id FROM dispute_history UNION ALL "
            "SELECT first_name FROM customer_profile_min UNION ALL "
            "SELECT DISTINCT merchant_name FROM transactions_enriched "
            "WHERE merchant_name IS NOT NULL"
        ).fetchall()
        amounts = con.execute(
            "SELECT DISTINCT t.amount, p.country FROM transactions_enriched AS t "
            "JOIN customer_profile_min AS p ON p.customer_id = t.customer_id"
        ).fetchall()
        endings = con.execute("SELECT card_last4 FROM customer_cards").fetchall()
    assert len(ids) > 100
    for (value,) in ids:
        assert value not in text
    for amount, country in amounts:
        for written in (spoken_amount(amount, country), f"{amount:,.2f}", str(amount)):
            assert written not in text
    for (last4,) in endings:
        assert f"terminada en {last4}" not in text
    assert leaks(text, result.cases) == []


def test_each_kind_of_row_value_is_caught_and_named_without_the_value(result: RunResult) -> None:
    case = next(c.case for c in result.cases if c.case.charge.merchant_name)
    charge = case.charge
    for value, kind in (
        (case.customer_id, "a customer id"),
        (charge.transaction_id, "a transaction id"),
        (charge.product_id, "a card id"),
        (charge.merchant_name, "a merchant"),
        (spoken_amount(charge.amount, charge.country), "an amount"),
        (f"{charge.amount:,.2f}", "an amount"),
        (str(charge.amount), "an amount"),
        (f"tarjeta terminada en {charge.card_last4}", "a card ending"),
        ("fecha 12/06/2026", "a transaction date"),
    ):
        found = leaks(f"The agent said: {value}.", result.cases)
        assert found == [kind]
        assert str(value) not in " ".join(found)
    foreign = next(
        c.case for c in result.cases if c.case.scenario == Scenario.OTHER_CUSTOMERS_CHARGE
    )
    assert leaks(foreign.charge.customer_id, result.cases) == ["a customer id"]
    assert leaks("12 of 12 cases passed on 2026-10-04, 39 s", result.cases) == []


def test_a_report_that_would_show_a_row_value_is_never_returned(
    result: RunResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    merchant = next(
        c.case.charge.merchant_name for c in result.cases if c.case.charge.merchant_name
    )
    title, _ = SCENARIOS[Scenario.RECOGNIZED]
    monkeypatch.setitem(
        report_module.SCENARIOS, Scenario.RECOGNIZED, (title, f"A charge at {merchant}.")
    )
    with pytest.raises(ReportLeak, match="the report would show a merchant") as caught:
        _report(result)
    assert merchant not in str(caught.value)


def test_a_failed_case_and_a_missing_scenario_are_in_the_report(result: RunResult) -> None:
    broken = replace(result.cases[0])
    broken.failures = ["turn 2: expected escalated, got confirm_dispute"]
    without = tuple(
        case for case in result.cases if case.case.scenario != Scenario.DISPUTE_CARD_NOT_ACTIVE
    )
    failed = replace(
        result,
        cases=(broken, *without[1:]),
        probes=("a turn without a token was not refused",),
        contract_problems=("extra_rows: table not in the serving contract",),
    )
    assert not failed.passed
    text = _report(failed)
    assert f"**{len(without) - 1} of {len(without)} cases passed**" in text
    assert "Service checks: a turn without a token was not refused." in text
    assert "`validate_serving_db()`: 1 problems" in text
    assert "- serving DB: extra_rows: table not in the serving contract" in text
    assert f"- `{broken.case.case_id}`: turn 2: expected escalated, got confirm_dispute" in text
    assert "- `dispute_card_not_active`: no row of this serving DB fits, so nothing ran." in text
    assert "`docs/evidence/data_audit.md`, B2" in text
    assert NO_ROW in text


def test_only_the_scenarios_asked_for_are_reported(result: RunResult) -> None:
    asked = (Scenario.ESCALATE_FRAUD, Scenario.RECOGNIZED)
    some = replace(
        result, cases=tuple(case for case in result.cases if case.case.scenario in asked)
    )
    text = _report(some, asked=asked)
    assert "(`escalate_fraud`)" in text
    assert "(`escalate_repeat`)" not in text
    assert "**4 of 4 cases passed**" in text


def test_the_fingerprint_names_the_picked_rows_and_nothing_else(
    bank: Path, policy: PolicyConfig, as_of: date, result: RunResult
) -> None:
    again = pick_cases(bank, policy, as_of, per_scenario=2, seed="19")
    assert [case.case for case in result.cases] == again
    mark = fingerprint(result.cases)
    assert len(mark) == 16
    assert f"`{mark}`" in _report(result)
    assert fingerprint(result.cases[:-1]) != mark
    assert fingerprint(tuple(reversed(result.cases))) != mark


def test_the_transcripts_hold_the_conversations_and_stay_out_of_the_report(
    result: RunResult, tmp_path: Path
) -> None:
    path = tmp_path / "transcripts.jsonl"
    write_transcripts(result, path)
    lines = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert [line["case_id"] for line in lines] == [case.case.case_id for case in result.cases]
    first, case = lines[0], result.cases[0]
    assert first["passed"] is True
    assert first["transaction_id"] == case.case.charge.transaction_id
    assert first["turns"][0]["customer"] == case.turns[0].says
    assert first["turns"][0]["agent"] == case.turns[0].reply
    assert first["turns"][-1]["steps"] == list(case.turns[-1].steps)
    # What the transcript holds is exactly what the report must not.
    assert leaks(path.read_text("utf-8"), result.cases) != []
