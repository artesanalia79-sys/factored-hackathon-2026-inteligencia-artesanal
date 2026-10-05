"""Every served row against the views the runtime reads it into (T19)."""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from bankagent.contracts.serving import validate_serving_db
from bankagent.contracts.tools import SearchTransactionsArgs
from bankagent.curated.rows import check_rows
from bankagent.store.serving import ServingDataError, ServingDB


def _altered(bank: Path, tmp_path: Path, statement: str, *params: object) -> Path:
    copy = tmp_path / "bank_altered.duckdb"
    shutil.copy(bank, copy)
    with duckdb.connect(str(copy)) as con:
        con.execute(statement, list(params))
        # Still a valid serving DB by the table contract: only a view can tell.
        assert validate_serving_db(con) == []
    return copy


def test_every_row_of_the_bank_fits_its_view(bank: Path) -> None:
    check = check_rows(bank)
    assert check.problems == ()
    assert (check.transactions, check.cards) == (40, 33)
    # Far fewer shapes than rows is the point: each shape is parsed once.
    assert 1 <= check.transaction_shapes < check.transactions
    assert check.card_shapes == 33  # every card has its own ending


@pytest.mark.parametrize(
    ("statement", "value", "problem"),
    [
        (
            "UPDATE transactions_enriched SET transaction_status = ? WHERE transaction_id = ?",
            "Bogus",
            "transactions_enriched: 1 rows do not fit TransactionView (transaction_status)",
        ),
        (
            "UPDATE transactions_enriched SET channel = ? WHERE transaction_id = ?",
            "Telegraph",
            "transactions_enriched: 1 rows do not fit TransactionView (channel)",
        ),
        (
            "UPDATE transactions_enriched SET transaction_type = ? WHERE transaction_id = ?",
            "Refund",
            "transactions_enriched: 1 rows do not fit TransactionView (transaction_type)",
        ),
        (
            "UPDATE transactions_enriched SET currency = ? WHERE transaction_id = ?",
            "pesos",
            "transactions_enriched: 1 rows do not fit TransactionView (currency)",
        ),
        (
            "UPDATE transactions_enriched SET transaction_country = ? WHERE transaction_id = ?",
            "Colombia",
            "transactions_enriched: 1 rows do not fit TransactionView (transaction_country)",
        ),
    ],
)
def test_a_transaction_the_runtime_cannot_read_is_named_without_its_value(
    bank: Path, tmp_path: Path, statement: str, value: str, problem: str
) -> None:
    broken = _altered(bank, tmp_path, statement, value, "TRX-T1900001")
    check = check_rows(broken)
    assert check.problems == (problem,)
    assert value not in problem
    # What the check predicts: that customer's reads fail in the runtime.
    with duckdb.connect(str(broken), read_only=True) as con:
        row = con.execute(
            "SELECT customer_id FROM transactions_enriched WHERE transaction_id = 'TRX-T1900001'"
        ).fetchone()
    assert row is not None
    with pytest.raises(ServingDataError):
        ServingDB(broken).search_transactions(row[0], SearchTransactionsArgs(), limit=50)


@pytest.mark.parametrize(
    ("statement", "value", "fields"),
    [
        ("UPDATE customer_cards SET card_last4 = ? WHERE product_id = ?", "12AB", "card_last4"),
        ("UPDATE customer_cards SET card_last4 = ? WHERE product_id = ?", "123", "card_last4"),
        ("UPDATE customer_cards SET card_type = ? WHERE product_id = ?", "prepaid", "card_type"),
        (
            "UPDATE customer_cards SET product_status = ? WHERE product_id = ?",
            "Frozen",
            "product_status",
        ),
        ("UPDATE customer_cards SET currency = ? WHERE product_id = ?", "US", "currency"),
    ],
)
def test_a_card_the_runtime_cannot_read_is_named(
    bank: Path, tmp_path: Path, statement: str, value: str, fields: str
) -> None:
    broken = _altered(bank, tmp_path, statement, value, "PRD-T190001")
    assert check_rows(broken).problems == (
        f"customer_cards: 1 rows do not fit CardView ({fields})",
    )


def test_rows_with_the_same_defect_are_counted_together(bank: Path, tmp_path: Path) -> None:
    broken = _altered(
        bank,
        tmp_path,
        "UPDATE transactions_enriched SET transaction_status = ?, channel = ? "
        "WHERE transaction_country = ?",
        "Bogus",
        "Telegraph",
        "MX",
    )
    (problem,) = check_rows(broken).problems
    with duckdb.connect(str(broken), read_only=True) as con:
        changed = con.execute(
            "SELECT count(*) FROM transactions_enriched WHERE transaction_country = 'MX'"
        ).fetchone()
    assert changed is not None
    assert changed[0] > 1
    assert problem == (
        f"transactions_enriched: {changed[0]} rows do not fit TransactionView "
        "(channel, transaction_status)"
    )


def test_an_id_longer_than_the_contract_allows_is_found(bank: Path, tmp_path: Path) -> None:
    broken = _altered(
        bank,
        tmp_path,
        "UPDATE transactions_enriched SET transaction_id = ? WHERE transaction_id = ?",
        "TRX-" + "9" * 61,
        "TRX-T1900001",
    )
    assert check_rows(broken).problems == (
        "transactions_enriched: 1 rows do not fit TransactionView (transaction_id)",
    )


def test_a_transaction_on_a_card_of_someone_else_is_found(bank: Path, tmp_path: Path) -> None:
    # Its card ending cannot be shown, so the agent could never ask about it.
    broken = _altered(
        bank,
        tmp_path,
        "UPDATE transactions_enriched SET product_id = ? WHERE transaction_id = ?",
        "PRD-T190002",
        "TRX-T1900001",
    )
    assert check_rows(broken).problems == (
        "transactions_enriched: 1 rows have no card of their customer in customer_cards",
    )
