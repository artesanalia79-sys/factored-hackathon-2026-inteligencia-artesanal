"""Shared fixtures for the policy engine: the real fixture bank (DuckDB) and a temp ops store."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pytest

from bankagent.fixtures.builder import build
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB


@pytest.fixture(scope="session")
def fixture_bank(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("policy-bank") / "bank_fixture.duckdb"
    _, problems = build(out=out)
    assert problems == []
    return out


@pytest.fixture
def serving(fixture_bank: Path) -> ServingDB:
    return ServingDB(fixture_bank)


@pytest.fixture
def ops_store(tmp_path: Path) -> Iterator[OpsStore]:
    with OpsStore(tmp_path / "ops.sqlite") as opened:
        yield opened


@pytest.fixture
def altered_bank(fixture_bank: Path, tmp_path: Path) -> Callable[..., ServingDB]:
    """A copy of the fixture bank changed by one parameterized statement."""

    def alter(statement: str, parameters: Sequence[Any] = ()) -> ServingDB:
        copy = tmp_path / f"bank_altered_{len(list(tmp_path.glob('bank_altered_*')))}.duckdb"
        shutil.copy(fixture_bank, copy)
        with duckdb.connect(str(copy)) as con:
            con.execute(statement, parameters)
        return ServingDB(copy)

    return alter
