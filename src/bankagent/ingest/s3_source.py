"""Thin wrapper over boto3 S3: list once, match keys to tables, download to `data/raw/`.

Only this module imports boto3, so the rest of the ingest package (and its tests) never needs
network access or real AWS credentials.
"""

from __future__ import annotations

import logging
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from bankagent.ingest.catalog import SourceObject, SourceTableNotFound, matches_table

logger = logging.getLogger(__name__)


class S3Unavailable(RuntimeError):
    """The bucket could not be listed or an object could not be downloaded."""


def _session(aws_profile: str, region: str) -> boto3.Session:
    return boto3.Session(profile_name=aws_profile, region_name=region)


def list_bucket_objects(*, bucket: str, aws_profile: str, region: str) -> list[SourceObject]:
    """List every object in the bucket (paginated). Raises `S3Unavailable` on any AWS error."""
    try:
        client = _session(aws_profile, region).client(
            "s3", config=BotoConfig(retries={"max_attempts": 3})
        )
        paginator = client.get_paginator("list_objects_v2")
        objects: list[SourceObject] = []
        for page in paginator.paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                objects.append(SourceObject(key=item["Key"], size=item["Size"]))
        return objects
    except (BotoCoreError, ClientError) as exc:
        raise S3Unavailable(f"could not list s3://{bucket}: {exc}") from exc


def resolve_table_keys(objects: list[SourceObject], table: str) -> list[SourceObject]:
    """All objects belonging to `table`, sorted for deterministic download order."""
    matches = sorted((obj for obj in objects if matches_table(obj.key, table)), key=lambda o: o.key)
    if not matches:
        raise SourceTableNotFound(table)
    return matches


def download_objects(
    objects: list[SourceObject], *, bucket: str, aws_profile: str, region: str, dest_dir: Path
) -> list[Path]:
    """Download each object under `dest_dir`, preserving its S3 key as the relative path."""
    try:
        client = _session(aws_profile, region).client(
            "s3", config=BotoConfig(retries={"max_attempts": 3})
        )
        paths: list[Path] = []
        for obj in objects:
            target = dest_dir / obj.key
            target.parent.mkdir(parents=True, exist_ok=True)
            logger.info("downloading s3://%s/%s -> %s", bucket, obj.key, target)
            client.download_file(bucket, obj.key, str(target))
            paths.append(target)
        return paths
    except (BotoCoreError, ClientError) as exc:
        raise S3Unavailable(f"could not download from s3://{bucket}: {exc}") from exc
