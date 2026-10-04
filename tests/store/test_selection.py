"""Which serving DB the service opens, and the checks that file must pass first (T19)."""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from bankagent.contracts.enums import DataMode
from bankagent.fixtures.builder import build
from bankagent.store.selection import (
    ROOT,
    SERVING_DBS,
    ServingConfigError,
    declared_data_mode,
    open_serving_db,
    serving_db_path,
)
from bankagent.store.serving import ServingDB


@pytest.fixture(scope="module")
def fixture_bank(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("selection") / "bank_fixture.duckdb"
    _, problems = build(out=out)
    assert problems == []
    return out


def _copy(fixture_bank: Path, tmp_path: Path, statement: str, *params: str) -> Path:
    """A copy of the fixture bank with one statement applied (a label, a broken contract)."""
    copy = tmp_path / "bank_copy.duckdb"
    shutil.copy(fixture_bank, copy)
    with duckdb.connect(str(copy)) as con:
        con.execute(statement, list(params))
    return copy


RELABEL = "UPDATE _serving_metadata SET value = ? WHERE key = 'data_mode'"


@pytest.mark.parametrize(
    ("raw", "mode"),
    [
        (None, DataMode.SYNTHETIC),
        ("", DataMode.SYNTHETIC),
        ("  ", DataMode.SYNTHETIC),
        ("synthetic", DataMode.SYNTHETIC),
        (" Curated ", DataMode.CURATED),
        ("CURATED", DataMode.CURATED),
    ],
)
def test_data_mode_is_synthetic_unless_declared(raw: str | None, mode: DataMode) -> None:
    env = {} if raw is None else {"DATA_MODE": raw}
    assert declared_data_mode(env) is mode


@pytest.mark.parametrize("raw", ["production", "real", "curated2", "synthetic;curated"])
def test_an_unknown_data_mode_fails_closed_without_echoing_it(raw: str) -> None:
    with pytest.raises(ServingConfigError, match="DATA_MODE must be") as caught:
        declared_data_mode({"DATA_MODE": raw})
    assert raw not in str(caught.value)


def test_data_mode_picks_the_default_file_and_serving_db_path_overrides_it(
    tmp_path: Path,
) -> None:
    assert serving_db_path({}) == SERVING_DBS[DataMode.SYNTHETIC]
    assert serving_db_path({"DATA_MODE": "curated"}) == SERVING_DBS[DataMode.CURATED]
    assert (
        serving_db_path({"DATA_MODE": "curated", "SERVING_DB_PATH": " "})
        == (SERVING_DBS[DataMode.CURATED])
    )
    own = tmp_path / "elsewhere.duckdb"
    assert serving_db_path({"DATA_MODE": "curated", "SERVING_DB_PATH": str(own)}) == own
    # Relative to the repository root, like OPS_DB_PATH and WEB_DIST_DIR.
    relative = {"SERVING_DB_PATH": "data/fixtures/bank_fixture.duckdb"}
    assert serving_db_path(relative) == ROOT / "data" / "fixtures" / "bank_fixture.duckdb"


def test_the_curated_default_is_the_file_the_serving_build_writes() -> None:
    # One path: `poe serving-build` writes the file DATA_MODE=curated opens. The gold package
    # needs the `data` group, so this compares the text of its constant, not an import.
    gold = (ROOT / "src" / "bankagent" / "gold" / "build.py").read_text("utf-8")
    assert "DEFAULT_SERVING_DB = SERVING_DBS[DataMode.CURATED]" in gold
    assert SERVING_DBS[DataMode.CURATED] == ROOT / "data" / "serving" / "bank_curated.duckdb"
    assert SERVING_DBS[DataMode.SYNTHETIC] == ROOT / "data" / "fixtures" / "bank_fixture.duckdb"


def test_the_fixture_bank_opens_under_synthetic(fixture_bank: Path) -> None:
    serving = open_serving_db({"SERVING_DB_PATH": str(fixture_bank)})
    assert serving.data_mode() == "synthetic"
    assert serving.contract_problems() == []


def test_a_curated_file_opens_under_curated(fixture_bank: Path, tmp_path: Path) -> None:
    curated = _copy(fixture_bank, tmp_path, RELABEL, "curated")
    serving = open_serving_db({"DATA_MODE": "curated", "SERVING_DB_PATH": str(curated)})
    assert serving.data_mode() == "curated"


def test_organizer_data_is_refused_where_synthetic_is_declared(
    fixture_bank: Path, tmp_path: Path
) -> None:
    # The dangerous direction: the public image declares synthetic. A curated file there would
    # serve organizer data (the persona picker lists its customers) with no exposed OTP to
    # trip the auth check.
    curated = _copy(fixture_bank, tmp_path, RELABEL, "curated")
    for declared in ({}, {"DATA_MODE": "synthetic"}):
        with pytest.raises(ServingConfigError, match="records 'curated'"):
            open_serving_db({**declared, "SERVING_DB_PATH": str(curated)})


def test_personas_are_refused_where_curated_is_declared(fixture_bank: Path) -> None:
    # A local check on organizer data must never quietly run on the personas instead.
    with pytest.raises(ServingConfigError, match=r"DATA_MODE is 'curated' but .* 'synthetic'"):
        open_serving_db({"DATA_MODE": "curated", "SERVING_DB_PATH": str(fixture_bank)})


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM _serving_metadata WHERE key = 'data_mode'",
        "DROP TABLE _serving_metadata",
    ],
)
@pytest.mark.parametrize("mode", ["synthetic", "curated"])
def test_a_file_that_records_no_mode_matches_neither(
    fixture_bank: Path, tmp_path: Path, statement: str, mode: str
) -> None:
    unlabeled = _copy(fixture_bank, tmp_path, statement)
    with pytest.raises(ServingConfigError, match="records 'unknown'"):
        open_serving_db({"DATA_MODE": mode, "SERVING_DB_PATH": str(unlabeled)})


