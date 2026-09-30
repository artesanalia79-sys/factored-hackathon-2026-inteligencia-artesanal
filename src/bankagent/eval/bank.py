"""Ground truth about the synthetic bank that the scorer needs.

Owners of every card, transaction and prior complaint, customer segments (for slices) and the
personal data that must never appear in a reply. Built from the fixture YAML with the same
deterministic generator as the fixture DuckDB, so no database file is needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from bankagent.contracts.enums import Language, Segment
from bankagent.contracts.serving import TABLES_BY_NAME
from bankagent.fixtures.builder import DEFAULT_SOURCE, build_rows, load_source


@dataclass(frozen=True, slots=True)
class TransactionFacts:
    transaction_id: str
    customer_id: str
    product_id: str
    card_last4: str
    ts: datetime
    amount: Decimal
    currency: str
    merchant_name: str | None


@dataclass(frozen=True, slots=True)
class CustomerFacts:
    customer_id: str
    first_name: str
    document_number: str
    segment: Segment
    preferred_language: Language


@dataclass(frozen=True, slots=True)
class BankIndex:
    customers: Mapping[str, CustomerFacts]
    transactions: Mapping[str, TransactionFacts]
    card_owner: Mapping[str, str]
    card_last4: Mapping[str, str]
    complaint_owner: Mapping[str, str]
    merchant_owners: Mapping[str, frozenset[str]]

    def owner_of(self, resource_id: str) -> str | None:
        """Customer that owns a transaction, card, complaint or customer id; None if unknown."""
        if resource_id in self.transactions:
            return self.transactions[resource_id].customer_id
        if resource_id in self.card_owner:
            return self.card_owner[resource_id]
        if resource_id in self.complaint_owner:
            return self.complaint_owner[resource_id]
        if resource_id in self.customers:
            return resource_id
        return None

    def segment_of(self, customer_id: str) -> str:
        customer = self.customers.get(customer_id)
        return customer.segment.value if customer else "unknown"

    def exclusive_merchants(self, customer_id: str) -> frozenset[str]:
        """Merchant names that appear only on this customer's transactions."""
        return frozenset(name for name, who in self.merchant_owners.items() if who == {customer_id})


def load_bank(source_dir: Path = DEFAULT_SOURCE) -> BankIndex:
    source = load_source(source_dir)
    rows = build_rows(source)
    columns = TABLES_BY_NAME["transactions_enriched"].column_names
    col = {name: i for i, name in enumerate(columns)}
    card_last4 = {c.product_id: c.card_last4 for c in source.cards}
    transactions: dict[str, TransactionFacts] = {}
    for row in rows["transactions_enriched"]:
        product_id = row[col["product_id"]]
        transactions[row[col["transaction_id"]]] = TransactionFacts(
            transaction_id=row[col["transaction_id"]],
            customer_id=row[col["customer_id"]],
            product_id=product_id,
            card_last4=card_last4[product_id],
            ts=row[col["transaction_ts"]],
            amount=Decimal(str(row[col["amount"]])),
            currency=row[col["currency"]],
            merchant_name=row[col["merchant_name"]],
        )
    merchant_owners: dict[str, set[str]] = {}
    for txn in transactions.values():
        if txn.merchant_name:
            merchant_owners.setdefault(txn.merchant_name, set()).add(txn.customer_id)
    return BankIndex(
        customers={
            c.customer_id: CustomerFacts(
                customer_id=c.customer_id,
                first_name=c.first_name,
                document_number=c.document_number,
                segment=c.segment,
                preferred_language=c.preferred_language,
            )
            for c in source.customers
        },
        transactions=transactions,
        card_owner={c.product_id: c.customer_id for c in source.cards},
        card_last4=card_last4,
        complaint_owner={d.complaint_id: d.customer_id for d in source.disputes},
        merchant_owners={name: frozenset(who) for name, who in merchant_owners.items()},
    )
