"""Realize an Infinigen FlowerPlant scene.blend into a single flowering-plant GLB.

Infinigen's `Flowerplant` factory stores its geometry as INSTANCES, so the headless
`generate_individual_assets ... --export obj` writes an EMPTY mesh. This script recovers a real
mesh from the saved .blend. Run with Blender (NOT bio3d-arena's venv):

    # 1. generate (in the infinigen conda env), keeping the .blend:
    cd ~/infinigen && ~/miniconda3/envs/infinigen/bin/python -m \
      infinigen_examples.generate_individual_assets \
      --output_folder OUT -f Flowerplant -n 1 --render none --export obj --save_blend
    # 2. realize one flowering plant → GLB:
    ~/blender/blender -b OUT/Flowerplant_000/scene.blend -P scripts/infinigen_flower_realize.py -- OUT/rose.glb
    # 3. ingest as procedural 'infinigen' on a crop subject (bio3d-arena venv):
    #    scripts.generate_infinigen.ingest_infinigen(db, [glb], to_glb=read_bytes,
    #      factory="Flowerplant", task_title="Rosa — single-image → 3D reconstruction", caveat="...generic flowering plant, not rose-specific...")

The scene holds ~11 `FlowerPlantFactory.spawn_asset(N)` plant BODIES (material shader_simple_greenery,
no bloom) plus separate `flower*` bloom objects (shader_petal / shader_flower_center). We pick ONE
plant body and the blooms whose world-XY footprint overlaps it, realize (convert→mesh), decimate to a
gallery budget, and export GLB. NB the realized objects must be linked into the view layer first —
Infinigen leaves them in unlinked collections, which is the real reason --export obj was empty.

Caveat: Flowerplant is a GENERIC parameterized flowering plant (open daisy-like blooms), not a rose.
"""

import sys

import bpy
from mathutils import Vector

MAX_FACES = 90_000


def _world_xy_box(o):
    cs = [o.matrix_world @ Vector(c) for c in o.bound_box]
    xs = [c.x for c in cs]
    ys = [c.y for c in cs]
    return (sum(xs) / 8, sum(ys) / 8), (min(xs), max(xs), min(ys), max(ys))


def main():
    out = sys.argv[sys.argv.index("--") + 1]
    sc = bpy.context.scene.collection
    for o in list(bpy.data.objects):  # link unlinked datablocks so they're selectable/exportable
        if o.name not in sc.objects:
            try:
                sc.objects.link(o)
            except Exception:  # noqa: BLE001
                pass
    bpy.context.view_layer.update()

    plants = sorted(
        (o for o in bpy.data.objects if "FlowerPlantFactory" in o.name and "spawn_asset" in o.name),
        key=lambda o: o.name,
    )
    blooms = [
        o
        for o in bpy.data.objects
        if o.name.lower().startswith("flower") and "Factory" not in o.name
    ]
    if not plants:
        print("NO FlowerPlantFactory spawn_asset roots in scene")
        return 1
    p = plants[0]
    _c, (x0, x1, y0, y1) = _world_xy_box(p)
    mx, my = (x1 - x0) * 0.6 + 0.15, (y1 - y0) * 0.6 + 0.15
    group = [p] + [
        b
        for b in blooms
        if x0 - mx <= _world_xy_box(b)[0][0] <= x1 + mx
        and y0 - my <= _world_xy_box(b)[0][1] <= y1 + my
    ]
    for o in group:  # realize instances + apply geonodes
        if (
            o.type in ("MESH", "CURVE")
            or o.instance_type != "NONE"
            or any(m.type == "NODES" for m in o.modifiers)
        ):
            try:
                bpy.ops.object.select_all(action="DESELECT")
                o.select_set(True)
                bpy.context.view_layer.objects.active = o
                bpy.ops.object.convert(target="MESH")
            except Exception as e:  # noqa: BLE001
                print("convert skip", o.name, e)
    keep = [o for o in group if o.type == "MESH" and len(o.data.polygons) > 0]
    total = sum(len(o.data.polygons) for o in keep)
    print(f"plant={p.name} blooms={len(keep) - 1} faces={total}")
    if total > MAX_FACES:
        ratio = max(0.02, MAX_FACES / total)
        for o in keep:
            o.modifiers.new("dec", "DECIMATE").ratio = ratio
    bpy.ops.object.select_all(action="DESELECT")
    for o in keep:
        o.select_set(True)
    bpy.ops.export_scene.gltf(
        filepath=out, export_format="GLB", use_selection=True, export_apply=True
    )
    print("WROTE", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
