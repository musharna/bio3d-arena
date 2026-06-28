"""Asset storage abstraction — decouples 3D blobs from the app server.

Local filesystem (default, served by StaticFiles) or S3-compatible object storage
(production scale: assets live in a bucket, optionally fronted by a CDN). Selecting
a backend is a config switch; the ingestion/serving code never changes.
"""

from __future__ import annotations

import abc
import functools
from pathlib import Path

from . import config

_CONTENT_TYPES = {
    "glb": "model/gltf-binary",
    "gltf": "model/gltf+json",
    "pdb": "chemical/x-pdb",
    "ent": "chemical/x-pdb",
    "cif": "chemical/x-cif",
    "mmcif": "chemical/x-cif",
    "sdf": "chemical/x-mdl-sdfile",
    "mol": "chemical/x-mdl-molfile",
}


def content_type_for(rel_path: str) -> str:
    ext = rel_path.rsplit(".", 1)[-1].lower()
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


class StorageBackend(abc.ABC):
    """Save bytes under a relative key, resolve a browser URL, read bytes back."""

    remote: bool = False

    @abc.abstractmethod
    def save(self, rel_path: str, data: bytes) -> None: ...

    @abc.abstractmethod
    def url_for(self, rel_path: str) -> str: ...

    @abc.abstractmethod
    def read(self, rel_path: str) -> bytes: ...

    @abc.abstractmethod
    def exists(self, rel_path: str) -> bool: ...


class LocalStorageBackend(StorageBackend):
    """Filesystem storage under ASSET_DIR, served at `url_prefix` by StaticFiles."""

    remote = False

    def __init__(self, root: Path, url_prefix: str = "/assets"):
        self.root = Path(root)
        self.url_prefix = url_prefix.rstrip("/")

    def save(self, rel_path: str, data: bytes) -> None:
        dest = self.root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def url_for(self, rel_path: str) -> str:
        return f"{self.url_prefix}/{rel_path}"

    def read(self, rel_path: str) -> bytes:
        return (self.root / rel_path).read_bytes()

    def exists(self, rel_path: str) -> bool:
        return (self.root / rel_path).is_file()


class S3StorageBackend(StorageBackend):
    """S3-compatible object storage. boto3 is imported lazily (optional dep).

    url_for returns a public CDN/base URL if configured, else a presigned GET URL.
    """

    remote = True

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        public_base_url: str = "",
        presign_ttl: int = 3600,
    ):
        import boto3  # lazy: only needed when the S3 backend is selected

        if not bucket:
            raise ValueError("S3 storage requires BIO3D_S3_BUCKET")
        self._s3 = boto3.client("s3")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.public_base_url = public_base_url.rstrip("/")
        self.presign_ttl = presign_ttl

    def _key(self, rel_path: str) -> str:
        return f"{self.prefix}/{rel_path}" if self.prefix else rel_path

    def save(self, rel_path: str, data: bytes) -> None:
        self._s3.put_object(
            Bucket=self.bucket,
            Key=self._key(rel_path),
            Body=data,
            ContentType=content_type_for(rel_path),
        )

    def url_for(self, rel_path: str) -> str:
        key = self._key(rel_path)
        if self.public_base_url:
            return f"{self.public_base_url}/{key}"
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.presign_ttl,
        )

    def read(self, rel_path: str) -> bytes:
        return self._s3.get_object(Bucket=self.bucket, Key=self._key(rel_path))["Body"].read()

    def exists(self, rel_path: str) -> bool:
        import botocore.exceptions

        try:
            self._s3.head_object(Bucket=self.bucket, Key=self._key(rel_path))
            return True
        except botocore.exceptions.ClientError as e:
            # Only a genuine "not found" means False; re-raise 403/5xx/network so a
            # transient/permission error can't masquerade as a missing object.
            code = str(e.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise


def _make_backend() -> StorageBackend:
    if config.STORAGE_BACKEND == "s3":
        return S3StorageBackend(
            config.S3_BUCKET, config.S3_PREFIX, config.S3_PUBLIC_BASE_URL, config.S3_PRESIGN_TTL
        )
    return LocalStorageBackend(config.ASSET_DIR)


@functools.lru_cache(maxsize=1)
def get_storage() -> StorageBackend:
    """Process-wide singleton storage backend selected from config."""
    return _make_backend()
