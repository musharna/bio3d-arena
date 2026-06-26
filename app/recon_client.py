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
    species_slug: str,
    *,
    base_url: str,
    n_points: int = 30000,
    tau: float = 0.05,
    scale_cm: float | None = None,
    timeout: float = 120.0,
) -> dict:
    """Score a GLB against a task's held-out GT via the live service (§11/§12 contract).

    Wire format is RAW GLB bytes in the body + query params (no multipart → no
    python-multipart dep). `species_slug` is the GT-bundle task key (e.g. "arabidopsis",
    "zea_mays") — intentionally decoupled from bio3d-arena's Task PKs so the held-out GT
    is reusable across DB resets. `n_points`/`tau`/`scale_cm` are scored confounds, echoed
    back in the response's `metrics.params`. Returns `{task_id, bundle_version, metrics}`.
    """
    params: dict[str, str | int | float] = {
        "task_id": species_slug,
        "n_points": n_points,
        "tau": tau,
    }
    if scale_cm is not None:
        params["scale_cm"] = scale_cm
    try:
        resp = httpx.post(
            f"{base_url}/score",
            params=params,
            content=glb_bytes,  # raw GLB in the body, NOT multipart
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise ScorerError(f"recon scorer at {base_url}: {e}") from e


def score_structure(
    record: dict,
    *,
    base_url: str,
    timeout: float = 30.0,
) -> dict:
    """Score a structure-known organ record on the Mode-B *organ-fidelity* axis (§ contract
    2026-06-26). POSTs the record as JSON to `/score_structure` (same service as `/score`,
    no GT bundle required) and returns the explainable board row
    `{species, botanical_fidelity, n_attributes, attributes}` (or `{..., note}` for an
    un-referenced species). `record` is the request body verbatim — a structure sidecar or a
    seed-PD record: `{"species": ..., "leaf_axis_count": ..., ...}`.

    Mirrors `score_output`'s offline handling: any HTTP failure (unreachable/non-2xx) raises
    ScorerError so the caller can store status='error' best-effort and never crash a batch.
    """
    try:
        resp = httpx.post(f"{base_url}/score_structure", json=record, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise ScorerError(f"structure scorer at {base_url}: {e}") from e
