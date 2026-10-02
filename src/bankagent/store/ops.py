"""Operational store (SQLite): everything the agent writes or needs to remember between turns.

Tables: sessions, login challenges and failures (T7 auth), confirmation tokens, disputes, card
blocks, handoffs and execution records (written by the T8 tools and the T13 orchestrator).

Rules that hold here so callers cannot get them wrong:

- Every statement is parameterized; no SQL is ever built from values.
- Disputes, card blocks and handoffs are scoped by ``customer_id`` and idempotent by
  ``(customer_id, idempotency_key)``: a replay returns the stored row and ``created=False``.
- Confirmation tokens and login challenges are consumed with one conditional ``UPDATE``, so a
  token can be used once even under concurrent calls.
- ``transaction()`` groups several calls atomically (a tool marks its token used and writes the
  dispute in the same transaction).

Timestamps are stored as fixed-width UTC ISO-8601 text, so string comparison is chronological.
Money is stored as text and read back as ``Decimal``. Owner: Victor (T7).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bankagent.contracts.domain import CardBlockEvent, ConfirmationToken, DisputeCase, Session
from bankagent.contracts.enums import ActionType
from bankagent.contracts.handoff import HandoffPacket
from bankagent.contracts.records import ExecutionRecord

SCHEMA_VERSION = 1

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)",
    """CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        language TEXT,
        revoked_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS login_challenges (
        challenge_id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL,
        otp_hash TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL,
        consumed_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS login_failures (
        failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id TEXT NOT NULL,
        failed_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS login_failures_customer ON login_failures (customer_id, failed_at)",
    """CREATE TABLE IF NOT EXISTS confirmation_tokens (
        token_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        action TEXT NOT NULL,
        args_hash TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS disputes (
        dispute_id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL,
        transaction_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        UNIQUE (customer_id, idempotency_key)
    )""",
    "CREATE INDEX IF NOT EXISTS disputes_transaction ON disputes (customer_id, transaction_id)",
    """CREATE TABLE IF NOT EXISTS card_blocks (
        block_id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        blocked_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        UNIQUE (customer_id, idempotency_key)
    )""",
    "CREATE INDEX IF NOT EXISTS card_blocks_product ON card_blocks (customer_id, product_id)",
    """CREATE TABLE IF NOT EXISTS handoffs (
        handoff_id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        UNIQUE (customer_id, idempotency_key)
    )""",
    """CREATE TABLE IF NOT EXISTS execution_records (
        record_id TEXT PRIMARY KEY,
        trace_id TEXT NOT NULL,
        turn_index INTEGER NOT NULL,
        step_index INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        payload TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS execution_records_trace ON execution_records (trace_id)",
)


class OpsStoreError(RuntimeError):
    """The operational store is unusable (wrong schema version, closed, corrupt)."""


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


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("naive datetimes are not accepted; pass an aware UTC datetime")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class OpsStore:
    """SQLite-backed operational store. One instance per process; safe across threads."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        # isolation_level=None: transactions are explicit (see `transaction`).
        self._con = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        self._con.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    # -- lifecycle ---------------------------------------------------------

    def _migrate(self) -> None:
        with self.transaction():
            for statement in _SCHEMA:
                self._con.execute(statement)
            row = self._con.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self._con.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", [SCHEMA_VERSION]
                )
            elif row["version"] != SCHEMA_VERSION:
                raise OpsStoreError(
                    f"ops store schema version {row['version']} != {SCHEMA_VERSION}; "
                    "delete the file at OPS_DB_PATH to recreate it"
                )

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def __enter__(self) -> OpsStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Atomic block. Re-entrant: nested blocks join the outer transaction."""
        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self._con.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outermost:
                    self._con.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outermost:
                    self._con.execute("COMMIT")

    def _one(self, sql: str, params: Sequence[Any]) -> sqlite3.Row | None:
        with self._lock:
            return self._con.execute(sql, params).fetchone()

    def _all(self, sql: str, params: Sequence[Any]) -> list[sqlite3.Row]:
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    def _write(self, sql: str, params: Sequence[Any]) -> int:
        with self.transaction():
            return self._con.execute(sql, params).rowcount

    # -- sessions ----------------------------------------------------------

    def save_session(self, session: Session) -> None:
        self._write(
            "INSERT INTO sessions (session_id, customer_id, issued_at, expires_at, language) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                session.session_id,
                session.customer_id,
                _ts(session.issued_at),
                _ts(session.expires_at),
                session.language.value if session.language else None,
            ],
        )

    def get_session(self, session_id: str) -> Session | None:
        """The stored session unless it was revoked. Expiry is the caller's check."""
        row = self._one(
            "SELECT session_id, customer_id, issued_at, expires_at, language FROM sessions "
            "WHERE session_id = ? AND revoked_at IS NULL",
            [session_id],
        )
        if row is None:
            return None
        return Session.model_validate(dict(row))

    def revoke_session(self, session_id: str, now: datetime) -> bool:
        changed = self._write(
            "UPDATE sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
            [_ts(now), session_id],
        )
        return changed == 1

    # -- login challenges --------------------------------------------------

    def create_challenge(self, challenge: LoginChallenge) -> None:
        self._write(
            "INSERT INTO login_challenges (challenge_id, customer_id, otp_hash, issued_at, "
            "expires_at, attempts, max_attempts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                challenge.challenge_id,
                challenge.customer_id,
                challenge.otp_hash,
                _ts(challenge.issued_at),
                _ts(challenge.expires_at),
                challenge.attempts,
                challenge.max_attempts,
            ],
        )

    def get_challenge(self, challenge_id: str) -> LoginChallenge | None:
        row = self._one(
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
            consumed_at=_dt(row["consumed_at"]),
        )

    def reserve_attempt(self, challenge_id: str, now: datetime) -> bool:
        """Spend one of the challenge's attempts before the code is compared.

        One conditional UPDATE, so concurrent guesses cannot exceed ``max_attempts``: each one
        either gets a distinct attempt or gets False. A correct code also spends an attempt;
        that is harmless because the challenge is consumed right after.
        """
        changed = self._write(
            "UPDATE login_challenges SET attempts = attempts + 1 WHERE challenge_id = ? "
            "AND consumed_at IS NULL AND attempts < max_attempts "
            "AND issued_at <= ? AND expires_at > ?",
            [challenge_id, _ts(now), _ts(now)],
        )
        return changed == 1

    def record_login_failure(self, customer_id: str, now: datetime) -> None:
        """Count one wrong code against the customer (lockout across challenges)."""
        self._write(
            "INSERT INTO login_failures (customer_id, failed_at) VALUES (?, ?)",
            [customer_id, _ts(now)],
        )

    def consume_challenge(self, challenge_id: str, now: datetime) -> bool:
        """Mark an open challenge used. False when it was already used or has expired."""
        changed = self._write(
            "UPDATE login_challenges SET consumed_at = ? WHERE challenge_id = ? "
            "AND consumed_at IS NULL AND issued_at <= ? AND expires_at > ?",
            [_ts(now), challenge_id, _ts(now), _ts(now)],
        )
        return changed == 1

    def failures_since(self, customer_id: str, since: datetime) -> list[datetime]:
        """Wrong-code timestamps of a customer at or after ``since``, oldest first."""
        rows = self._all(
            "SELECT failed_at FROM login_failures WHERE customer_id = ? AND failed_at >= ? "
            "ORDER BY failed_at",
            [customer_id, _ts(since)],
        )
        return [datetime.fromisoformat(r["failed_at"]) for r in rows]

    def clear_failures(self, customer_id: str) -> None:
        self._write("DELETE FROM login_failures WHERE customer_id = ?", [customer_id])

    def purge_login_data(self, before: datetime) -> None:
        """Delete challenges that expired, and failures recorded, before ``before``."""
        with self.transaction():
            self._con.execute("DELETE FROM login_challenges WHERE expires_at < ?", [_ts(before)])
            self._con.execute("DELETE FROM login_failures WHERE failed_at < ?", [_ts(before)])

    # -- confirmation tokens -----------------------------------------------

    def save_confirmation_token(self, token: ConfirmationToken) -> None:
        self._write(
            "INSERT INTO confirmation_tokens (token_id, session_id, action, args_hash, "
            "issued_at, expires_at, used_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                token.token_id,
                token.session_id,
                token.action.value,
                token.args_hash,
                _ts(token.issued_at),
                _ts(token.expires_at),
                _ts(token.used_at) if token.used_at else None,
            ],
        )

    def get_confirmation_token(self, token_id: str) -> ConfirmationToken | None:
        row = self._one(
            "SELECT token_id, session_id, action, args_hash, issued_at, expires_at, used_at "
            "FROM confirmation_tokens WHERE token_id = ?",
            [token_id],
        )
        return None if row is None else ConfirmationToken.model_validate(dict(row))

    def consume_confirmation_token(
        self, token_id: str, *, session_id: str, action: ActionType, args_hash: str, now: datetime
    ) -> bool:
        """Use the token for exactly this session, action and arguments. True only once.

        One conditional UPDATE: a missing, foreign, mismatched, expired or already used token
        changes no row. Call it inside the same ``transaction()`` as the write it authorizes.
        """
        changed = self._write(
            "UPDATE confirmation_tokens SET used_at = ? WHERE token_id = ? AND session_id = ? "
            "AND action = ? AND args_hash = ? AND used_at IS NULL "
            "AND issued_at <= ? AND expires_at > ?",
            [_ts(now), token_id, session_id, action.value, args_hash, _ts(now), _ts(now)],
        )
        return changed == 1

    # -- disputes ----------------------------------------------------------

    def insert_dispute(self, customer_id: str, dispute: DisputeCase) -> tuple[DisputeCase, bool]:
        """Store the dispute, or return the one already stored for this idempotency key."""
        with self.transaction():
            existing = self._one(
                "SELECT payload FROM disputes WHERE customer_id = ? AND idempotency_key = ?",
                [customer_id, dispute.idempotency_key],
            )
            if existing is not None:
                return DisputeCase.model_validate_json(existing["payload"]), False
            self._con.execute(
                "INSERT INTO disputes (dispute_id, customer_id, transaction_id, idempotency_key, "
                "created_at, payload) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    dispute.dispute_id,
                    customer_id,
                    dispute.transaction_id,
                    dispute.idempotency_key,
                    _ts(dispute.created_at),
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
        """A dispute of this customer by id, or the oldest one on a transaction.

        Another customer's dispute returns ``None``, exactly like a missing one.
        """
        if (dispute_id is None) == (transaction_id is None):
            raise ValueError("provide exactly one of dispute_id or transaction_id")
        if dispute_id is not None:
            row = self._one(
                "SELECT payload FROM disputes WHERE customer_id = ? AND dispute_id = ?",
                [customer_id, dispute_id],
            )
        else:
            row = self._one(
                "SELECT payload FROM disputes WHERE customer_id = ? AND transaction_id = ? "
                "ORDER BY created_at, dispute_id LIMIT 1",
                [customer_id, transaction_id],
            )
        return None if row is None else DisputeCase.model_validate_json(row["payload"])

    # -- card blocks -------------------------------------------------------

    def insert_card_block(
        self, customer_id: str, block: CardBlockEvent
    ) -> tuple[CardBlockEvent, bool]:
        with self.transaction():
            existing = self._one(
                "SELECT payload FROM card_blocks WHERE customer_id = ? AND idempotency_key = ?",
                [customer_id, block.idempotency_key],
            )
            if existing is not None:
                return CardBlockEvent.model_validate_json(existing["payload"]), False
            self._con.execute(
                "INSERT INTO card_blocks (block_id, customer_id, product_id, idempotency_key, "
                "blocked_at, payload) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    block.block_id,
                    customer_id,
                    block.product_id,
                    block.idempotency_key,
                    _ts(block.blocked_at),
                    block.model_dump_json(),
                ],
            )
        return block, True

    def get_card_block(self, customer_id: str, product_id: str) -> CardBlockEvent | None:
        """The first block of this customer's card, if any (``None`` for foreign cards too)."""
        row = self._one(
            "SELECT payload FROM card_blocks WHERE customer_id = ? AND product_id = ? "
            "ORDER BY blocked_at, block_id LIMIT 1",
            [customer_id, product_id],
        )
        return None if row is None else CardBlockEvent.model_validate_json(row["payload"])

    # -- handoffs ----------------------------------------------------------

    def insert_handoff(
        self, packet: HandoffPacket, idempotency_key: str
    ) -> tuple[HandoffPacket, bool]:
        with self.transaction():
            existing = self._one(
                "SELECT payload FROM handoffs WHERE customer_id = ? AND idempotency_key = ?",
                [packet.customer_id, idempotency_key],
            )
            if existing is not None:
                return HandoffPacket.model_validate_json(existing["payload"]), False
            self._con.execute(
                "INSERT INTO handoffs (handoff_id, customer_id, idempotency_key, created_at, "
                "payload) VALUES (?, ?, ?, ?, ?)",
                [
                    packet.handoff_id,
                    packet.customer_id,
                    idempotency_key,
                    _ts(packet.created_at),
                    packet.model_dump_json(),
                ],
            )
        return packet, True

    def get_handoff(self, handoff_id: str) -> HandoffPacket | None:
        """A stored handoff by id. For the human-agent console, not for customer sessions."""
        row = self._one("SELECT payload FROM handoffs WHERE handoff_id = ?", [handoff_id])
        return None if row is None else HandoffPacket.model_validate_json(row["payload"])

    def list_handoffs(self, limit: int = 50) -> list[HandoffPacket]:
        """Newest handoffs first (human-agent queue)."""
        rows = self._all(
            "SELECT payload FROM handoffs ORDER BY created_at DESC, handoff_id LIMIT ?", [limit]
        )
        return [HandoffPacket.model_validate_json(r["payload"]) for r in rows]

    # -- execution records -------------------------------------------------

    def append_records(self, records: Sequence[ExecutionRecord]) -> None:
        with self.transaction():
            self._con.executemany(
                "INSERT INTO execution_records (record_id, trace_id, turn_index, step_index, "
                "created_at, payload) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    [
                        r.record_id,
                        r.trace_id,
                        r.turn_index,
                        r.step_index,
                        _ts(r.created_at),
                        r.model_dump_json(),
                    ]
                    for r in records
                ],
            )

    def list_records(self, trace_id: str) -> list[ExecutionRecord]:
        rows = self._all(
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
            "confirmation_tokens": "SELECT count(*) FROM confirmation_tokens",
            "disputes": "SELECT count(*) FROM disputes",
            "card_blocks": "SELECT count(*) FROM card_blocks",
            "handoffs": "SELECT count(*) FROM handoffs",
            "execution_records": "SELECT count(*) FROM execution_records",
        }
        row = self._one(statements[table], [])
        return int(row[0]) if row is not None else 0
