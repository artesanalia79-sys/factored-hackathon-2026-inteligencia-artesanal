"""Does every served row fit the view the runtime reads it into? (T19)

``validate_serving_db`` checks tables, columns, types, NULLs and keys. The runtime then parses
each row into a contract view (``TransactionView``, ``CardView``), which is stricter: enums,
currency and country codes, the card ending, the length of an id. One row that does not fit
turns its customer's transaction list into an error and a tool call into "temporarily
unavailable", and no dbt test of the gold layer covers the status, type and channel values.

Every row is checked without parsing every row: the views constrain only a few columns, so
each distinct combination of their values is parsed once, through the real models. A problem
names the table, the view and the fields, never a value.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from pydantic import ValidationError

from bankagent.contracts.domain import CardView, TransactionView

# One row per combination of everything `TransactionView` constrains: the enum and code
# columns and the length of each id (an id is only limited in length).
_TRANSACTION_SHAPES_SQL = """
SELECT transaction_type, channel, transaction_status, currency, transaction_country,
       length(transaction_id), length(product_id), count(*)
FROM transactions_enriched
GROUP BY ALL
"""
_CARD_SHAPES_SQL = """
SELECT card_type, card_last4, currency, product_status, length(product_id), count(*)
FROM customer_cards
GROUP BY ALL
"""
# A transaction is shown with the ending of its card, which must be a card of its customer.
_NO_CARD_SQL = """
SELECT count(*)
FROM transactions_enriched AS t
LEFT JOIN customer_cards AS c ON c.product_id = t.product_id AND c.customer_id = t.customer_id
WHERE c.product_id IS NULL
"""
_WHEN = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RowCheck:
    """How many rows were covered, through how many distinct shapes, and what did not fit."""

    transactions: int
    transaction_shapes: int
    cards: int
    card_shapes: int
    problems: tuple[str, ...]


def _fields(error: ValidationError) -> str:
    return ", ".join(sorted({".".join(str(part) for part in e["loc"]) for e in error.errors()}))


def _transaction_misfit(shape: tuple[Any, ...], id_length: int, card_length: int) -> str | None:
    """The fields that keep a transaction of this shape out of its view; ``None`` if it fits."""
    kind, channel, status, currency, country = shape
    try:
        TransactionView(
            transaction_id="x" * id_length,
            product_id="x" * card_length,
            transaction_ts=_WHEN,
            transaction_type=kind,
            amount=Decimal("1.00"),
            currency=currency,
            channel=channel,
            transaction_country=country,
            transaction_status=status,
            is_foreign=False,
        )
    except ValidationError as error:
        return _fields(error)
    return None


def _card_misfit(shape: tuple[Any, ...], id_length: int) -> str | None:
    card_type, last4, currency, status = shape
    try:
        CardView(
            product_id="x" * id_length,
            card_type=card_type,
            card_last4=last4,
            currency=currency,
            product_status=status,
        )
    except ValidationError as error:
        return _fields(error)
    return None


def check_rows(serving_db: Path) -> RowCheck:
    with duckdb.connect(str(serving_db), read_only=True) as con:
        transactions = con.execute(_TRANSACTION_SHAPES_SQL).fetchall()
        cards = con.execute(_CARD_SHAPES_SQL).fetchall()
        no_card = con.execute(_NO_CARD_SQL).fetchone()

    misfits: Counter[tuple[str, str, str]] = Counter()
    for *shape, id_length, card_length, rows in transactions:
        found = _transaction_misfit(tuple(shape), id_length, card_length)
        if found is not None:
            misfits["transactions_enriched", "TransactionView", found] += rows
    for *shape, id_length, rows in cards:
        found = _card_misfit(tuple(shape), id_length)
        if found is not None:
            misfits["customer_cards", "CardView", found] += rows

    problems = [
        f"{table}: {rows:,} rows do not fit {view} ({fields})"
        for (table, view, fields), rows in sorted(misfits.items())
    ]
    if no_card and no_card[0]:
        problems.append(
            f"transactions_enriched: {no_card[0]:,} rows have no card of their customer in "
            "customer_cards"
        )
    return RowCheck(
        transactions=sum(row[-1] for row in transactions),
        transaction_shapes=len(transactions),
        cards=sum(row[-1] for row in cards),
        card_shapes=len(cards),
        problems=tuple(problems),
    )
