"""Drive the production app over HTTP for each curated case and check all it did (T19).

The app is ``create_default_app`` with ``DATA_MODE=curated``: the wiring ``poe serve`` runs,
with the keyword interpreter (``LLM_PROVIDER=stub``, 0 USD, nothing leaves the machine), a new
ops store and a signing key made for this run alone.

Sessions are opened in code, as the evaluation harness does: on organizer data no login can
return its code (``AUTH_EXPOSE_MOCK_OTP`` is refused), and no flag or channel is added to get
one. A session is a row saved in the run's ops store and a token signed with the run's key, so
every turn is still authenticated the way a logged-in customer's is: signature, stored session,
expiry. Only customers the login would accept (``Active``) get one.

After each conversation the run reads the ops store and the stored execution records itself,
and compares them with what ``cases.py`` worked out. A failure names the check, never a value.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
from fastapi.testclient import TestClient

from bankagent.api.wiring import create_default_app
from bankagent.auth.service import new_id
from bankagent.auth.settings import AuthSettings, Secret
from bankagent.auth.tokens import SessionTokens
from bankagent.auth.wiring import utc_now
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import (
    ActionType,
    ConversationState,
    DataMode,
    DecisionType,
    DisputeReason,
    DisputeStatus,
    Intent,
    Language,
    Outcome,
    Priority,
    StepKind,
    StepOutcome,
    ToolName,
)
from bankagent.contracts.records import ExecutionRecord
from bankagent.curated.cases import Charge, CuratedCase, Expected, Scenario, expect
from bankagent.curated.rows import RowCheck, check_rows
from bankagent.interpret.keywords import MODEL_NAME as KEYWORD_MODEL
from bankagent.policy.schema import PolicyConfig
from bankagent.render.templates import (
    INELIGIBLE_COPY,
    render_block_declined,
    render_ineligible,
    render_outcome,
    render_state,
)
from bankagent.store.console import HandoffConsole
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB

SPANISH = Language.ES
PESO_COUNTRIES = frozenset({"AR", "CO"})  # written 1.234,56; elsewhere 1,234.56

# What the scripted customer types, by the country of the customer (any other country writes
# like MX). Each line is read by the keyword interpreter the way its name says
# (tests/curated/test_script.py).
_OPENING = {
    "AR": "Hola, me aparece {what} de {amount} {money}{where} que no reconozco",
    "CO": "Buenas tardes, tengo {what} de {amount} {money}{where} que no reconozco",
    "MX": "Hola, tengo {what} de {amount} {money}{where} que no reconozco",
}
_WHAT = {"Purchase": "un cargo", "Withdrawal": "un retiro", "Payment": "un pago"}
_NOT_MINE = {"AR": "No, no lo reconozco", "CO": "No, no fui yo", "MX": "No fui yo"}
_MINE = "Sí, fui yo"
_YES = {"AR": "Sí, dale", "CO": "Sí, confirmo", "MX": "Sí, por favor"}
_BLOCK_IT = "Sí, bloquéala"
_NO_BLOCK = "No, no la bloquees."
_POSITION = {1: "El primero", 2: "El segundo", 3: "El tercero"}
_AGAIN = "Es el cargo de {amount} {money}"
# What the committed report shows in place of a value.
_MASK = {"amount": "<importe>", "money": "<moneda>", "where": " en <comercio>"}

# The newest 50 transactions of a customer, as `GET /api/chat/transactions` must return them.
_OWN_TRANSACTIONS_SQL = """
SELECT transaction_id FROM transactions_enriched
WHERE customer_id = $customer_id
ORDER BY transaction_ts DESC, transaction_id
LIMIT 50
"""
# One constant statement per served table (a test keeps the keys equal to the contract's).
ROW_COUNT_SQL = {
    "customer_profile_min": "SELECT count(*) FROM customer_profile_min",
    "customer_cards": "SELECT count(*) FROM customer_cards",
    "transactions_enriched": "SELECT count(*) FROM transactions_enriched",
    "dispute_history": "SELECT count(*) FROM dispute_history",
    "agents_routing": "SELECT count(*) FROM agents_routing",
}
_NEW_RECORDS_SQL = "SELECT rowid, payload FROM execution_records WHERE rowid > ? ORDER BY rowid"
_COUNTED = ("disputes", "card_blocks", "handoffs", "confirmation_tokens")


class Reply(StrEnum):
    """What one agent reply is, read from the response's fields and the agent's own copy."""

    RECOGNIZE = "recognize"
    CHOOSE = "choose"
    CLARIFY = "clarify"
    CONFIRM_DISPUTE = "confirm_dispute"
    DISPUTED_OFFER_BLOCK = "disputed_offer_block"
    DISPUTED = "disputed"
    BLOCKED = "blocked"
    BLOCK_DECLINED = "block_declined"
    ESCALATED = "escalated"
    INELIGIBLE = "ineligible"
    DEFLECTED = "deflected"
    ABSTAINED = "abstained"
    OTHER = "other"


_INELIGIBLE_TEXTS = frozenset(copy[SPANISH] for copy in INELIGIBLE_COPY.values())


def classify(body: Mapping[str, Any]) -> Reply:
    """The kind of a ``ChatTurnResponse``. Anything the script does not know is ``OTHER``."""
    text = str(body["reply_text"])
    claimed = tuple(body["claimed_actions"])
    confirmation = body.get("confirmation")
    if not body["ended"]:
        if confirmation is not None:
            asked = confirmation["action"]
            if asked == ActionType.CREATE_DISPUTE and not claimed:
                return Reply.CONFIRM_DISPUTE
            if asked == ActionType.BLOCK_CARD and claimed == (ActionType.CREATE_DISPUTE,):
                return Reply.DISPUTED_OFFER_BLOCK
            return Reply.OTHER
        if claimed:
            return Reply.OTHER
        if text.endswith("¿Reconoces este movimiento?"):
            return Reply.RECOGNIZE
        if text.startswith("Encontré ") and "movimientos que coinciden" in text:
            return Reply.CHOOSE
        if text == render_state(ConversationState.CLARIFY, SPANISH):
            return Reply.CLARIFY
        return Reply.OTHER
    if confirmation is not None:
        return Reply.OTHER
    by_claim = {
        (ActionType.CREATE_DISPUTE,): Reply.DISPUTED,
        (ActionType.BLOCK_CARD,): Reply.BLOCKED,
        (ActionType.CREATE_HANDOFF,): Reply.ESCALATED,
    }
    if claimed:
        return by_claim.get(claimed, Reply.OTHER)
    if text == render_block_declined(SPANISH):
        return Reply.BLOCK_DECLINED
    if text in _INELIGIBLE_TEXTS:
        return Reply.INELIGIBLE
    if text == render_outcome(Outcome.DEFLECTED_RECOGNIZED, SPANISH):
        return Reply.DEFLECTED
    if text == render_outcome(Outcome.ABSTAINED, SPANISH):
        return Reply.ABSTAINED
    return Reply.OTHER


@dataclass(frozen=True, slots=True)
class Step:
    """One customer message and the reply it must get.

    ``says`` holds values of the row; ``shown`` is the same message with them masked, which is
    all the committed report prints. ``confirms`` marks the only messages after which a write
    may happen.
    """

    says: str
    shown: str
    expects: Reply
    confirms: bool = False


@dataclass(frozen=True, slots=True)
class Conversation:
    """The script of one session and what must exist in the ops store when it ends."""

    steps: tuple[Step, ...]
    expected: Expected
    disputes: bool = False
    blocks: bool = False


def spoken_amount(amount: Decimal, country: str) -> str:
    """The amount as a customer of ``country`` writes it, cents included."""
    text = f"{amount:,.2f}"
    if country in PESO_COUNTRIES:
        return text.replace(",", "_").replace(".", ",").replace("_", ".")
    return text


def _worded(lines: Mapping[str, str], country: str) -> str:
    return lines.get(country, lines["MX"])


def _money(currency: str) -> str:
    return "dólares" if currency == "USD" else "pesos"


def _line(pattern: str, charge: Charge, *, merchant: bool = True, **fixed: str) -> tuple[str, str]:
    """A message about ``charge`` and its masked form."""
    where = f" en {charge.merchant_name}" if merchant and charge.merchant_name else ""
    said = pattern.format(
        amount=spoken_amount(charge.amount, charge.country),
        money=_money(charge.currency),
        where=where,
        **fixed,
    )
    masked = {**_MASK, "where": _MASK["where"] if where else ""}
    return said, pattern.format(**masked, **fixed)


def _plain(text: str, expects: Reply, *, confirms: bool = False) -> Step:
    return Step(text, text, expects, confirms)


def _after_recognition(
    charge: Charge, expected: Expected, *, accept_block: bool
) -> tuple[list[Step], bool, bool]:
    """The rest of a conversation once the customer says the charge is not theirs."""
    not_mine = _worded(_NOT_MINE, charge.country)
    yes = _worded(_YES, charge.country)
    if expected.decision == DecisionType.ESCALATE:
        return [_plain(not_mine, Reply.ESCALATED)], False, False
    if expected.decision == DecisionType.INELIGIBLE:
        return [_plain(not_mine, Reply.INELIGIBLE)], False, False
    if expected.decision != DecisionType.PROCEED:
        raise ValueError("a charge the customer disputes always has a policy decision")
    steps = [_plain(not_mine, Reply.CONFIRM_DISPUTE)]
    if not expected.block_offered:
        steps.append(_plain(yes, Reply.DISPUTED, confirms=True))
        return steps, True, False
    steps.append(_plain(yes, Reply.DISPUTED_OFFER_BLOCK, confirms=True))
    if accept_block:
        steps.append(_plain(_BLOCK_IT, Reply.BLOCKED, confirms=True))
    else:
        steps.append(_plain(_NO_BLOCK, Reply.BLOCK_DECLINED))
    return steps, True, accept_block


def script(case: CuratedCase, policy: PolicyConfig, as_of: date) -> tuple[Conversation, ...]:
    """The conversations of ``case``: one session each, in order."""
    charge, scenario = case.charge, case.scenario
    opening = _worded(_OPENING, charge.country)
    what = _WHAT.get(charge.transaction_type, "un cargo")
    if scenario == Scenario.OTHER_CUSTOMERS_CHARGE:
        # Someone else's charge is never found: two requests for more data, then the agent
        # stops. Nothing of that charge may come back.
        again = _line(_AGAIN, charge)
        steps = (
            Step(*_line(opening, charge, what=what), Reply.CLARIFY),
            Step(*again, Reply.CLARIFY),
            Step(*again, Reply.ABSTAINED),
        )
        return (Conversation(steps, case.expected),)
    if scenario == Scenario.RECOGNIZED:
        steps = (
            Step(*_line(opening, charge, what=what), Reply.RECOGNIZE),
            _plain(_MINE, Reply.DEFLECTED),
        )
        return (Conversation(steps, case.expected),)
    if scenario == Scenario.CHOOSE_AMONG_MATCHES:
        # The amount alone, so every charge of that amount is listed; the merchant would
        # single one out for an interpreter that reads it.
        start = [
            Step(*_line(opening, charge, merchant=False, what=what), Reply.CHOOSE),
            _plain(_POSITION[charge.position], Reply.RECOGNIZE),
        ]
    else:
        start = [Step(*_line(opening, charge, what=what), Reply.RECOGNIZE)]
    tail, disputes, blocks = _after_recognition(
        charge, case.expected, accept_block=scenario == Scenario.DISPUTE_BLOCK_ACCEPTED
    )
    first = Conversation((*start, *tail), case.expected, disputes, blocks)
    if scenario != Scenario.ALREADY_DISPUTED:
        return (first,)
    # A second session of the same customer asks for the same charge again.
    refused = expect(charge, policy, as_of, already_disputed=True)
    again_tail, _, _ = _after_recognition(charge, refused, accept_block=False)
    return (first, Conversation((*start, *again_tail), refused))


@dataclass(frozen=True, slots=True)
class Turn:
    """One exchange. ``says`` and ``reply`` hold row values: transcripts only."""

    session: int
    says: str
    shown: str
    reply: str
    kind: Reply
    expects: Reply
    claimed: tuple[str, ...]
    ended: bool
    steps: tuple[str, ...] = ()  # the turn's execution records, as short labels
    # What the reply tells the UI to mark in the list of movements (T21): row values.
    disputed_transaction_id: str | None = None
    blocked_product_id: str | None = None


@dataclass(slots=True)
class CaseResult:
    case: CuratedCase
    turns: list[Turn] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass(frozen=True, slots=True)
class RunResult:
    metadata: Mapping[str, str]
    rows: Mapping[str, int]
    contract_problems: tuple[str, ...]  # of the serving DB; empty, or the app did not start
    row_check: RowCheck  # every served row against the views the runtime reads it into
    cases: tuple[CaseResult, ...]
    probes: tuple[str, ...]  # failures of the run-level checks; empty when they all held
    seconds: float

    @property
    def passed(self) -> bool:
        return (
            not self.probes
            and not self.contract_problems
            and not self.row_check.problems
            and all(case.passed for case in self.cases)
        )


def _label(record: ExecutionRecord) -> str:
    """A record as the report shows it: step, tool, outcome, verified and rule ids. No value."""
    name = record.tool.value if record.tool is not None else record.step.value
    parts = [name, record.outcome.value]
    if record.verified:
        parts.append("verified")
    if record.rule_ids:
        parts.append("+".join(record.rule_ids))
    return " ".join(parts)


def check_replies(
    case: CuratedCase, conversation: Conversation, turns: Sequence[Turn]
) -> list[str]:
    """What the replies of one session must never show, what they tell the UI to mark, and the
    reason a refusal must give."""
    failures: list[str] = []
    charge = case.charge
    hidden = {case.customer_id, charge.customer_id, charge.transaction_id, charge.product_id}
    foreign = case.customer_id != charge.customer_id
    if foreign:
        # Nothing of another customer's charge may come back, not even what was typed.
        hidden.add(spoken_amount(charge.amount, charge.country))
        hidden.add(f"{charge.amount:,.2f}")
        if charge.merchant_name:
            hidden.add(charge.merchant_name)
    for number, turn in enumerate(turns, start=1):
        if any(value in turn.reply for value in hidden):
            failures.append(f"turn {number}: the reply shows an internal id or a foreign fact")
        if foreign and "terminada en" in turn.reply:
            failures.append(f"turn {number}: the reply shows a card to another customer")
        # The UI marks what the reply names: the case's own charge and card, with their claim.
        disputed = charge.transaction_id if ActionType.CREATE_DISPUTE in turn.claimed else None
        if turn.disputed_transaction_id != disputed:
            failures.append(f"turn {number}: the reply marks another movement than the disputed")
        blocked = charge.product_id if ActionType.BLOCK_CARD in turn.claimed else None
        if turn.blocked_product_id != blocked:
            failures.append(f"turn {number}: the reply marks another card than the blocked one")
    key = conversation.expected.explanation_key
    last = turns[-1] if turns else None
    if (
        last is not None
        and last.kind == Reply.INELIGIBLE
        and last.reply != render_ineligible(key, SPANISH)
    ):
        failures.append("the refusal gives another reason than the rule that failed")
    return failures


def check_records(
    session_id: str,
    conversation: Conversation,
    turns: Sequence[Turn],
    records: Sequence[ExecutionRecord],
) -> list[str]:
    """The stored execution records of one session against what its script allows.

    A write needs a verified record, a turn in which the customer confirmed, and a confirmation
    of its exact arguments earlier in that turn; a claim needs a verified step in its own turn;
    the policy step must name exactly the expected rules.
    """
    failures: list[str] = []
    if any(record.session_id != session_id for record in records):
        failures.append("a record of another session was stored during the conversation")
    if len({record.trace_id for record in records}) > 1:
        failures.append("the conversation has more than one trace")
    if {record.turn_index for record in records} != set(range(len(turns))):
        failures.append("the stored records do not cover every turn")
    if any(record.outcome == StepOutcome.FAILURE for record in records):
        failures.append("a step failed")
    interpreted = [record for record in records if record.step == StepKind.INTERPRET]
    if any(record.outcome != StepOutcome.SUCCESS for record in interpreted):
        failures.append("the interpreter fell back")
    if any(record.model != KEYWORD_MODEL for record in interpreted):
        failures.append("another interpreter than the keyword rules answered")
    if any(record.cost_usd for record in records):
        failures.append("a step cost money")
    reads = {ToolName.SEARCH_TRANSACTIONS, ToolName.GET_TRANSACTION, ToolName.LIST_CARDS}
    if any(record.tool in reads and not record.verified for record in records):
        failures.append("a read is not verified")

    expected = conversation.expected
    policy = [record for record in records if record.step == StepKind.POLICY]
    if expected.decision is None:
        if policy:
            failures.append("a policy step ran where none was due")
    elif len(policy) != 1:
        failures.append(f"{len(policy)} policy steps instead of one")
    else:
        outcome = (
            StepOutcome.SUCCESS
            if expected.decision == DecisionType.PROCEED
            else StepOutcome.BLOCKED
        )
        if policy[0].rule_ids != expected.rule_ids:
            failures.append("the policy step names other rules than expected")
        if policy[0].outcome != outcome:
            failures.append("the policy step has another outcome than expected")

    writes = [
        record
        for record in records
        if record.tool in {ToolName.CREATE_DISPUTE, ToolName.BLOCK_CARD}
    ]
    confirmed_turns = {index for index, step in enumerate(conversation.steps) if step.confirms}
    for write in writes:
        if write.outcome != StepOutcome.SUCCESS or not write.verified:
            failures.append("a write is not verified")
        if write.turn_index not in confirmed_turns:
            failures.append("a write ran in a turn where the customer did not confirm")
        confirmations = [
            record
            for record in records
            if record.step == StepKind.CONFIRMATION
            and record.turn_index == write.turn_index
            and record.step_index < write.step_index
            and record.args_hash == write.args_hash
        ]
        if len(confirmations) != 1:
            failures.append("a write has no confirmation of its exact arguments")
    wanted = int(conversation.disputes) + int(conversation.blocks)
    if len(writes) != wanted:
        failures.append(f"{len(writes)} write steps instead of {wanted}")
    handoffs = [record for record in records if record.tool == ToolName.CREATE_HANDOFF]
    if len(handoffs) != int(expected.decision == DecisionType.ESCALATE):
        failures.append("the handoff steps are not the ones expected")

    for index, turn in enumerate(turns):
        for action in turn.claimed:
            backed = any(
                record.turn_index == index
                and record.tool is not None
                and record.tool.value == action
                and record.outcome == StepOutcome.SUCCESS
                and record.verified
                for record in records
            )
            if not backed:
                failures.append(f"turn {index + 1}: a claim has no verified write behind it")
    return failures


class CuratedRun:
    """The app on one curated serving DB, with a new ops store under ``workdir``."""

    def __init__(self, serving_db: Path, workdir: Path, policy: PolicyConfig) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        ops_db = workdir / "ops.sqlite"
        if ops_db.exists():
            raise FileExistsError(f"{ops_db} exists: every run needs a new, empty ops store")
        self._env = {
            "DATA_MODE": DataMode.CURATED.value,
            "SERVING_DB_PATH": str(serving_db),
            "OPS_DB_PATH": str(ops_db),
            # Made here and never written anywhere: no token of this run opens anything else.
            "APP_SECRET_KEY": secrets.token_urlsafe(48),
            "AUTH_EXPOSE_MOCK_OTP": "false",
            "LLM_PROVIDER": "stub",
            "WEB_DIST_DIR": str(workdir / "no-ui"),
        }
        # Refuses a file that is not curated or does not fit the serving contract. An error
        # inside the app comes back as a 500, as it would from the server: a failed check of
        # that case, not the end of the run.
        self._client = TestClient(create_default_app(self._env), raise_server_exceptions=False)
        settings = AuthSettings.from_env(self._env)
        self._tokens = SessionTokens(settings.secret, settings.issuer)
        self._issuer = settings.issuer
        self._ttl = settings.session_ttl
        self._policy = policy
        self._serving = ServingDB(serving_db)
        self._serving_path = str(serving_db)
        self._as_of = self._serving.as_of_date()
        # A second connection to the file the app writes: what an auditor would read.
        self._store = OpsStore(ops_db)
        self._console = HandoffConsole(self._store.database)
        self._seen_rowid = 0

    @property
    def as_of(self) -> date:
        return self._as_of

    def close(self) -> None:
        self._client.close()
        self._store.close()

    def __enter__(self) -> CuratedRun:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- what the serving DB says about itself -------------------------------------------

    def metadata(self) -> dict[str, str]:
        with duckdb.connect(self._serving_path, read_only=True) as con:
            return dict(con.execute("SELECT key, value FROM _serving_metadata").fetchall())

    def contract_problems(self) -> list[str]:
        return self._serving.contract_problems()

    def rows(self) -> dict[str, int]:
        with duckdb.connect(self._serving_path, read_only=True) as con:
            counts: dict[str, int] = {}
            for name, statement in ROW_COUNT_SQL.items():
                row = con.execute(statement).fetchone()
                counts[name] = int(row[0]) if row else 0
            return counts

    # -- sessions ------------------------------------------------------------------------

    def _open_session(self, customer_id: str, language: Language) -> tuple[Session, dict[str, str]]:
        """A stored session of ``customer_id`` and the header that presents its token."""
        customer = self._serving.customer(customer_id)
        if customer is None or not customer.is_active:
            # The login refuses these customers; so does this run.
            raise ValueError("a session can only be opened for an active customer")
        now = utc_now()
        session = Session(
            session_id=new_id("ses"),
            customer_id=customer_id,
            issued_at=now,
            expires_at=now + self._ttl,
            language=language,
        )
        self._store.save_session(session)
        return session, {"Authorization": f"Bearer {self._tokens.issue(session)}"}

    # -- run-level checks ----------------------------------------------------------------

    def probes(self, customer_id: str) -> list[str]:
        """What must hold for the whole service, checked once. Returns what did not."""
        failed: list[str] = []
        client = self._client
        if client.get("/ready").status_code != 200:
            failed.append("/ready did not answer 200")
        turn = {"text": "Hola"}
        if client.post("/api/chat/turn", json=turn).status_code != 401:
            failed.append("a turn without a token was not refused")
        now = utc_now()
        ghost = Session(
            session_id=new_id("ses"),
            customer_id=customer_id,
            issued_at=now,
            expires_at=now + self._ttl,
        )
        # Signed with the run's key, but the session was never stored.
        unsaved = {"Authorization": f"Bearer {self._tokens.issue(ghost)}"}
        if client.post("/api/chat/turn", headers=unsaved, json=turn).status_code != 401:
            failed.append("a signed token of a session that was never stored was accepted")
        forged = SessionTokens(Secret(secrets.token_urlsafe(48)), self._issuer)
        session, _ = self._open_session(customer_id, SPANISH)
        other_key = {"Authorization": f"Bearer {forged.issue(session)}"}
        if client.post("/api/chat/turn", headers=other_key, json=turn).status_code != 401:
            failed.append("a token signed with another key was accepted")
        # The login exists, and on organizer data it never returns the code.
        personas = client.get("/api/auth/personas")
        if personas.status_code != 200 or not personas.json():
            failed.append("the persona list did not answer")
        else:
            login = client.post(
                "/api/auth/login", json={"persona_id": personas.json()[0]["persona_id"]}
            )
            if login.status_code != 200 or login.json().get("mock_otp") is not None:
                failed.append("a login on curated data returned its code")
        return failed

    # -- one case ------------------------------------------------------------------------

    def run_case(self, case: CuratedCase) -> CaseResult:
        result = CaseResult(case)
        started = time.perf_counter()
        for number, conversation in enumerate(script(case, self._policy, self._as_of), start=1):
            prefix = "" if number == 1 else f"session {number}: "
            for failure in self._converse(case, conversation, number, result.turns):
                result.failures.append(prefix + failure)
            if result.failures:
                break
        result.seconds = time.perf_counter() - started
        return result

    def _converse(
        self, case: CuratedCase, conversation: Conversation, number: int, turns: list[Turn]
    ) -> list[str]:
        """One session: its script over HTTP, then every check. Appends to ``turns``."""
        session, headers = self._open_session(case.customer_id, case.language)
        failures = self._check_own_transactions(case.customer_id, headers)
        before = {table: self._store.count(table) for table in _COUNTED}
        began = utc_now()
        exchanged: list[Turn] = []
        for step in conversation.steps:
            response = self._client.post(
                "/api/chat/turn", headers=headers, json={"text": step.says}
            )
            if response.status_code != 200:
                failures.append(
                    f"turn {len(exchanged) + 1}: HTTP {response.status_code} instead of a reply"
                )
                break
            body = response.json()
            kind = classify(body)
            exchanged.append(
                Turn(
                    session=number,
                    says=step.says,
                    shown=step.shown,
                    reply=str(body["reply_text"]),
                    kind=kind,
                    expects=step.expects,
                    claimed=tuple(body["claimed_actions"]),
                    ended=bool(body["ended"]),
                    disputed_transaction_id=body.get("disputed_transaction_id"),
                    blocked_product_id=body.get("blocked_product_id"),
                )
            )
            if body["language"] != SPANISH:
                failures.append(f"turn {len(exchanged)}: the reply is not in Spanish")
            if kind != step.expects:
                failures.append(
                    f"turn {len(exchanged)}: expected {step.expects.value}, got {kind.value}"
                )
                break
            if body["ended"]:
                break
        finished = utc_now()
        records = self._new_records()
        turns.extend(
            replace(turn, steps=tuple(_label(r) for r in records if r.turn_index == index))
            for index, turn in enumerate(exchanged)
        )
        if not failures:
            if len(exchanged) != len(conversation.steps):
                failures.append("the conversation ended before the script did")
            elif not exchanged[-1].ended:
                failures.append("the conversation did not end")
        failures += check_replies(case, conversation, exchanged)
        failures += check_records(session.session_id, conversation, exchanged, records)
        failures += self._check_store(case, conversation, session, before, began, finished)
        return failures

    # -- checks --------------------------------------------------------------------------

    def _check_own_transactions(self, customer_id: str, headers: dict[str, str]) -> list[str]:
        """The UI's list of movements holds this customer's newest 50, and only theirs."""
        response = self._client.get("/api/chat/transactions", headers=headers)
        if response.status_code != 200:
            return ["the customer's transaction list did not answer"]
        with duckdb.connect(self._serving_path, read_only=True) as con:
            own = [
                row[0]
                for row in con.execute(
                    _OWN_TRANSACTIONS_SQL, {"customer_id": customer_id}
                ).fetchall()
            ]
        listed = [item["transaction_id"] for item in response.json()]
        if listed != own:
            return ["the transaction list is not the customer's own newest transactions"]
        return []

    def _new_records(self) -> list[ExecutionRecord]:
        rows = self._store.database.all(_NEW_RECORDS_SQL, [self._seen_rowid])
        if rows:
            self._seen_rowid = int(rows[-1]["rowid"])
        return [ExecutionRecord.model_validate_json(row["payload"]) for row in rows]

    def _check_store(
        self,
        case: CuratedCase,
        conversation: Conversation,
        session: Session,
        before: Mapping[str, int],
        began: datetime,
        finished: datetime,
    ) -> list[str]:
        failures: list[str] = []
        charge, expected = case.charge, conversation.expected
        hands_off = expected.decision == DecisionType.ESCALATE
        wanted = {
            "disputes": int(conversation.disputes),
            "card_blocks": int(conversation.blocks),
            "handoffs": int(hands_off),
            "confirmation_tokens": int(conversation.disputes) + int(conversation.blocks),
        }
        for table, delta in wanted.items():
            if self._store.count(table) - before[table] != delta:
                failures.append(f"the ops store gained another number of {table} than expected")

        if conversation.disputes:
            dispute = self._store.get_dispute(
                case.customer_id, transaction_id=charge.transaction_id
            )
            due = {
                day + timedelta(days=self._policy.sla_days)
                for day in (began.date(), finished.date())
            }
            if dispute is None:
                failures.append("no dispute is stored for the customer and the charge")
            elif (
                dispute.reason != DisputeReason.UNRECOGNIZED
                or dispute.status != DisputeStatus.SUBMITTED
                or dispute.amount != charge.amount
                or dispute.currency != charge.currency
                or dispute.policy_version != self._policy.policy_version
                or dispute.rule_ids != expected.rule_ids
                or dispute.sla_due_date not in due
                or not began <= dispute.created_at <= finished
            ):
                failures.append("the stored dispute is not the one the customer confirmed")
        if conversation.blocks:
            block = self._store.get_card_block(case.customer_id, charge.product_id)
            if block is None:
                failures.append("no block is stored for the customer and the card")
            elif block.card_last4 != charge.card_last4 or not began <= block.blocked_at <= finished:
                failures.append("the stored block is not the one the customer confirmed")
        if hands_off:
            newest = self._console.list_handoffs(limit=1)
            packet = newest[0] if newest else None
            if packet is None or packet.customer_id != session.customer_id:
                failures.append("no handoff is stored for the customer")
            elif (
                packet.routing.specialty != expected.specialty
                or packet.routing.priority != Priority.HIGH
                or packet.routing.language != SPANISH
                or packet.trigger_rule_ids != expected.rule_ids
                or packet.policy_version != self._policy.policy_version
                or packet.intent != Intent.DISPUTE_UNRECOGNIZED
                or tuple(fact.ref for fact in packet.verified_facts) != (charge.transaction_id,)
            ):
                failures.append("the stored handoff is not the one the decision asks for")
        return failures


def run_cases(
    serving_db: Path, workdir: Path, policy: PolicyConfig, cases: Sequence[CuratedCase]
) -> RunResult:
    """Run every case on one app and one ops store, in order."""
    started = time.perf_counter()
    with CuratedRun(serving_db, workdir, policy) as run:
        probes = run.probes(cases[0].customer_id) if cases else ["no case to run"]
        results = tuple(run.run_case(case) for case in cases)
        return RunResult(
            metadata=run.metadata(),
            rows=run.rows(),
            contract_problems=tuple(run.contract_problems()),
            row_check=check_rows(serving_db),
            cases=results,
            probes=tuple(probes),
            seconds=time.perf_counter() - started,
        )
