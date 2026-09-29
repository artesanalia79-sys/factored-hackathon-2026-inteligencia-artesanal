"""Table-name matching against S3 keys."""

from __future__ import annotations

import pytest

from bankagent.ingest.catalog import is_data_file, matches_table


@pytest.mark.parametrize(
    ("key", "table", "expected"),
    [
        ("customers.csv", "customers", True),
        ("bronze/customers.parquet", "customers", True),
        ("transactions/process_date=2026-06-01/part-0.parquet", "transactions", True),
        ("transactions/process_date=2026-06-02/part-1.csv", "transactions", True),
        ("service_agents.csv", "service_agents", True),
        ("service_agents.csv", "agents", False),
        ("customers_2026.csv", "customers", True),
        ("customer_reviews.csv", "customers", False),
        ("branches.csv", "branch", False),
        ("readme.txt", "customers", False),
        ("customers.csv/_SUCCESS", "customers", False),
    ],
)
def test_matches_table(key: str, table: str, expected: bool) -> None:
    assert matches_table(key, table) is expected


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("a.csv", True),
        ("a.parquet", True),
        ("a.json", True),
        ("a.jsonl", True),
        ("a.txt", False),
        ("a", False),
        ("dir/_SUCCESS", False),
    ],
)
def test_is_data_file(key: str, expected: bool) -> None:
    assert is_data_file(key) is expected
