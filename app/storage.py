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
    # Web assets. Absent until the reference galleries began travelling the S3 path: images had
    # only ever been served by the LOCAL backend, where StaticFiles derives the type from the
    # filename and the stored metadata never mattered. On S3 the stored ContentType IS what the
    # browser gets, and every .jpg was landing as application/octet-stream.
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "json": "application/json",
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

    def size(self, rel_path: str) -> int | None:
        """Byte size of a stored object, or None when it is absent.

        Exists so callers can ask "is the stored copy the SAME as mine", not merely "is something
        there". The bundle uploader needs that distinction: re-publishing a mesh at a key that
        already holds an older version is the normal case for a release, and an exists()-only
        check silently skips it — which would have shipped a 9.1x compression that never replaced
        a single live file.
        """
        return len(self.read(rel_path)) if self.exists(rel_path) else None


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

    def size(self, rel_path: str) -> int | None:
        p = self.root / rel_path
        return p.stat().st_size if p.is_file() else None


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

    def size(self, rel_path: str) -> int | None:
        """ContentLength from a HEAD — one cheap round trip, no body transfer."""
        import botocore.exceptions

        try:
            return int(
                self._s3.head_object(Bucket=self.bucket, Key=self._key(rel_path))["ContentLength"]
            )
        except botocore.exceptions.ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
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
