"""Gold dbt build and serving DB on deterministic synthetic silver (no organizer data).

Silver comes from `bankagent.silver.synthetic` (seeded defects included). A few extra rows,
labelled `*-GD-*`, are inserted into silver to reach the gold rules the base generator does not
cover (card lifecycle, missing merchant, duplicates, foreign, non-card product, agent mapping).
The late-arrival test uses its own warehouse and synthetic `TXN-LATE-*` rows: the real source
has no late arrivals.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from bankagent.contracts.serving import (
    FORBIDDEN_COLUMNS,
    SERVING_CONTRACT_VERSION,
    SERVING_TABLES,
    validate_serving_db,
)
from bankagent.gold.build import (
    GOLD_MODELS,
    ServingBuildError,
    ServingResult,
    build_serving,
    layer_counts,
    read_silver_metadata,
    write_serving_db,
)
from bankagent.silver.build import BuildConfig, DbtBuildError, build_silver, write_build_metadata
from bankagent.silver.synthetic import generate_synthetic_bronze

# Needs the `data` group (dbt, pyarrow); tests/conftest.py skips this directory locally without it.

SILVER_FLAGS = {
    "dq_cast_failed",
    "dq_missing_required",
    "dq_invalid_enum",
    "dq_orphan_customer",
    "dq_orphan_product",
    "dq_orphan_branch",
    "dq_product_customer_mismatch",
    "dq_out_of_range",
    "dq_late_arrival",
    "dq_missing_fx_rate",
    "dq_amount_usd_mismatch",
    "dq_amount_usd_missing",
}
GOLD_FLAGS = {
    "dq_txn_before_product_opening",
    "dq_txn_after_card_expiry",
    "dq_missing_merchant",
    "dq_possible_duplicate",
}
# Synthetic silver transactions that must not be served, and why.
NOT_SERVED = {
    "TXN-FX-0003": "amount failed its cast (NULL amount)",
    "TXN-FX-0007": "customer is not the card owner (orphan customer)",
    "TXN-FX-0016": "customer is not the card owner",
    "TXN-FX-0017": "product does not exist",
    "TXN-GD-0008": "savings account, not a card",
}


@dataclass(frozen=True, slots=True)
class Built:
    config: BuildConfig
    result: ServingResult
    gold_tables_after_silver_build: list[str]


def _config(tmp: Path) -> BuildConfig:
    return BuildConfig(
        bronze_dir=tmp / "bronze",
        warehouse=tmp / "warehouse.duckdb",
        memory_limit="1GB",
        threads=2,
        target_path=tmp / "target",
        log_path=tmp / "logs",
    )


def _build_silver(config: BuildConfig) -> None:
    """Synthetic silver; its seeded hard defects fail dbt tests, the tables are still built."""
    manifest = generate_synthetic_bronze(config.bronze_dir)
    with pytest.raises(DbtBuildError):
        build_silver(config)
    write_build_metadata(config.warehouse, config.bronze_dir, manifest)


def _con(path: Path) -> duckdb.DuckDBPyConnection:
    # dbt-duckdb keeps its in-process instance open: connect with the default configuration.
    return duckdb.connect(str(path))


def _copy_row(
    con: duckdb.DuckDBPyConnection, table: str, key: str, template: str, **values: object
) -> None:
    """Insert a copy of the `template` row of silver `table` with `values` replaced."""
    replaced = ", ".join(f"? AS {column}" for column in values)
    con.execute(
        f"INSERT INTO {table} SELECT * REPLACE ({replaced}) FROM {table} WHERE {key} = ?",  # noqa: S608
        [*values.values(), template],
    )


def _add_gold_rows(warehouse: Path) -> None:
    with _con(warehouse) as con:
        _copy_row(
            con,
            "silver_products",
            "product_id",
            "PRD-FX-001",
            product_id="PRD-GD-001",
            product_type="Tarjeta Débito",
            card_type="debit",
            card_last4="0777",
            opening_date="2026-01-01",
            expiration_date="2026-06-01",
        )
        _copy_row(
            con,
            "silver_products",
            "product_id",
            "PRD-FX-001",
            product_id="PRD-GD-002",
            product_type="Cuenta Ahorro",
            card_type=None,
            card_last4=None,
        )

        def txn(txn_id: str, ts: str, **values: object) -> None:
            _copy_row(
                con,
                "silver_transactions",
                "transaction_id",
                "TXN-FX-0014",  # a row that raises no flag
                transaction_id=txn_id,
                transaction_ts=ts,
                process_date=ts[:10],
                **values,
            )

        txn("TXN-GD-0001", "2025-12-15 12:00:00", product_id="PRD-GD-001")  # before opening
        txn("TXN-GD-0002", "2026-06-05 12:00:00", product_id="PRD-GD-001")  # after expiry
        txn("TXN-GD-0003", "2026-06-03 09:00:00", merchant_name=None)  # purchase, no merchant
        txn(
            "TXN-GD-0004",
            "2026-06-04 09:00:00",
            transaction_country="Brasil",
            transaction_country_code="BR",
        )
        # 60 s apart: duplicates; the third is 121 s after the second: not a duplicate.
        for txn_id, ts in (
            ("TXN-GD-0005", "2026-05-20 10:00:00"),
            ("TXN-GD-0006", "2026-05-20 10:01:00"),
            ("TXN-GD-0007", "2026-05-20 10:03:01"),
        ):
            txn(txn_id, ts, product_id="PRD-GD-001", merchant_name="Duplicado", amount="777.00")
        txn("TXN-GD-0008", "2026-06-06 09:00:00", product_id="PRD-GD-002")  # not a card

        _copy_row(
            con,
            "silver_service_agents",
            "agent_id",
            "AGT-FX-01",
            agent_id="AGT-GD-01",
            specialty="Quejas y Reclamos",
            languages="español, inglés",
            agent_status="Vacation",
        )
        _copy_row(
            con,
            "silver_service_agents",
            "agent_id",
            "AGT-FX-01",
            agent_id="AGT-GD-02",
            specialty=None,
        )


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Built:
    config = _config(tmp_path_factory.mktemp("gold"))
    _build_silver(config)
    with _con(config.warehouse) as con:
        gold_after_silver = [
            name
            for (name,) in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'gold_%'"
            ).fetchall()
        ]
    _add_gold_rows(config.warehouse)
    result = build_serving(config, config.warehouse.parent / "serving" / "bank.duckdb")
    return Built(config, result, gold_after_silver)


def _serving(built: Built) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(built.result.serving_db), read_only=True)


def _flags(built: Built) -> dict[str, list[str]]:
    with _serving(built) as con:
        return dict(
            con.execute("SELECT transaction_id, dq_flags FROM transactions_enriched").fetchall()
        )


def test_silver_build_does_not_build_gold(built: Built) -> None:
    assert built.gold_tables_after_silver_build == []


def test_serving_db_passes_the_contract(built: Built) -> None:
    with _serving(built) as con:
        assert validate_serving_db(con) == []
        tables = {
            name
            for (name,) in con.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
    assert tables == {table.name for table in SERVING_TABLES}  # no baselines, no silver


def test_no_forbidden_column_in_any_gold_or_serving_table(built: Built) -> None:
    with _con(built.config.warehouse) as con:
        gold_columns = con.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_name LIKE 'gold_%'"
        ).fetchall()
    with _serving(built) as con:
        serving_columns = con.execute(
            "SELECT table_name, column_name FROM information_schema.columns"
        ).fetchall()
    assert {table for table, _ in gold_columns} >= set(GOLD_MODELS.values())
    leaked = [
        f"{table}.{column}"
        for table, column in gold_columns + serving_columns
        if column.lower() in FORBIDDEN_COLUMNS
    ]
    assert leaked == []


def test_metadata_records_mode_as_of_date_source_and_version(built: Built) -> None:
    metadata = built.result.metadata
    assert metadata["data_mode"] == "synthetic"  # the manifest's mode: synthetic bronze here
    assert metadata["as_of_date"] == "2026-07-11"  # max silver process_date, not last_updated
    assert metadata["contract_version"] == SERVING_CONTRACT_VERSION
    assert metadata["source"].startswith("dbt gold on silver built from bronze manifest sha256 ")
    assert metadata["built_at"].endswith("Z")


def test_only_card_transactions_of_the_card_owner_are_served(built: Built) -> None:
    flags = _flags(built)
    assert set(NOT_SERVED).isdisjoint(flags)
    with _con(built.config.warehouse) as con:
        silver_ids = {
            i for (i,) in con.execute("SELECT transaction_id FROM silver_transactions").fetchall()
        }
    assert set(flags) == silver_ids - set(NOT_SERVED)


def test_dq_flags_use_one_sorted_vocabulary(built: Built) -> None:
    for flags in _flags(built).values():
        assert set(flags) <= SILVER_FLAGS | GOLD_FLAGS
        assert flags == sorted(flags)


def test_silver_flags_are_carried_by_name(built: Built) -> None:
    flags = _flags(built)
    assert "dq_out_of_range" in flags["TXN-FX-0004"]
    assert "dq_missing_fx_rate" in flags["TXN-FX-0008"]
    assert "dq_amount_usd_mismatch" in flags["TXN-FX-0009"]
    assert "dq_amount_usd_missing" in flags["TXN-FX-0013"]
    assert flags["TXN-FX-0014"] == []


def test_dispute_flags_are_computed_in_gold(built: Built) -> None:
    flags = _flags(built)
    assert flags["TXN-GD-0001"] == ["dq_txn_before_product_opening"]
    assert flags["TXN-GD-0002"] == ["dq_txn_after_card_expiry"]
    assert flags["TXN-GD-0003"] == ["dq_missing_merchant"]
    assert flags["TXN-GD-0005"] == ["dq_possible_duplicate"]
    assert flags["TXN-GD-0006"] == ["dq_possible_duplicate"]
    assert flags["TXN-GD-0007"] == []  # 121 s after the previous one: outside the window


def test_amount_usd_is_backfilled_with_the_source_rule(built: Built) -> None:
    with _serving(built) as con:
        rows = dict(
            con.execute(
                "SELECT transaction_id, amount_usd FROM transactions_enriched "
                "WHERE transaction_id IN ('TXN-FX-0009', 'TXN-FX-0010', 'TXN-FX-0013')"
            ).fetchall()
        )
    assert rows["TXN-FX-0010"] == Decimal("25.00")  # USD: amount
    assert rows["TXN-FX-0013"] == Decimal("10.00")  # COP NULL: 40000 / 4000
    assert rows["TXN-FX-0009"] == Decimal("99.00")  # a published value is kept as published


def test_is_foreign_compares_with_the_customer_country(built: Built) -> None:
    with _serving(built) as con:
        foreign = dict(
            con.execute("SELECT transaction_id, is_foreign FROM transactions_enriched").fetchall()
        )
    assert foreign["TXN-GD-0004"] is True  # BR transaction, CO customer
    assert [tid for tid, value in foreign.items() if value] == ["TXN-GD-0004"]


def test_customers_and_cards_follow_the_contract(built: Built) -> None:
    with _serving(built) as con:
        customers = con.execute(
            "SELECT customer_id, country, preferred_language FROM customer_profile_min ORDER BY 1"
        ).fetchall()
        cards = con.execute(
            "SELECT product_id, card_type, card_last4 FROM customer_cards ORDER BY 1"
        ).fetchall()
    # CUST-FX-003 has no segment (NOT NULL): not served, and neither is its card PRD-FX-003.
    assert customers == [("CUST-FX-001", "CO", "es"), ("CUST-FX-002", "CO", "es")]
    assert cards == [
        ("PRD-FX-001", "credit", "0001"),
        ("PRD-FX-002", "credit", "0002"),
        ("PRD-FX-004", "debit", "0042"),
        ("PRD-GD-001", "debit", "0777"),
    ]


def test_agents_map_specialty_languages_and_activity(built: Built) -> None:
    with _serving(built) as con:
        agents = {
            row[0]: row[1:]
            for row in con.execute(
                "SELECT agent_id, languages, specialty, is_active FROM agents_routing"
            ).fetchall()
        }
    assert agents["AGT-FX-01"] == (["es", "pt"], "fraud", True)
    assert agents["AGT-GD-01"] == (["es", "en"], "disputes", False)
    assert agents["AGT-GD-02"] == (["es", "pt"], "general", True)


def test_dispute_history_never_invents_a_transaction_link(built: Built) -> None:
    with _serving(built) as con:
        rows = con.execute(
            "SELECT complaint_id, related_transaction_id FROM dispute_history"
        ).fetchall()
    assert rows == [("CMP-FX-101", None)]


def test_layer_counts_reconcile_silver_and_gold(built: Built) -> None:
    counts = {
        table: (silver, gold) for table, _, silver, gold in layer_counts(built.config.warehouse)
    }
    assert counts == {
        "customer_profile_min": (3, 2),
        "customer_cards": (7, 4),
        "transactions_enriched": (26, 21),
        "dispute_history": (1, 1),
        "agents_routing": (6, 6),
    }
    assert built.result.rows["transactions_enriched"] == 21


def test_baselines_cover_every_silver_row(built: Built) -> None:
    with _con(built.config.warehouse) as con:
        contacts = con.execute(
            "SELECT (SELECT sum(contacts) FROM gold_cc_contact_baseline), "
            "(SELECT count(*) FROM silver_call_center_interactions)"
        ).fetchone()
        complaints = con.execute(
            "SELECT (SELECT sum(complaints) FROM gold_complaints_baseline), "
            "(SELECT count(*) FROM silver_complaints)"
        ).fetchone()
    assert contacts == (2, 2)
    assert complaints == (1, 1)


def test_serving_build_refuses_a_partial_or_stale_silver(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = generate_synthetic_bronze(config.bronze_dir)
    with pytest.raises(ServingBuildError, match="not found"):
        read_silver_metadata(config)
    write_build_metadata(
        config.warehouse, config.bronze_dir, manifest, selection="silver_customers"
    )
    with pytest.raises(ServingBuildError, match="build_scope"):
        read_silver_metadata(config)
    write_build_metadata(config.warehouse, config.bronze_dir, manifest)
    (config.bronze_dir / "_manifest.json").write_text("{}")
    with pytest.raises(ServingBuildError, match="another bronze manifest"):
        read_silver_metadata(config)


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("DELETE FROM gold_serving_metadata WHERE key = 'source'", "missing key 'source'"),
        (
            "CREATE OR REPLACE TABLE gold_customer_profile_min AS "
            "SELECT * REPLACE (NULL::VARCHAR AS first_name) FROM gold_customer_profile_min",
            "first_name",
        ),
    ],
)
def test_a_contract_violation_fails_and_keeps_the_previous_serving_db(
    built: Built, tmp_path: Path, corruption: str, message: str
) -> None:
    copy = tmp_path / "warehouse_copy.duckdb"
    with _con(built.config.warehouse) as con:
        (source_db,) = con.execute("SELECT current_database()").fetchone() or ("",)
        con.execute(f"ATTACH '{copy.as_posix()}' AS broken")
        con.execute(f'COPY FROM DATABASE "{source_db}" TO broken')
        con.execute("DETACH broken")
    with duckdb.connect(str(copy)) as con:
        con.execute(corruption)
    dest = tmp_path / "serving.duckdb"
    shutil.copy(built.result.serving_db, dest)
    before = dest.read_bytes()
    with pytest.raises(ServingBuildError, match=message):
        write_serving_db(copy, dest)
    assert dest.read_bytes() == before
    assert not (tmp_path / "serving.duckdb.tmp").exists()


def test_a_late_row_inside_the_lookback_window_is_picked_up(tmp_path: Path) -> None:
    """Incremental gold: rows are loaded up to process_date 2026-07-11 (lookback 3 days)."""
    config = _config(tmp_path)
    _build_silver(config)
    serving_db = tmp_path / "serving.duckdb"
    build_serving(config, serving_db)

    with _con(config.warehouse) as con:
        for txn_id, day in (("TXN-LATE-IN", "2026-07-10"), ("TXN-LATE-OUT", "2026-07-01")):
            _copy_row(
                con,
                "silver_transactions",
                "transaction_id",
                "TXN-FX-0014",
                transaction_id=txn_id,
                transaction_ts=f"{day} 10:00:00",
                process_date=day,
            )

    def served() -> set[str]:
        with duckdb.connect(str(serving_db), read_only=True) as con:
            return {
                i
                for (i,) in con.execute(
                    "SELECT transaction_id FROM transactions_enriched "
                    "WHERE transaction_id LIKE 'TXN-LATE-%'"
                ).fetchall()
            }

    build_serving(config, serving_db)  # incremental run
    assert served() == {"TXN-LATE-IN"}  # inside the window; OUT is older than the lookback

    build_serving(config, serving_db, full_refresh=True)
    assert served() == {"TXN-LATE-IN", "TXN-LATE-OUT"}


# TODO(T19, Juan José): once the T8 tools exist, run their test suite unchanged against both the
# synthetic fixture bank and a curated serving DB built by `uv run poe serving-build`.
