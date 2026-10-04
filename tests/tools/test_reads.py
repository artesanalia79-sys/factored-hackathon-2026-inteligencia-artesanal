"""Read tools on the fixture bank: session scoping (BOLA), filters, hostile input, expiry.

Fixtures (`desk`, `make_desk`, `altered_bank`) are in conftest.py.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pytest

from bankagent.contracts.base import Contract
from bankagent.contracts.domain import DisputeCase
from bankagent.contracts.enums import (
    CardType,
    Channel,
    DisputeReason,
    DisputeStatus,
    Intent,
    Language,
    Priority,
    ProductStatus,
    Specialty,
    ToolName,
    TransactionStatus,
    TransactionType,
)
from bankagent.contracts.errors import InvalidArguments, NotFound, SessionExpired, ToolError
from bankagent.contracts.handoff import HandoffDraft, HandoffRouting
from bankagent.contracts.tools import (
    BlockCardArgs,
    CreateDisputeArgs,
    CreateHandoffArgs,
    GetDisputeArgs,
    GetTransactionArgs,
    ListCardsArgs,
    SearchTransactionsArgs,
)
from bankagent.eval.bank import load_bank
from bankagent.store.serving import ServingDB

if TYPE_CHECKING:
    from .conftest import Desk

MARIANA, ANDRES, CARLOS, VALENTINA = "CUST-FX-001", "CUST-FX-002", "CUST-FX-006", "CUST-FX-007"
EVERY_CUSTOMER = [f"CUST-FX-00{n}" for n in range(1, 9)]
BANK = load_bank()  # ground truth of who owns what, independent of the tools

MakeDesk = Callable[..., "Desk"]
AlteredBank = Callable[..., ServingDB]


def _refusal(desk: Desk, tool: ToolName, args: Contract, customer_id: str) -> tuple[object, ...]:
    """Everything a caller can observe of a refused call."""
    with pytest.raises(ToolError) as refused:
        desk.run(tool, args, customer_id)
    error = refused.value
    return type(error), error.code, error.message, str(error), error.retryable


def _dispute_args(transaction_id: str = "TXN-FX-0101") -> CreateDisputeArgs:
    return CreateDisputeArgs(
        transaction_id=transaction_id,
        reason=DisputeReason.UNRECOGNIZED,
        idempotency_key="idem-read-0001",
    )


# -- list_cards ---------------------------------------------------------------


def test_list_cards_returns_only_the_session_customers_active_cards(desk: Desk) -> None:
    cards = desk.run(ToolName.LIST_CARDS, ListCardsArgs()).cards
    assert [c.product_id for c in cards] == ["CARD-FX-011", "CARD-FX-012"]
    credit = cards[0]
    assert (credit.card_type, credit.card_last4, credit.currency) == (
        CardType.CREDIT,
        "4821",
        "MXN",
    )
    assert credit.product_status == ProductStatus.ACTIVE
    assert credit.expiration_date == date(2028, 3, 31)


def test_list_cards_hides_inactive_cards_unless_asked(desk: Desk) -> None:
    active = desk.run(ToolName.LIST_CARDS, ListCardsArgs(), CARLOS).cards
    assert [c.product_id for c in active] == ["CARD-FX-061"]
    every = desk.run(ToolName.LIST_CARDS, ListCardsArgs(include_inactive=True), CARLOS).cards
    assert [(c.product_id, c.product_status) for c in every] == [
        ("CARD-FX-061", ProductStatus.ACTIVE),
        ("CARD-FX-062", ProductStatus.BLOCKED),
    ]


def test_list_cards_shows_a_block_made_through_the_agent(desk: Desk) -> None:
    args = BlockCardArgs(
        product_id="CARD-FX-011", reason="unrecognized charge", idempotency_key="idem-read-0002"
    )
    desk.confirmed(ToolName.BLOCK_CARD, args)
    active = desk.run(ToolName.LIST_CARDS, ListCardsArgs()).cards
    assert [c.product_id for c in active] == ["CARD-FX-012"]
    every = desk.run(ToolName.LIST_CARDS, ListCardsArgs(include_inactive=True)).cards
    assert [(c.product_id, c.product_status) for c in every] == [
        ("CARD-FX-011", ProductStatus.BLOCKED),
        ("CARD-FX-012", ProductStatus.ACTIVE),
    ]
    # The block belongs to Mariana only: nobody else's cards change.
    other = desk.run(ToolName.LIST_CARDS, ListCardsArgs(), ANDRES).cards
    assert [(c.product_id, c.product_status) for c in other] == [
        ("CARD-FX-021", ProductStatus.ACTIVE)
    ]


@pytest.mark.parametrize("core_status", [ProductStatus.CLOSED, ProductStatus.SUSPENDED])
def test_a_block_does_not_relabel_a_card_the_core_already_closed_or_suspended(
    make_desk: MakeDesk, altered_bank: AlteredBank, core_status: ProductStatus
) -> None:
    bank = altered_bank(
        "UPDATE customer_cards SET product_status = ? WHERE product_id = ?",
        [core_status.value, "CARD-FX-011"],
    )
    desk = make_desk(serving_db=bank)
    args = BlockCardArgs(
        product_id="CARD-FX-011", reason="unrecognized charge", idempotency_key="idem-read-0005"
    )
    assert desk.confirmed(ToolName.BLOCK_CARD, args).verified is True
    every = desk.run(ToolName.LIST_CARDS, ListCardsArgs(include_inactive=True)).cards
    assert [(c.product_id, c.product_status) for c in every] == [
        ("CARD-FX-011", core_status),
        ("CARD-FX-012", ProductStatus.ACTIVE),
    ]


@pytest.mark.parametrize("customer_id", EVERY_CUSTOMER)
def test_reads_never_return_another_customers_cards_or_transactions(
    desk: Desk, customer_id: str
) -> None:
    cards = desk.run(ToolName.LIST_CARDS, ListCardsArgs(include_inactive=True), customer_id).cards
    owned = {card for card, owner in BANK.card_owner.items() if owner == customer_id}
    assert {c.product_id for c in cards} == owned
    found = desk.run(
        ToolName.SEARCH_TRANSACTIONS, SearchTransactionsArgs(limit=50), customer_id
    ).transactions
    mine = {t for t, facts in BANK.transactions.items() if facts.customer_id == customer_id}
    assert {t.transaction_id for t in found} == mine
    assert {t.product_id for t in found} <= owned


# -- search_transactions ------------------------------------------------------


def _search(desk: Desk, customer_id: str = MARIANA, /, **filters: object) -> list[str]:
    args = SearchTransactionsArgs.model_validate({"limit": 50, **filters})
    result = desk.run(ToolName.SEARCH_TRANSACTIONS, args, customer_id)
    return [t.transaction_id for t in result.transactions]


def test_search_is_newest_first_and_reports_truncation(desk: Desk) -> None:
    everything = desk.run(ToolName.SEARCH_TRANSACTIONS, SearchTransactionsArgs(limit=50))
    assert len(everything.transactions) == 13
    assert everything.truncated is False
    stamps = [t.transaction_ts for t in everything.transactions]
    assert stamps == sorted(stamps, reverse=True)
    exact = desk.run(ToolName.SEARCH_TRANSACTIONS, SearchTransactionsArgs(limit=13))
    assert (len(exact.transactions), exact.truncated) == (13, False)
    cut = desk.run(ToolName.SEARCH_TRANSACTIONS, SearchTransactionsArgs(limit=12))
    assert (len(cut.transactions), cut.truncated) == (12, True)
    assert cut.transactions == everything.transactions[:12]
    default = desk.run(ToolName.SEARCH_TRANSACTIONS, SearchTransactionsArgs())
    assert (len(default.transactions), default.truncated) == (10, True)


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"merchant_query": "amazon"}, ["TXN-FX-0105", "TXN-FX-0104"]),
        ({"merchant_query": "  ELECTROMUNDO "}, ["TXN-FX-0101"]),
        ({"merchant_query": "amazon mx marketplace"}, ["TXN-FX-0104"]),
        ({"amount_min": "2450", "amount_max": "2450.00"}, ["TXN-FX-0101"]),
        ({"amount_min": "1249", "amount_max": "2450", "merchant_query": "amazon"}, ["TXN-FX-0105"]),
        ({"date_from": "2026-06-12", "date_to": "2026-06-12"}, ["TXN-FX-0101"]),
        ({"date_from": "2026-06-11", "merchant_query": "amazon"}, ["TXN-FX-0105"]),
        ({"date_to": "2026-06-10", "merchant_query": "amazon"}, ["TXN-FX-0104"]),
        ({"card_last4": "4821", "merchant_query": "electro"}, ["TXN-FX-0101"]),
        ({"card_last4": "0937", "merchant_query": "electro"}, []),
        ({"currency": "USD"}, []),
        ({"merchant_query": "no such merchant"}, []),
    ],
)
def test_search_filters(desk: Desk, filters: dict[str, object], expected: list[str]) -> None:
    assert _search(desk, **filters) == expected


def test_search_filters_by_currency_and_ignores_accents(desk: Desk) -> None:
    lucia = "CUST-FX-003"
    assert _search(desk, lucia, currency="USD") == ["TXN-FX-0302"]
    assert "TXN-FX-0302" not in _search(desk, lucia, currency="ARS")
    # The customer types without the accent the merchant name has, or the other way round.
    assert _search(desk, lucia, merchant_query="hôtel copacabana") == ["TXN-FX-0302"]


def test_search_matches_a_card_by_last4_only_within_the_session(desk: Desk) -> None:
    assert len(_search(desk, card_last4="0937")) == 5
    assert _search(desk, card_last4="7310") == []  # Andrés's card
    assert _search(desk, merchant_query="RAPPI") == []  # Andrés's merchant
    assert _search(desk, ANDRES, merchant_query="rappi") == ["TXN-FX-0202", "TXN-FX-0201"]


@pytest.mark.parametrize(
    ("customer_id", "written", "expected"),
    [
        ("CUST-FX-003", "Mercado Libre", ["TXN-FX-0301"]),  # MERCADOLIBRE*TIENDA
        ("CUST-FX-003", "mercado-libre tienda", ["TXN-FX-0301"]),
        ("CUST-FX-005", "PAYPAL SPOTIFYMX", ["TXN-FX-0501"]),  # PAYPAL *SPOTIFYMX
        ("CUST-FX-005", "Spotify MX", ["TXN-FX-0501"]),
        (MARIANA, "Electro Mundo", ["TXN-FX-0101"]),  # ELECTROMUNDO ONLINE
        ("CUST-FX-003", "Mércado Libre", ["TXN-FX-0301"]),  # an accent inside a word, too
        (ANDRES, "Rappi restaurante", ["TXN-FX-0202", "TXN-FX-0201"]),  # RAPPI*RESTAURANTE
        (MARIANA, "Mercado Libre", []),  # Lucía's merchant
        (MARIANA, "Electro Mundo Tienda", []),  # more than the name says
    ],
)
def test_search_finds_a_merchant_written_with_other_spaces_or_punctuation(
    desk: Desk, customer_id: str, written: str, expected: list[str]
) -> None:
    # A real model returns the merchant as the customer wrote it. Compared as plain text it
    # found nothing, and the agent asked for "un dato más" until it gave up.
    assert _search(desk, customer_id, merchant_query=written) == expected


def test_search_drops_the_accents_of_the_stored_name_too(
    make_desk: MakeDesk, altered_bank: AlteredBank
) -> None:
    # 7 of the 24 merchant names in the curated serving DB carry an accented letter; this one
    # also differs in punctuation, so only the compacted comparison can find it.
    bank = altered_bank(
        "UPDATE transactions_enriched SET merchant_name = ? WHERE transaction_id = ?",
        ["PANADERÍA*LA ESPIGA", "TXN-FX-0201"],
    )
    desk = make_desk(serving_db=bank)
    for written in ("Panadería La Espiga", "panaderia la espiga"):
        assert _search(desk, ANDRES, merchant_query=written) == ["TXN-FX-0201"]


def test_a_query_of_one_or_two_letters_still_matches_as_plain_text(desk: Desk) -> None:
    # Too short for the compacted comparison, it keeps the plain-text match it always had.
    assert _search(desk, merchant_query="mx") == ["TXN-FX-0105", "TXN-FX-0104"]


def test_blank_merchant_query_is_no_filter_and_wildcards_are_plain_text(desk: Desk) -> None:
    assert _search(desk, merchant_query="   ") == _search(desk)
    # "a%z" and "a-z" leave two letters once the punctuation is dropped ("az", as in AMAZON):
    # too little to be a merchant, so they match only as the plain text they are.
    for wildcard in ("%", "_", "%%", "a%z", "a-z", "*"):
        assert _search(desk, merchant_query=wildcard) == []
    assert _search(desk, merchant_query="a.m.a") == ["TXN-FX-0105", "TXN-FX-0104"]


@pytest.mark.parametrize(
    "hostile",
    [
        "x' OR '1'='1",
        "'; DROP TABLE transactions_enriched; --",
        '" OR ""="',
        "$customer_id",
        "?",
        "\x00",
        "CUST-FX-002",
    ],
)
def test_values_are_parameters_never_sql(desk: Desk, fixture_bank: Path, hostile: str) -> None:
    assert _search(desk, merchant_query=hostile) == []
    with pytest.raises(NotFound):
        desk.run(ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id=hostile))
    with pytest.raises(NotFound):
        desk.run(ToolName.GET_DISPUTE, GetDisputeArgs(dispute_id=hostile))
    with duckdb.connect(str(fixture_bank), read_only=True) as con:
        row = con.execute("SELECT count(*) FROM transactions_enriched").fetchone()
    assert row is not None
    assert row[0] == len(BANK.transactions)


# -- get_transaction ----------------------------------------------------------


def test_get_transaction_returns_the_bank_facts_without_risk_signals(desk: Desk) -> None:
    result = desk.run(ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0101"))
    txn = result.transaction
    assert (txn.transaction_id, txn.product_id, txn.card_last4) == (
        "TXN-FX-0101",
        "CARD-FX-011",
        "4821",
    )
    assert txn.transaction_ts == datetime(2026, 6, 12, 21, 14, 5, tzinfo=UTC)
    assert (txn.amount, txn.currency, txn.amount_usd) == (
        Decimal("2450.00"),
        "MXN",
        Decimal("140.00"),
    )
    assert (txn.transaction_type, txn.channel, txn.transaction_status) == (
        TransactionType.PURCHASE,
        Channel.WEB,
        TransactionStatus.APPROVED,
    )
    assert (txn.merchant_name, txn.transaction_country, txn.is_foreign) == (
        "ELECTROMUNDO ONLINE",
        "MX",
        False,
    )
    assert result.open_dispute_id is None
    dumped = result.model_dump_json()
    assert "fraud" not in dumped
    assert "dq_" not in dumped
    assert MARIANA not in dumped


def test_another_customers_transaction_is_indistinguishable_from_a_missing_one(desk: Desk) -> None:
    foreign = _refusal(
        desk, ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0201"), MARIANA
    )
    missing = _refusal(
        desk, ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-9999"), MARIANA
    )
    assert foreign == missing
    assert foreign[0] is NotFound
    assert "TXN-FX-0201" not in str(foreign)
    # The id does exist: its owner reads it.
    owner = desk.run(
        ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0201"), ANDRES
    )
    assert owner.transaction.transaction_id == "TXN-FX-0201"


def test_get_transaction_reports_an_open_prior_complaint(desk: Desk) -> None:
    args = GetTransactionArgs(transaction_id="TXN-FX-0701")
    assert desk.run(ToolName.GET_TRANSACTION, args, VALENTINA).open_dispute_id == "CMP-FX-701"
    other = GetTransactionArgs(transaction_id="TXN-FX-0702")
    assert desk.run(ToolName.GET_TRANSACTION, other, VALENTINA).open_dispute_id is None


@pytest.mark.parametrize(
    ("status", "is_open"),
    [
        ("Open", True),
        ("In Process", True),
        ("Escalated", True),
        ("Resolved", False),
        ("Closed", False),
        ("Rejected", False),
    ],
)
def test_only_open_statuses_of_the_history_count_as_an_open_dispute(
    make_desk: MakeDesk, altered_bank: AlteredBank, status: str, is_open: bool
) -> None:
    bank = altered_bank(
        "UPDATE dispute_history SET status = ? WHERE complaint_id = ?", [status, "CMP-FX-701"]
    )
    desk = make_desk(serving_db=bank)
    result = desk.run(
        ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0701"), VALENTINA
    )
    assert result.open_dispute_id == ("CMP-FX-701" if is_open else None)


def test_the_newest_open_complaint_is_the_one_reported(
    make_desk: MakeDesk, altered_bank: AlteredBank
) -> None:
    bank = altered_bank(
        "INSERT INTO dispute_history VALUES (?, ?, ?, 'Claim', 'Transactions', NULL, 'Open', "
        "NULL, NULL, ?, NULL)",
        ["CMP-FX-799", VALENTINA, datetime(2026, 6, 1, 9, 0), "TXN-FX-0701"],
    )
    desk = make_desk(serving_db=bank)
    result = desk.run(
        ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0701"), VALENTINA
    )
    assert result.open_dispute_id == "CMP-FX-799"  # opened after CMP-FX-701


def test_a_complaint_filed_by_someone_else_is_not_the_customers_open_dispute(
    make_desk: MakeDesk, altered_bank: AlteredBank
) -> None:
    bank = altered_bank(
        "UPDATE dispute_history SET customer_id = ? WHERE complaint_id = ?",
        [MARIANA, "CMP-FX-701"],
    )
    desk = make_desk(serving_db=bank)
    result = desk.run(
        ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0701"), VALENTINA
    )
    assert result.open_dispute_id is None


def test_a_dispute_made_through_the_agent_is_the_open_dispute(desk: Desk) -> None:
    created = desk.confirmed(ToolName.CREATE_DISPUTE, _dispute_args())
    result = desk.run(ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0101"))
    assert result.open_dispute_id == created.dispute.dispute_id
    # It takes precedence over the history when both exist. `create_dispute` itself now
    # refuses a transaction with an open prior complaint (tests/tools/test_writes.py), so this
    # state is simulated directly in the store rather than through the tool.
    transaction = desk.serving.transaction(VALENTINA, "TXN-FX-0701")
    assert transaction is not None
    mine = DisputeCase(
        dispute_id="DSP-PRE-0701",
        transaction_id="TXN-FX-0701",
        reason=DisputeReason.UNRECOGNIZED,
        status=DisputeStatus.SUBMITTED,
        created_at=desk.now,
        amount=transaction.amount,
        currency=transaction.currency,
        idempotency_key="idem-pre-0701",
        policy_version="test-policy-v1",
    )
    desk.store.insert_dispute(VALENTINA, mine)
    again = desk.run(
        ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0701"), VALENTINA
    )
    assert again.open_dispute_id == mine.dispute_id


def test_a_transaction_is_never_labelled_with_another_customers_card(
    make_desk: MakeDesk, altered_bank: AlteredBank
) -> None:
    # Defence in depth for a serving DB that breaks "a transaction is on its owner's card".
    bank = altered_bank(
        "UPDATE transactions_enriched SET product_id = ? WHERE transaction_id = ?",
        ["CARD-FX-021", "TXN-FX-0101"],  # Andrés's card on Mariana's transaction
    )
    desk = make_desk(serving_db=bank)
    result = desk.run(ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0101"))
    assert result.transaction.card_last4 is None
    assert _search(desk, card_last4="7310") == []


# -- get_dispute --------------------------------------------------------------


def test_get_dispute_by_id_and_by_transaction(desk: Desk) -> None:
    created = desk.confirmed(ToolName.CREATE_DISPUTE, _dispute_args()).dispute
    by_id = desk.run(ToolName.GET_DISPUTE, GetDisputeArgs(dispute_id=created.dispute_id))
    by_txn = desk.run(ToolName.GET_DISPUTE, GetDisputeArgs(transaction_id="TXN-FX-0101"))
    assert by_id.dispute == by_txn.dispute == created


def test_another_customers_dispute_is_indistinguishable_from_a_missing_one(desk: Desk) -> None:
    created = desk.confirmed(ToolName.CREATE_DISPUTE, _dispute_args()).dispute
    for foreign_args, missing_args in (
        (GetDisputeArgs(dispute_id=created.dispute_id), GetDisputeArgs(dispute_id="DSP-9999")),
        (GetDisputeArgs(transaction_id="TXN-FX-0101"), GetDisputeArgs(transaction_id="TXN-FX-9")),
    ):
        foreign = _refusal(desk, ToolName.GET_DISPUTE, foreign_args, ANDRES)
        missing = _refusal(desk, ToolName.GET_DISPUTE, missing_args, ANDRES)
        assert foreign == missing
        assert foreign[0] is NotFound
        assert created.dispute_id not in str(foreign)
    # A prior complaint is not a DisputeCase: it is reported by get_transaction only.
    with pytest.raises(NotFound):
        desk.run(ToolName.GET_DISPUTE, GetDisputeArgs(transaction_id="TXN-FX-0701"), VALENTINA)


# -- every tool: session and argument checks -----------------------------------

_HANDOFF = CreateHandoffArgs(
    draft=HandoffDraft(
        trace_id="trace-1",
        language=Language.ES,
        request="cliente pide hablar con una persona",
        intent=Intent.HUMAN_REQUEST,
        policy_version="test-policy-v1",
        routing=HandoffRouting(
            specialty=Specialty.DISPUTES, language=Language.ES, priority=Priority.MEDIUM
        ),
    ),
    idempotency_key="idem-read-0003",
)
VALID_ARGS: dict[ToolName, Contract] = {
    ToolName.LIST_CARDS: ListCardsArgs(),
    ToolName.SEARCH_TRANSACTIONS: SearchTransactionsArgs(),
    ToolName.GET_TRANSACTION: GetTransactionArgs(transaction_id="TXN-FX-0101"),
    ToolName.GET_DISPUTE: GetDisputeArgs(transaction_id="TXN-FX-0101"),
    ToolName.CREATE_DISPUTE: _dispute_args(),
    ToolName.BLOCK_CARD: BlockCardArgs(
        product_id="CARD-FX-011", reason="unrecognized charge", idempotency_key="idem-read-0004"
    ),
    ToolName.CREATE_HANDOFF: _HANDOFF,
}


def test_valid_args_cover_every_tool() -> None:
    assert set(VALID_ARGS) == set(ToolName)


@pytest.mark.parametrize("tool", list(ToolName))
@pytest.mark.parametrize("moment", ["at-expiry", "long-after", "before-issue"])
def test_an_inactive_session_is_refused_by_every_tool(
    desk: Desk, tool: ToolName, moment: str
) -> None:
    args = VALID_ARGS[tool]
    token = desk.confirm(tool, args) if desk.tools[tool].spec.requires_confirmation else None
    session = desk.session()
    desk.now = {
        "at-expiry": session.expires_at,
        "long-after": session.expires_at + timedelta(hours=3),
        "before-issue": session.issued_at - timedelta(seconds=1),
    }[moment]
    with pytest.raises(SessionExpired):
        desk.run(tool, args, token=token)
    for table in ("disputes", "card_blocks", "handoffs"):
        assert desk.store.count(table) == 0
    if token is not None:
        assert desk.token_is_spent(token) is False
    # One second before expiry the same call is served.
    desk.now = desk.session().expires_at - timedelta(seconds=1)
    if tool == ToolName.GET_DISPUTE:
        with pytest.raises(NotFound):  # served: there is simply no dispute yet
            desk.run(tool, args)
    else:
        fresh = desk.confirm(tool, args) if token is not None else None
        desk.run(tool, args, token=fresh)


@pytest.mark.parametrize("tool", list(ToolName))
def test_arguments_of_another_tool_are_refused(desk: Desk, tool: ToolName) -> None:
    wrong = VALID_ARGS[
        ToolName.LIST_CARDS if tool != ToolName.LIST_CARDS else ToolName.GET_TRANSACTION
    ]
    with pytest.raises(InvalidArguments):
        desk.run(tool, wrong)
    for table in ("disputes", "card_blocks", "handoffs"):
        assert desk.store.count(table) == 0
