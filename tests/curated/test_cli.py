"""`uv run poe curated-e2e` and `uv run poe curated-check` (T19)."""

from __future__ import annotations

import json
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from bankagent.contracts.enums import DataMode
from bankagent.curated import cli
from bankagent.curated import report as report_module
from bankagent.curated.cases import Scenario
from bankagent.store.selection import ROOT, SERVING_DBS


def _run(bank: Path, tmp_path: Path, *extra: str) -> int:
    return cli.main(
        [
            "--serving-db",
            str(bank),
            "--workdir",
            str(tmp_path / "work"),
            "--report",
            str(tmp_path / "report.md"),
            *extra,
        ]
    )


def test_the_whole_run_passes_and_writes_the_report_and_the_transcripts(
    bank: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(bank, tmp_path, "--per-scenario", "1") == cli.OK
    out = capsys.readouterr().out
    assert "validate_serving_db: 0 problems" in out
    assert "40 transactions" in out
    assert "12 of 12 cases passed" in out
    assert "service checks: all held" in out
    for scenario in Scenario:
        assert f"  {scenario.value:26}   1/1   passed" in out
    report = (tmp_path / "report.md").read_text("utf-8")
    assert "**12 of 12 cases passed**" in report
    assert "seed `19`, 1 cases per scenario" in report
    transcripts = (tmp_path / "work" / "transcripts.jsonl").read_text("utf-8").splitlines()
    assert len(transcripts) == 12
    assert all(json.loads(line)["passed"] for line in transcripts)
    # Nothing the run printed is a value of a row: the screen may be shared or logged.
    with duckdb.connect(str(bank), read_only=True) as con:
        ids = con.execute(
            "SELECT customer_id FROM customer_profile_min UNION ALL "
            "SELECT transaction_id FROM transactions_enriched"
        ).fetchall()
    assert not any(value in out for (value,) in ids)


def test_the_defaults_are_the_curated_file_and_the_committed_evidence() -> None:
    assert cli.DEFAULT_REPORT == ROOT / "docs" / "evidence" / "curated_e2e.md"
    assert cli.DEFAULT_WORK_ROOT == ROOT / "data" / "runtime" / "curated_e2e"
    tasks = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["poe"]["tasks"]
    assert tasks["curated-e2e"]["cmd"] == "python -m bankagent.curated.cli"
    assert tasks["curated-check"]["cmd"] == "python -m bankagent.curated.cli --check"
    # No `.env` is loaded: the run builds its own environment.
    assert "envfile" not in tasks["curated-e2e"]
    assert "envfile" not in tasks["curated-check"]
    assert SERVING_DBS[DataMode.CURATED].is_relative_to(ROOT / "data")


def test_only_a_run_of_every_scenario_replaces_the_committed_evidence(
    bank: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "evidence" / "curated_e2e.md"
    monkeypatch.setattr(cli, "DEFAULT_REPORT", evidence)
    base = ["--serving-db", str(bank), "--per-scenario", "1"]
    some = [*base, "--workdir", str(tmp_path / "some"), "--scenario", "recognized"]
    assert cli.main(some) == cli.OK
    assert "no report: only some scenarios ran" in capsys.readouterr().out
    assert not evidence.exists()
    assert cli.main([*base, "--workdir", str(tmp_path / "all")]) == cli.OK
    assert "**12 of 12 cases passed**" in evidence.read_text("utf-8")
    # Asked for by path, part of a run is written too.
    own = tmp_path / "own.md"
    again = [*base, "--workdir", str(tmp_path / "own"), "--scenario", "recognized"]
    assert cli.main([*again, "--report", str(own)]) == cli.OK
    assert "**1 of 1 cases passed**" in own.read_text("utf-8")


def test_no_report_is_written_when_asked_not_to(bank: Path, tmp_path: Path) -> None:
    code = _run(bank, tmp_path, "--per-scenario", "1", "--scenario", "recognized", "--no-report")
    assert code == cli.OK
    assert not (tmp_path / "report.md").exists()
    assert (tmp_path / "work" / "transcripts.jsonl").exists()


def test_only_the_scenarios_asked_for_run(
    bank: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(
        bank,
        tmp_path,
        "--per-scenario",
        "2",
        "--scenario",
        "escalate_fraud",
        "--scenario",
        "recognized",
    )
    assert code == cli.OK
    out = capsys.readouterr().out
    assert "4 of 4 cases passed" in out
    assert "ineligible_window" not in out
    report = (tmp_path / "report.md").read_text("utf-8")
    assert "(`escalate_fraud`)" in report
    assert "(`ineligible_window`)" not in report


def test_a_scenario_with_no_row_is_said_and_does_not_fail_the_run(
    bank: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Like the organizer data: no charge on a card that is not active.
    trimmed = tmp_path / "bank_curated.duckdb"
    shutil.copy(bank, trimmed)
    with duckdb.connect(str(trimmed)) as con:
        con.execute("UPDATE customer_cards SET product_status = 'Active'")
    code = _run(
        trimmed,
        tmp_path,
        "--per-scenario",
        "1",
        "--scenario",
        "dispute_card_not_active",
        "--scenario",
        "recognized",
    )
    assert code == cli.OK
    out = capsys.readouterr().out
    assert "dispute_card_not_active      0/0   passed  (no row of the serving DB fits)" in out
    assert "No row of this serving DB fits" in (tmp_path / "report.md").read_text("utf-8")


def test_a_run_in_which_nothing_can_run_fails(
    bank: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "bank_curated.duckdb"
    shutil.copy(bank, empty)
    with duckdb.connect(str(empty)) as con:
        con.execute("UPDATE customer_profile_min SET customer_status = 'Inactive'")
    assert _run(empty, tmp_path, "--per-scenario", "1") == cli.FAILED
    assert "no row of the serving DB fits any scenario" in capsys.readouterr().err
    assert not (tmp_path / "report.md").exists()


def test_a_failing_case_fails_the_command_and_is_named(
    bank: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("bankagent.orchestrator.agent.FRAUD_TRIGGER", "no-such-trigger")
    code = _run(bank, tmp_path, "--per-scenario", "1", "--scenario", "escalate_fraud")
    assert code == cli.FAILED
    out = capsys.readouterr().out
    assert "0 of 1 cases passed" in out
    assert (
        "FAILED escalate_fraud-01: the stored handoff is not the one the decision asks for" in out
    )
    report = (tmp_path / "report.md").read_text("utf-8")
    assert "### Failures" in report
    assert "**0 of 1 cases passed**" in report


def test_a_report_that_would_show_a_row_value_is_not_written(
    bank: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title, _ = report_module.SCENARIOS[Scenario.RECOGNIZED]
    monkeypatch.setitem(
        report_module.SCENARIOS, Scenario.RECOGNIZED, (title, "A charge at Tienda del Sur.")
    )
    code = _run(bank, tmp_path, "--per-scenario", "2", "--scenario", "recognized")
    assert code == cli.FAILED
    assert "ERROR: report not written: the report would show a merchant" in capsys.readouterr().err
    assert not (tmp_path / "report.md").exists()


def test_a_work_folder_that_was_used_is_refused_with_a_message(
    bank: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    extra = ("--per-scenario", "1", "--scenario", "recognized", "--no-report")
    assert _run(bank, tmp_path, *extra) == cli.OK
    capsys.readouterr()
    assert _run(bank, tmp_path, *extra) == cli.FAILED
    assert "every run needs a new, empty ops store" in capsys.readouterr().err


def test_the_conversations_never_go_where_git_would_see_them(
    bank: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inside = ROOT / "docs" / "curated-run-that-must-not-exist"
    try:
        code = cli.main(["--serving-db", str(bank), "--workdir", str(inside), "--no-report"])
        assert code == cli.FAILED
        assert "would put organizer data inside the repository" in capsys.readouterr().err
        assert not inside.exists()
    finally:
        # If the guard ever fails, this test must not be what leaves a run in the repository.
        shutil.rmtree(inside, ignore_errors=True)
    assert not cli.keeps_data_local(ROOT / "docs" / "x")
    assert not cli.keeps_data_local(ROOT / "src" / "bankagent" / "x")
    assert not cli.keeps_data_local(ROOT)
    assert cli.keeps_data_local(ROOT / "data" / "runtime" / "curated_e2e" / "x")
    assert cli.keeps_data_local(cli.DEFAULT_WORK_ROOT / "20261004T000000Z")
    assert cli.keeps_data_local(tmp_path / "x")
    # Not a folder that only looks like data/.
    assert not cli.keeps_data_local(ROOT / "data-export" / "x")
    assert not cli.keeps_data_local(ROOT / "data" / ".." / "docs" / "x")


def test_a_relative_path_is_read_from_the_callers_folder(
    bank: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(bank.parent)
    code = cli.main(["--serving-db", bank.name, "--check"])
    assert code == cli.OK


def test_check_passes_on_a_curated_file_and_runs_nothing(
    bank: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(bank, tmp_path, "--check") == cli.OK
    out = capsys.readouterr().out
    assert "data_mode=curated, as_of_date=2026-06-17, validate_serving_db: 0 problems" in out
    assert "rows against the runtime's views: 40 transactions" in out
    assert "33 cards (33): 0 problems" in out
    assert not (tmp_path / "work").exists()
    assert not (tmp_path / "report.md").exists()


def test_personas_are_not_curated_data(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    make_bank: Callable[..., dict[str, str]],
) -> None:
    personas = tmp_path / "bank.duckdb"
    make_bank(personas, data_mode="synthetic")
    for extra in (("--check",), ("--per-scenario", "1")):
        assert _run(personas, tmp_path, *extra) == cli.NOT_CURATED
        assert "DATA_MODE is 'curated' but the serving DB records 'synthetic'" in (
            capsys.readouterr().err
        )
    assert not (tmp_path / "work").exists()


def test_a_missing_file_names_the_command_that_builds_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path / "absent.duckdb", tmp_path, "--check") == cli.NOT_CURATED
    assert "uv run poe serving-build" in capsys.readouterr().err


def test_a_file_that_breaks_the_contract_is_refused(
    bank: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "bank_curated.duckdb"
    shutil.copy(bank, broken)
    with duckdb.connect(str(broken)) as con:
        con.execute("ALTER TABLE transactions_enriched ADD COLUMN is_fraud BOOLEAN")
    assert _run(broken, tmp_path, "--check") == cli.NOT_CURATED
    assert "is_fraud: forbidden column" in capsys.readouterr().err


def test_check_fails_on_a_row_the_runtime_cannot_read_and_the_run_reports_it(
    bank: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "bank_curated.duckdb"
    shutil.copy(bank, broken)
    with duckdb.connect(str(broken)) as con:
        con.execute(
            "UPDATE transactions_enriched SET transaction_status = 'Bogus' "
            "WHERE transaction_id = 'TRX-T1900030'"
        )
    problem = "transactions_enriched: 1 rows do not fit TransactionView (transaction_status)"
    assert _run(broken, tmp_path, "--check") == cli.NOT_CURATED
    assert f"ERROR: {problem}" in capsys.readouterr().err
    # The run goes on, so its report shows what works and what does not, and it fails.
    code = _run(broken, tmp_path, "--per-scenario", "1", "--scenario", "recognized")
    assert code == cli.FAILED
    assert f"- serving DB: {problem}" in (tmp_path / "report.md").read_text("utf-8")


def test_a_bad_count_is_refused_by_the_parser(bank: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as caught:
        _run(bank, tmp_path, "--per-scenario", "0")
    assert caught.value.code == 2
    with pytest.raises(SystemExit):
        _run(bank, tmp_path, "--scenario", "no_such_scenario")
