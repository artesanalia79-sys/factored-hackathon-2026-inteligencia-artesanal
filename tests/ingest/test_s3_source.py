"""`download_objects` parallelizes downloads but preserves input order in its return value."""

from __future__ import annotations

import time
from pathlib import Path

from bankagent.ingest.catalog import SourceObject
from bankagent.ingest.s3_source import download_objects


class FakeS3Client:
    """Slower for early keys, so completion order differs from input order."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.calls: list[str] = []

    def download_file(self, bucket: str, key: str, dest: str) -> None:
        self.calls.append(key)
        if key.endswith("0.csv"):
            time.sleep(0.05)
        Path(dest).write_bytes(self.files[key])


def test_download_objects_preserves_input_order_despite_concurrency(
    tmp_path: Path, monkeypatch
) -> None:
    fake = FakeS3Client({f"t/{i}.csv": f"{i}".encode() for i in range(20)})
    monkeypatch.setattr(
        "bankagent.ingest.s3_source._session",
        lambda profile, region: type("S", (), {"client": lambda self, *a, **k: fake})(),
    )

    objects = [SourceObject(key=f"t/{i}.csv", size=1) for i in range(20)]
    paths = download_objects(
        objects, bucket="b", aws_profile="p", region="r", dest_dir=tmp_path, max_workers=8
    )

    assert [p.name for p in paths] == [f"{i}.csv" for i in range(20)]
    assert len(fake.calls) == 20
    for i, path in enumerate(paths):
        assert path.read_bytes() == str(i).encode()
