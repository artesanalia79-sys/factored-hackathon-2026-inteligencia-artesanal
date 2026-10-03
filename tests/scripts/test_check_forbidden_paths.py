"""The pre-commit guard blocks data, organizer files, dotenv files and credentials.

Fake credentials are assembled at runtime so this file never contains a secret-looking literal.
"""

from __future__ import annotations

import importlib.util
import random
import string
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load("check_forbidden_paths", "scripts/hooks/check_forbidden_paths.py")
RNG = random.Random(0)


def _fake_aws_key_id() -> str:
    return "AK" + "IA" + "".join(RNG.choices(string.ascii_uppercase + string.digits, k=16))


def _fake_aws_secret() -> str:
    return "".join(RNG.choices(string.ascii_letters + string.digits + "/+", k=40))


def _fake_openai_key() -> str:
    return "sk-" + "proj-" + "".join(RNG.choices(string.ascii_letters + string.digits, k=40))


@pytest.mark.parametrize(
    "path",
    [
        "private/Latam_bank_complete_data_dictionary.txt",
        "private/notes.md",
        "data/bronze/transactions.parquet",
        "data/fixtures/bank_fixture.duckdb",
        "eval/heldout/case-001.yaml",
        "mlruns/0/meta.yaml",
        "some/dir/warehouse.duckdb",
        "export.parquet",
        "ops.sqlite",
        "LATAM_Bank_Complete_Data_Dictionary.pdf",
        "Factored AI & Data Hackathon 2026.docx",
        ".env",
        ".env.local",
        "web/.env.production",
        "./data/raw/x.csv",
        "data\\raw\\x.csv",
    ],
)
def test_forbidden_paths_are_blocked(path: str) -> None:
    assert guard.check_path(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "src/bankagent/contracts/domain.py",
        "docs/challenge/data_dictionary.md",
        "tests/fixtures/bank/cards.yaml",
        "eval/dev/case-001.yaml",
        "src/bankagent/fixtures/builder.py",
    ],
)
def test_allowed_paths_pass(path: str) -> None:
    assert guard.check_path(path) is None


def test_secret_patterns_are_detected() -> None:
    assert guard.scan_text(f"key = {_fake_aws_key_id()}") == ["aws_access_key_id"]
    assert guard.scan_text(f"aws_secret_access_key = {_fake_aws_secret()}") == [
        "aws_secret_access_key"
    ]
    assert guard.scan_text(f"OPENAI_API_KEY={_fake_openai_key()}") == ["openai_api_key"]
    assert guard.scan_text("-----BEGIN " + "RSA PRIVATE KEY-----") == ["private_key_block"]
    assert guard.scan_text("nothing to see here: AKIA is a prefix only") == []


def test_local_env_values_are_detected(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    bucket = "bucket-" + "".join(RNG.choices(string.ascii_lowercase, k=12))
    env.write_text(
        f"S3_BUCKET={bucket}\nAPP_SECRET_KEY=short\nLLM_PROVIDER=stub\n", encoding="utf-8"
    )
    values = guard.load_sensitive_env_values(env)
    assert values == {"S3_BUCKET": bucket}  # short values and non-sensitive keys are ignored
    assert guard.scan_env_leaks(f"s3://{bucket}/transactions", values) == ["S3_BUCKET"]
    assert guard.scan_env_leaks("s3://other/transactions", values) == []


@pytest.mark.parametrize("key", ["DEMO_ACCESS_CODE", "LLM_API_KEY"])
def test_the_demo_access_code_and_the_compat_key_are_guarded_too(tmp_path: Path, key: str) -> None:
    # Neither has a recognizable prefix, so only the comparison with the local value finds it.
    env = tmp_path / ".env"
    value = "".join(RNG.choices(string.ascii_lowercase + string.digits, k=16))
    env.write_text(f"{key}={value}\n", encoding="utf-8")
    values = guard.load_sensitive_env_values(env)
    assert guard.scan_env_leaks(f"Use the code {value} to log in.", values) == [key]


def test_main_blocks_a_staged_secret_and_passes_clean_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    leaked = tmp_path / "config.py"
    leaked.write_text(f'AWS_KEY = "{_fake_aws_key_id()}"\n', encoding="utf-8")
    clean = tmp_path / "clean.py"
    clean.write_text("print('ok')\n", encoding="utf-8")

    assert guard.main([str(clean.name)]) == 0
    assert guard.main([leaked.name, "private/x.txt"]) == 1
    err = capsys.readouterr().err
    assert "aws_access_key_id" in err
    assert "forbidden location" in err
    assert "AK" + "IA" not in err  # the value itself is never printed
