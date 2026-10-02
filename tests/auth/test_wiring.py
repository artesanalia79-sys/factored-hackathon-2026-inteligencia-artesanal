"""Auth wired from the environment on the real fixture bank (DuckDB) and a temporary ops DB."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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
