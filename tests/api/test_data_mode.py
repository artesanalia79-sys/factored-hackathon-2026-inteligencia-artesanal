"""The production app and ``DATA_MODE`` (T19): it serves only the data the deployment declares.

``create_default_app`` is what ``poe serve`` and the image run. Before T19 a serving DB that
recorded ``curated`` started under ``DATA_MODE=synthetic`` as long as the mock OTP was off, and
its persona picker then listed organizer customers. Now the app refuses to start.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from bankagent.api.wiring import create_default_app
from bankagent.auth.settings import AuthConfigError
from bankagent.fixtures.builder import build
from bankagent.store.selection import ServingConfigError

SECRET = "test-only-" + "".join(chr(97 + index % 26) for index in range(40))


@pytest.fixture(scope="module")
def fixture_bank(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("t19-mode") / "bank_fixture.duckdb"
    _, problems = build(out=out)
    assert problems == []
    return out


@pytest.fixture
def curated_bank(fixture_bank: Path, tmp_path: Path) -> Path:
    """The fixture bank, labelled as its curated build would label it."""
    copy = tmp_path / "bank_curated.duckdb"
    shutil.copy(fixture_bank, copy)
    with duckdb.connect(str(copy)) as con:
        con.execute("UPDATE _serving_metadata SET value = 'curated' WHERE key = 'data_mode'")
    return copy


def _env(serving: Path, tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "APP_SECRET_KEY": SECRET,
        "SERVING_DB_PATH": str(serving),
        "OPS_DB_PATH": str(tmp_path / "runtime" / "ops.sqlite"),
        "WEB_DIST_DIR": str(tmp_path / "no-ui"),
        "LLM_PROVIDER": "stub",
        **extra,
    }


def test_the_public_configuration_starts_on_the_fixture_bank(
    fixture_bank: Path, tmp_path: Path
) -> None:
    env = _env(fixture_bank, tmp_path, DATA_MODE="synthetic", AUTH_EXPOSE_MOCK_OTP="true")
    client = TestClient(create_default_app(env))
    assert client.get("/ready").json() == {"status": "ready"}
    assert len(client.get("/api/auth/personas").json()) == 8


@pytest.mark.parametrize("declared", [{}, {"DATA_MODE": "synthetic"}])
def test_organizer_data_never_starts_as_synthetic_even_with_the_otp_hidden(
    curated_bank: Path, tmp_path: Path, declared: dict[str, str]
) -> None:
    env = _env(curated_bank, tmp_path, AUTH_EXPOSE_MOCK_OTP="false", **declared)
    with pytest.raises(ServingConfigError, match="records 'curated'"):
        create_default_app(env)
    # Refused before anything was opened for writing.
    assert not (tmp_path / "runtime" / "ops.sqlite").exists()


def test_curated_mode_never_runs_on_the_personas(fixture_bank: Path, tmp_path: Path) -> None:
    env = _env(fixture_bank, tmp_path, DATA_MODE="curated", AUTH_EXPOSE_MOCK_OTP="false")
    with pytest.raises(ServingConfigError, match="records 'synthetic'"):
        create_default_app(env)


def test_curated_mode_starts_without_a_way_to_log_in(curated_bank: Path, tmp_path: Path) -> None:
    # The curated end-to-end run (`poe curated-e2e`) opens its sessions in code instead: no
    # flag, variable or channel ever returns the code on organizer data.
    env = _env(curated_bank, tmp_path, DATA_MODE="curated", AUTH_EXPOSE_MOCK_OTP="false")
    client = TestClient(create_default_app(env))
    assert client.get("/ready").json() == {"status": "ready"}
    persona = client.get("/api/auth/personas").json()[0]
    challenge = client.post("/api/auth/login", json={"persona_id": persona["persona_id"]})
    assert challenge.status_code == 200
    assert challenge.json()["mock_otp"] is None


def test_curated_mode_still_refuses_the_exposed_otp(curated_bank: Path, tmp_path: Path) -> None:
    env = _env(curated_bank, tmp_path, DATA_MODE="curated", AUTH_EXPOSE_MOCK_OTP="true")
    with pytest.raises(AuthConfigError, match="only allowed with synthetic"):
        create_default_app(env)


def test_the_environment_passed_in_is_the_only_one_read(
    fixture_bank: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The process says curated; the mapping the caller passes says synthetic and wins.
    monkeypatch.setenv("DATA_MODE", "curated")
    monkeypatch.setenv("SERVING_DB_PATH", str(tmp_path / "missing.duckdb"))
    env = _env(fixture_bank, tmp_path, DATA_MODE="synthetic")
    assert TestClient(create_default_app(env)).get("/ready").status_code == 200


def test_a_serving_db_that_breaks_the_contract_never_starts(
    fixture_bank: Path, tmp_path: Path
) -> None:
    broken = tmp_path / "bank_broken.duckdb"
    shutil.copy(fixture_bank, broken)
    with duckdb.connect(str(broken)) as con:
        con.execute("ALTER TABLE transactions_enriched ADD COLUMN is_fraud BOOLEAN")
    with pytest.raises(ServingConfigError, match="is_fraud: forbidden column"):
        create_default_app(_env(broken, tmp_path, DATA_MODE="synthetic"))
