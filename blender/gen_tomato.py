# Procedural tomato in native Blender bpy. Author-controlled DCC procedural generator.
# Pinnately compound serrated leaves (with real thickness via solidify), drooping habit,
# upright stem with spiral phyllotaxy, red fruit trusses + green calyces. Exports GLB.
import bpy
import bmesh
import math
import random
import sys
from mathutils import Euler

argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/tomato.glb"
SEED = int(argv[1]) if len(argv) > 1 else 0

bpy.ops.wm.read_factory_settings(use_empty=True)


def new_mat(name, color, rough=0.55):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Roughness"].default_value = rough
    return m


LEAF = new_mat("leaf", (0.11, 0.36, 0.08), 0.48)
STEM = new_mat("stem", (0.22, 0.44, 0.13), 0.6)
FRUIT = new_mat("fruit", (0.80, 0.09, 0.05), 0.30)
CALYX = new_mat("calyx", (0.18, 0.42, 0.10), 0.6)


def _leaflet_mesh(length, width, name):
    """Serrated lanceolate leaflet, midrib +X, blade in XY; from_pydata + solidify for thickness."""
    n = 12
    verts = [(0.0, 0.0, 0.0)]
    for i in range(1, n + 1):
        t = i / n
        x = length * t
        w = width * max(0.0, (1.0 - abs(t - 0.42) / 0.62)) ** 0.80
        w *= 1.16 if i % 2 == 0 else 0.90  # serration
        verts.append((x, w, 0.0))
        verts.append((x, -w, 0.0))
    faces = []
    for i in range(n):
        l0, r0 = 1 + 2 * i, 2 + 2 * i
        if i < n - 1:
            l1, r1 = 1 + 2 * (i + 1), 2 + 2 * (i + 1)
            faces += [(0, l0, l1), (0, r1, r0)]
        else:
            faces += [(0, l0, r0)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    ob.data.materials.append(LEAF)
    sol = ob.modifiers.new("sol", "SOLIDIFY")
    sol.thickness = 0.01 * length
    sol.offset = 0
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.modifier_apply(modifier="sol")
    ob.select_set(False)
    return ob


def _cyl(r0, r1, length, name, mat, verts=8):
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, segments=verts, radius1=r0, radius2=r1, depth=length
    )
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, length / 2))  # base at origin, grows +Z
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    ob.data.materials.append(mat)
    return ob


def compound_leaf(scale, name):
    """Rachis (+X) bearing 5 separated leaflet pairs (alt large/small) + terminal; one joined obj."""
    L = 11.0 * scale
    parts = []
    rach = _cyl(0.09 * scale, 0.05 * scale, L, name + "_rachis", STEM, 6)
    rach.rotation_euler = Euler((0, math.radians(90), 0))  # lay rachis along +X
    parts.append(rach)
    for k in range(5):
        t = (k + 0.85) / 5.6
        x = L * t
        big = k % 2 == 0
        lflen = (3.2 if big else 2.1) * scale * (1.15 - 0.28 * t)
        for sgn in (1, -1):
            lf = _leaflet_mesh(lflen, lflen * (0.52 if big else 0.62), f"{name}_lf{k}{sgn}")
            lf.rotation_euler = Euler((math.radians(16), 0, math.radians(62 * sgn)))
            lf.location = (x, 0, 0)
            parts.append(lf)
    tip = _leaflet_mesh(3.6 * scale, 3.6 * scale * 0.52, name + "_tip")
    tip.location = (L, 0, 0)
    parts.append(tip)
    # apply transforms + join
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = rach
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.join()
    leaf = bpy.context.view_layer.objects.active
    leaf.name = name
    return leaf


def truss(scale, n, name):
    parts = []
    R = 3.3 * scale
    for i in range(n):
        a = i * 2.4
        x, z = 1.7 * scale * math.cos(a), 1.7 * scale * math.sin(a)
        y = -2.4 * scale - 2.7 * scale * i  # bigger + more vertical separation -> distinct tomatoes
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=R, location=(x, y, z))
        fr = bpy.context.view_layer.objects.active
        fr.name = f"{name}_fruit{i}"
        fr.data.materials.append(FRUIT)
        for poly in fr.data.polygons:
            poly.use_smooth = True
        parts.append(fr)
        # calyx: small flattened cone star on top
        bpy.ops.mesh.primitive_cone_add(
            vertices=5,
            radius1=1.3 * scale,
            radius2=0,
            depth=1.1 * scale,
            location=(x, y + R * 0.8, z),
        )
        ca = bpy.context.view_layer.objects.active
        ca.name = f"{name}_calyx{i}"
        ca.rotation_euler = Euler((math.radians(180), 0, 0))
        ca.data.materials.append(CALYX)
        parts.append(ca)
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.join()
    t = bpy.context.view_layer.objects.active
    t.name = name
    return t


# ---- assemble plant ----

random.seed(SEED)
all_objs = []
# main stem: stacked internodes up +Z. SEED drives real per-plant variation (so distinct seeds
# yield distinct meshes — otherwise they would be byte-identical and dedup to one object).
nodes = random.choice((8, 9, 10))
phyllo_step = 137.5 + random.uniform(-4, 4)  # base spiral, jittered per plant
truss_nodes = sorted(random.sample(range(2, nodes - 1), random.choice((2, 3))))  # which nodes fruit
z = 0.0
xw = yw = 0.0  # accumulating stem wander
for s in range(nodes):
    internode = 5.0 * random.uniform(0.88, 1.12)
    xw += random.uniform(-0.25, 0.25)
    yw += random.uniform(-0.25, 0.25)
    seg = _cyl(0.45 - 0.02 * s, 0.43 - 0.02 * s, internode, f"stem{s}", STEM, 8)
    seg.location = (xw, yw, z)
    all_objs.append(seg)
    phyllo = math.radians(phyllo_step * s + random.uniform(-6, 6))
    # compound leaf at node, drooping (pitch down, jittered), rolled by phyllotaxy
    leaf = compound_leaf(1.4 * random.uniform(0.9, 1.12), f"leaf{s}")
    leaf.rotation_euler = Euler((math.radians(-58 + random.uniform(-8, 8)), 0, phyllo))
    leaf.location = (xw, yw, z + internode * 0.5)
    all_objs.append(leaf)
    # trusses at the chosen nodes, with a varied fruit count
    if s in truss_nodes:
        tr = truss(1.0, random.choice((2, 3)), f"truss{s}")
        tr.rotation_euler = Euler((math.radians(-20), 0, phyllo + math.radians(40)))
        tr.location = (xw, yw, z + internode * 0.55)
        all_objs.append(tr)
    z += internode * 0.92

bpy.ops.object.select_all(action="DESELECT")
for o in all_objs:
    o.select_set(True)
bpy.context.view_layer.objects.active = all_objs[0]
bpy.ops.object.join()
plant = bpy.context.view_layer.objects.active
plant.name = "tomato"

bpy.ops.export_scene.gltf(filepath=OUT, export_format="GLB", use_selection=False, export_yup=True)
print("EXPORTED", OUT, "verts", len(plant.data.vertices))
