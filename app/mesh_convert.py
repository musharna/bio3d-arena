"""Convert a scan-dataset mesh (.obj/.ply/.glb) to GLB bytes for <model-viewer>.

Point-cloud assets (vertices, no faces) cannot be rendered as a surface mesh by
model-viewer, so they raise MeshConvertError and are skipped by callers (a future
increment can add a cloud→points-GLTF bridge). trimesh is already a dependency.
"""

from __future__ import annotations

import trimesh


class MeshConvertError(Exception):
    """Raised when an asset cannot be converted to a renderable GLB mesh."""


def to_glb(src_path: str) -> bytes:
    loaded = trimesh.load(src_path, force="mesh")  # concatenate scene parts into one mesh
    faces = getattr(loaded, "faces", None)
    if faces is None or len(faces) == 0:
        raise MeshConvertError(f"{src_path}: point-cloud / no faces, not renderable")
    glb = loaded.export(file_type="glb")
    if not glb:
        raise MeshConvertError(f"{src_path}: empty GLB export")
    return glb
