"""The synthetic fixture bank is deterministic, contract-valid and covers every scenario."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import duckdb
import pytest

from bankagent.contracts.serving import FORBIDDEN_COLUMNS, validate_serving_db
from bankagent.fixtures.builder import (
    FixtureError,
    build,
    build_rows,
    committed_hash,
    content_hash,
    load_source,
    write_db,
)

AS_OF = date(2026, 6, 17)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    out = tmp_path_factory.mktemp("fixture") / "bank.duckdb"
    digest, problems = build(out=out)
    assert problems == []
    return out, digest


@pytest.fixture
def con(built: tuple[Path, str]) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(str(built[0]), read_only=True)
    yield connection
    connection.close()


def test_build_matches_committed_hash(built: tuple[Path, str]) -> None:
    assert built[1] == committed_hash(), "run `uv run poe fixtures --update-hash` if intended"


def test_build_is_deterministic(built: tuple[Path, str], tmp_path: Path) -> None:
    digest, _ = build(out=tmp_path / "again.duckdb")
    assert digest == built[1]


def test_contract_is_valid_and_has_no_forbidden_columns(con: duckdb.DuckDBPyConnection) -> None:
    assert validate_serving_db(con) == []
    columns = {
        row[0]
        for row in con.execute("SELECT column_name FROM information_schema.columns").fetchall()
    }
    assert not FORBIDDEN_COLUMNS.intersection(columns)


def test_metadata_records_synthetic_mode_and_as_of_date(con: duckdb.DuckDBPyConnection) -> None:
    metadata = dict(con.execute("SELECT key, value FROM _serving_metadata").fetchall())
    assert metadata["data_mode"] == "synthetic"
    assert metadata["as_of_date"] == AS_OF.isoformat()


def test_every_transaction_belongs_to_its_card_owner(con: duckdb.DuckDBPyConnection) -> None:
    mismatches = con.execute(
        "SELECT t.transaction_id FROM transactions_enriched t "
        "JOIN customer_cards c USING (product_id) WHERE t.customer_id <> c.customer_id"
    ).fetchall()
    assert mismatches == []


def _txn(con: duckdb.DuckDBPyConnection, transaction_id: str) -> dict[str, object]:
    cursor = con.execute(
        "SELECT * FROM transactions_enriched WHERE transaction_id = ?", [transaction_id]
    )
    names = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    assert row is not None, transaction_id
    return dict(zip(names, row, strict=True))


def test_scenarios_are_present(con: duckdb.DuckDBPyConnection) -> None:
    # Duplicate charges ~37 s apart are flagged on both rows.
    assert "dq_possible_duplicate" in _txn(con, "TXN-FX-0201")["dq_flags"]  # type: ignore[operator]
    assert "dq_possible_duplicate" in _txn(con, "TXN-FX-0404")["dq_flags"]  # type: ignore[operator]
    # Foreign transactions.
    assert _txn(con, "TXN-FX-0302")["is_foreign"] is True
    assert _txn(con, "TXN-FX-0601")["fraud_score"] == 78.5
    # Data-quality scenarios.
    assert _txn(con, "TXN-FX-0702")["dq_flags"] == ["dq_txn_after_card_expiry"]
    assert _txn(con, "TXN-FX-0802")["dq_flags"] == ["dq_txn_before_product_opening"]
    # Statuses.
    assert _txn(con, "TXN-FX-0803")["transaction_status"] == "Declined"
    assert _txn(con, "TXN-FX-0804")["transaction_status"] == "Pending"
    # Out of the dispute window relative to as_of_date.
    assert (AS_OF - _txn(con, "TXN-FX-0801")["transaction_ts"].date()).days > 120  # type: ignore[attr-defined]
    # Open prior dispute on a transaction.
    (status,) = con.execute(
        "SELECT status FROM dispute_history WHERE related_transaction_id = 'TXN-FX-0701'"
    ).fetchone() or (None,)
    assert status == "In Process"
    # Blocked card and Portuguese persona.
    assert con.execute(
        "SELECT count(*) FROM customer_cards WHERE product_status = 'Blocked'"
    ).fetchone() == (1,)
    assert con.execute(
        "SELECT preferred_language FROM customer_profile_min WHERE customer_id = 'CUST-FX-004'"
    ).fetchone() == ("pt",)
    # PT-speaking active agents exist for routing.
    assert con.execute(
        "SELECT count(*) FROM agents_routing WHERE list_contains(languages, 'pt') AND is_active"
    ).fetchone() == (2,)


def test_background_rows_have_no_accidental_quality_flags(con: duckdb.DuckDBPyConnection) -> None:
    flagged = con.execute(
        "SELECT transaction_id FROM transactions_enriched "
        "WHERE transaction_id LIKE 'TXN-FX-BG-%' AND len(dq_flags) > 0"
    ).fetchall()
    assert flagged == []
    (count,) = con.execute(
        "SELECT count(*) FROM transactions_enriched WHERE transaction_id LIKE 'TXN-FX-BG-%'"
    ).fetchone() or (0,)
    assert count > 0


def test_content_hash_ignores_built_at(tmp_path: Path) -> None:
    rows = build_rows(load_source())
    write_db(rows, tmp_path / "a.duckdb")
    rows["_serving_metadata"] = [
        (k, "1999-01-01T00:00:00+00:00" if k == "built_at" else v)
        for k, v in rows["_serving_metadata"]
    ]
    write_db(rows, tmp_path / "b.duckdb")
    with (
        duckdb.connect(str(tmp_path / "a.duckdb"), read_only=True) as a,
        duckdb.connect(str(tmp_path / "b.duckdb"), read_only=True) as b,
    ):
        assert content_hash(a) == content_hash(b)


def test_bad_reference_is_rejected() -> None:
    source = load_source()
    broken = source.model_copy(
        update={
            "transactions": (
                source.transactions[0].model_copy(update={"product_id": "CARD-FX-999"}),
            )
        }
    )
    with pytest.raises(FixtureError, match="unknown product"):
        build_rows(broken)
