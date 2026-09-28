"""Serving DB contract: the only tables and columns the runtime may read.

Both the synthetic fixture bank (Task 3) and the curated dbt gold layer (Task 6) must produce a
DuckDB database that passes ``validate_serving_db``. Timestamps are naive ``TIMESTAMP`` in UTC by
convention. Bump ``SERVING_CONTRACT_VERSION`` (semver) on every change.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

SERVING_CONTRACT_VERSION = "1.0.0"

# Columns that must never reach the serving DB (labels, personal data, sensitive scores).
FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "is_fraud",
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
)

REQUIRED_METADATA_KEYS: tuple[str, ...] = (
    "data_mode",
    "as_of_date",
    "built_at",
    "source",
    "contract_version",
)


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    sql_type: str
    nullable: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    primary_key: str
    columns: tuple[Column, ...]
    description: str = ""

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


def _c(name: str, sql_type: str, nullable: bool = False, description: str = "") -> Column:
    return Column(name, sql_type, nullable, description)


SERVING_TABLES: tuple[Table, ...] = (
    Table(
        "customer_profile_min",
        "customer_id",
        (
            _c("customer_id", "VARCHAR"),
            _c("document_hash", "VARCHAR", description="sha256 of the identity document"),
            _c("first_name", "VARCHAR"),
            _c("country", "VARCHAR", description="MX, CO or AR"),
            _c("segment", "VARCHAR", description="Premium, Plus, Basic, Student"),
            _c("customer_status", "VARCHAR"),
            _c("preferred_language", "VARCHAR", description="es or pt"),
            _c("registration_date", "DATE"),
        ),
        "Minimal customer profile for session and language. No contact or identity data.",
    ),
    Table(
        "customer_cards",
        "product_id",
        (
            _c("product_id", "VARCHAR"),
            _c("customer_id", "VARCHAR"),
            _c("card_type", "VARCHAR", description="credit or debit"),
            _c("card_last4", "VARCHAR"),
            _c("currency", "VARCHAR"),
            _c("product_status", "VARCHAR"),
            _c("opening_date", "DATE"),
            _c("expiration_date", "DATE", nullable=True),
        ),
        "Credit and debit cards. Only the last four digits of the card number.",
    ),
    Table(
        "transactions_enriched",
        "transaction_id",
        (
            _c("transaction_id", "VARCHAR"),
            _c("customer_id", "VARCHAR"),
            _c("product_id", "VARCHAR"),
            _c("transaction_ts", "TIMESTAMP", description="UTC"),
            _c("process_date", "DATE"),
            _c("transaction_type", "VARCHAR"),
            _c("transaction_category", "VARCHAR", nullable=True),
            _c("amount", "DECIMAL(15,2)", description="original currency"),
            _c("currency", "VARCHAR"),
            _c("amount_usd", "DECIMAL(15,2)", nullable=True),
            _c("channel", "VARCHAR"),
            _c("merchant_name", "VARCHAR", nullable=True),
            _c("merchant_category", "VARCHAR", nullable=True),
            _c("transaction_country", "VARCHAR", description="ISO 3166-1 alpha-2"),
            _c("transaction_city", "VARCHAR", nullable=True),
            _c("transaction_status", "VARCHAR"),
            _c("fraud_score", "DOUBLE", nullable=True, description="policy-only input (ADR 0003)"),
            _c("is_foreign", "BOOLEAN"),
            _c("dq_flags", "VARCHAR[]", description="data-quality flags, empty list if none"),
        ),
        "Card transactions with data-quality flags. No fraud label.",
    ),
    Table(
        "dispute_history",
        "complaint_id",
        (
            _c("complaint_id", "VARCHAR"),
            _c("customer_id", "VARCHAR"),
            _c("created_at", "TIMESTAMP", description="UTC"),
            _c("case_type", "VARCHAR"),
            _c("category", "VARCHAR"),
            _c("subcategory", "VARCHAR", nullable=True),
            _c("status", "VARCHAR"),
            _c("claimed_amount", "DECIMAL(15,2)", nullable=True),
            _c("currency", "VARCHAR", nullable=True),
            _c("related_transaction_id", "VARCHAR", nullable=True),
            _c("resolution_days", "INTEGER", nullable=True),
        ),
        "Prior complaints and claims (for open-case and repeat-disputer rules).",
    ),
    Table(
        "agents_routing",
        "agent_id",
        (
            _c("agent_id", "VARCHAR"),
            _c("languages", "VARCHAR[]", description="ISO 639-1 codes"),
            _c("specialty", "VARCHAR", description="disputes, fraud, cards, general"),
            _c("agent_type", "VARCHAR"),
            _c("country_of_origin", "VARCHAR"),
            _c("is_active", "BOOLEAN"),
        ),
        "Human agents available for handoff routing.",
    ),
    Table(
        "_serving_metadata",
        "key",
        (_c("key", "VARCHAR"), _c("value", "VARCHAR")),
        "Build metadata: data_mode, as_of_date, built_at, source, contract_version.",
    ),
)

TABLES_BY_NAME: dict[str, Table] = {table.name: table for table in SERVING_TABLES}


class _Connection(Protocol):
    """The subset of ``duckdb.DuckDBPyConnection`` used here (keeps this module dependency-free)."""

    def execute(self, query: str, parameters: Sequence[Any] = ..., /) -> Any: ...


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def ddl() -> str:
    """CREATE TABLE statements for every serving table (with NOT NULL and PRIMARY KEY)."""
    statements: list[str] = []
    for table in SERVING_TABLES:
        lines = []
        for column in table.columns:
            null = "" if column.nullable else " NOT NULL"
            lines.append(f"    {_quote(column.name)} {column.sql_type}{null}")
        lines.append(f"    PRIMARY KEY ({_quote(table.primary_key)})")
        body = ",\n".join(lines)
        statements.append(f"CREATE TABLE {_quote(table.name)} (\n{body}\n);")
    return "\n\n".join(statements) + "\n"


def validate_serving_db(con: _Connection) -> list[str]:
    """Return every contract violation found in the database (empty list means valid).

    Checks table and column names, column types, forbidden columns in any table, NOT NULL and
    primary-key uniqueness on the data itself, and the required metadata keys.
    """
    problems: list[str] = []
    rows = con.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position"
    ).fetchall()
    actual: dict[str, list[tuple[str, str]]] = {}
    for table_name, column_name, data_type in rows:
        actual.setdefault(table_name, []).append((column_name, data_type))
        if column_name.lower() in FORBIDDEN_COLUMNS:
            problems.append(f"{table_name}.{column_name}: forbidden column")

    for table in SERVING_TABLES:
        columns = actual.get(table.name)
        if columns is None:
            problems.append(f"{table.name}: missing table")
            continue
        names = tuple(name for name, _ in columns)
        if names != table.column_names:
            problems.append(
                f"{table.name}: columns {list(names)} != contract {list(table.column_names)}"
            )
            continue
        for (name, data_type), column in zip(columns, table.columns, strict=True):
            if data_type.replace(" ", "").upper() != column.sql_type.upper():
                problems.append(f"{table.name}.{name}: type {data_type} != {column.sql_type}")
        problems.extend(_check_data(con, table))

    if "_serving_metadata" in actual:
        found = {
            key: value
            for key, value in con.execute("SELECT key, value FROM _serving_metadata").fetchall()
        }
        for key in REQUIRED_METADATA_KEYS:
            if key not in found:
                problems.append(f"_serving_metadata: missing key '{key}'")
        version = found.get("contract_version")
        if version is not None and version != SERVING_CONTRACT_VERSION:
            problems.append(
                f"_serving_metadata: contract_version {version} != {SERVING_CONTRACT_VERSION}"
            )
    return problems


def _check_data(con: _Connection, table: Table) -> list[str]:
    problems: list[str] = []
    # Identifiers come from the contract constants above, never from user input.
    for column in table.columns:
        if column.nullable:
            continue
        query = f"SELECT count(*) FROM {_quote(table.name)} WHERE {_quote(column.name)} IS NULL"  # noqa: S608
        (nulls,) = con.execute(query).fetchone()
        if nulls:
            problems.append(f"{table.name}.{column.name}: {nulls} NULL values in a NOT NULL column")
    pk = _quote(table.primary_key)
    query = f"SELECT count(*) - count(DISTINCT {pk}) FROM {_quote(table.name)}"  # noqa: S608
    (dupes,) = con.execute(query).fetchone()
    if dupes:
        problems.append(f"{table.name}.{table.primary_key}: {dupes} duplicate primary keys")
    return problems
