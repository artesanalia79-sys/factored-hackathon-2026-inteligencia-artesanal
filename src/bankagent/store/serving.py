"""Read-only access to the serving DB (DuckDB): identity lookups and the reads behind the tools.

Rules that hold here so callers cannot get them wrong:

- Every statement is a constant string with bound parameters; no SQL is ever built from values.
- Every read behind a tool (cards, transactions, prior complaints) takes ``customer_id`` and
  filters by it, so another customer's card or transaction is indistinguishable from a missing
  one. Not scoped to one customer: the persona picker of the login (``active_customers``, T7).
- Views exclude ``fraud_score`` and ``dq_flags`` (ADR 0003); the policy engine (T9) reads its own
  inputs below (``risk_signals``, ``last_dispute_date``, ``as_of_date``): no tool calls them, and
  no tool result or template ever carries ``fraud_score`` or ``dq_flags``.

Timestamps are naive UTC in the file and are returned as aware UTC datetimes.
Owner: Juan José (T7, T8, T19); policy inputs: Santiago (T9).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date
from pathlib import Path
from typing import Any

import duckdb
from pydantic import ValidationError

from bankagent.contracts.domain import CardView, TransactionRiskSignals, TransactionView
from bankagent.contracts.enums import Language
from bankagent.contracts.tools import SearchTransactionsArgs

UNKNOWN_DATA_MODE = "unknown"


_CARDS_SQL = """
SELECT product_id, card_type, card_last4, currency, product_status, expiration_date
FROM customer_cards
WHERE customer_id = $customer_id
  AND ($product_id::VARCHAR IS NULL OR product_id = $product_id::VARCHAR)
ORDER BY product_id
"""

# One statement for the lookup by id and for the search: every filter is optional and bound.
# The card join repeats the customer, so a card of someone else can never label a transaction.
_TRANSACTIONS_SQL = """
SELECT t.transaction_id, t.product_id, c.card_last4, t.transaction_ts, t.transaction_type,
       t.transaction_category, t.amount, t.currency, t.amount_usd, t.channel, t.merchant_name,
       t.merchant_category, t.transaction_country, t.transaction_city, t.transaction_status,
       t.is_foreign
FROM transactions_enriched AS t
LEFT JOIN customer_cards AS c
       ON c.product_id = t.product_id AND c.customer_id = t.customer_id
WHERE t.customer_id = $customer_id
  AND ($transaction_id::VARCHAR IS NULL OR t.transaction_id = $transaction_id::VARCHAR)
  AND ($amount_min::DECIMAL(15,2) IS NULL OR t.amount >= $amount_min::DECIMAL(15,2))
  AND ($amount_max::DECIMAL(15,2) IS NULL OR t.amount <= $amount_max::DECIMAL(15,2))
  AND ($currency::VARCHAR IS NULL OR t.currency = $currency::VARCHAR)
  AND ($merchant::VARCHAR IS NULL
       OR contains(strip_accents(lower(t.merchant_name)), strip_accents(lower($merchant::VARCHAR))))
  AND ($date_from::DATE IS NULL OR CAST(t.transaction_ts AS DATE) >= $date_from::DATE)
  AND ($date_to::DATE IS NULL OR CAST(t.transaction_ts AS DATE) <= $date_to::DATE)
  AND ($card_last4::VARCHAR IS NULL OR c.card_last4 = $card_last4::VARCHAR)
ORDER BY t.transaction_ts DESC, t.transaction_id
LIMIT $limit
"""

# Source statuses that still count as an open case (the rest: Resolved, Closed, Rejected).
_OPEN_COMPLAINT_SQL = """
SELECT complaint_id
FROM dispute_history
WHERE customer_id = ? AND related_transaction_id = ?
  AND status IN ('Open', 'In Process', 'Escalated')
