"""Shared fixtures for the policy engine: the real fixture bank (DuckDB), a writable copy of it
and a temp ops store."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

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
def bank_copy(fixture_bank: Path, tmp_path: Path) -> Path:
    """A writable copy of the fixture bank, for cases the committed personas do not cover."""
    copy = tmp_path / "bank_copy.duckdb"
    shutil.copyfile(fixture_bank, copy)
    return copy
