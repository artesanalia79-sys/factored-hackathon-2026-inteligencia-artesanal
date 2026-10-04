"""validate_serving_db catches every kind of contract drift."""

from __future__ import annotations

import duckdb
import pytest

from bankagent.contracts.serving import (
    FORBIDDEN_COLUMNS,
    REQUIRED_METADATA_KEYS,
    SERVING_CONTRACT_VERSION,
    SERVING_TABLES,
    ddl,
    validate_serving_db,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute(ddl())
    metadata = {key: "x" for key in REQUIRED_METADATA_KEYS}
    metadata["contract_version"] = SERVING_CONTRACT_VERSION
    connection.executemany("INSERT INTO _serving_metadata VALUES (?, ?)", list(metadata.items()))
    return connection


def test_ddl_database_is_valid(con: duckdb.DuckDBPyConnection) -> None:
    assert validate_serving_db(con) == []


def test_no_forbidden_column_in_contract() -> None:
    for table in SERVING_TABLES:
        assert not FORBIDDEN_COLUMNS.intersection(table.column_names), table.name


def test_detects_forbidden_column_even_in_extra_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE TABLE extra_labels (transaction_id VARCHAR, is_fraud BOOLEAN)")
    assert "extra_labels.is_fraud: forbidden column" in validate_serving_db(con)


def test_detects_tables_outside_the_contract_in_any_schema(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # Both passed before the PR #29 audit: only schema `main` was read, and only by column name.
    con.execute("CREATE TABLE raw_customers (customer_id VARCHAR, full_name VARCHAR)")
    con.execute("CREATE SCHEMA labels")
    con.execute("CREATE TABLE labels.fraud (transaction_id VARCHAR, is_fraud BOOLEAN)")
    problems = validate_serving_db(con)
    assert "raw_customers: table not in the serving contract" in problems
    assert "labels.fraud: table not in the serving contract" in problems
    assert "labels.fraud.is_fraud: forbidden column" in problems


def test_a_temporary_table_of_the_connection_is_not_part_of_the_database(
    con: duckdb.DuckDBPyConnection,
) -> None:
    con.execute("CREATE TEMP TABLE scratch (x INTEGER)")
    assert validate_serving_db(con) == []


def test_detects_missing_table_and_type_drift() -> None:
    con = duckdb.connect(":memory:")
    con.execute(ddl().replace('"amount" DECIMAL(15,2)', '"amount" DOUBLE'))
    con.execute("DROP TABLE agents_routing")
    problems = validate_serving_db(con)
    assert "agents_routing: missing table" in problems
    assert any(p.startswith("transactions_enriched.amount: type DOUBLE") for p in problems)
    assert "_serving_metadata: missing key 'data_mode'" in problems


def test_detects_nulls_duplicates_and_version(con: duckdb.DuckDBPyConnection) -> None:
    # Constraint-free copy so bad data can be inserted.
    con.execute("DROP TABLE agents_routing")
    con.execute(
        "CREATE TABLE agents_routing (agent_id VARCHAR, languages VARCHAR[], specialty VARCHAR, "
        "agent_type VARCHAR, country_of_origin VARCHAR, is_active BOOLEAN)"
    )
    con.execute(
        "INSERT INTO agents_routing VALUES ('A1', ['es'], 'disputes', 'Phone', 'MX', true), "
        "('A1', ['es'], NULL, 'Phone', 'MX', true)"
    )
    con.execute("UPDATE _serving_metadata SET value = '0.0.1' WHERE key = 'contract_version'")
    problems = validate_serving_db(con)
    assert "agents_routing.specialty: 1 NULL values in a NOT NULL column" in problems
    assert "agents_routing.agent_id: 1 duplicate primary keys" in problems
    assert any("contract_version 0.0.1" in p for p in problems)