ORDER BY created_at DESC, complaint_id
LIMIT 1
"""

# Policy-only (T9): fraud_score and dq_flags are excluded from every other statement here.
_RISK_SQL = """
SELECT fraud_score, dq_flags FROM transactions_enriched
WHERE customer_id = ? AND transaction_id = ?
"""

# Any status, any transaction: a repeat disputer is a property of the customer, not of one
# transaction. A dispute is T16's strict definition (`t16_dispute_definition.sql`): a
# Complaint or Claim in category Transactions. Not `case_type = 'Claim'` alone: on the curated
# data 81% of the customers with a recent Claim had only Fees, app, branch or service claims,
# and most unrecognized-charge cases are filed as Complaint (PR #44 review).
_LAST_DISPUTE_SQL = """
SELECT max(created_at) FROM dispute_history
WHERE customer_id = ? AND category = 'Transactions' AND case_type IN ('Complaint', 'Claim')
"""


class ServingDataError(RuntimeError):
    """A served row does not fit its contract view.

    Names the table, the view and the offending fields, never a value: it is raised outside
    the ``except`` block so the validation error (which quotes the row) is not chained to it.
    """

    def __init__(self, table: str, error: ValidationError) -> None:
        fields = sorted({".".join(str(part) for part in e["loc"]) for e in error.errors()})
        super().__init__(f"a {table} row does not fit {error.title} ({', '.join(fields)})")


@dataclass(frozen=True, slots=True)
class CustomerProfile:
    """Minimal profile used to open a session. Server-side only."""

    customer_id: str
    first_name: str
    country: str
    customer_status: str
    language: Language | None

    @property
    def is_active(self) -> bool:
        return self.customer_status == "Active"


def _profile(row: tuple[str, str, str, str, str | None]) -> CustomerProfile:
    customer_id, first_name, country, status, language = row
    return CustomerProfile(
        customer_id=customer_id,
        first_name=first_name,
        country=country,
        customer_status=status,
        language=Language(language) if language in set(Language) else None,
    )


def _card(row: tuple[Any, ...]) -> CardView:
    product_id, card_type, card_last4, currency, status, expiration_date = row
    try:
        return CardView(
            product_id=product_id,
            card_type=card_type,
            card_last4=card_last4,
            currency=currency,
            product_status=status,
            expiration_date=expiration_date,
        )
    except ValidationError as exc:
        problem = ServingDataError("customer_cards", exc)
    raise problem


def _transaction(row: tuple[Any, ...]) -> TransactionView:
    try:
        return TransactionView(
            transaction_id=row[0],
            product_id=row[1],
            card_last4=row[2],
            transaction_ts=row[3].replace(tzinfo=UTC),
            transaction_type=row[4],
            transaction_category=row[5],
            amount=row[6],
            currency=row[7],
            amount_usd=row[8],
            channel=row[9],
            merchant_name=row[10],
            merchant_category=row[11],
            transaction_country=row[12],
            transaction_city=row[13],
            transaction_status=row[14],
            is_foreign=row[15],
        )
    except ValidationError as exc:
        problem = ServingDataError("transactions_enriched", exc)
    raise problem


class ServingDB:
    """Opens a short read-only connection per call; the file is never written from here."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if not Path(self._path).exists():
            raise FileNotFoundError(
                f"serving DB not found at {self._path}; run `uv run poe fixtures` "
                "(synthetic) or `uv run poe serving-build` (curated)"
            )

    def data_mode(self) -> str:
        """``synthetic`` or ``curated``, as recorded by the build in ``_serving_metadata``.

        ``unknown`` when the table or the row is missing: callers that gate on synthetic data
        must treat it as not synthetic (fail closed).
        """
        with duckdb.connect(self._path, read_only=True) as con:
            try:
                row = con.execute(
                    "SELECT value FROM _serving_metadata WHERE key = ?", ["data_mode"]
                ).fetchone()
            except duckdb.CatalogException:
                return UNKNOWN_DATA_MODE
        return UNKNOWN_DATA_MODE if row is None else str(row[0])

    def customer(self, customer_id: str) -> CustomerProfile | None:
        with duckdb.connect(self._path, read_only=True) as con:
            row = con.execute(
                "SELECT customer_id, first_name, country, customer_status, preferred_language "
                "FROM customer_profile_min WHERE customer_id = ?",
                [customer_id],
            ).fetchone()
        return None if row is None else _profile(row)

    def active_customers(self, limit: int) -> list[CustomerProfile]:
        """The first ``limit`` active customers by id (the demo persona picker)."""
        with duckdb.connect(self._path, read_only=True) as con:
            rows = con.execute(
                "SELECT customer_id, first_name, country, customer_status, preferred_language "
                "FROM customer_profile_min WHERE customer_status = 'Active' "
                "ORDER BY customer_id LIMIT ?",
                [limit],
            ).fetchall()
        return [_profile(row) for row in rows]

    # -- reads behind the tools (T8): customer data is always scoped by customer_id ----------

    def cards(self, customer_id: str, product_id: str | None = None) -> list[CardView]:
        """The customer's cards by product id, or only ``product_id`` if it is theirs."""
        with duckdb.connect(self._path, read_only=True) as con:
            rows = con.execute(
                _CARDS_SQL, {"customer_id": customer_id, "product_id": product_id}
            ).fetchall()
        return [_card(row) for row in rows]

    def transaction(self, customer_id: str, transaction_id: str) -> TransactionView | None:
        """One transaction of this customer; ``None`` for a missing or a foreign id alike."""
        found = self._transactions(customer_id, SearchTransactionsArgs(), transaction_id, 1)
        return found[0] if found else None

    def search_transactions(
        self, customer_id: str, filters: SearchTransactionsArgs, *, limit: int
    ) -> list[TransactionView]:
        """The customer's transactions matching every given filter, newest first.

        ``limit`` is separate from ``filters.limit`` so a caller can ask for one extra row to
        learn whether the result was truncated. Dates compare against the UTC date.
        """
        return self._transactions(customer_id, filters, None, limit)

    def _transactions(
        self,
        customer_id: str,
        filters: SearchTransactionsArgs,
        transaction_id: str | None,
        limit: int,
    ) -> list[TransactionView]:
        parameters = {
            "customer_id": customer_id,
            "transaction_id": transaction_id,
            "amount_min": filters.amount_min,
            "amount_max": filters.amount_max,
            "currency": filters.currency,
            # A blank query carries no information; it must not turn into "match everything".
            "merchant": (filters.merchant_query or "").strip() or None,
            "date_from": filters.date_from,
            "date_to": filters.date_to,
            "card_last4": filters.card_last4,
            "limit": limit,
        }
        with duckdb.connect(self._path, read_only=True) as con:
            rows = con.execute(_TRANSACTIONS_SQL, parameters).fetchall()
        return [_transaction(row) for row in rows]

    def open_complaint_id(self, customer_id: str, transaction_id: str) -> str | None:
        """Newest still-open prior complaint of this customer on the transaction, if any."""
        with duckdb.connect(self._path, read_only=True) as con:
            row = con.execute(_OPEN_COMPLAINT_SQL, [customer_id, transaction_id]).fetchone()
        return None if row is None else str(row[0])

    # -- policy engine inputs (T9): never read by a tool, a template or the model --------------

    def as_of_date(self) -> date:
        """The build's as-of date (``_serving_metadata``). Policy compares windows to this, not
        the wall clock, so a frozen fixture or curated snapshot stays internally consistent."""
        with duckdb.connect(self._path, read_only=True) as con:
            row = con.execute(
                "SELECT value FROM _serving_metadata WHERE key = 'as_of_date'"
            ).fetchone()
        if row is None:
            raise RuntimeError("_serving_metadata has no 'as_of_date' row")
        return date.fromisoformat(str(row[0]))

    def risk_signals(self, customer_id: str, transaction_id: str) -> TransactionRiskSignals | None:
        """``fraud_score`` and ``dq_flags`` for one of the customer's transactions (ADR 0003).
        ``None`` for a missing or a foreign id, exactly like the tools' reads."""
        with duckdb.connect(self._path, read_only=True) as con:
            row = con.execute(_RISK_SQL, [customer_id, transaction_id]).fetchone()
        if row is None:
            return None
        return TransactionRiskSignals(
            transaction_id=transaction_id, fraud_score=row[0], dq_flags=tuple(row[1] or ())
        )

    def last_dispute_date(self, customer_id: str) -> date | None:
        """Date of this customer's most recent dispute in the bank's history (any status, any
        transaction), or ``None``. Input to the repeat-disputer trigger (T9), compared to
        ``as_of_date``; the agent's own disputes are in ``OpsStore.last_dispute_date`` and are
        compared to the filing day instead (``bankagent.policy.inputs.build_inputs``).
        """
        with duckdb.connect(self._path, read_only=True) as con:
            row = con.execute(_LAST_DISPUTE_SQL, [customer_id]).fetchone()
        value = row[0] if row else None
        return None if value is None else value.date()
