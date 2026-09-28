"""Build the synthetic fixture bank (a serving DB) from ``tests/fixtures/bank/*.yaml``.

Usage:
    python -m bankagent.fixtures.builder                 # build data/fixtures/bank_fixture.duckdb
    python -m bankagent.fixtures.builder --check         # build to a temp dir; fail on drift
    python -m bankagent.fixtures.builder --update-hash   # build and record the new content hash

The build is deterministic: background purchases use ``random.Random(f"{seed}:{product_id}")``.
Determinism is checked with a content hash (canonical JSON of every row ordered by primary key,
excluding ``built_at``), because DuckDB files are not byte-stable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import tempfile
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb
import yaml
from pydantic import Field

from bankagent.contracts.base import Contract, CountryCode, CurrencyCode, Last4
from bankagent.contracts.enums import (
    CardType,
    Channel,
    Country,
    CustomerStatus,
    DataMode,
    Dialect,
    Language,
    ProductStatus,
    Segment,
    Specialty,
    TransactionStatus,
    TransactionType,
)
from bankagent.contracts.serving import (
    SERVING_CONTRACT_VERSION,
    SERVING_TABLES,
    ddl,
    validate_serving_db,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = ROOT / "tests" / "fixtures" / "bank"
DEFAULT_OUT = ROOT / "data" / "fixtures" / "bank_fixture.duckdb"
HASH_FILE_NAME = "content_hash.txt"
DUPLICATE_WINDOW = timedelta(seconds=120)
CENT = Decimal("0.01")


class FixtureError(ValueError):
    """The fixture YAML is inconsistent (bad reference, duplicate id, ...)."""


# ---------------------------------------------------------------------------
# YAML schema (strict: unknown keys fail)
# ---------------------------------------------------------------------------


class MerchantSpec(Contract):
    name: str
    category: str
    city: str


class BackgroundSpec(Contract):
    per_active_card: int = Field(ge=0, le=50)
    lookback_days: int = Field(ge=1, le=365)
    amount_ranges: dict[CurrencyCode, tuple[Decimal, Decimal]]
    channels: tuple[Channel, ...]
    merchants: dict[Country, tuple[MerchantSpec, ...]]


class FixtureMeta(Contract):
    fixture_version: int
    seed: int
    as_of_date: date
    source: str
    fx_rates_per_usd: dict[CurrencyCode, Decimal]
    background: BackgroundSpec


class CustomerSpec(Contract):
    customer_id: str
    first_name: str
    document_number: str
    country: Country
    segment: Segment
    customer_status: CustomerStatus
    preferred_language: Language
    registration_date: date
    dialect: Dialect
    scenarios: tuple[str, ...] = ()


class CardSpec(Contract):
    product_id: str
    customer_id: str
    card_type: CardType
    card_last4: Last4
    currency: CurrencyCode
    product_status: ProductStatus
    opening_date: date
    expiration_date: date | None = None


class TransactionSpec(Contract):
    transaction_id: str
    product_id: str
    ts: datetime
    type: TransactionType
    category: str | None = None
    amount: Decimal
    currency: CurrencyCode
    channel: Channel
    merchant_name: str | None = None
    merchant_category: str | None = None
    country: CountryCode
    city: str | None = None
    status: TransactionStatus
    fraud_score: float | None = Field(default=None, ge=0, le=100)
    process_date: date | None = None


class DisputeSpec(Contract):
    complaint_id: str
    customer_id: str
    created_at: datetime
    case_type: str
    category: str
    subcategory: str | None = None
    status: str
    claimed_amount: Decimal | None = None
    currency: CurrencyCode | None = None
    related_transaction_id: str | None = None
    resolution_days: int | None = None


class AgentSpec(Contract):
    agent_id: str
    languages: tuple[Language, ...]
    specialty: Specialty
    agent_type: str
    country_of_origin: str
    is_active: bool


class FixtureSource(Contract):
    meta: FixtureMeta
    customers: tuple[CustomerSpec, ...]
    cards: tuple[CardSpec, ...]
    transactions: tuple[TransactionSpec, ...]
    disputes: tuple[DisputeSpec, ...]
    agents: tuple[AgentSpec, ...]


def _read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_source(source_dir: Path = DEFAULT_SOURCE) -> FixtureSource:
    return FixtureSource(
        meta=FixtureMeta.model_validate(_read_yaml(source_dir / "fixture.yaml")),
        customers=_read_yaml(source_dir / "personas.yaml")["customers"],
        cards=_read_yaml(source_dir / "cards.yaml")["cards"],
        transactions=_read_yaml(source_dir / "transactions.yaml")["transactions"],
        disputes=_read_yaml(source_dir / "disputes.yaml")["disputes"],
        agents=_read_yaml(source_dir / "agents.yaml")["agents"],
    )


# ---------------------------------------------------------------------------
# Row generation
# ---------------------------------------------------------------------------


def _unique(ids: Iterable[str], what: str) -> None:
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            raise FixtureError(f"duplicate {what}: {item}")
        seen.add(item)


def _background(source: FixtureSource, cards: Sequence[CardSpec]) -> list[TransactionSpec]:
    """Seeded filler purchases, kept inside each card's validity window (no accidental dq flags)."""
    meta, spec = source.meta, source.meta.background
    customers = {c.customer_id: c for c in source.customers}
    generated: list[TransactionSpec] = []
    for card in cards:
        if card.product_status != ProductStatus.ACTIVE or card.currency not in spec.amount_ranges:
            continue
        country = customers[card.customer_id].country
        rng = random.Random(f"{meta.seed}:{card.product_id}")
        start = max(card.opening_date, meta.as_of_date - timedelta(days=spec.lookback_days))
        end = min(card.expiration_date or meta.as_of_date, meta.as_of_date)
        span = (end - start).days
        if span < 1:
            continue
        low, high = spec.amount_ranges[card.currency]
        card_suffix = card.product_id.removeprefix("CARD-FX-")
        for n in range(spec.per_active_card):
            merchant = rng.choice(spec.merchants[country])
            day = start + timedelta(days=rng.randint(0, span - 1))
            seconds = rng.randint(6 * 3600, 23 * 3600)
            generated.append(
                TransactionSpec(
                    transaction_id=f"TXN-FX-BG-{card_suffix}-{n + 1:02d}",
                    product_id=card.product_id,
                    ts=datetime.combine(day, time()) + timedelta(seconds=seconds),
                    type=TransactionType.PURCHASE,
                    category=merchant.category,
                    amount=Decimal(rng.randint(int(low), int(high))).quantize(CENT),
                    currency=card.currency,
                    channel=rng.choice(spec.channels),
                    merchant_name=merchant.name,
                    merchant_category=merchant.category,
                    country=country.value,
                    city=merchant.city,
                    status=TransactionStatus.APPROVED,
                    fraud_score=round(rng.uniform(0.0, 20.0), 1),
                )
            )
    return generated


