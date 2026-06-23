"""Standalone Demeter mesh exporter — RUN WITH DEMETER'S OWN INTERPRETER, not bio3d-arena's.

bio3d-arena does not depend on Demeter; `generate_demeter.py` invokes this script via subprocess
using Demeter's env (e.g. the `partcrafter` conda env) with the Demeter checkout on sys.path. It
mirrors Demeter's `decode.decode_params` but writes the generated triangle mesh to an OBJ instead
of visualizing it. Demeter is treated read-only — this script only imports it. Requires CUDA
(Demeter hardcodes `.cuda()`). Demeter is Academic-Non-Commercial: internal use only.

Usage: python demeter_runner.py <demeter_dir> <species> <sample> <out.obj>
"""

import os
import sys

import open3d as o3d
import torch

demeter_dir = sys.argv[1]
species = sys.argv[2] if len(sys.argv) > 2 else "maize"
sample = sys.argv[3] if len(sys.argv) > 3 else "10008da"
out = sys.argv[4] if len(sys.argv) > 4 else "/tmp/demeter.obj"

sys.path.insert(0, demeter_dir)
os.chdir(demeter_dir)

from representation.graph import PlantGraphFixedTopology  # noqa: E402
from utils.graph import load_class, load_parent  # noqa: E402
from utils.pca import NodePCA  # noqa: E402

df = "sample_params"
inst = os.path.join(df, species, "instances", sample)
g = PlantGraphFixedTopology(
    classes=load_class(os.path.join(inst, "info", "class.txt")),
    parents=load_parent(os.path.join(inst, "info", "parent.txt")),
    species=species,
    pca_leaf_3d=NodePCA(path=os.path.join(df, species, "3d_leaf_pca.pth")),
    pca_stem_3d=NodePCA(path=os.path.join(df, species, "3d_stem_pca.pth")),
    pca_leaf_2d=NodePCA(path=os.path.join(df, species, "2d_leaf_pca.pth")),
)
g.load(os.path.join(inst, "graph.pkl"))
g.cuda()
with torch.no_grad():
    mesh = g.generate(output_format="mesh", color="gray", align_global=True)
mesh.compute_vertex_normals()
ok = o3d.io.write_triangle_mesh(out, mesh)
print(f"WROTE {out} ok={ok} verts={len(mesh.vertices)} tris={len(mesh.triangles)}")
