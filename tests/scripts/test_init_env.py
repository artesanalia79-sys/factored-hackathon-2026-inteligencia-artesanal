"""scripts/init_env.py rebuilds .env from the template without losing or printing local values.

Every value here is fake and every file lives in a temporary directory.
"""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = """# comment from the template
OPENAI_API_KEY=
LLM_PROVIDER=stub
APP_SECRET_KEY=
S3_BUCKET=
"""


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("init_env", ROOT / "scripts/init_env.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def init_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load()
    (tmp_path / ".env.example").write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(module, "EXAMPLE", tmp_path / ".env.example")
    monkeypatch.setattr(module, "TARGET", tmp_path / ".env")
    return module


def _values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.startswith("#"):
            values[key.strip()] = value
    return values


def test_a_new_env_gets_the_template_and_a_generated_secret(
    init_env: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert init_env.main([]) == 0
    values = _values(init_env.TARGET)
    assert values["LLM_PROVIDER"] == "stub"
    assert len(values["APP_SECRET_KEY"]) >= 48
    assert values["APP_SECRET_KEY"] not in capsys.readouterr().out  # names only, never values


def test_rerunning_keeps_every_local_value(
    init_env: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    # PR #29 audit: a key missing from the template was deleted, an `export` line was lost and
    # a spaced `KEY = value` line was reset to the template default.
    init_env.TARGET.write_text(
        "OPENAI_API_KEY=fake-openai-value-123456\n"
        "export S3_BUCKET=fake-bucket-abc\n"
        "LLM_PROVIDER = compat\n"
        "GROQ_API_KEY=fake-groq-value-123456\n"
        "export WAREHOUSE_PATH=/srv/fake-warehouse.duckdb\n",
        encoding="utf-8",
    )
    assert init_env.main([]) == 0
    text = init_env.TARGET.read_text(encoding="utf-8")
    values = _values(init_env.TARGET)
    assert values["OPENAI_API_KEY"] == "fake-openai-value-123456"
    assert values["S3_BUCKET"] == "fake-bucket-abc"
    assert values["LLM_PROVIDER"] == "compat"
    assert "GROQ_API_KEY=fake-groq-value-123456" in text
    assert "export WAREHOUSE_PATH=/srv/fake-warehouse.duckdb" in text
    out = capsys.readouterr().out
    assert "GROQ_API_KEY" in out
    assert "fake-" not in out

    assert init_env.main([]) == 0  # a second run changes nothing
    assert init_env.TARGET.read_text(encoding="utf-8") == text


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_the_env_file_is_readable_by_its_owner_only(init_env: ModuleType) -> None:
    init_env.TARGET.write_text("OPENAI_API_KEY=fake-openai-value-123456\n", encoding="utf-8")
    init_env.TARGET.chmod(0o664)
    assert init_env.main([]) == 0
    assert stat.S_IMODE(init_env.TARGET.stat().st_mode) == 0o600
