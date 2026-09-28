"""The generated JSON Schemas in docs/contracts must match the code."""

from __future__ import annotations

import json
from pathlib import Path

from bankagent.contracts.export import DEFAULT_OUT, check_all, render_all, write_all


def test_render_is_deterministic() -> None:
    assert render_all() == render_all()


def test_committed_schemas_are_current() -> None:
    assert check_all(DEFAULT_OUT) == [], "run `uv run poe contracts` and commit docs/contracts/"


def test_check_detects_stale_missing_and_unexpected_files(tmp_path: Path) -> None:
    write_all(tmp_path)
    assert check_all(tmp_path) == []

    (tmp_path / "Session.schema.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "CardView.schema.json").unlink()
    (tmp_path / "Old.schema.json").write_text("{}\n", encoding="utf-8")
    problems = check_all(tmp_path)
    assert "stale: Session.schema.json" in problems
    assert "missing: CardView.schema.json" in problems
    assert "unexpected: Old.schema.json" in problems


def test_serving_contract_export_lists_forbidden_columns() -> None:
    serving = json.loads(render_all()["serving_tables.json"])
    assert "is_fraud" in serving["forbidden_columns"]
    names = [table["name"] for table in serving["tables"]]
    assert "transactions_enriched" in names
