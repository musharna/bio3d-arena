"""Client for AgriGen's recon-scoring microservice (POST /score).

Microservice boundary (decision D1): bio3d-arena never imports agrigen's heavy 3D deps
(trimesh/open3d/scipy) — it ships GLB bytes over HTTP and gets back the metric bundle.
Base URL is config.RECON_SCORER_URL.
"""

from __future__ import annotations

import httpx


class ScorerError(Exception):
    """Raised when the recon-scoring service is unreachable or returns non-2xx."""


def score_output(
    glb_bytes: bytes,
    task_id: int,
    *,
    base_url: str,
    point_count: int = 16384,
    seed: int = 0,
    timeout: float = 120.0,
) -> dict:
    """POST GLB bytes + task_id to {base_url}/score; return the metric-bundle dict."""
    files = {"glb": ("output.glb", glb_bytes, "model/gltf-binary")}
    data = {"task_id": str(task_id), "point_count": str(point_count), "seed": str(seed)}
    try:
        resp = httpx.post(f"{base_url}/score", files=files, data=data, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise ScorerError(f"recon scorer at {base_url}: {e}") from e
