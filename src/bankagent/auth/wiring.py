"""Build the authentication service from the environment (used by the app in T13).

Paths default to the values in ``.env.example``: the serving DB of ``DATA_MODE``
(``bankagent.store.selection``) and a runtime SQLite file under ``data/`` (gitignored).
Nothing here reads or prints ``.env``; the process environment must already carry
``APP_SECRET_KEY``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from bankagent.auth.service import AuthService
from bankagent.auth.settings import AuthSettings
from bankagent.store.ops import OpsStore
from bankagent.store.selection import ROOT, open_serving_db
from bankagent.store.serving import ServingDB

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
    customers: ServingDB | None = None,
) -> AuthService:
    """Auth service on the configured serving DB and ops store.

    Pass ``store`` to share one ``OpsStore`` with the tools, and ``customers`` to share the
    serving DB the app already opened and checked (``open_serving_db``); otherwise each is
    opened from the environment, the serving DB with the same checks the app runs. Raises
    ``AuthConfigError`` when the mock OTP is exposed on data that is not synthetic, and
    ``ServingConfigError`` when a serving DB opened here does not hold what ``DATA_MODE``
    declares or does not fit the serving contract.
    """
    env = os.environ if environ is None else environ
    settings = AuthSettings.from_env(env)
    serving = customers if customers is not None else open_serving_db(env)
    # DATA_MODE is only a declaration; the serving DB records what it really holds.
    settings.require_safe_for(serving.data_mode())
    return AuthService(
        settings=settings,
        store=store or OpsStore(_path(env, "OPS_DB_PATH", DEFAULT_OPS_DB)),
        customers=serving,
        clock=clock,
    )
