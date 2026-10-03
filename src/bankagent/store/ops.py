"""Operational store (SQLite): everything the agent writes or needs to remember between turns.

Tables: sessions, login challenges and failures (T7 auth), confirmation tokens, disputes, card
blocks, handoffs and execution records (written by the T8 tools and the T13 orchestrator).

Rules that hold here so callers cannot get them wrong:

- Every statement is parameterized; no SQL is ever built from values.
- Every read a customer-facing tool can make is scoped by ``customer_id`` or ``session_id``.
  Unscoped reads for human agents live in `bankagent.store.console`, which tools never import.
- Disputes, card blocks and handoffs are idempotent by ``(customer_id, idempotency_key)``: an
  exact replay returns the stored row and ``created=False``; the same key with different
  arguments raises ``IdempotencyConflict`` and writes nothing.
- One dispute per transaction and one block per card, enforced inside the insert transaction
  and by a UNIQUE constraint: a second one raises ``DisputeAlreadyExists`` /
  ``CardAlreadyBlocked`` carrying the stored record.
- Confirmation tokens and login challenges are consumed with one conditional ``UPDATE``, so a
  token can be used once even under concurrent calls or several processes.
- ``transaction()`` groups several calls atomically (a tool marks its token used and writes the
  dispute in the same transaction).

Money is stored as text inside the JSON payload and read back as ``Decimal``.
Owner: Juan José (T7).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from bankagent.contracts.domain import CardBlockEvent, ConfirmationToken, DisputeCase, Session
from bankagent.contracts.enums import ActionType
from bankagent.contracts.handoff import HandoffPacket
from bankagent.contracts.records import ExecutionRecord
from bankagent.store.sqlite import SCHEMA_VERSION, Database, OpsStoreError, from_text, to_text

__all__ = [
    "SCHEMA_VERSION",
    "CardAlreadyBlocked",
    "DisputeAlreadyExists",
    "IdempotencyConflict",
    "LoginChallenge",
    "OpsStore",
    "OpsStoreError",
]

# Fields a handoff replay must repeat; ids and timestamps are minted per call.
_HANDOFF_VOLATILE = {"handoff_id", "created_at"}


class IdempotencyConflict(OpsStoreError):
    """An idempotency key was reused for a different request. Nothing was written."""


class DisputeAlreadyExists(OpsStoreError):
    """The transaction already has a dispute (under another idempotency key)."""

    def __init__(self, existing: DisputeCase) -> None:
        super().__init__("a dispute already exists for this transaction")
        self.existing = existing


class CardAlreadyBlocked(OpsStoreError):
    """The card already has a block (under another idempotency key)."""

    def __init__(self, existing: CardBlockEvent) -> None:
        super().__init__("this card is already blocked")
        self.existing = existing


@dataclass(frozen=True, slots=True)
class LoginChallenge:
    """One pending OTP login. ``otp_hash`` is a keyed hash; the code itself is never stored."""

    challenge_id: str
    customer_id: str
    otp_hash: str
    issued_at: datetime
    expires_at: datetime
    max_attempts: int
    attempts: int = 0
    consumed_at: datetime | None = None


class OpsStore:
    """Customer-facing operational store. Pass a path, or a `Database` to share a connection."""

    def __init__(self, source: Path | str | Database) -> None:
        self._db = source if isinstance(source, Database) else Database(source)

    @property
    def database(self) -> Database:
        """The underlying connection, to build a `HandoffConsole` on the same file."""
        return self._db

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> OpsStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Atomic block. Re-entrant: nested blocks join the outer transaction."""
        with self._db.transaction():
            yield

    # -- sessions ----------------------------------------------------------

    def save_session(self, session: Session) -> None:
        self._db.write(
            "INSERT INTO sessions (session_id, customer_id, issued_at, expires_at, language) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                session.session_id,
                session.customer_id,
                to_text(session.issued_at),
                to_text(session.expires_at),
                session.language.value if session.language else None,
            ],
        )

    def get_session(self, session_id: str) -> Session | None:
        """The stored session unless it was revoked. Expiry is the caller's check."""
        row = self._db.one(
            "SELECT session_id, customer_id, issued_at, expires_at, language FROM sessions "
            "WHERE session_id = ? AND revoked_at IS NULL",
            [session_id],
        )
        return None if row is None else Session.model_validate(dict(row))

    def revoke_session(self, session_id: str, now: datetime) -> bool:
        cursor = self._db.write(
            "UPDATE sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
            [to_text(now), session_id],
        )
        return cursor.rowcount == 1

    # -- login challenges --------------------------------------------------

    def create_challenge(self, challenge: LoginChallenge) -> None:
        self._db.write(
            "INSERT INTO login_challenges (challenge_id, customer_id, otp_hash, issued_at, "
            "expires_at, attempts, max_attempts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                challenge.challenge_id,
                challenge.customer_id,
                challenge.otp_hash,
                to_text(challenge.issued_at),
                to_text(challenge.expires_at),
                challenge.attempts,
                challenge.max_attempts,
            ],
        )

    def get_challenge(self, challenge_id: str) -> LoginChallenge | None:
        row = self._db.one(
            "SELECT challenge_id, customer_id, otp_hash, issued_at, expires_at, attempts, "
            "max_attempts, consumed_at FROM login_challenges WHERE challenge_id = ?",
            [challenge_id],
        )
        if row is None:
            return None
        return LoginChallenge(
            challenge_id=row["challenge_id"],
            customer_id=row["customer_id"],
            otp_hash=row["otp_hash"],
            issued_at=datetime.fromisoformat(row["issued_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            consumed_at=from_text(row["consumed_at"]),
        )

    def reserve_attempt(self, challenge_id: str, now: datetime) -> bool:
        """Spend one of the challenge's attempts before the code is compared.

        One conditional UPDATE, so concurrent guesses cannot exceed ``max_attempts``: each one
        either gets a distinct attempt or gets False. A correct code also spends an attempt;
        that is harmless because the challenge is consumed right after.
        """
        cursor = self._db.write(
            "UPDATE login_challenges SET attempts = attempts + 1 WHERE challenge_id = ? "
            "AND consumed_at IS NULL AND attempts < max_attempts "
            "AND issued_at <= ? AND expires_at > ?",
            [challenge_id, to_text(now), to_text(now)],
        )
        return cursor.rowcount == 1

    def record_login_failure(self, customer_id: str, now: datetime) -> int:
        """Count one wrong code against the customer; returns the id of that count."""
        cursor = self._db.write(
            "INSERT INTO login_failures (customer_id, failed_at) VALUES (?, ?)",
            [customer_id, to_text(now)],
        )
        if cursor.lastrowid is None:
            raise OpsStoreError("login failure was not recorded")
        return cursor.lastrowid

    def forget_login_failure(self, failure_id: int) -> None:
        """Remove one counted failure: the attempt it stood for turned out to be correct."""
        self._db.write("DELETE FROM login_failures WHERE failure_id = ?", [failure_id])

    def consume_challenge(self, challenge_id: str, now: datetime) -> bool:
        """Mark an open challenge used. False when it was already used or has expired."""
        cursor = self._db.write(
            "UPDATE login_challenges SET consumed_at = ? WHERE challenge_id = ? "
            "AND consumed_at IS NULL AND issued_at <= ? AND expires_at > ?",
            [to_text(now), challenge_id, to_text(now), to_text(now)],
        )
        return cursor.rowcount == 1

    def failures_since(self, customer_id: str, since: datetime) -> list[datetime]:
        """Wrong-code timestamps of a customer at or after ``since``, oldest first."""
        rows = self._db.all(
            "SELECT failed_at FROM login_failures WHERE customer_id = ? AND failed_at >= ? "
            "ORDER BY failed_at",
            [customer_id, to_text(since)],
        )
        return [datetime.fromisoformat(r["failed_at"]) for r in rows]

    def purge_login_data(self, before: datetime) -> None:
        """Delete challenges that expired, and failures recorded, before ``before``."""
        with self._db.transaction():
            self._db.write("DELETE FROM login_challenges WHERE expires_at < ?", [to_text(before)])
            self._db.write("DELETE FROM login_failures WHERE failed_at < ?", [to_text(before)])

    # -- confirmation tokens -----------------------------------------------

    def save_confirmation_token(self, token: ConfirmationToken) -> None:
        self._db.write(
            "INSERT INTO confirmation_tokens (token_id, session_id, action, args_hash, "
            "issued_at, expires_at, used_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                token.token_id,
                token.session_id,
                token.action.value,
                token.args_hash,
                to_text(token.issued_at),
                to_text(token.expires_at),
                to_text(token.used_at) if token.used_at else None,
            ],
        )

    def get_confirmation_token(self, token_id: str, session_id: str) -> ConfirmationToken | None:
        """A token of this session. Another session's token looks like a missing one."""
        row = self._db.one(
            "SELECT token_id, session_id, action, args_hash, issued_at, expires_at, used_at "
            "FROM confirmation_tokens WHERE token_id = ? AND session_id = ?",
            [token_id, session_id],
        )
        return None if row is None else ConfirmationToken.model_validate(dict(row))

    def consume_confirmation_token(
        self, token_id: str, *, session_id: str, action: ActionType, args_hash: str, now: datetime
    ) -> bool:
        """Use the token for exactly this session, action and arguments. True only once.

        One conditional UPDATE: a missing, foreign, mismatched, expired or already used token
        changes no row. Call it inside the same ``transaction()`` as the write it authorizes.
        """
        cursor = self._db.write(
            "UPDATE confirmation_tokens SET used_at = ? WHERE token_id = ? AND session_id = ? "
            "AND action = ? AND args_hash = ? AND used_at IS NULL "
            "AND issued_at <= ? AND expires_at > ?",
            [
                to_text(now),
                token_id,
                session_id,
                action.value,
                args_hash,
                to_text(now),
                to_text(now),
            ],
        )
        return cursor.rowcount == 1

    # -- disputes ----------------------------------------------------------

    def insert_dispute(self, customer_id: str, dispute: DisputeCase) -> tuple[DisputeCase, bool]:
        """Store the dispute, or return the stored one when this is an exact replay.

        Raises ``IdempotencyConflict`` when the key was used for another transaction or reason,
        and ``DisputeAlreadyExists`` when the transaction already has a dispute under another
        key. Both checks and the insert run in one transaction.
        """
        with self._db.transaction():
            replay = self._db.one(
                "SELECT payload FROM disputes WHERE customer_id = ? AND idempotency_key = ?",
                [customer_id, dispute.idempotency_key],
            )
            if replay is not None:
                stored = DisputeCase.model_validate_json(replay["payload"])
                if (stored.transaction_id, stored.reason) != (
                    dispute.transaction_id,
                    dispute.reason,
                ):
                    raise IdempotencyConflict(
                        "idempotency key already used for a different dispute"
                    )
                return stored, False
            other = self._db.one(
                "SELECT payload FROM disputes WHERE customer_id = ? AND transaction_id = ?",
                [customer_id, dispute.transaction_id],
            )
            if other is not None:
                raise DisputeAlreadyExists(DisputeCase.model_validate_json(other["payload"]))
            self._db.write(
                "INSERT INTO disputes (dispute_id, customer_id, transaction_id, idempotency_key, "
                "created_at, payload) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    dispute.dispute_id,
                    customer_id,
                    dispute.transaction_id,
                    dispute.idempotency_key,
                    to_text(dispute.created_at),
                    dispute.model_dump_json(),
                ],
            )
        return dispute, True

    def get_dispute(
        self,
        customer_id: str,
        *,
        dispute_id: str | None = None,
        transaction_id: str | None = None,
    ) -> DisputeCase | None:
        """A dispute of this customer by id or by transaction.

        Another customer's dispute returns ``None``, exactly like a missing one.
        """
        if (dispute_id is None) == (transaction_id is None):
            raise ValueError("provide exactly one of dispute_id or transaction_id")
        if dispute_id is not None:
            row = self._db.one(
                "SELECT payload FROM disputes WHERE customer_id = ? AND dispute_id = ?",
                [customer_id, dispute_id],
            )
        else:
            row = self._db.one(
                "SELECT payload FROM disputes WHERE customer_id = ? AND transaction_id = ?",
                [customer_id, transaction_id],
            )
        return None if row is None else DisputeCase.model_validate_json(row["payload"])

    def last_dispute_date(self, customer_id: str) -> date | None:
        """Filing date of this customer's most recent agent-made dispute, across every
        transaction, or ``None``. Feeds ``PolicyInputs.last_agent_dispute_date``
        (``bankagent.policy.inputs.build_inputs``), compared against ``filed_on`` (real time),
        never against ``as_of_date`` (the frozen serving-DB snapshot): this date and
        ``ServingDB.last_claim_date``'s are on different clocks and must not be merged into one
        before comparing (PR #44 review, round 2), or an agent dispute never expires.
        """
        row = self._db.one(
            "SELECT max(created_at) AS latest FROM disputes WHERE customer_id = ?", [customer_id]
        )
        value = from_text(row["latest"]) if row else None
        return None if value is None else value.date()

    # -- card blocks -------------------------------------------------------

    def insert_card_block(
        self, customer_id: str, block: CardBlockEvent
    ) -> tuple[CardBlockEvent, bool]:
        """Store the block, or return the stored one when this is an exact replay.

        Raises ``IdempotencyConflict`` when the key was used for another card and
        ``CardAlreadyBlocked`` when the card already has a block under another key.
        """
        with self._db.transaction():
            replay = self._db.one(
                "SELECT payload FROM card_blocks WHERE customer_id = ? AND idempotency_key = ?",
                [customer_id, block.idempotency_key],
            )
            if replay is not None:
                stored = CardBlockEvent.model_validate_json(replay["payload"])
                if stored.product_id != block.product_id:
                    raise IdempotencyConflict("idempotency key already used for a different card")
                return stored, False
            other = self._db.one(
                "SELECT payload FROM card_blocks WHERE customer_id = ? AND product_id = ?",
                [customer_id, block.product_id],
            )
            if other is not None:
                raise CardAlreadyBlocked(CardBlockEvent.model_validate_json(other["payload"]))
            self._db.write(
                "INSERT INTO card_blocks (block_id, customer_id, product_id, idempotency_key, "
                "blocked_at, payload) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    block.block_id,
                    customer_id,
                    block.product_id,
                    block.idempotency_key,
                    to_text(block.blocked_at),
                    block.model_dump_json(),
                ],
            )
        return block, True

    def get_card_block(self, customer_id: str, product_id: str) -> CardBlockEvent | None:
        """The block of this customer's card, if any (``None`` for foreign cards too)."""
        row = self._db.one(
            "SELECT payload FROM card_blocks WHERE customer_id = ? AND product_id = ?",
            [customer_id, product_id],
        )
        return None if row is None else CardBlockEvent.model_validate_json(row["payload"])

    # -- handoffs ----------------------------------------------------------

    def insert_handoff(
        self, packet: HandoffPacket, idempotency_key: str
    ) -> tuple[HandoffPacket, bool]:
        """Store the handoff, or return the stored one when this is an exact replay.

        A replay must carry the same content (everything but the id and timestamp minted per
        call); otherwise ``IdempotencyConflict``.
        """
        with self._db.transaction():
            replay = self._db.one(
                "SELECT payload FROM handoffs WHERE customer_id = ? AND idempotency_key = ?",
                [packet.customer_id, idempotency_key],
            )
            if replay is not None:
                stored = HandoffPacket.model_validate_json(replay["payload"])
                if stored.model_dump(exclude=_HANDOFF_VOLATILE) != packet.model_dump(
                    exclude=_HANDOFF_VOLATILE
                ):
                    raise IdempotencyConflict(
                        "idempotency key already used for a different handoff"
                    )
                return stored, False
            self._db.write(
                "INSERT INTO handoffs (handoff_id, customer_id, idempotency_key, created_at, "
                "payload) VALUES (?, ?, ?, ?, ?)",
                [
                    packet.handoff_id,
                    packet.customer_id,
                    idempotency_key,
                    to_text(packet.created_at),
                    packet.model_dump_json(),
                ],
            )
        return packet, True

    def get_handoff(self, customer_id: str, handoff_id: str) -> HandoffPacket | None:
        """A handoff of this customer (read-back after `insert_handoff`); ``None`` otherwise."""
        row = self._db.one(
            "SELECT payload FROM handoffs WHERE customer_id = ? AND handoff_id = ?",
            [customer_id, handoff_id],
        )
        return None if row is None else HandoffPacket.model_validate_json(row["payload"])

    # -- execution records -------------------------------------------------

    def append_records(self, records: Sequence[ExecutionRecord]) -> None:
        self._db.write_many(
            "INSERT INTO execution_records (record_id, trace_id, turn_index, step_index, "
            "created_at, payload) VALUES (?, ?, ?, ?, ?, ?)",
            [
                [
                    r.record_id,
                    r.trace_id,
                    r.turn_index,
                    r.step_index,
                    to_text(r.created_at),
                    r.model_dump_json(),
                ]
                for r in records
            ],
        )

    def list_records(self, trace_id: str) -> list[ExecutionRecord]:
        rows = self._db.all(
            "SELECT payload FROM execution_records WHERE trace_id = ? "
            "ORDER BY turn_index, step_index, created_at",
            [trace_id],
        )
        return [ExecutionRecord.model_validate_json(r["payload"]) for r in rows]

    def count(self, table: str) -> int:
        """Row count of one known table (tests and health checks)."""
        statements = {
            "sessions": "SELECT count(*) FROM sessions",
            "login_challenges": "SELECT count(*) FROM login_challenges",
            "login_failures": "SELECT count(*) FROM login_failures",
            "confirmation_tokens": "SELECT count(*) FROM confirmation_tokens",
            "disputes": "SELECT count(*) FROM disputes",
            "card_blocks": "SELECT count(*) FROM card_blocks",
            "handoffs": "SELECT count(*) FROM handoffs",
            "execution_records": "SELECT count(*) FROM execution_records",
        }
        row = self._db.one(statements[table])
        return int(row[0]) if row is not None else 0
