"""scripts/sync_skills.py mirrors skills strictly and detects drift."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_skills", ROOT / "scripts/sync_skills.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_skills = _load()


def _make_source(base: Path) -> Path:
    source = base / "source"
    (source / "alpha").mkdir(parents=True)
    (source / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\n", encoding="utf-8")
    (source / "beta").mkdir()
    (source / "beta" / "SKILL.md").write_text("---\nname: beta\n---\n", encoding="utf-8")
    return source


def test_sync_then_check_is_clean(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    mirror = tmp_path / "mirror"
    sync_skills.sync(source, mirror)
    assert sync_skills.diff(source, mirror) == []


def test_detects_missing_stale_and_modified_files(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    mirror = tmp_path / "mirror"
    sync_skills.sync(source, mirror)

    (mirror / "alpha" / "SKILL.md").write_text("edited in the mirror\n", encoding="utf-8")
    (mirror / "beta" / "SKILL.md").unlink()
    (mirror / "gamma").mkdir()
    (mirror / "gamma" / "SKILL.md").write_text("orphan\n", encoding="utf-8")

    problems = sync_skills.diff(source, mirror)
    assert any(p.startswith("differs in") and p.endswith("alpha/SKILL.md") for p in problems)
    assert any(p.startswith("missing in") and p.endswith("beta/SKILL.md") for p in problems)
    assert any(p.startswith("stale in") and p.endswith("gamma/SKILL.md") for p in problems)

    sync_skills.sync(source, mirror)  # strict mirror removes the orphan
    assert sync_skills.diff(source, mirror) == []


def test_repository_mirrors_are_in_sync() -> None:
    for mirror in sync_skills.MIRRORS:
        assert sync_skills.diff(sync_skills.SOURCE, mirror) == [], "run `uv run poe sync-skills`"
