"""`download_objects`: concurrent, order-preserving, and reuses already-downloaded files."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from bankagent.ingest.catalog import SourceObject
from bankagent.ingest.s3_source import download_objects


class FakeS3Client:
    """Slower for keys ending in 0, so completion order differs from input order."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.calls: list[str] = []

    def download_file(self, bucket: str, key: str, dest: str) -> None:
        self.calls.append(key)
        if key.endswith("0.csv"):
            time.sleep(0.05)
        Path(dest).write_bytes(self.files[key])


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeS3Client:
    client = FakeS3Client({f"t/{i}.csv": f"row-{i}".encode() for i in range(20)})
    monkeypatch.setattr(
        "bankagent.ingest.s3_source._session",
        lambda profile, region: type("S", (), {"client": lambda self, *a, **k: client})(),
    )
    return client


def _objects(fake: FakeS3Client) -> list[SourceObject]:
    return [SourceObject(key=key, size=len(data)) for key, data in fake.files.items()]


def _download(objects: list[SourceObject], dest: Path) -> list[Path]:
    return download_objects(
        objects, bucket="b", aws_profile="p", region="r", dest_dir=dest, max_workers=8
    )


def test_download_objects_preserves_input_order_despite_concurrency(
    tmp_path: Path, fake: FakeS3Client
) -> None:
    paths = _download(_objects(fake), tmp_path)

    assert [p.name for p in paths] == [f"{i}.csv" for i in range(20)]
    assert len(fake.calls) == 20
    for i, path in enumerate(paths):
        assert path.read_bytes() == f"row-{i}".encode()


def test_files_already_downloaded_with_the_same_size_are_not_downloaded_again(
    tmp_path: Path, fake: FakeS3Client
) -> None:
    objects = _objects(fake)
    _download(objects, tmp_path)
    fake.calls.clear()

    paths = _download(objects, tmp_path)

    assert fake.calls == []
    assert len(paths) == 20


def test_partial_or_changed_files_are_downloaded_again(tmp_path: Path, fake: FakeS3Client) -> None:
    objects = _objects(fake)
    _download(objects, tmp_path)
    fake.calls.clear()
    (tmp_path / "t" / "3.csv").write_bytes(b"ro")  # simulates an interrupted download
    (tmp_path / "t" / "7.csv").unlink()

    _download(objects, tmp_path)

    assert sorted(fake.calls) == ["t/3.csv", "t/7.csv"]
    assert (tmp_path / "t" / "3.csv").read_bytes() == b"row-3"
