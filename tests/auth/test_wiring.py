"""Auth wired from the environment on the real fixture bank (DuckDB) and a temporary ops DB."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from bankagent.auth.settings import AuthConfigError
from bankagent.auth.wiring import create_auth_service
from bankagent.contracts.enums import Language
from bankagent.fixtures.builder import build
from bankagent.store.serving import ServingDB

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def fixture_bank(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("bank") / "bank_fixture.duckdb"
    _, problems = build(out=out)
    assert problems == []
    return out


def _env(fixture_bank: Path, tmp_path: Path, secret: str) -> dict[str, str]:
    return {
        "APP_SECRET_KEY": secret,
        "AUTH_EXPOSE_MOCK_OTP": "true",
        "SERVING_DB_PATH": str(fixture_bank),
        "OPS_DB_PATH": str(tmp_path / "runtime" / "ops.sqlite"),
    }


def test_serving_db_reads_the_minimal_profile(fixture_bank: Path) -> None:
    serving = ServingDB(fixture_bank)
    rafael = serving.customer("CUST-FX-004")
    assert rafael is not None
    assert (rafael.first_name, rafael.language, rafael.is_active) == ("Rafael", Language.PT, True)
    assert serving.customer("CUST-FX-999") is None
    assert serving.customer("x' OR '1'='1") is None
    assert [c.customer_id for c in serving.active_customers(2)] == ["CUST-FX-001", "CUST-FX-002"]


def test_login_end_to_end_on_the_fixture_bank(
    fixture_bank: Path, tmp_path: Path, secret: str
) -> None:
    service = create_auth_service(_env(fixture_bank, tmp_path, secret), clock=lambda: NOW)
    personas = service.personas()
    assert len(personas) == 8
    rafael = next(p for p in personas if p.first_name == "Rafael")
    challenge = service.start_login(rafael.persona_id)
    assert challenge.mock_otp is not None
    result = service.verify_otp(challenge.challenge_id, challenge.mock_otp)
    session = service.authenticate(result.token)
    assert session.customer_id == "CUST-FX-004"
    assert session.language == Language.PT
    assert (tmp_path / "runtime" / "ops.sqlite").exists()


def test_missing_secret_or_serving_db_fails_closed(
    fixture_bank: Path, tmp_path: Path, secret: str
) -> None:
    env = _env(fixture_bank, tmp_path, secret)
    with pytest.raises(AuthConfigError):
        create_auth_service({**env, "APP_SECRET_KEY": ""})
    with pytest.raises(FileNotFoundError, match="poe fixtures"):
        create_auth_service({**env, "SERVING_DB_PATH": str(tmp_path / "missing.duckdb")})


def test_serving_db_reports_its_data_mode(fixture_bank: Path) -> None:
    assert ServingDB(fixture_bank).data_mode() == "synthetic"


def test_exposed_mock_otp_is_refused_on_a_curated_serving_db(
    fixture_bank: Path, tmp_path: Path, secret: str
) -> None:
    # DATA_MODE is only a declaration: here it says synthetic, but the DB itself is curated.
    curated = tmp_path / "bank_curated.duckdb"
    shutil.copy(fixture_bank, curated)
    with duckdb.connect(str(curated)) as con:
        con.execute(
            "UPDATE _serving_metadata SET value = ? WHERE key = ?", ["curated", "data_mode"]
        )
    env = {**_env(fixture_bank, tmp_path, secret), "SERVING_DB_PATH": str(curated)}
    with pytest.raises(AuthConfigError, match="only allowed with synthetic"):
        create_auth_service({**env, "DATA_MODE": "synthetic"})
    # Without the exposed OTP the curated DB is accepted, and no code is returned.
    service = create_auth_service(
        {**env, "DATA_MODE": "curated", "AUTH_EXPOSE_MOCK_OTP": "false"}, clock=lambda: NOW
    )
    assert service.start_login(service.personas()[0].persona_id).mock_otp is None


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM _serving_metadata WHERE key = 'data_mode'",  # row missing
        "DROP TABLE _serving_metadata",  # table missing
    ],
)
def test_serving_db_without_a_data_mode_is_not_treated_as_synthetic(
    fixture_bank: Path, tmp_path: Path, secret: str, statement: str
) -> None:
    # Regression guard (PR #41 review): falling back to "synthetic" here would silently
    # re-open the exposed-OTP hole on a serving DB of unknown origin.
    unlabeled = tmp_path / "bank_unlabeled.duckdb"
    shutil.copy(fixture_bank, unlabeled)
    with duckdb.connect(str(unlabeled)) as con:
        con.execute(statement)
    assert ServingDB(unlabeled).data_mode() == "unknown"
    env = {**_env(fixture_bank, tmp_path, secret), "SERVING_DB_PATH": str(unlabeled)}
    with pytest.raises(AuthConfigError, match="only allowed with synthetic"):
        create_auth_service(env)
    # It still starts when the OTP is not exposed.
    assert create_auth_service({**env, "AUTH_EXPOSE_MOCK_OTP": "false"}, clock=lambda: NOW)
