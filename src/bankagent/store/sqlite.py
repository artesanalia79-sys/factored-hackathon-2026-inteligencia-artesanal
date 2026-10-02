"""SQLite connection, schema and transactions shared by the operational store classes.

`Database` owns the single connection of a process (thread-safe through one re-entrant lock),
creates the schema and exposes parameterized `one` / `all` / `write` plus an atomic, re-entrant
`transaction()`. `OpsStore` (customer-facing) and `HandoffConsole` (human agents) are thin
classes over it, so the reads a tool must never use live on a different class.

File databases use WAL so several processes (uvicorn workers) can share the file; every write
transaction starts with ``BEGIN IMMEDIATE`` and waits up to 5 s for the write lock.
Timestamps are fixed-width UTC ISO-8601 text, so string comparison is chronological.
Owner: Juan José (T7).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    # No foreign key to `sessions`: the evaluation harness issues tokens for sessions it builds
    # itself, without a login. The token is bound to its session id by `consume`.
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
        UNIQUE (customer_id, idempotency_key),
        UNIQUE (customer_id, transaction_id)
    )""",
    """CREATE TABLE IF NOT EXISTS card_blocks (
        block_id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        blocked_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        UNIQUE (customer_id, idempotency_key),
        UNIQUE (customer_id, product_id)
    )""",
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
    """The operational store is unusable or refused a write."""


def to_text(value: datetime) -> str:
    """Fixed-width UTC timestamp. Naive datetimes are refused rather than guessed."""
    if value.tzinfo is None:
        raise ValueError("naive datetimes are not accepted; pass an aware UTC datetime")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def from_text(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class Database:
    """One SQLite connection per process, safe across threads and across processes."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        in_memory = self._path == ":memory:"
        if not in_memory:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        # isolation_level=None: transactions are explicit (see `transaction`).
        self._con = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA busy_timeout = 5000")  # ms to wait for another writer
        if not in_memory:
            self._con.execute("PRAGMA journal_mode = WAL")
        self._migrate()

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

    def __enter__(self) -> Database:
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

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._con.execute(sql, params).fetchone()

    def all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    def write(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """One statement in its own transaction (or the enclosing one)."""
        with self.transaction():
            return self._con.execute(sql, params)

    def write_many(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        with self.transaction():
            self._con.executemany(sql, rows)
