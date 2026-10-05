"""A small serving DB that looks like the curated one, built from the contract's own DDL.

No organizer data: every row is written here, with invented names and amounts. Only the shapes
follow the curated file: ids start with `CLI-`, `PRD-` and `TRX-` (so the keyword interpreter
cannot read a reference, as on the real data) and amounts have cents. It records
`data_mode=curated`, so the app and the run treat it as the real file. Each customer exists for
one scenario of `bankagent.curated.cases`; the "decoys" are rows the picker must leave alone or
the policy must read on the right side of a limit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from bankagent.contracts.serving import SERVING_CONTRACT_VERSION, ddl, validate_serving_db
from bankagent.policy.schema import PolicyConfig, load_policy

AS_OF = date(2026, 6, 17)
CURRENCY = {"MX": "USD", "CO": "COP", "AR": "ARS"}

_CUSTOMER = (
    "INSERT INTO customer_profile_min (customer_id, document_hash, first_name, country, segment, "
    "customer_status, preferred_language, registration_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_CARD = (
    "INSERT INTO customer_cards (product_id, customer_id, card_type, card_last4, currency, "
    "product_status, opening_date, expiration_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_TRANSACTION = (
    "INSERT INTO transactions_enriched (transaction_id, customer_id, product_id, transaction_ts, "
    "process_date, transaction_type, transaction_category, amount, currency, amount_usd, channel, "
    "merchant_name, merchant_category, transaction_country, transaction_city, transaction_status, "
    "fraud_score, is_foreign, dq_flags) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_COMPLAINT = (
    "INSERT INTO dispute_history (complaint_id, customer_id, created_at, case_type, category, "
    "subcategory, status, claimed_amount, currency, related_transaction_id, resolution_days) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_AGENT = (
    "INSERT INTO agents_routing (agent_id, languages, specialty, agent_type, country_of_origin, "
    "is_active) VALUES (?, ?, ?, ?, ?, ?)"
)
_METADATA = "INSERT INTO _serving_metadata (key, value) VALUES (?, ?)"


class Bank:
    """Writes the rows of one synthetic bank. Ids are numbered in the order they are added."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con
        self._customers = 0
        self._transactions = 0
        self._complaints = 0

    def customer(self, country: str, *, status: str = "Active", card: str = "Active") -> str:
        """A customer with one card; returns the customer id (the card is `PRD-` + number)."""
        self._customers += 1
        number = f"{self._customers:04d}"
        customer_id = f"CLI-T19{number}"
        self._con.execute(
            _CUSTOMER,
            [
                customer_id,
                hashlib.sha256(customer_id.encode()).hexdigest(),
                f"Persona{number}",
                country,
                "Basic",
                status,
                "es",
                date(2020, 1, 1),
            ],
        )
        self._con.execute(
            _CARD,
            [
                f"PRD-T19{number}",
                customer_id,
                "credit",
                f"{4000 + self._customers:04d}",
                CURRENCY[country],
                card,
                date(2020, 1, 2),
                date(2030, 1, 1),
            ],
        )
        return customer_id

    def charge(
        self,
        customer_id: str,
        amount: str,
        *,
        days_ago: int = 5,
        status: str = "Approved",
        fraud_score: float | None = 4.5,
        flags: tuple[str, ...] = (),
        merchant: str | None = "Tienda del Sur",
        kind: str = "Purchase",
        currency: str | None = None,
        hour: int = 15,
    ) -> str:
        self._transactions += 1
        transaction_id = f"TRX-T19{self._transactions:05d}"
        country = self._country(customer_id)
        moment = datetime.combine(AS_OF - timedelta(days=days_ago), time(hour, 30))
        self._con.execute(
            _TRANSACTION,
            [
                transaction_id,
                customer_id,
                customer_id.replace("CLI-", "PRD-"),
                moment,
                moment.date(),
                kind,
                None,
                Decimal(amount),
                currency or CURRENCY[country],
                None,
                "ATM" if kind == "Withdrawal" else "POS",
                merchant,
                None,
                country,
                None,
                status,
                fraud_score,
                False,
                list(flags),
            ],
        )
        return transaction_id

    def complaint(
        self,
        customer_id: str,
        *,
        days_ago: int,
        category: str = "Transactions",
        case_type: str = "Claim",
        status: str = "Resolved",
        about: str | None = None,
    ) -> None:
        self._complaints += 1
        created = datetime.combine(AS_OF - timedelta(days=days_ago), time(9, 0))
        self._con.execute(
            _COMPLAINT,
            [
                f"QJA-T19{self._complaints:04d}",
                customer_id,
                created,
                case_type,
                category,
                None,
                status,
                None,
                None,
                about,
                None,
            ],
        )

    def _country(self, customer_id: str) -> str:
        row = self._con.execute(
            "SELECT country FROM customer_profile_min WHERE customer_id = ?", [customer_id]
        ).fetchone()
        assert row is not None
        return str(row[0])


