"""Thin wrapper over boto3 S3: list once, match keys to tables, download to `data/raw/`.

Only this module imports boto3, so the rest of the ingest package (and its tests) never needs
network access or real AWS credentials.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from bankagent.ingest.catalog import SourceObject, SourceTableNotFound, matches_table

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_WORKERS = 16
"""Some tables are split into 1000+ daily-partition files (e.g. transactions spans ~1100 days).
Downloading them one at a time made a full ingest take well over 25 minutes; boto3 clients are
thread-safe for this, and S3 comfortably serves this many concurrent GETs."""


class S3Unavailable(RuntimeError):
    """The bucket could not be listed or an object could not be downloaded."""


def _session(aws_profile: str, region: str) -> boto3.Session:
    return boto3.Session(profile_name=aws_profile, region_name=region)


def list_bucket_objects(
    *, bucket: str, aws_profile: str, region: str, prefix: str = ""
) -> list[SourceObject]:
    """List objects under `prefix` (paginated). Raises `S3Unavailable` on any AWS error.

    `prefix` scopes the listing to the active dataset root, e.g. `data/`. This bucket has also
    been observed to hold sibling snapshot/backup folders (e.g. `data_backup_20260831/`) at the
    bucket root; without a prefix those would be listed and ingested too, silently doubling every
    table. Always pass the dataset's prefix explicitly; do not rely on the empty default in
    production use (`resolve_config` sets `INGEST_SOURCE_PREFIX`, default `data/`).
    """
    try:
        client = _session(aws_profile, region).client(
            "s3", config=BotoConfig(retries={"max_attempts": 3})
        )
        paginator = client.get_paginator("list_objects_v2")
        objects: list[SourceObject] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                objects.append(SourceObject(key=item["Key"], size=item["Size"]))
        return objects
    except (BotoCoreError, ClientError) as exc:
        raise S3Unavailable(f"could not list s3://{bucket}/{prefix}: {exc}") from exc


def resolve_table_keys(objects: list[SourceObject], table: str) -> list[SourceObject]:
    """All objects belonging to `table`, sorted for deterministic download order."""
    matches = sorted((obj for obj in objects if matches_table(obj.key, table)), key=lambda o: o.key)
    if not matches:
        raise SourceTableNotFound(table)
    return matches


def download_objects(
    objects: list[SourceObject],
    *,
    bucket: str,
    aws_profile: str,
    region: str,
    dest_dir: Path,
    max_workers: int = MAX_DOWNLOAD_WORKERS,
) -> list[Path]:
    """Download every object under `dest_dir`, preserving its S3 key as the relative path.

    Downloads run concurrently (`max_workers` threads sharing one boto3 client, which is
    thread-safe) since a single table can be split into 1000+ small daily-partition files.
    Returned paths are in the same order as `objects`, regardless of completion order.

    A file already present in `dest_dir` with the same byte size as the S3 object is reused
    instead of downloaded again, so re-running an ingest (or rebuilding bronze after deleting
    it) does not repeat a ~20-minute download. A size mismatch (e.g. an interrupted download)
    triggers a fresh download. Known limit: a local file corrupted without changing its size is
    reused; the bronze sha256 in the manifest would then differ and be reported as drift.
    Delete `data/raw/` to force a clean download.
    """
    client = _session(aws_profile, region).client(
        "s3", config=BotoConfig(retries={"max_attempts": 3}, max_pool_connections=max_workers)
    )

    def _download_one(obj: SourceObject) -> Path:
        target = dest_dir / obj.key
        if target.is_file() and target.stat().st_size == obj.size:
            logger.debug("cached, skipping s3://%s/%s", bucket, obj.key)
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        logger.info("downloading s3://%s/%s -> %s", bucket, obj.key, target)
        client.download_file(bucket, obj.key, str(target))
        return target

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return list(pool.map(_download_one, objects))
    except (BotoCoreError, ClientError) as exc:
        raise S3Unavailable(f"could not download from s3://{bucket}: {exc}") from exc
