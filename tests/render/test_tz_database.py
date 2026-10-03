"""The templates build `ZoneInfo` objects at import, so the IANA database is a runtime need.

Windows has no system copy. Until Task 15 `tzdata` only arrived through the `data` group (dbt),
so a default `uv sync` on Windows could not import the app.
"""

from __future__ import annotations

from importlib.metadata import requires
from zoneinfo import ZoneInfo


def test_tzdata_is_a_declared_runtime_dependency_and_the_zones_resolve() -> None:
    # Requires-Dist lists the project's own dependencies only, never a dependency group.
    declared = requires("bankagent") or []
    assert any(requirement.startswith("tzdata==") for requirement in declared), declared
    for key in ("America/Argentina/Buenos_Aires", "America/Bogota", "America/Mexico_City"):
        assert ZoneInfo(key).key == key
