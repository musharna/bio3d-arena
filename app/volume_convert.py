"""Convert a volumetric scan (CT / micro-CT / X-ray / MRI) to a surface-mesh GLB for
<model-viewer>. The volumetric sibling of mesh_convert.py (meshes) and points_convert.py
(point clouds): here the data is a 3-D intensity volume, so we threshold it to an occupancy
field and extract an iso-surface with marching cubes. trimesh + numpy were already deps;
scikit-image / nibabel / tifffile are added for this modality.

Reads a NIfTI (.nii/.nii.gz; voxel spacing from the affine) or a TIFF z-stack (.tif/.tiff,
a single multipage file OR a directory of slices; isotropic unit spacing). Other formats raise
VolumeConvertError — add a reader when a dataset needs it (DICOM/RAW are deliberately deferred).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh


class VolumeConvertError(Exception):
    """Raised when a volume cannot be converted to a renderable surface-mesh GLB."""


def _load_volume(src_path: str) -> tuple[np.ndarray, tuple[float, float, float]]:
    """Return (3-D float array, (z,y,x) voxel spacing). Dispatch by extension."""
    p = Path(src_path)
    suffixes = "".join(p.suffixes).lower()
    if p.is_dir():
        import tifffile

        slices = sorted(p.glob("*.tif")) + sorted(p.glob("*.tiff"))
        if not slices:
            raise VolumeConvertError(f"{src_path}: no .tif/.tiff slices in directory")
        try:
            vol = np.stack([tifffile.imread(str(s)) for s in slices])
        except Exception as e:  # noqa: BLE001
            raise VolumeConvertError(f"{src_path}: unreadable TIFF slices: {e}") from e
        return vol.astype(np.float32), (1.0, 1.0, 1.0)
    if suffixes.endswith(".nii") or suffixes.endswith(".nii.gz"):
        import nibabel as nib

        try:
            img = nib.load(src_path)
            vol = np.asarray(img.get_fdata(), dtype=np.float32)
        except Exception as e:  # noqa: BLE001
            raise VolumeConvertError(f"{src_path}: unreadable NIfTI: {e}") from e
        zooms = img.header.get_zooms()
        spacing = tuple(float(z) for z in zooms[:3]) if len(zooms) >= 3 else (1.0, 1.0, 1.0)
        return vol, spacing
    if suffixes.endswith(".tif") or suffixes.endswith(".tiff"):
        import tifffile

        try:
            vol = np.asarray(tifffile.imread(src_path), dtype=np.float32)
        except Exception as e:  # noqa: BLE001
            raise VolumeConvertError(f"{src_path}: unreadable TIFF: {e}") from e
        if vol.ndim != 3:
            raise VolumeConvertError(
                f"{src_path}: expected a 3-D TIFF stack, got shape {vol.shape}"
            )
        return vol, (1.0, 1.0, 1.0)
    raise VolumeConvertError(
        f"{src_path}: unsupported volume format (need .nii/.nii.gz/.tif/.tiff)"
    )


def volume_to_glb(
    src_path: str,
    *,
    threshold: float | None = None,
    max_faces: int = 200_000,
    step_size: int = 1,
) -> bytes:
    """Load a 3-D volume and export a marching-cubes surface-mesh GLB.

    threshold: iso-level. None → Otsu (skimage.filters.threshold_otsu). For low-contrast MRI
    a caller-supplied absolute level is often better; the mesh is threshold-dependent and so is
    an approximate, honest surface, not a polished asset.
    max_faces: quadric-decimate above this budget so the GLB stays web-viable.
    step_size: marching-cubes downsample stride (>1 for very large volumes).

    Raises VolumeConvertError on unsupported format, unreadable/flat volume, or empty surface.
    """
    from skimage import filters, measure

    vol, spacing = _load_volume(src_path)
    if vol.size == 0 or float(vol.min()) == float(vol.max()):
        raise VolumeConvertError(f"{src_path}: flat/empty volume, no surface to extract")

    if threshold is None:
        try:
            threshold = float(filters.threshold_otsu(vol))
        except ValueError as e:
            raise VolumeConvertError(f"{src_path}: cannot auto-threshold: {e}") from e
    if not (float(vol.min()) < threshold < float(vol.max())):
        raise VolumeConvertError(f"{src_path}: threshold {threshold} outside volume range")

    # Run marching cubes, then adaptively coarsen step_size if the face budget is exceeded.
    # fast_simplification (trimesh's simplify_quadric_decimation backend) hits a topological
    # floor on marching-cubes meshes and cannot reliably halve face counts; increasing step_size
    # at the source is the reliable path to the budget.
    _step = step_size
    while True:
        try:
            verts, faces, _normals, _vals = measure.marching_cubes(
                vol, level=threshold, spacing=spacing, step_size=_step
            )
        except (ValueError, RuntimeError) as e:
            raise VolumeConvertError(f"{src_path}: marching cubes produced no surface: {e}") from e
        if len(faces) == 0:
            raise VolumeConvertError(f"{src_path}: empty surface at threshold {threshold}")
        if len(faces) <= max_faces or _step > 8:
            break
        _step += 1

    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    if len(mesh.faces) > max_faces:
        mesh = mesh.simplify_quadric_decimation(face_count=max_faces)
    if len(mesh.faces) > max_faces:
        print(
            f"WARNING: {src_path}: mesh exceeds face budget after decimation "
            f"({len(mesh.faces)} faces > {max_faces} limit); returning over-budget mesh",
            flush=True,
        )
    glb = mesh.export(file_type="glb")
    if not glb:
        raise VolumeConvertError(f"{src_path}: empty GLB export")
    return glb
