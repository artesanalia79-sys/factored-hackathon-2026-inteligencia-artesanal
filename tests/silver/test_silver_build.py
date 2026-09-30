"""Silver dbt build on deterministic synthetic bronze: flags fire, keys are unique, tests bite."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from bankagent.silver.build import (
    BUILD_SCOPE_FULL,
    METADATA_TABLE,
    BuildConfig,
    DbtBuildError,
    build_silver,
    write_build_metadata,
)
from bankagent.silver.synthetic import (
    CLEAN_KEYS,
    DQ_ANY_EXCLUDED,
    DUPLICATED_KEYS,
    EXPECTED_FAILING_TESTS,
    KEYLESS_ROWS,
    SEEDED_DEFECTS,
    generate_synthetic_bronze,
)
from bankagent.silver.verify import BronzeVerificationError, verify_bronze

# Needs the `data` group (dbt, pyarrow); tests/conftest.py skips this directory locally without it.

KEYS = {
    "silver_customers": "customer_id",
    "silver_products": "product_id",
    "silver_branches": "branch_id",
    "silver_service_agents": "agent_id",
    "silver_marketing_campaigns": "campaign_id",
    "silver_transactions": "transaction_id",
    "silver_call_center_interactions": "interaction_id",
    "silver_call_transcripts": "transcript_id",
    "silver_satisfaction_surveys": "survey_id",
    "silver_digital_events": "event_id",
    "silver_complaints": "complaint_id",
    "silver_campaign_sends": "send_id",
    "silver_daily_exchange_rates": "fx_rate_id",
}
FORBIDDEN_IN_SILVER = {
    "email",
    "mobile_phone",
    "landline_phone",
    "address",
    "document_number",
    "date_of_birth",
    "product_number",
    "latitude",
    "longitude",
    "estimated_monthly_income",
    "credit_score",
    "ip_address",
}


@dataclass(frozen=True, slots=True)
class Built:
    config: BuildConfig
    failing_tests: list[str]
    metadata_survived_failed_build: bool


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Built:
    """One `build_silver` for the module; the seeded hard defects make it raise DbtBuildError."""
    tmp = tmp_path_factory.mktemp("silver")
    config = BuildConfig(
        bronze_dir=tmp / "bronze",
        warehouse=tmp / "warehouse.duckdb",
        memory_limit="1GB",
        threads=2,
        target_path=tmp / "target",
        log_path=tmp / "logs",
    )
    manifest = generate_synthetic_bronze(config.bronze_dir)
    # Stale metadata from an earlier build must not survive a failed build.
    write_build_metadata(config.warehouse, config.bronze_dir, manifest)
    with pytest.raises(DbtBuildError) as info:
        build_silver(config)
    run = info.value.run
    assert run is not None
    assert [n.unique_id for n in run.nodes if n.status == "skipped"] == []
    failing = [n.unique_id.split(".")[2] for n in run.nodes if n.status in {"fail", "error"}]
    with _con(config) as con:
        found = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [METADATA_TABLE]
        ).fetchone()
    survived = found is not None and found[0] > 0
    # The remaining tests read the tables as if the build had been accepted.
    write_build_metadata(config.warehouse, config.bronze_dir, manifest)
    return Built(config, failing, survived)


def _con(config: BuildConfig) -> duckdb.DuckDBPyConnection:
    # dbt-duckdb keeps its in-process database instance open; a read_only connection would
    # need a different instance configuration, so connect with the defaults.
    return duckdb.connect(str(config.warehouse))


def test_every_business_key_is_unique_and_not_null(built: Built) -> None:
    with _con(built.config) as con:
        for model, key in KEYS.items():
            row = con.execute(
                f"SELECT count(*), count(DISTINCT {key}), count({key}) FROM {model}"  # noqa: S608
            ).fetchone()
            assert row is not None
            total, distinct, non_null = row
            assert total == distinct == non_null, model


def test_seeded_defects_raise_their_flags(built: Built) -> None:
    with _con(built.config) as con:
        for model, key_value, flag in SEEDED_DEFECTS:
            row = con.execute(
                f"SELECT {flag}, dq_any FROM {model} WHERE {KEYS[model]} = ?",  # noqa: S608
                [key_value],
            ).fetchone()
            assert row is not None, (model, key_value)
            assert row[0] is True, (model, key_value, flag)
            if (model, flag) not in DQ_ANY_EXCLUDED:
                assert row[1] is True, (model, key_value, flag)


def test_orphan_branch_alone_does_not_set_dq_any(built: Built) -> None:
    """The branch FK is a random token on ~all customers and agents in the source; folding it
    into dq_any flagged 149,995 of 150,000 customers."""
    with _con(built.config) as con:
        row = con.execute(
            "SELECT dq_orphan_branch, dq_any FROM silver_service_agents "
            "WHERE agent_id = 'AGT-FX-04'"
        ).fetchone()
    assert row == (True, False)


def test_expected_or_label_only_oddities_raise_no_flag(built: Built) -> None:
    with _con(built.config) as con:
        for model, key_value in CLEAN_KEYS:
            row = con.execute(
                f"SELECT dq_any FROM {model} WHERE {KEYS[model]} = ?",  # noqa: S608
                [key_value],
            ).fetchone()
            assert row == (False,), (model, key_value)
        flagged_fx = con.execute(
            "SELECT count(*) FROM silver_daily_exchange_rates WHERE dq_any"
        ).fetchone()
    assert flagged_fx == (0,)


def test_fx_rate_is_joined_on_the_process_date(built: Built) -> None:
    with _con(built.config) as con:
        row = con.execute(
            "SELECT fx_rate_to_usd, dq_missing_fx_rate FROM silver_transactions "
            "WHERE transaction_id = 'TXN-FX-0014'"
        ).fetchone()
    assert row == (Decimal("0.000250"), False)


def test_duplicate_keys_are_collapsed_counted_and_not_code_collisions(built: Built) -> None:
    with _con(built.config) as con:
        for model, key_value in DUPLICATED_KEYS:
            rows = con.execute(
                f"SELECT dq_duplicate_count FROM {model} WHERE {KEYS[model]} = ?",  # noqa: S608
                [key_value],
            ).fetchall()
            assert rows == [(2,)], (model, key_value)
        product = con.execute(
            "SELECT dq_product_number_duplicate FROM silver_products "
            "WHERE product_id = 'PRD-FX-002'"
        ).fetchone()
        agent = con.execute(
            "SELECT dq_employee_code_duplicate FROM silver_service_agents "
            "WHERE agent_id = 'AGT-FX-01'"
        ).fetchone()
    assert product == (False,)
    assert agent == (False,)


def test_card_fields_come_from_product_type_and_number(built: Built) -> None:
    with _con(built.config) as con:
        rows = con.execute(
            "SELECT product_id, card_type, card_last4 FROM silver_products "
            "WHERE product_id IN ('PRD-FX-001', 'PRD-FX-004', 'PRD-FX-005') ORDER BY product_id"
        ).fetchall()
    assert rows == [
        ("PRD-FX-001", "credit", "0001"),
        ("PRD-FX-004", "debit", "0042"),
        ("PRD-FX-005", None, "0003"),
    ]


def test_document_hash_is_sha256_of_the_trimmed_number(built: Built) -> None:
    with _con(built.config) as con:
        rows = dict(
            con.execute(
                "SELECT customer_id, document_hash FROM silver_customers "
                "WHERE customer_id IN ('CUST-FX-001', 'CUST-FX-002')"
            ).fetchall()
        )
    assert rows == {
        "CUST-FX-001": hashlib.sha256(b"10000001").hexdigest(),
        "CUST-FX-002": hashlib.sha256(b"10000002").hexdigest(),
    }


def test_keyless_bronze_rows_are_excluded_and_counted(built: Built) -> None:
    from bankagent.silver.report import build_metadata, model_counts

    with _con(built.config) as con:
        counts = model_counts(con, build_metadata(con))
    for model, entry in counts.items():
        expected = KEYLESS_ROWS.get(model, 0)
        assert entry["dropped_keyless"] == expected, model
        assert entry["bronze_rows"] is not None
        assert entry["silver_rows"] is not None
    customers = counts["silver_customers"]
    assert (customers["bronze_rows"], customers["silver_rows"]) == (4, 3)


def test_sensitive_columns_do_not_reach_silver(built: Built) -> None:
    with _con(built.config) as con:
        columns = con.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_name LIKE 'silver_%'"
        ).fetchall()
    leaked = {(t, c) for t, c in columns if c in FORBIDDEN_IN_SILVER}
    is_fraud_tables = {t for t, c in columns if c == "is_fraud"}
    assert leaked == set()
    assert is_fraud_tables == {"silver_transactions"}


def test_failed_build_raises_on_exactly_the_seeded_hard_defects(built: Built) -> None:
    matched = {
        expected
        for expected in EXPECTED_FAILING_TESTS
        if any(name.startswith(expected) for name in built.failing_tests)
    }
    assert matched == EXPECTED_FAILING_TESTS
    assert len(built.failing_tests) == len(EXPECTED_FAILING_TESTS), built.failing_tests
    assert not built.metadata_survived_failed_build


def test_build_metadata_records_manifest_mode_and_scope(built: Built) -> None:
    with _con(built.config) as con:
        found = dict(con.execute(f"SELECT key, value FROM {METADATA_TABLE}").fetchall())  # noqa: S608
    assert found["data_mode"] == "synthetic"
    assert found["build_scope"] == BUILD_SCOPE_FULL
    assert found["selection"] == ""
    assert len(found["manifest_sha256"]) == 64
    assert found["bronze_rows.customers"] == "4"
    assert {"dbt_version", "built_at"} <= found.keys()


def test_dq_report_refuses_missing_or_partial_build_metadata(tmp_path: Path) -> None:
    from bankagent.silver.report import IncompleteBuildError, build_metadata, main

    bronze = tmp_path / "bronze"
    manifest = generate_synthetic_bronze(bronze)
    warehouse = tmp_path / "w.duckdb"
    with duckdb.connect(str(warehouse)) as con, pytest.raises(IncompleteBuildError):
        build_metadata(con)
    write_build_metadata(warehouse, bronze, manifest, selection="silver_transactions+")
    out = tmp_path / "evidence"
    assert main(["--warehouse", str(warehouse), "--out-dir", str(out)]) == 1
    assert not out.exists()


def test_tampered_bronze_fails_verification_listing_tables(tmp_path: Path) -> None:
    bronze = tmp_path / "bronze"
    generate_synthetic_bronze(bronze)
    manifest_path = bronze / "_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in payload["files"]:
        if entry["table"] == "branches":
            entry["row_count"] += 1
        if entry["table"] == "customers":
            entry["relative_path"] = "old/customers.parquet"  # not the file dbt reads
    payload["files"] = [e for e in payload["files"] if e["table"] != "complaints"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    (bronze / "products.parquet").write_bytes(b"tampered")

    with pytest.raises(BronzeVerificationError) as info:
        verify_bronze(bronze)
    text = "\n".join(info.value.problems)
    for table in ("branches", "complaints", "products"):
        assert table in text
    assert "customers: manifest relative_path" in text


def test_build_stops_before_dbt_when_bronze_is_invalid(tmp_path: Path) -> None:
    config = BuildConfig(bronze_dir=tmp_path / "missing", warehouse=tmp_path / "w.duckdb")
    with pytest.raises(BronzeVerificationError):
        build_silver(config)
    assert not config.warehouse.exists()


def test_evidence_reports_are_aggregate_only(built: Built) -> None:
    from bankagent.silver.report import dq_report, fraud_report

    with _con(built.config) as con:
        text = dq_report(con) + fraud_report(con)
    assert "silver_transactions" in text
    assert "dropped_keyless" in text
    assert "| purchase |" in text
    for identifier in ("CUST-FX-", "TXN-FX-", "PRD-FX-", "AGT-FX-", "Persona", "Tienda Prueba"):
        assert identifier not in text