def _dq_flags(txn: TransactionSpec, card: CardSpec, duplicates: set[str]) -> list[str]:
    flags: list[str] = []
    if txn.ts.date() < card.opening_date:
        flags.append("dq_txn_before_product_opening")
    if card.expiration_date is not None and txn.ts.date() > card.expiration_date:
        flags.append("dq_txn_after_card_expiry")
    if txn.type == TransactionType.PURCHASE and not txn.merchant_name:
        flags.append("dq_missing_merchant")
    if txn.transaction_id in duplicates:
        flags.append("dq_possible_duplicate")
    return sorted(flags)


def _possible_duplicates(transactions: Sequence[TransactionSpec]) -> set[str]:
    """Same card, merchant and amount within DUPLICATE_WINDOW (mirrors the silver-layer rule)."""
    flagged: set[str] = set()
    ordered = sorted(transactions, key=lambda t: (t.product_id, t.ts, t.transaction_id))
    for previous, current in pairwise(ordered):
        if (
            previous.product_id == current.product_id
            and previous.merchant_name == current.merchant_name
            and previous.amount == current.amount
            and current.ts - previous.ts <= DUPLICATE_WINDOW
        ):
            flagged.update({previous.transaction_id, current.transaction_id})
    return flagged


def build_rows(source: FixtureSource) -> dict[str, list[tuple[Any, ...]]]:
    """Return rows per serving table, in contract column order."""
    meta = source.meta
    _unique((c.customer_id for c in source.customers), "customer_id")
    _unique((c.product_id for c in source.cards), "product_id")
    _unique((d.complaint_id for d in source.disputes), "complaint_id")
    _unique((a.agent_id for a in source.agents), "agent_id")

    customers = {c.customer_id: c for c in source.customers}
    cards = {c.product_id: c for c in source.cards}
    for card in source.cards:
        if card.customer_id not in customers:
            raise FixtureError(f"{card.product_id}: unknown customer {card.customer_id}")

    transactions = list(source.transactions) + _background(source, source.cards)
    _unique((t.transaction_id for t in transactions), "transaction_id")
    duplicates = _possible_duplicates(transactions)
    txn_owner: dict[str, str] = {}

    txn_rows: list[tuple[Any, ...]] = []
    for txn in transactions:
        card = cards.get(txn.product_id)
        if card is None:
            raise FixtureError(f"{txn.transaction_id}: unknown product {txn.product_id}")
        if txn.currency not in meta.fx_rates_per_usd:
            raise FixtureError(f"{txn.transaction_id}: no FX rate for {txn.currency}")
        customer = customers[card.customer_id]
        txn_owner[txn.transaction_id] = customer.customer_id
        amount_usd = (txn.amount / meta.fx_rates_per_usd[txn.currency]).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        txn_rows.append(
            (
                txn.transaction_id,
                customer.customer_id,
                txn.product_id,
                txn.ts,
                txn.process_date or txn.ts.date(),
                txn.type.value,
                txn.category,
                txn.amount.quantize(CENT),
                txn.currency,
                amount_usd,
                txn.channel.value,
                txn.merchant_name,
                txn.merchant_category,
                txn.country,
                txn.city,
                txn.status.value,
                txn.fraud_score,
                txn.country != customer.country.value,
                _dq_flags(txn, card, duplicates),
            )
        )

    dispute_rows: list[tuple[Any, ...]] = []
    for dispute in source.disputes:
        if dispute.customer_id not in customers:
            raise FixtureError(f"{dispute.complaint_id}: unknown customer {dispute.customer_id}")
        related = dispute.related_transaction_id
        if related is not None and txn_owner.get(related) != dispute.customer_id:
            raise FixtureError(f"{dispute.complaint_id}: {related} is not the customer's")
        dispute_rows.append(
            (
                dispute.complaint_id,
                dispute.customer_id,
                dispute.created_at,
                dispute.case_type,
                dispute.category,
                dispute.subcategory,
                dispute.status,
                dispute.claimed_amount.quantize(CENT)
                if dispute.claimed_amount is not None
                else None,
                dispute.currency,
                related,
                dispute.resolution_days,
            )
        )

    return {
        "customer_profile_min": [
            (
                c.customer_id,
                hashlib.sha256(f"fixture:{c.document_number}".encode()).hexdigest(),
                c.first_name,
                c.country.value,
                c.segment.value,
                c.customer_status.value,
                c.preferred_language.value,
                c.registration_date,
            )
            for c in source.customers
        ],
        "customer_cards": [
            (
                c.product_id,
                c.customer_id,
                c.card_type.value,
                c.card_last4,
                c.currency,
                c.product_status.value,
                c.opening_date,
                c.expiration_date,
            )
            for c in source.cards
        ],
        "transactions_enriched": txn_rows,
        "dispute_history": dispute_rows,
        "agents_routing": [
            (
                a.agent_id,
                [lang.value for lang in a.languages],
                a.specialty.value,
                a.agent_type,
                a.country_of_origin,
                a.is_active,
            )
            for a in source.agents
        ],
        "_serving_metadata": [
            ("data_mode", DataMode.SYNTHETIC.value),
            ("as_of_date", meta.as_of_date.isoformat()),
            ("built_at", datetime.now(UTC).replace(microsecond=0).isoformat()),
            ("source", meta.source),
            ("contract_version", SERVING_CONTRACT_VERSION),
            ("fixture_version", str(meta.fixture_version)),
            ("seed", str(meta.seed)),
        ],
    }


