"""Build the authentication service from the environment (used by the app in T13).

Paths default to the values in ``.env.example``: the synthetic fixture bank and a runtime
SQLite file under ``data/`` (gitignored). Nothing here reads or prints ``.env``; the process
environment must already carry ``APP_SECRET_KEY``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from bankagent.auth.service import AuthService
from bankagent.auth.settings import AuthSettings
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERVING_DB = ROOT / "data" / "fixtures" / "bank_fixture.duckdb"
DEFAULT_OPS_DB = ROOT / "data" / "runtime" / "ops.sqlite"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _path(env: Mapping[str, str], key: str, default: Path) -> Path:
    value = env.get(key)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def create_auth_service(
    environ: Mapping[str, str] | None = None,
    *,
    clock: Callable[[], datetime] = utc_now,
    store: OpsStore | None = None,
) -> AuthService:
    """Auth service on the configured serving DB and ops store.

    Pass ``store`` to share one ``OpsStore`` with the tools; otherwise it is opened at
    ``OPS_DB_PATH``.
    """
    env = os.environ if environ is None else environ
    return AuthService(
        settings=AuthSettings.from_env(env),
        store=store or OpsStore(_path(env, "OPS_DB_PATH", DEFAULT_OPS_DB)),
        customers=ServingDB(_path(env, "SERVING_DB_PATH", DEFAULT_SERVING_DB)),
        clock=clock,
    )
