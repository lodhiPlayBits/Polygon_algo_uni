import logging

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .base import StorageBackend, StorageError

logger = logging.getLogger(__name__)


class S3StorageBackend(StorageBackend):
    """
    Stores files in any S3-compatible bucket.

    Leave endpoint_url empty for AWS S3. Set it for other S3-compatible
    providers, e.g. Cloudflare R2, Oracle Cloud Object Storage, or MinIO.
    """

    name = "S3"

    def __init__(self, bucket, region=None, access_key_id=None, secret_access_key=None, endpoint_url=None, client=None):
        self.bucket = bucket
        if client is not None:
            self.client = client
            return
        config = Config(
            # Some S3-compatible providers reject the checksum headers newer boto3 sends by default.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            # Custom endpoints (R2, OCI, MinIO) need path-style URLs; AWS works with either.
            s3={"addressing_style": "path" if endpoint_url else "auto"},
        )
        self.client = boto3.client(
            "s3",
            region_name=region or None,
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=secret_access_key or None,
            config=config,
        )

    def upload(self, key, data):
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        except (BotoCoreError, ClientError) as e:
            raise StorageError(f"S3 upload failed for '{key}': {e}") from e
        logger.info("Uploaded %s (%d bytes) to bucket %s", key, len(data), self.bucket)

    def list_keys(self, prefix):
        keys = []
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
        except (BotoCoreError, ClientError) as e:
            raise StorageError(f"S3 list failed for prefix '{prefix}': {e}") from e
        return keys

    def delete_prefix(self, prefix):
        keys = self.list_keys(prefix)
        try:
            # delete_objects accepts at most 1000 keys per call.
            for start in range(0, len(keys), 1000):
                batch = [{"Key": k} for k in keys[start:start + 1000]]
                response = self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})
                if response.get("Errors"):
                    raise StorageError(f"S3 delete failed for {len(response['Errors'])} objects under '{prefix}': {response['Errors'][0]}")
        except (BotoCoreError, ClientError) as e:
            raise StorageError(f"S3 delete failed for prefix '{prefix}': {e}") from e
        logger.info("Deleted %d objects under %s in bucket %s", len(keys), prefix, self.bucket)
        return len(keys)