# ---------------------------------------------------------------------------
# DuckDB output and content hash
# ---------------------------------------------------------------------------


def write_db(rows: dict[str, list[tuple[Any, ...]]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    for stale in (out, out.with_name(out.name + ".wal")):
        stale.unlink(missing_ok=True)
    with duckdb.connect(str(out)) as con:
        con.execute(ddl())
        for table in SERVING_TABLES:
            table_rows = rows[table.name]
            if not table_rows:
                continue
            placeholders = ", ".join("?" for _ in table.columns)
            # Table names come from the contract constants, values are bound parameters.
            con.executemany(f'INSERT INTO "{table.name}" VALUES ({placeholders})', table_rows)  # noqa: S608


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def content_hash(con: duckdb.DuckDBPyConnection) -> str:
    """sha256 of every serving row ordered by primary key, excluding ``built_at``."""
    payload: dict[str, list[list[Any]]] = {}
    for table in SERVING_TABLES:
        query = f'SELECT * FROM "{table.name}" ORDER BY "{table.primary_key}"'  # noqa: S608
        rows = [[_jsonable(v) for v in row] for row in con.execute(query).fetchall()]
        if table.name == "_serving_metadata":
            rows = [row for row in rows if row[0] != "built_at"]
        payload[table.name] = rows
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build(source_dir: Path = DEFAULT_SOURCE, out: Path = DEFAULT_OUT) -> tuple[str, list[str]]:
    """Build the fixture DB. Returns (content hash, contract problems)."""
    write_db(build_rows(load_source(source_dir)), out)
    with duckdb.connect(str(out), read_only=True) as con:
        return content_hash(con), validate_serving_db(con)


def committed_hash(source_dir: Path = DEFAULT_SOURCE) -> str | None:
    path = source_dir / HASH_FILE_NAME
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic fixture bank DuckDB.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail on hash or contract drift")
    mode.add_argument("--update-hash", action="store_true", help="record the new content hash")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    try:
        if args.check:
            with tempfile.TemporaryDirectory() as tmp:
                digest, problems = build(args.source, Path(tmp) / "bank_fixture.duckdb")
        else:
            digest, problems = build(args.source, args.out)
    except FixtureError as exc:
        print(f"ERROR: invalid fixture data: {exc}", file=sys.stderr)
        return 1

    if problems:
        print("Fixture bank violates the serving contract:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    expected = committed_hash(args.source)
    if args.update_hash:
        (args.source / HASH_FILE_NAME).write_text(digest + "\n", encoding="utf-8", newline="\n")
        print(f"Built {args.out} and recorded content hash {digest}")
        return 0
    if args.check:
        if digest != expected:
            print(
                f"Fixture content hash {digest} != committed {expected}.\n"
                "If the YAML change was intended: uv run poe fixtures --update-hash",
                file=sys.stderr,
            )
            return 1
        print(f"Fixture bank OK (contract v{SERVING_CONTRACT_VERSION}, hash {digest[:12]}).")
        return 0

    status = "matches" if digest == expected else "DIFFERS FROM"
    print(f"Built {args.out} (content hash {digest[:12]} {status} the committed hash).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