def write_bank(path: Path, *, data_mode: str = "curated") -> dict[str, str]:
    """Build the bank at ``path``. Returns the customer of each named situation."""
    who: dict[str, str] = {}
    with duckdb.connect(str(path)) as con:
        con.execute(ddl())
        bank = Bank(con)

        # Fraud signal: well above the threshold, and exactly on it (30.0 escalates).
        who["fraud"] = bank.customer("MX")
        bank.charge(who["fraud"], "312.47", fraud_score=45.0)
        who["fraud_on_threshold"] = bank.customer("CO")
        bank.charge(who["fraud_on_threshold"], "1250300.75", fraud_score=30.0)

        # Repeat disputer: a Transactions claim 30 days ago, and one exactly 90 days ago.
        who["repeat"] = bank.customer("AR")
        bank.charge(who["repeat"], "98765.43")
        bank.complaint(who["repeat"], days_ago=30)
        who["repeat_on_limit"] = bank.customer("MX")
        bank.charge(who["repeat_on_limit"], "12.40")
        bank.complaint(who["repeat_on_limit"], days_ago=90, case_type="Complaint")

        # Inconsistent record: dated before its card was opened.
        who["flagged"] = bank.customer("CO")
        bank.charge(who["flagged"], "21450.60", flags=("dq_txn_before_product_opening",))

        # Two and three charges of the same amount.
        who["twins"] = bank.customer("AR")
        bank.charge(who["twins"], "6800.25", days_ago=3, merchant="Farmacia Norte")
        bank.charge(who["twins"], "6800.25", days_ago=4, merchant="Café Central")
        who["triplets"] = bank.customer("MX")
        for days_ago in (2, 6, 9):
            bank.charge(who["triplets"], "49.99", days_ago=days_ago)

        # An eligible charge on a card the core reports blocked: no block is offered.
        who["card_blocked"] = bank.customer("CO", card="Blocked")
        bank.charge(who["card_blocked"], "85900.50")

        # Plain eligible charges, one customer each; several kinds of text and currency.
        who["plain_mx"] = bank.customer("MX")
        bank.charge(who["plain_mx"], "1234.56", merchant="Cinépolis")
        who["plain_co"] = bank.customer("CO")
        bank.charge(who["plain_co"], "499000.00", kind="Withdrawal", merchant=None)
        who["plain_ar"] = bank.customer("AR")
        bank.charge(who["plain_ar"], "1890.35", kind="Payment", merchant="Óptica Mirada")
        who["plain_ar_usd"] = bank.customer("AR")
        bank.charge(who["plain_ar_usd"], "6.00", currency="USD")
        who["no_score"] = bank.customer("MX")
        bank.charge(who["no_score"], "77.10", fraud_score=None)
        who["window_edge"] = bank.customer("CO")
        bank.charge(who["window_edge"], "31000.75", days_ago=90)  # the last day inside
        who["below_threshold"] = bank.customer("AR")
        bank.charge(who["below_threshold"], "9100.10", fraud_score=29.99)
        who["old_claim"] = bank.customer("MX")
        bank.charge(who["old_claim"], "61.00")
        bank.complaint(who["old_claim"], days_ago=91)  # one day outside the repeat window
        who["other_claim"] = bank.customer("CO")
        bank.charge(who["other_claim"], "45100.30")
        bank.complaint(who["other_claim"], days_ago=10, category="Fees")  # not a dispute
        who["tomorrow"] = bank.customer("AR")
        bank.charge(who["tomorrow"], "2222.22", days_ago=-1)  # dated after as_of_date

        # Out of the window: one day too old, and much older.
        who["old"] = bank.customer("MX")
        bank.charge(who["old"], "310.00", days_ago=91)
        who["very_old"] = bank.customer("CO")
        bank.charge(who["very_old"], "70500.90", days_ago=400)

        # Not settled.
        for name, status in (("pending", "Pending"), ("declined", "Declined")):
            who[name] = bank.customer("AR")
            bank.charge(who[name], "15000.15", status=status)
        who["reversed"] = bank.customer("MX")
        bank.charge(who["reversed"], "18.75", status="Reversed", fraud_score=99.0)

        # A charge with an open prior complaint about it.
        who["open_complaint"] = bank.customer("CO")
        disputed = bank.charge(who["open_complaint"], "64000.40")
        bank.complaint(
            who["open_complaint"], days_ago=200, category="Fees", status="Open", about=disputed
        )

        # Decoys the picker must leave alone: customers the login refuses, and one with no
        # transactions at all.
        for status in ("Inactive", "Suspended", "Closed"):
            who[status.lower()] = bank.customer("MX", status=status)
            bank.charge(who[status.lower()], "99.90", fraud_score=80.0)
        who["no_charges"] = bank.customer("CO")

        # A fraud signal and a recent claim at once: a fraud case, never a "repeat" one.
        who["fraud_and_repeat"] = bank.customer("CO")
        bank.charge(who["fraud_and_repeat"], "52300.20", fraud_score=61.0)
        bank.complaint(who["fraud_and_repeat"], days_ago=12)
        # A closed complaint about a charge is not an open case: the charge can be disputed.
        who["closed_complaint"] = bank.customer("AR")
        settled = bank.charge(who["closed_complaint"], "7300.45")
        bank.complaint(
            who["closed_complaint"], days_ago=200, category="Fees", status="Resolved", about=settled
        )

        # Charges of one amount where only the newer can be disputed, so either pick shows
        # which one the agent took. The pairs after it must be left alone: a wrong pick would
        # end the same way (both out of the window), or no pick reaches a write (both refused,
        # for different reasons). Added last, so the rows above keep their ids.
        who["one_disputable_twin"] = bank.customer("CO")
        bank.charge(who["one_disputable_twin"], "23450.80", days_ago=8, merchant="Panadería Luna")
        bank.charge(who["one_disputable_twin"], "23450.80", days_ago=150, merchant="Librería Sol")
        # The same amount as the pair above: a charge's twins are its own customer's only.
        who["old_twins"] = bank.customer("MX")
        for days_ago in (120, 200):
            bank.charge(who["old_twins"], "23450.80", days_ago=days_ago)
        who["refused_twins"] = bank.customer("AR")
        bank.charge(who["refused_twins"], "4410.70", days_ago=6, status="Pending")
        bank.charge(who["refused_twins"], "4410.70", days_ago=130)
        # A second amount the "twins" customer paid twice: its charges are not twins of the
        # first pair.
        for days_ago in (100, 300):
            bank.charge(who["twins"], "3100.40", days_ago=days_ago)

        for agent_id, specialty in (
            ("AGT-1", "fraud"),
            ("AGT-2", "disputes"),
            ("AGT-3", "general"),
        ):
            con.execute(_AGENT, [agent_id, ["es"], specialty, "Human", "CO", True])
        for key, value in (
            ("data_mode", data_mode),
            ("as_of_date", AS_OF.isoformat()),
            ("built_at", "2026-06-18T00:00:00Z"),
            ("source", "tests/curated/conftest.py (synthetic rows)"),
            ("contract_version", SERVING_CONTRACT_VERSION),
        ):
            con.execute(_METADATA, [key, value])
        assert validate_serving_db(con) == []
    return who


@pytest.fixture(scope="session")
def bank_and_people(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str]]:
    path = tmp_path_factory.mktemp("curated-like") / "bank_curated.duckdb"
    return path, write_bank(path)


@pytest.fixture(scope="session")
def bank(bank_and_people: tuple[Path, dict[str, str]]) -> Path:
    """The curated-like serving DB. Read-only: the tests share one file."""
    return bank_and_people[0]


@pytest.fixture(scope="session")
def people(bank_and_people: tuple[Path, dict[str, str]]) -> dict[str, str]:
    """Customer id by the situation the customer was written for (`write_bank`)."""
    return bank_and_people[1]


@pytest.fixture(scope="session")
def policy() -> PolicyConfig:
    return load_policy()


@pytest.fixture(scope="session")
def as_of() -> date:
    return AS_OF


@pytest.fixture(scope="session")
def make_bank() -> Callable[..., dict[str, str]]:
    """`write_bank`, for a test that needs its own file (another data mode, altered rows)."""
    return write_bank
