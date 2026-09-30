"""T16 evidence end to end on a tiny hand-made warehouse (invented rows, no organizer data).

The warehouse carries only the columns the `t16_*` analyses read, plus the build metadata and
the two gold baselines, so these tests need neither dbt nor the `data` dependency group.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from bankagent.analysis.assumptions import Label, load_assumptions, load_prices
from bankagent.analysis.model import Swing
from bankagent.analysis.report import ANALYSES, EvidenceError, eval_overrides, generate, main
from bankagent.contracts.enums import Outcome, SystemVariant
from bankagent.contracts.evaluation import EvalResult
from bankagent.silver.report import IncompleteBuildError

COUNTRIES = ("MX", "CO", "AR")
CHANNELS = ("Call Center", "Call Center", "Email", "Web", "App", "Branch", "Regulator")
STATUSES = ("Open", "In Process", "Resolved", "Closed", "Escalated")


def _customers(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "CREATE TABLE silver_customers (customer_id VARCHAR, country_code VARCHAR, segment VARCHAR)"
    )
    con.executemany(
        "INSERT INTO silver_customers VALUES (?, ?, ?)",
        [(f"CUST-T16-{i:03d}", COUNTRIES[i % 3], ("Basic", "Plus")[i % 2]) for i in range(60)],
    )


def _complaints(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "CREATE TABLE silver_complaints (customer_id VARCHAR, creation_ts TIMESTAMP, "
        "process_date DATE, case_type VARCHAR, category VARCHAR, subcategory VARCHAR, "
        "reception_channel VARCHAR, status VARCHAR, resolution_days INTEGER, "
        "sla_breached BOOLEAN, is_repeat_complainer BOOLEAN, assignment_ts TIMESTAMP, "
        "first_response_ts TIMESTAMP, claimed_amount DECIMAL(15, 2), currency VARCHAR, "
        "compensation_granted DECIMAL(15, 2))"
    )
    rows = []
    for i in range(300):
        created = datetime(2025, 1, 1, 10) + timedelta(days=i % 365)
        dispute = i % 3 != 2
        status = STATUSES[i % 5]
        rows.append(
            (
                f"CUST-T16-{i % 60:03d}",
                created,
                created.date(),
                ("Complaint", "Claim", "Request")[i % 3],
                "Transactions" if dispute else "Fees",
                ("Cargo no reconocido" if dispute else "Cobro indebido") if i % 10 else None,
                CHANNELS[i % len(CHANNELS)],
                status,
                15 if status in ("Resolved", "Closed") else None,
                i % 5 == 0,
                i % 7 == 0,
                created + timedelta(hours=12),
                created + timedelta(hours=37),
                100 + i if i % 4 else None,
                ("MXN", "COP", None)[i % 3],
                None,
            )
        )
    con.executemany(
        "INSERT INTO silver_complaints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def _contacts(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "CREATE TABLE silver_call_center_interactions (customer_id VARCHAR, agent_id VARCHAR, "
        "interaction_ts TIMESTAMP, process_date DATE, reason_category VARCHAR, channel VARCHAR, "
        "duration_seconds INTEGER, wait_time_seconds INTEGER, was_resolved BOOLEAN, "
        "requires_followup BOOLEAN, was_escalated BOOLEAN)"
    )
    rows = []
    for i in range(600):
        ts = datetime(2025, 1, 1, 9) + timedelta(hours=14 * i)
        phone = i % 4 != 3
        rows.append(
            (
                f"CUST-T16-{i % 60:03d}",
                f"AGT-T16-{i % 5}",
                ts,
                ts.date(),
                ("Transaccional", "Producto", "Queja")[i % 3],
                "Phone" if phone else "Email",
                180 + i % 120 if phone else None,
                60 + i % 60 if phone else None,
                i % 5 != 0,
                i % 4 == 0,
                i % 10 == 0,
            )
        )
    con.executemany(
        "INSERT INTO silver_call_center_interactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def _rest(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "CREATE TABLE silver_service_agents (agent_status VARCHAR, "
        "total_monthly_interactions INTEGER)"
    )
    con.executemany(
        "INSERT INTO silver_service_agents VALUES (?, ?)",
        [("Active", 400), ("Active", 500), ("Leave", 300)],
    )
    con.execute(
        "CREATE TABLE silver_transactions (product_id VARCHAR, transaction_ts TIMESTAMP, "
        "amount_usd DECIMAL(15, 2), is_fraud BOOLEAN)"
    )
    con.executemany(
        "INSERT INTO silver_transactions VALUES (?, ?, ?, ?)",
        [
            ("PRD-T16-1", datetime(2025, 3, 1, 10), 50, True),
            ("PRD-T16-1", datetime(2025, 3, 1, 20), 70, True),
            ("PRD-T16-2", datetime(2025, 3, 2, 10), 30, True),
            ("PRD-T16-3", datetime(2025, 3, 3, 10), 10, False),
        ],
    )
    con.execute(
        "CREATE TABLE gold_cc_contact_baseline AS SELECT c.country_code AS country, "
        "count(*) AS contacts, count(*) FILTER (WHERE was_resolved) AS resolved_contacts, "
        "count(*) FILTER (WHERE was_escalated) AS escalated_contacts, "
        "count(duration_seconds) AS contacts_with_duration, "
        "coalesce(sum(duration_seconds), 0) AS total_duration_seconds "
        "FROM silver_call_center_interactions i JOIN silver_customers c USING (customer_id) "
        "GROUP BY 1"
    )
    con.execute(
        "CREATE TABLE gold_complaints_baseline AS SELECT c.country_code AS country, category, "
        "count(*) AS complaints, count(resolution_days) AS complaints_with_resolution_days, "
        "avg(resolution_days) AS avg_resolution_days, "
        "count(*) FILTER (WHERE sla_breached) AS sla_breached_complaints, "
        "count(sla_breached) AS complaints_with_sla "
        "FROM silver_complaints k JOIN silver_customers c USING (customer_id) GROUP BY 1, 2"
    )


def _warehouse(tmp: Path, manifest_sha: str, scope: str = "full") -> Path:
    path = tmp / "warehouse.duckdb"
    with duckdb.connect(str(path)) as con:
        _customers(con)
        _complaints(con)
        _contacts(con)
        _rest(con)
        con.execute("CREATE TABLE _silver_build_metadata (key VARCHAR, value VARCHAR)")
        con.executemany(
            "INSERT INTO _silver_build_metadata VALUES (?, ?)",
            [
                ("manifest_sha256", manifest_sha),
                ("build_scope", scope),
                ("selection", ""),
                ("data_mode", "synthetic"),
                ("built_at", "2026-09-30T00:00:00+00:00"),
            ],
        )
    return path


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    path = tmp_path / "_manifest.json"
    path.write_text('{"synthetic": true}\n', encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generate(warehouse: Path, manifest: Path, eval_results: Path | None = None) -> dict[str, str]:
    a = load_assumptions()
    with duckdb.connect(str(warehouse), read_only=True) as con:
        return generate(con, a, load_prices(a.llm.model), manifest, eval_results)


def test_generates_all_evidence_aggregate_only(tmp_path: Path, manifest: Path) -> None:
    out = _generate(_warehouse(tmp_path, _sha(manifest)), manifest)
    assert set(out) == {
        "call_center_baseline.md",
        "complaints_baseline.md",
        "roi.md",
        "roi_tornado.svg",
    }
    text = "".join(out.values())
    for identifier in ("CUST-T16-", "AGT-T16-", "PRD-T16-"):
        assert identifier not in text
    for name in ANALYSES:  # every analysis is cited somewhere
        assert f"{name}.sql" in text
    roi = out["roi.md"]
    assert _sha(manifest) in roi
    for label in ("`measured`", "`market`", "`industry`", "`pending_eval`", "`projection`"):
        assert label in roi
    assert "not** a measured production result" in roi
    assert out["roi_tornado.svg"].startswith("<svg")


def test_main_writes_files(tmp_path: Path, manifest: Path) -> None:
    warehouse = _warehouse(tmp_path, _sha(manifest))
    out_dir = tmp_path / "evidence"
    code = main(
        ["--warehouse", str(warehouse), "--out-dir", str(out_dir), "--manifest", str(manifest)]
    )
    assert code == 0
    assert (out_dir / "roi.md").read_text(encoding="utf-8").startswith("# ROI")


def test_refuses_partial_build(tmp_path: Path, manifest: Path) -> None:
    warehouse = _warehouse(tmp_path, _sha(manifest), scope="partial")
    with pytest.raises(IncompleteBuildError):
        _generate(warehouse, manifest)


def test_refuses_a_warehouse_built_from_another_manifest(tmp_path: Path, manifest: Path) -> None:
    warehouse = _warehouse(tmp_path, "0" * 64)
    with pytest.raises(EvidenceError, match="does not match"):
        _generate(warehouse, manifest)


def test_refuses_without_gold_baselines(tmp_path: Path, manifest: Path) -> None:
    warehouse = _warehouse(tmp_path, _sha(manifest))
    with duckdb.connect(str(warehouse)) as con:
        con.execute("DROP TABLE gold_complaints_baseline")
    with pytest.raises(EvidenceError, match="serving-build"):
        _generate(warehouse, manifest)


def _result(i: int, system: SystemVariant, outcome: Outcome, cost: str) -> str:
    return EvalResult(
        case_id=f"case-{i:03d}",
        system=system,
        run_id="run-1",
        repeat_index=0,
        final_outcome=outcome,
        escalated=outcome == Outcome.ESCALATED,
        turns_used=3,
        cost_usd_total=cost,  # type: ignore[arg-type]
        correct=True,
        safe_automated_resolution=outcome == Outcome.AUTOMATED_RESOLUTION,
        automation_attempted=True,
    ).model_dump_json()


def test_eval_results_replace_the_provisional_inputs(tmp_path: Path, manifest: Path) -> None:
    results = tmp_path / "results.jsonl"
    lines = [
        _result(0, SystemVariant.PROPOSED, Outcome.AUTOMATED_RESOLUTION, "0.004"),
        _result(1, SystemVariant.PROPOSED, Outcome.ESCALATED, "0.002"),
        _result(2, SystemVariant.PROPOSED, Outcome.ABSTAINED, "0.003"),
        _result(3, SystemVariant.PROPOSED, Outcome.DEFLECTED_RECOGNIZED, "0.003"),
        _result(4, SystemVariant.BASELINE_LLM_ONLY, Outcome.ESCALATED, "0.5"),
    ]
    results.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = _generate(_warehouse(tmp_path, _sha(manifest)), manifest, results)
    assert "4 proposed runs" in out["roi.md"]
    assert "offline_eval" in out["roi.md"]
    base = {
        "escalation_share": Swing("escalation_share", Label.PENDING_EVAL, 0.1, 0.2, 0.4),
        "llm_cost_per_case_usd": Swing("llm_cost_per_case_usd", Label.PENDING_EVAL, 0, 0.003, 1),
    }
    overrides, n = eval_overrides(results, base)
    assert n == 4
    assert overrides["escalation_share"].central == pytest.approx(0.5)
    assert overrides["llm_cost_per_case_usd"].central == pytest.approx(0.003)
    assert overrides["escalation_share"].label == Label.OFFLINE_EVAL


def test_eval_results_without_the_proposed_system_are_rejected(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(
        _result(0, SystemVariant.BASELINE_LLM_ONLY, Outcome.ESCALATED, "0.1") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(EvidenceError, match="proposed"):
        eval_overrides(results, {})
