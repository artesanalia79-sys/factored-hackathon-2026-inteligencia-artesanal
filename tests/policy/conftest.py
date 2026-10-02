"""Shared fixtures for the policy engine: the real fixture bank (DuckDB)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankagent.fixtures.builder import build
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