def test_an_unexpected_recorded_mode_is_not_echoed(fixture_bank: Path, tmp_path: Path) -> None:
    odd = _copy(fixture_bank, tmp_path, RELABEL, "CLI-0042 Juana")
    with pytest.raises(ServingConfigError, match="an unknown value") as caught:
        open_serving_db({"SERVING_DB_PATH": str(odd)})
    assert "Juana" not in str(caught.value)
    assert "CLI-0042" not in str(caught.value)


@pytest.mark.parametrize(
    ("statement", "problem"),
    [
        ("ALTER TABLE customer_profile_min ADD COLUMN email VARCHAR", "forbidden column"),
        ("CREATE TABLE extra_rows (id INTEGER)", "table not in the serving contract"),
        ("DROP TABLE agents_routing", "agents_routing: missing table"),
        (
            "UPDATE _serving_metadata SET value = '0.9.0' WHERE key = 'contract_version'",
            "contract_version 0.9.0",
        ),
    ],
)
def test_a_file_that_breaks_the_contract_is_refused(
    fixture_bank: Path, tmp_path: Path, statement: str, problem: str
) -> None:
    broken = _copy(fixture_bank, tmp_path, statement)
    assert any(problem in found for found in ServingDB(broken).contract_problems())
    with pytest.raises(ServingConfigError, match="does not fit serving contract") as caught:
        open_serving_db({"SERVING_DB_PATH": str(broken)})
    assert problem in str(caught.value)


@pytest.mark.parametrize("kind", ["text", "empty", "folder"])
def test_a_file_that_is_not_a_database_is_refused_without_quoting_its_path(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "secret-folder-name" / "bank.duckdb"
    path.parent.mkdir()
    if kind == "folder":
        path.mkdir()
    else:
        path.write_text("" if kind == "empty" else "this is not a database\n", encoding="utf-8")
    with pytest.raises(ServingConfigError, match="cannot be read as a DuckDB database") as caught:
        open_serving_db({"SERVING_DB_PATH": str(path)})
    assert "secret-folder-name" not in str(caught.value)
    assert caught.value.__cause__ is None  # the engine's message, which quotes the path, is dropped
    assert caught.value.__suppress_context__


def test_a_missing_file_names_the_command_that_builds_it(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="poe serving-build"):
        open_serving_db({"DATA_MODE": "curated", "SERVING_DB_PATH": str(tmp_path / "no.duckdb")})


def test_an_unknown_mode_is_refused_before_any_file_is_opened(tmp_path: Path) -> None:
    # The mode is checked first: a typo never falls back to a default file.
    with pytest.raises(ServingConfigError, match="DATA_MODE must be"):
        open_serving_db({"DATA_MODE": "curatd", "SERVING_DB_PATH": str(tmp_path / "no.duckdb")})
