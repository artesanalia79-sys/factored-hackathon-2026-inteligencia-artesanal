"""Skip test directories that need the optional `data` dependency group when it is not installed.

Locally, `uv sync` installs only the default groups, so `tests/ingest` and `tests/silver` are
skipped with a reason naming `uv sync --group data`. In CI (env `CI` truthy) nothing is skipped:
CI installs the data group, so a missing or broken module must fail collection there.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

# Test directory (relative to tests/) -> modules it needs from the `data` group.
_DATA_GROUP_DIRS: dict[str, tuple[str, ...]] = {
    "ingest": ("boto3", "pyarrow"),
    "silver": ("dbt.cli.main", "pyarrow"),
}


def pytest_collect_directory(path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    """Replace a data-group directory with a collector that skips it when a module is missing."""
    try:
        relative = path.relative_to(Path(__file__).parent).as_posix()
    except ValueError:
        return None
    modules = _DATA_GROUP_DIRS.get(relative)
    if modules is None or _in_ci():
        return None
    missing = _first_missing(modules)
    if missing is None:
        return None
    skipped = _SkippedDirectory.from_parent(parent, path=path)
    skipped.reason = f"{missing} not installed: run `uv sync --group data`"
    return skipped


class _SkippedDirectory(pytest.Directory):
    """A directory collector that reports one skip instead of importing its test modules."""

    reason: str = ""

    def collect(self) -> list[pytest.Item | pytest.Collector]:
        pytest.skip(self.reason)


def _in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() not in {"", "0", "false", "no"}


def _first_missing(modules: tuple[str, ...]) -> str | None:
    for module in modules:
        try:
            importlib.import_module(module)
        except ImportError:
            return module
    return None
