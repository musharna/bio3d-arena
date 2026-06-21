"""Convert a scan-dataset mesh (.obj/.ply/.glb) to GLB bytes for <model-viewer>.

Point-cloud assets (vertices, no faces) cannot be rendered as a surface mesh by
model-viewer, so they raise MeshConvertError and are skipped by callers (a future
increment can add a cloud→points-GLTF bridge). trimesh is already a dependency.
"""

from __future__ import annotations

import trimesh


class MeshConvertError(Exception):
    """Raised when an asset cannot be converted to a renderable GLB mesh."""


def to_glb(src_path: str, *, max_faces: int | None = None) -> bytes:
    """Load a mesh asset and export GLB bytes. Raises MeshConvertError for a point cloud
    (no faces) or an empty export.

    `max_faces`: when set and the mesh exceeds it, quadric-decimate down to that face budget
    before export. Laser-scan meshes run to millions of triangles (tens of MB GLB) — far too
    heavy for a browser grid — so callers cap them to a web-viable budget. Decimation reduces
    detail but does not invent geometry (the points/surface remain the real scan), so a
    decimated scan stays honest as an audit reference. Default None preserves full resolution.
    """
    loaded = trimesh.load(src_path, force="mesh")  # concatenate scene parts into one mesh
    faces = getattr(loaded, "faces", None)
    if faces is None or len(faces) == 0:
        raise MeshConvertError(f"{src_path}: point-cloud / no faces, not renderable")
    if max_faces is not None and len(faces) > max_faces:
        loaded = loaded.simplify_quadric_decimation(face_count=max_faces)
    glb = loaded.export(file_type="glb")
    if not glb:
        raise MeshConvertError(f"{src_path}: empty GLB export")
    return glb
