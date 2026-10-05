"""Which serving DB the service opens, and what that file must prove before it is used (T19).

``DATA_MODE`` declares what the service serves: ``synthetic`` (the team's fixture personas, the
default and the only mode of the public deployment) or ``curated`` (the serving DB that
``uv run poe serving-build`` builds from organizer data, local only). It picks the default
file; ``SERVING_DB_PATH`` overrides the file, never the mode. Before the service reads a row,
the file must:

- hold what ``DATA_MODE`` declares, as its own build recorded it in ``_serving_metadata``. A
  curated file under ``synthetic`` would serve organizer data where everyone believes it serves
  personas; a synthetic file under ``curated`` would make a local check on organizer data
  silently check the personas instead. A file with no record (``unknown``) matches neither.
- fit the serving contract (``validate_serving_db``), so a file built before a contract change
  is refused at start instead of failing on the first read that meets the difference.

Errors name variables, tables and columns, never a value from a row.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import duckdb

from bankagent.contracts.enums import DataMode
from bankagent.contracts.serving import SERVING_CONTRACT_VERSION
from bankagent.store.serving import UNKNOWN_DATA_MODE, ServingDB

ROOT = Path(__file__).resolve().parents[3]
# Where `uv run poe fixtures` and `uv run poe serving-build` write their files.
SERVING_DBS: Mapping[DataMode, Path] = MappingProxyType(
    {
        DataMode.SYNTHETIC: ROOT / "data" / "fixtures" / "bank_fixture.duckdb",
        DataMode.CURATED: ROOT / "data" / "serving" / "bank_curated.duckdb",
    }
)


class ServingConfigError(RuntimeError):
    """The serving DB does not hold what ``DATA_MODE`` declares, or does not fit the contract.

    The service must not start.
    """


def declared_data_mode(env: Mapping[str, str]) -> DataMode:
    """``DATA_MODE``, ``synthetic`` when unset; anything else fails closed."""
    raw = env.get("DATA_MODE", "").strip().lower() or DataMode.SYNTHETIC.value
    try:
        return DataMode(raw)
    except ValueError:
        raise ServingConfigError("DATA_MODE must be 'synthetic' or 'curated'") from None


def serving_db_path(env: Mapping[str, str]) -> Path:
    """``SERVING_DB_PATH`` (relative to the repository root), else the file of ``DATA_MODE``."""
    configured = env.get("SERVING_DB_PATH", "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else ROOT / path
    return SERVING_DBS[declared_data_mode(env)]


def open_serving_db(env: Mapping[str, str]) -> ServingDB:
    """The serving DB of ``env``, once it holds the declared mode and fits the contract.

    Raises ``FileNotFoundError`` (with the command that builds the file) when it is missing and
    ``ServingConfigError`` when it cannot be read or cannot be trusted.
    """
    mode = declared_data_mode(env)
    serving = ServingDB(serving_db_path(env))
    try:
        recorded = serving.data_mode()
        problems = serving.contract_problems()
    except duckdb.Error as error:
        # Not a DuckDB database (or one this version cannot read). The engine's own message
        # quotes the path; only its kind is kept.
        raise ServingConfigError(
            f"the serving DB cannot be read as a DuckDB database ({type(error).__name__}): "
            "rebuild it with `uv run poe fixtures` (synthetic) or `uv run poe serving-build` "
            "(curated)"
        ) from None
    if recorded != mode.value:
        # A known name only: the metadata row is data, and a startup error must not echo it.
        shown = recorded if recorded in {*DataMode, UNKNOWN_DATA_MODE} else "an unknown value"
        raise ServingConfigError(
            f"DATA_MODE is '{mode.value}' but the serving DB records '{shown}': point "
            "SERVING_DB_PATH at a file built for that mode, or leave it empty for the default "
            "file of DATA_MODE"
        )
    if problems:
        listed = "; ".join(problems[:5]) + ("; ..." if len(problems) > 5 else "")
        raise ServingConfigError(
            f"the serving DB does not fit serving contract {SERVING_CONTRACT_VERSION} "
            f"({len(problems)} problems: {listed}); rebuild it with `uv run poe fixtures` "
            "(synthetic) or `uv run poe serving-build` (curated)"
        )
    return serving
