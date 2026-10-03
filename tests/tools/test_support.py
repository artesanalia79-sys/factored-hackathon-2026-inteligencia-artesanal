"""Token issuing, execution records, wiring and failure handling of the tools layer.

Fixtures (`desk`, `make_desk`, `altered_bank`) are in conftest.py.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from bankagent.contracts.base import Contract, args_hash
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import (
    ActionType,
    ConversationState,
    DisputeReason,
    Intent,
    Language,
    Priority,
    Specialty,
    StepKind,
    StepOutcome,
    ToolErrorCode,
    ToolName,
)
from bankagent.contracts.errors import (
    ERRORS_BY_CODE,
    InvalidArguments,
    SessionExpired,
    ToolUnavailable,
)
from bankagent.contracts.handoff import HandoffDraft, HandoffRouting
from bankagent.contracts.tools import (
    TOOL_SPECS,
    BlockCardArgs,
    CreateDisputeArgs,
    CreateHandoffArgs,
    GetDisputeArgs,
    GetTransactionArgs,
    ListCardsArgs,
    SearchTransactionsArgs,
    ToolContext,
    ToolSpec,
)
from bankagent.eval.bank import load_bank
from bankagent.eval.system import ToolObserver
from bankagent.eval.tools import instrument_tools
from bankagent.eval.tools import outcome_for as harness_outcome_for
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDataError, ServingDB
from bankagent.tools import DEFAULT_TOKEN_TTL, build_tools, call_tool, issue_confirmation_token
from bankagent.tools.base import ToolFailure
from bankagent.tools.records import outcome_for, record_id_for

if TYPE_CHECKING:
    from .conftest import Desk

MARIANA, ANDRES = "CUST-FX-001", "CUST-FX-002"
START = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
SRC = Path(__file__).resolve().parents[2] / "src" / "bankagent"
DISPUTE = CreateDisputeArgs(
    transaction_id="TXN-FX-0101",
    reason=DisputeReason.UNRECOGNIZED,
    idempotency_key="idem-support-0001",
)
BLOCK = BlockCardArgs(
    product_id="CARD-FX-011", reason="cargo extraño", idempotency_key="idem-support-0002"
)
HANDOFF = CreateHandoffArgs(
    draft=HandoffDraft(
        trace_id="trace-1",
        language=Language.ES,
        request="cliente pide hablar con una persona",
        intent=Intent.HUMAN_REQUEST,
        policy_version="test-policy-v1",
        routing=HandoffRouting(
            specialty=Specialty.GENERAL, language=Language.ES, priority=Priority.LOW
        ),
    ),
    idempotency_key="idem-support-0003",
)

MakeDesk = Callable[..., "Desk"]
AlteredBank = Callable[..., ServingDB]


# -- issue_confirmation_token -----------------------------------------------------


def test_an_issued_token_is_stored_and_bound_to_the_exact_call(desk: Desk) -> None:
    session = desk.session()
    token = issue_confirmation_token(
        desk.store, session=session, tool=ToolName.CREATE_DISPUTE, args=DISPUTE, now=desk.now
    )
    assert (token.session_id, token.action, token.args_hash) == (
        session.session_id,
        ActionType.CREATE_DISPUTE,
        args_hash(DISPUTE),
    )
    assert (token.issued_at, token.expires_at, token.used_at) == (
        desk.now,
        desk.now + DEFAULT_TOKEN_TTL,
        None,
    )
    assert desk.store.get_confirmation_token(token.token_id, session.session_id) == token
    assert timedelta(minutes=5) == DEFAULT_TOKEN_TTL


def test_a_token_is_honoured_only_in_the_store_the_tools_use(
    tmp_path: Path, make_desk: MakeDesk
) -> None:
    # The orchestrator and the tools may hold different OpsStore objects (the harness injects
    # the tools): on the same file the token works, on another store it does not exist.
    path = tmp_path / "shared.sqlite"
    with OpsStore(path) as tools_store, OpsStore(path) as orchestrator_store:
        desk = make_desk(ops=tools_store)
        token = issue_confirmation_token(
            orchestrator_store,
            session=desk.session(),
            tool=ToolName.CREATE_DISPUTE,
            args=DISPUTE,
            now=desk.now,
        )
        result = desk.run(ToolName.CREATE_DISPUTE, DISPUTE, token=token.token_id)
        assert (result.created, result.verified) == (True, True)
        with OpsStore(tmp_path / "elsewhere.sqlite") as elsewhere:
            stray = issue_confirmation_token(
                elsewhere,
                session=desk.session(),
                tool=ToolName.BLOCK_CARD,
                args=BLOCK,
                now=desk.now,
            )
        with pytest.raises(ERRORS_BY_CODE[ToolErrorCode.CONFIRMATION_REQUIRED]):
            desk.run(ToolName.BLOCK_CARD, BLOCK, token=stray.token_id)
        assert tools_store.count("card_blocks") == 0


def test_token_ids_are_long_random_and_never_repeat(desk: Desk) -> None:
    ids = {desk.confirm(ToolName.BLOCK_CARD, BLOCK) for _ in range(50)}
    assert len(ids) == 50
    assert all(len(token_id) == 32 for token_id in ids)  # 24 random bytes, url-safe


def test_a_token_never_outlives_its_session(desk: Desk) -> None:
    session = desk.session()
    desk.now = session.expires_at - timedelta(minutes=1)
    token = issue_confirmation_token(
        desk.store, session=session, tool=ToolName.BLOCK_CARD, args=BLOCK, now=desk.now
    )
    assert token.expires_at == session.expires_at


def test_issuing_is_refused_for_an_inactive_session(desk: Desk) -> None:
    session = desk.session()
    for now in (session.expires_at, session.issued_at - timedelta(seconds=1)):
        with pytest.raises(SessionExpired):
            issue_confirmation_token(
                desk.store, session=session, tool=ToolName.BLOCK_CARD, args=BLOCK, now=now
            )
    assert desk.store.count("confirmation_tokens") == 0


@pytest.mark.parametrize(
    ("tool", "args", "ttl"),
    [
        (ToolName.LIST_CARDS, ListCardsArgs(), DEFAULT_TOKEN_TTL),  # a read
        (ToolName.CREATE_HANDOFF, HANDOFF, DEFAULT_TOKEN_TTL),  # a write without confirmation
        (ToolName.CREATE_DISPUTE, BLOCK, DEFAULT_TOKEN_TTL),  # arguments of another tool
        (ToolName.BLOCK_CARD, BLOCK, timedelta(0)),
        (ToolName.BLOCK_CARD, BLOCK, timedelta(seconds=-5)),
    ],
)
def test_issuing_is_refused_for_calls_that_cannot_be_confirmed(
    desk: Desk, tool: ToolName, args: Contract, ttl: timedelta
) -> None:
    with pytest.raises(InvalidArguments):
        issue_confirmation_token(
            desk.store, session=desk.session(), tool=tool, args=args, now=desk.now, ttl=ttl
        )
    assert desk.store.count("confirmation_tokens") == 0


# -- call_tool and its ExecutionRecord ----------------------------------------------


class Timer:
    """Advances by a fixed number of seconds on every reading."""

    def __init__(self, step_s: float) -> None:
        self._now = 100.0
        self._step = step_s

    def __call__(self) -> float:
        self._now += self._step
        return self._now


def _call(desk: Desk, tool: ToolName, args: Contract, **ctx: Any) -> Any:
    return call_tool(
        desk.tools[tool],
        desk.ctx(**ctx),
        args,
        turn_index=2,
        step_index=3,
        state=ConversationState.IDENTIFY_TXN,
        timer=Timer(0.25),
    )


def test_a_read_call_is_recorded_with_its_hash_latency_and_position(desk: Desk) -> None:
    args = GetTransactionArgs(transaction_id="TXN-FX-0101")
    call = _call(desk, ToolName.GET_TRANSACTION, args)
    assert call.error is None
    assert call.unwrap().transaction.transaction_id == "TXN-FX-0101"
    record = call.record
    assert (record.record_id, record.trace_id, record.session_id) == (
        "trace-1-t2-s3",
        "trace-1",
        desk.session().session_id,
    )
    assert (record.turn_index, record.step_index, record.step, record.state) == (
        2,
        3,
        StepKind.TOOL_CALL,
        ConversationState.IDENTIFY_TXN,
    )
    assert (record.tool, record.args_hash) == (ToolName.GET_TRANSACTION, args_hash(args))
    assert (record.outcome, record.error_code, record.verified) == (
        StepOutcome.SUCCESS,
        None,
        True,  # a successful, session-scoped read of the requested id (docs/rules/backend.md)
    )
    assert record.latency_ms == pytest.approx(250.0)
    assert record.created_at == desk.now


def test_every_successful_read_is_recorded_as_verified_for_the_templates(desk: Desk) -> None:
    for tool, args in (
        (ToolName.LIST_CARDS, ListCardsArgs()),
        (ToolName.SEARCH_TRANSACTIONS, SearchTransactionsArgs()),
        (ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0105")),
    ):
        record = _call(desk, tool, args).record
        assert (record.tool, record.args_hash, record.verified) == (tool, args_hash(args), True)


def test_a_read_that_returns_something_else_is_not_verified(desk: Desk) -> None:
    class Confused:
        """Answers with another transaction, or with the result of another tool."""

        def __init__(self, answer: Contract) -> None:
            self._answer = answer

        @property
        def spec(self) -> ToolSpec:
            return TOOL_SPECS[ToolName.GET_TRANSACTION]

        def run(self, ctx: ToolContext, args: GetTransactionArgs, /) -> Any:
            return self._answer

    asked = GetTransactionArgs(transaction_id="TXN-FX-0101")
    other = desk.run(ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0104"))
    cards = desk.run(ToolName.LIST_CARDS, ListCardsArgs())
    for answer in (other, cards):
        call = call_tool(
            Confused(answer),
            desk.ctx(),
            asked,
            turn_index=0,
            step_index=0,
            state=ConversationState.IDENTIFY_TXN,
        )
        assert (call.record.outcome, call.record.verified) == (StepOutcome.SUCCESS, False)


def test_a_long_trace_id_still_gets_a_valid_record_id(desk: Desk) -> None:
    # Regression: "<trace>-t<turn>-s<step>" of a 64-character trace id exceeded Identifier.
    long_trace = "t" * 64
    call = _call(desk, ToolName.LIST_CARDS, ListCardsArgs(), trace_id=long_trace)
    again = _call(desk, ToolName.LIST_CARDS, ListCardsArgs(), trace_id=long_trace)
    assert call.record.trace_id == long_trace
    assert re.fullmatch(r"rec-[0-9a-f]{32}", call.record.record_id)
    assert call.record.record_id == again.record.record_id  # deterministic
    assert record_id_for(long_trace, 2, 4) != call.record.record_id
    assert record_id_for("t" * 58, 2, 3) == "t" * 58 + "-t2-s3"  # exactly 64: kept readable


def test_a_record_carries_no_argument_result_or_identity_values(desk: Desk) -> None:
    token = desk.confirm(ToolName.CREATE_DISPUTE, DISPUTE)
    call = _call(desk, ToolName.CREATE_DISPUTE, DISPUTE, token=token)
    dumped = call.record.model_dump_json()
    for secret in (MARIANA, "TXN-FX-0101", "2450", token, "idem-support-0001", "DSP-0001"):
        assert secret not in dumped


def test_a_verified_write_is_recorded_as_verified(desk: Desk) -> None:
    call = _call(
        desk, ToolName.CREATE_DISPUTE, DISPUTE, token=desk.confirm(ToolName.CREATE_DISPUTE, DISPUTE)
    )
    assert (call.record.outcome, call.record.verified) == (StepOutcome.SUCCESS, True)
    assert call.unwrap().verified is True


def test_an_unverified_write_is_not_recorded_as_verified(
    tmp_path: Path, make_desk: MakeDesk
) -> None:
    class Forgetful(OpsStore):
        def get_card_block(self, customer_id: str, product_id: str) -> None:
            return None

    with Forgetful(tmp_path / "forgetful.sqlite") as store:
        desk = make_desk(ops=store)
        call = _call(
            desk, ToolName.BLOCK_CARD, BLOCK, token=desk.confirm(ToolName.BLOCK_CARD, BLOCK)
        )
        assert call.unwrap().created is True
        assert (call.record.outcome, call.record.verified) == (StepOutcome.SUCCESS, False)


def test_refusals_are_returned_with_a_record_not_raised(desk: Desk) -> None:
    foreign = _call(
        desk, ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0201")
    )
    assert (foreign.record.outcome, foreign.record.error_code) == (
        StepOutcome.BLOCKED,
        ToolErrorCode.NOT_FOUND,
    )
    unconfirmed = _call(desk, ToolName.CREATE_DISPUTE, DISPUTE)
    assert (unconfirmed.record.outcome, unconfirmed.record.error_code) == (
        StepOutcome.BLOCKED,
        ToolErrorCode.CONFIRMATION_REQUIRED,
    )
    desk.now += timedelta(hours=1)
    expired = _call(desk, ToolName.LIST_CARDS, ListCardsArgs())
    assert (expired.record.outcome, expired.record.error_code) == (
        StepOutcome.FAILURE,
        ToolErrorCode.SESSION_EXPIRED,
    )
    for call in (foreign, unconfirmed, expired):
        assert call.result is None
        assert call.record.verified is False
        with pytest.raises(type(call.error)):
            call.unwrap()


def test_a_bug_in_a_tool_is_not_turned_into_a_record(desk: Desk) -> None:
    class Broken:
        @property
        def spec(self) -> ToolSpec:
            return TOOL_SPECS[ToolName.LIST_CARDS]

        def run(self, ctx: ToolContext, args: ListCardsArgs, /) -> Any:
            raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        call_tool(
            Broken(),
            desk.ctx(),
            ListCardsArgs(),
            turn_index=0,
            step_index=0,
            state=ConversationState.ACT,
        )


@pytest.mark.parametrize("code", list(ToolErrorCode))
def test_outcomes_match_what_the_evaluation_harness_observes(code: ToolErrorCode) -> None:
    assert outcome_for(code) == harness_outcome_for(code)
    assert code in ERRORS_BY_CODE


# -- wiring and structural guards ---------------------------------------------------


def test_build_tools_provides_every_tool_of_the_registry(desk: Desk) -> None:
    assert set(desk.tools) == set(TOOL_SPECS) == set(ToolName)
    for name, tool in desk.tools.items():
        assert tool.spec is TOOL_SPECS[name]


def test_build_tools_refuses_to_start_when_the_registry_and_the_tools_differ(
    serving: ServingDB, store: OpsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A tool added to (or dropped from) the contract without its implementation must fail at
    # start-up, not at the first call.
    monkeypatch.delitem(TOOL_SPECS, ToolName.GET_DISPUTE)
    with pytest.raises(RuntimeError, match="exactly the tools of TOOL_SPECS"):
        build_tools(serving, store)


def test_the_default_wiring_requires_a_policy_decision(serving: ServingDB, store: OpsStore) -> None:
    session = Session(
        session_id="ses-wiring",
        customer_id=MARIANA,
        issued_at=START,
        expires_at=START + timedelta(minutes=15),
    )
    token = issue_confirmation_token(
        store, session=session, tool=ToolName.BLOCK_CARD, args=BLOCK, now=START
    )
    ctx = ToolContext(
        session=session, trace_id="trace-1", now=START, confirmation_token_id=token.token_id
    )
    with pytest.raises(InvalidArguments):
        build_tools(serving, store)[ToolName.BLOCK_CARD].run(ctx, BLOCK)
    assert store.count("card_blocks") == 0
    # The baseline wiring accepts the same call; ids of the default factory are random.
    baseline = build_tools(serving, store, require_policy=False)
    block = baseline[ToolName.BLOCK_CARD].run(ctx, BLOCK).block
    assert re.fullmatch(r"BLK-[0-9A-F]{16}", block.block_id)


def test_the_tools_serve_a_session_that_never_went_through_login(desk: Desk) -> None:
    # The evaluation harness, and the curated end-to-end run (T19, where the mock OTP is
    # refused by design), open the `Session` directly. A tool must not require the session to
    # be stored: identity is whatever the server-side context carries.
    assert desk.store.get_session(desk.session().session_id) is None
    created = desk.confirmed(ToolName.CREATE_DISPUTE, DISPUTE)
    assert (created.created, created.verified) == (True, True)
    assert desk.run(ToolName.LIST_CARDS, ListCardsArgs()).cards
    assert desk.store.count("sessions") == 0


def _sources(*packages: str) -> list[Path]:
    return [path for package in packages for path in (SRC / package).rglob("*.py")]


def test_the_tools_hold_no_sql_and_open_no_database() -> None:
    forbidden = re.compile(
        r"\b(SELECT|INSERT|UPDATE|DELETE)\b|\.execute\(|duckdb\.connect|sqlite3\.connect"
    )
    offenders = [p.name for p in _sources("tools") if forbidden.search(p.read_text("utf-8"))]
    assert offenders == []


def test_no_runtime_statement_reads_fraud_labels_or_risk_columns() -> None:
    for path in _sources("tools", "store"):
        text = path.read_text("utf-8")
        assert "is_fraud" not in text, path.name
    serving = (SRC / "store" / "serving.py").read_text("utf-8")
    # Policy-only statements (T9, ADR 0003) are the sole, named exception to "no fraud_score or
    # dq_flags": the tools never read them, and every other statement in this file still must not.
    policy_only = {"_RISK_SQL", "_LAST_DISPUTE_SQL"}
    statements = dict(re.findall(r'(\w+)_SQL = """(.*?)"""', serving, re.S))
    assert {f"{name}_SQL" for name in statements} - policy_only == {
        "_CARDS_SQL",
        "_TRANSACTIONS_SQL",
        "_OPEN_COMPLAINT_SQL",
    }
    for name, statement in statements.items():
        assert "*" not in statement  # columns are always listed, never SELECT *
        assert "{" not in statement  # nothing is ever formatted into SQL
        if f"{name}_SQL" not in policy_only:
            assert "fraud" not in statement
            assert "dq_flags" not in statement
        else:
            assert "WHERE customer_id = ?" in statement  # scoped like every tool read


def test_the_tools_never_print_or_log() -> None:
    offenders = [
        p.name
        for p in _sources("tools")
        if re.search(r"\bprint\(|\blogging\b|\blogger\b", p.read_text("utf-8"))
    ]
    assert offenders == []


# -- infrastructure failures --------------------------------------------------------


def _chain(error: BaseException) -> str:
    """Everything a traceback of ``error`` would print: the error and all it is chained to."""
    seen: list[BaseException] = []
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or current in seen:
            continue
        seen.append(current)
        pending += [current.__cause__, current.__context__]
    return " | ".join(f"{type(e).__name__}: {e}" for e in seen)


def test_a_closed_ops_store_is_unavailable_not_a_crash(desk: Desk) -> None:
    token = desk.confirm(ToolName.BLOCK_CARD, BLOCK)
    desk.store.close()
    for tool, args, ctx in (
        (ToolName.LIST_CARDS, ListCardsArgs(), {}),
        (ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0101"), {}),
        (ToolName.BLOCK_CARD, BLOCK, {"token": token}),
    ):
        with pytest.raises(ToolUnavailable) as refused:
            desk.run(tool, args, **ctx)
        assert refused.value.retryable is True
        assert refused.value.message == f"{tool.value} is temporarily unavailable"
        # The cause says what kind of failure it was, and is not the database's own error.
        assert isinstance(refused.value.__cause__, ToolFailure)
        assert "sqlite3.ProgrammingError" in _chain(refused.value)
        assert refused.value.__context__ is None


def test_an_unreadable_serving_db_is_unavailable(
    tmp_path: Path, fixture_bank: Path, make_desk: MakeDesk
) -> None:
    broken = tmp_path / "broken.duckdb"
    broken.write_bytes(fixture_bank.read_bytes()[:4096])
    desk = make_desk(serving_db=ServingDB(broken))
    for tool, args in (
        (ToolName.LIST_CARDS, ListCardsArgs()),
        (ToolName.SEARCH_TRANSACTIONS, SearchTransactionsArgs()),
        (ToolName.CREATE_DISPUTE, DISPUTE),
    ):
        with pytest.raises(ToolUnavailable) as refused:
            desk.run(tool, args)
        assert str(tmp_path) not in _chain(refused.value)
    assert desk.store.count("disputes") == 0


@pytest.mark.parametrize(
    ("statement", "tool", "args", "field"),
    [
        (
            "UPDATE transactions_enriched SET transaction_status = 'Chargeback' "
            "WHERE transaction_id = 'TXN-FX-0101'",
            ToolName.GET_TRANSACTION,
            GetTransactionArgs(transaction_id="TXN-FX-0101"),
            "transaction_status",
        ),
        (
            "UPDATE transactions_enriched SET currency = 'pesos' "
            "WHERE transaction_id = 'TXN-FX-0101'",
            ToolName.SEARCH_TRANSACTIONS,
            SearchTransactionsArgs(limit=50),
            "currency",
        ),
        (
            "UPDATE customer_cards SET card_last4 = '48215' WHERE product_id = 'CARD-FX-011'",
            ToolName.LIST_CARDS,
            ListCardsArgs(),
            "card_last4",
        ),
    ],
)
def test_a_served_row_outside_its_contract_is_unavailable_without_leaking_it(
    make_desk: MakeDesk,
    altered_bank: AlteredBank,
    statement: str,
    tool: ToolName,
    args: Contract,
    field: str,
) -> None:
    desk = make_desk(serving_db=altered_bank(statement))
    with pytest.raises(ToolUnavailable) as refused:
        desk.run(tool, args)
    assert refused.value.message == f"{tool.value} is temporarily unavailable"
    # Regression: the validation error quotes the whole row; nothing of it may be chained.
    chain = _chain(refused.value)
    for value in ("Chargeback", "pesos", "48215", "ELECTROMUNDO", "2450", "TXN-FX", MARIANA):
        assert value not in chain
    assert field in chain  # the cause still names what to fix


def test_the_serving_db_itself_never_chains_a_row_to_its_error(altered_bank: AlteredBank) -> None:
    # The policy engine and auth read the serving DB without the tools in between.
    bank = altered_bank(
        "UPDATE transactions_enriched SET transaction_status = ? WHERE transaction_id = ?",
        ["Chargeback", "TXN-FX-0101"],
    )
    with pytest.raises(ServingDataError) as transaction:
        bank.transaction(MARIANA, "TXN-FX-0101")
    cards = altered_bank(
        "UPDATE customer_cards SET card_last4 = ? WHERE product_id = ?", ["48215", "CARD-FX-011"]
    )
    with pytest.raises(ServingDataError) as card:
        cards.cards(MARIANA)
    assert str(transaction.value) == (
        "a transactions_enriched row does not fit TransactionView (transaction_status)"
    )
    assert str(card.value) == "a customer_cards row does not fit CardView (card_last4)"
    for error in (transaction.value, card.value):
        assert error.__cause__ is None
        assert error.__context__ is None


def test_a_stored_record_that_no_longer_fits_its_contract_is_unavailable(desk: Desk) -> None:
    # Regression: a payload written under an older contract must not crash the turn, and the
    # validation error (which quotes the payload) must not be chained to the tool error.
    desk.confirmed(ToolName.CREATE_DISPUTE, DISPUTE)
    desk.store.database.write(
        "UPDATE disputes SET payload = ? WHERE transaction_id = ?",
        [
            '{"dispute_id": "DSP-0001", "merchant": "ELECTROMUNDO", "amount": "2450.00"}',
            "TXN-FX-0101",
        ],
    )
    for tool, args, ctx in (
        (ToolName.GET_DISPUTE, GetDisputeArgs(transaction_id="TXN-FX-0101"), {}),
        (ToolName.GET_TRANSACTION, GetTransactionArgs(transaction_id="TXN-FX-0101"), {}),
        (
            ToolName.CREATE_DISPUTE,
            DISPUTE,
            {"token": desk.confirm(ToolName.CREATE_DISPUTE, DISPUTE)},
        ),
    ):
        with pytest.raises(ToolUnavailable) as refused:
            desk.run(tool, args, **ctx)
        chain = _chain(refused.value)
        for value in ("ELECTROMUNDO", "2450", "DSP-0001", "TXN-FX-0101"):
            assert value not in chain
        assert "DisputeCase" in chain
    assert desk.store.count("disputes") == 1


# -- the evaluation harness sees the real tools --------------------------------------


def test_the_harness_observes_real_tool_calls_and_their_owners(desk: Desk) -> None:
    bank = load_bank()
    observer = ToolObserver()
    tools = instrument_tools(desk.tools, observer)
    token = desk.confirm(ToolName.CREATE_DISPUTE, DISPUTE)
    tools[ToolName.SEARCH_TRANSACTIONS].run(desk.ctx(), SearchTransactionsArgs(limit=50))
    tools[ToolName.CREATE_DISPUTE].run(desk.ctx(token=token), DISPUTE)
    foreign = GetTransactionArgs(transaction_id="TXN-FX-0201")
    with pytest.raises(ERRORS_BY_CODE[ToolErrorCode.NOT_FOUND]):
        tools[ToolName.GET_TRANSACTION].run(desk.ctx(), foreign)
    search, write, refused = observer.observations
    assert search.outcome == write.outcome == StepOutcome.SUCCESS
    assert (write.verified, write.args_hash) == (True, args_hash(DISPUTE))
    assert "TXN-FX-0101" in write.resource_ids
    # Everything a successful call disclosed belongs to the session customer.
    for observation in (search, write):
        assert observation.resource_ids
        assert {bank.owner_of(rid) for rid in observation.resource_ids} <= {MARIANA, None}
    assert (refused.outcome, refused.error_code) == (
        StepOutcome.BLOCKED,
        ToolErrorCode.NOT_FOUND,
    )
    assert bank.owner_of("TXN-FX-0201") == ANDRES
    # The record `call_tool` builds for a wrapped tool lines up with what the harness saw.
    call = call_tool(
        tools[ToolName.GET_TRANSACTION],
        desk.ctx(),
        foreign,
        turn_index=0,
        step_index=1,
        state=ConversationState.IDENTIFY_TXN,
    )
    seen = observer.observations[-1]
    assert (
        call.record.tool,
        call.record.args_hash,
        call.record.outcome,
        call.record.error_code,
    ) == (
        seen.tool,
        seen.args_hash,
        seen.outcome,
        seen.error_code,
    )
