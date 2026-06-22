# Tomato 3D-Generation Field Map

> Backbone artifact (brainstorm 2026-06-21). Goal: enumerate EVERY feasible venue to produce a
> tomato (_Solanum lycopersicum_) 3D model, mark **have / partial / gap**, note how AgriGen can
> leverage each, and emit a prioritized roadmap of gaps for bio3d-arena to wire. Synthesis of a
> 4-agent verified research fan-out (all claims carry source URLs in the bucket sections).
> Mission (user): "compare the WHOLE field as much as feasible" to understand where the models
> stand and how AgriGen can leverage them to generate accurate models.

## Thesis (what the field map shows)

The field converges on one load-bearing finding, independently across buckets: **generic
generative-3D (image/text→3D) blobs thin-leaved plants** — implicit-surface meshing thickens
leaves, fuses blades, drops fine stems, fills holes. The **gene-controllable, botanically-faithful
path is procedural / FSPM** (Helios, GroIMP, AgriGen's own UnifiedGenerator, L-Py) **+ part-based
generation** (PartCrafter). This corroborates the existing `rose-parametric-petal-plateau` /
`feedback_procedural_is_the_product` memory: procedural is the product; generative-3D is a
component/organ generator, not a faithful whole-plant source.

## Coverage at a glance

| Bucket                          | Status                         | What we have / the gap                                                                                          |
| ------------------------------- | ------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| 2. Image→3D (single image)      | **HAVE**                       | Tripo direct + fal/Replicate aggregators (~10 models: Hunyuan v2/v3, Trellis/Trellis2, Rodin, TripoSR). Merged. |
| 6. Scan-derived / reference     | **HAVE**                       | Plant3D laser scans (15 live) + point-cloud bridge + the Moneymaker CC reference photo. Merged.                 |
| 1. Procedural                   | **GAP** (Infinigen infra only) | No gene-controllable tomato source live. Helios/AgriGen-gen/L-Py/GroIMP all un-wired.                           |
| 3. Text→3D                      | **GAP (cheap)**                | Aggregator text endpoints = new model ids; trivial to add. Organ-level only.                                    |
| 4. Multi-view / reconstruction  | **GAP**                        | The "compose synth views → 3D" modality is entirely un-wired.                                                   |
| 5. Neural-procedural / frontier | **GAP (research-grade)**       | PartCrafter / Hunyuan3D-2.1 self-host / GroIMP-FSPM / PlantDreamer / Demeter un-wired.                          |

---

## Bucket 1 — Procedural (gene-controllable; the faithful path)

| Tool                                                                                                                                                                                               | Open/License         | Headless                                  | →GLB                                   | Tomato?                                                                                         | Status                        | Wire it                                                                  |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- | ----------------------------------------- | -------------------------------------- | ----------------------------------------------------------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------ |
| **Helios** (UC Davis Bailey Lab)                                                                                                                                                                   | GPL-2.0              | ✅ C++ + **PyHelios**                     | OBJ/PLY→GLB                            | ✅ **built-in `TomatoParameters` + `buildPlant()`**                                             | active, commit 2026-06-21     | C++/PyHelios `buildPlant()`→writeOBJ→trimesh GLB                         |
| **AgriGen UnifiedGenerator**                                                                                                                                                                       | (AgriGen, read-only) | ✅ `generate_procedural_geometry` service | GeometryResult                         | ✅ **tomato PD `solanum_lycopersicum.yaml`** (richer-PD adds pinnate compound leaves)           | active (the field's own tool) | consume the service like the :8077 scorer (read-only)                    |
| **L-Py + PlantGL** (OpenAlea)                                                                                                                                                                      | CeCILL-C             | ✅ pure-Python, no Qt                     | `Scene.save("obj")`→GLB                | ⚠️ author `tomato.lpy` (tomato is the docs landing image)                                       | both released Feb 2026        | author one `.lpy`; OBJ→trimesh GLB                                       |
| **GroIMP + XL**                                                                                                                                                                                    | GPL                  | ✅ `core.jar --headless`                  | glTF plugin / X3D/OBJ→GLB              | ✅ **published validated tomato FSPM** (Sarlikioti et al.) + 2 newer (in silico Plants 2025/26) | v2.2.1, GitLab                | acquire/reimplement the `.gsz`; headless export                          |
| **Houdini 21 + SideFX Labs**                                                                                                                                                                       | paid (Indie)         | ✅ `hython`                               | **native GLB ROP**                     | ⚠️ build via L-System SOP + leaf/fruit input geo                                                | active H21                    | only if native-GLB + max-programmability wanted                          |
| **CPlantBox** (FZ Jülich)                                                                                                                                                                          | GPL-3.0              | ✅ Python                                 | PlantVisualiser arrays→trimesh GLB     | ⚠️ root+shoot, no fruit/serrated-leaf mesh                                                      | active                        | build the surface mesh ourselves                                         |
| **Infinigen** (Princeton)                                                                                                                                                                          | BSD-3                | ✅                                        | OBJ→GLB (no native; **no decimation**) | ❌ **no tomato; tree-architecture; instanced foliage export hostile**                           | v1.19.1 (installed)           | low priority: custom Bush+fruit factory + realize→decimate→gltf exporter |
| Botaniq (paid) ships ONE fixed tomato mesh (EULA caveat); SpeedTree/PlantFactory/Arbaro/ngPlant/Sapling/Modular-Tree = tree-focused or GUI-bound or abandoned — **dismissed** for a fruiting herb. |

**Leverage / pick:** Helios is the #1 open procedural (real parametric tomato today). AgriGen's own
UnifiedGenerator is the most on-mission (the field's tool, tomato PD, consumed like the scorer).
L-Py is the cleanest fully-open author-it path. These three are the gene→geometry lever.

---

## Bucket 2 — Image→3D (HAVE)

Merged: `app/image3d.py` Tripo direct + fal.ai (`FAL_KEY`) Hunyuan3D v2/v3, Trellis, TripoSR,
Rodin + Replicate (`REPLICATE_API_TOKEN`) Hunyuan3D-3.1, Trellis, Trellis2, Rodin. ~10 generators,
key-gated. **Botanical caveat (verified):** all blob thin foliage on a tomato photo — they are the
"generic generative-3D" baseline the procedural path is measured against, which is exactly what the
audit wants to show. Gap within this bucket: feed MULTIPLE views (see Bucket 4) instead of one.

---

## Bucket 3 — Text→3D (GAP, cheap)

| System               | Aggregator endpoint                                                                        | →GLB | Plant?     | Wire                                              |
| -------------------- | ------------------------------------------------------------------------------------------ | ---- | ---------- | ------------------------------------------------- |
| Tripo P1/H3.1        | fal `tripo3d/p1/text-to-3d`, `tripo3d/h3.1/text-to-3d` (out `model_mesh`)                  | ✅   | organ-only | **new model id on the fal queue we already have** |
| Hunyuan3D v3/3.1     | fal `fal-ai/hunyuan3d-v3/text-to-3d` (out `model_glb`); Replicate `tencent/hunyuan-3d-3.1` | ✅   | organ-only | **new model id**                                  |
| Rodin Gen-2          | fal `fal-ai/hyper3d/rodin`; Replicate `hyper3d/rodin`                                      | ✅   | organ-only | **new model id**                                  |
| Meshy-6 / Luma Genie | first-party only (not on aggregators)                                                      | ✅   | generic    | new integration (same async shape)                |

**Verified:** a whole tomato plant is **out of reach as one-shot text→3D** (Tripo's own foliage
blog treats plants as an artist-refined _component_ workflow). **Use as organ/part generators**;
keep whole-plant assembly procedural. Wiring cost: near-zero (the aggregator adapters take a
`prompt` instead of an image — a small input-mode flag on `generate_fal`/`generate_replicate`, then
new `PROVIDERS` entries). Output-key per model is non-uniform (`model_glb` vs `model_mesh`) → a
tiny per-model result extractor.

---

## Bucket 4 — Multi-view / reconstruction (GAP — the "compose synth views → 3D" idea)

**Novel-view synthesis (1 img → N consistent views):** SV3D (best consistency, NC), SyncDreamer
(MIT, 16 views), Era3D (highest detail, AGPL), Zero123++ (6-view, weights CC-BY-NC). **Multi-view→
mesh:** InstantMesh (Apache-2.0, GLB), TRELLIS multi-view endpoint (`fal-ai/trellis/multi`, MIT,
best topology), Hunyuan3D-2.1, TripoSG, Wonder3D (MIT, GLB). **Photogrammetry/splatting→mesh:**
COLMAP/Meshroom/RealityScan; GOF / 2DGS / SuGaR (all NC Inria 3DGS license).

**Critical verified findings:**

- COLMAP SfM **fails to pose AI-generated views** (rarely multiview-consistent) — _variance in COLMAP
  poses is even used as an inconsistency metric_. Escape hatch: generate views at **known/controlled
  camera poses** → skip SfM → GOF/2DGS for the mesh; or feed multi-views directly to a feed-forward
  multiview→mesh model.
- **Recommended turnkey path (zero local GPU):** synthetic image(s) → `fal-ai/trellis/multi` or
  `fal-ai/hunyuan3d/v2` → textured GLB. One `FAL_KEY`, same submit→poll→download pattern we have.
- **Recommended faithful path:** plant-specific parametric reconstruction — **CropCraft** (inverse
  procedural: images→parametric crop), **Demeter** (ICCV'25, MIT — learned parametric plant-as-graph,
  a "tomato body model"), **NeuraLeaf** (thin-curved-leaf primitive). These complete occluded regions
  and stay gene-controllable, unlike generic feed-forward.

Sources: CropCraft 2411.09693 · Demeter github.com/Tianhang-Cheng/Demeter · NeuraLeaf 2507.12714 ·
Plant-Methods eval 10.1186/s13007-025-01482-6.

---

## Bucket 5 — Neural-procedural / frontier + adjacent (GAP, research-grade)

| Method                                                                                          | Type                                        | Open            | →GLB            | Tomato                                                   | Maturity                      |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------- | --------------- | --------------- | -------------------------------------------------------- | ----------------------------- |
| **PartCrafter** (NeurIPS'25)                                                                    | part-based gen (stem/leaf/fruit) from 1 img | ✅              | per-part meshes | generic; **part-decomp ideal for plants + gene control** | USABLE (new)                  |
| **Hunyuan3D 2.1**                                                                               | open image→textured GLB                     | ✅ full weights | OBJ/FBX/GLB     | generic                                                  | USABLE (self-host ~10GB VRAM) |
| **TRELLIS / TRELLIS.2**                                                                         | 3D-native SLAT                              | ✅ MIT          | GLB             | generic                                                  | USABLE                        |
| **TripoSG / Direct3D-S2**                                                                       | 3D-native rectified-flow                    | ✅              | mesh→GLB        | generic high-res                                         | USABLE                        |
| **GroIMP tomato FSPM (+ dwarf)**                                                                | FSPM, phytomer-based                        | paper→GroIMP    | via GroIMP      | **tomato-specific, botanically grounded**                | USABLE (research)             |
| **PlantDreamer** (ICCVW'25)                                                                     | L-system mesh → photoreal 3DGS              | ✅ Apache-2.0   | 3DGS→mesh       | bean/kale/mint (LoRA for tomato)                         | PROTO                         |
| **Demeter** (ICCV'25)                                                                           | parametric plant-as-graph                   | ✅ MIT          | mesh            | "tomato body model"                                      | PROTO→USABLE                  |
| **Blender-MCP**                                                                                 | LLM authors Blender geo-nodes headless      | ✅              | native GLB      | anything Blender models                                  | USABLE                        |
| Tree-D Fusion / DeepTree / Diff-Tree / AdTree / TreeQSM = **woody-tree only** — wrong organism. |

**Leverage:** PartCrafter (part separation = the bridge between generative speed and gene→part
control) and GroIMP tomato FSPM (most botanically grounded) are the two frontier picks most aligned
with AgriGen's gene→geometry mission. PlantDreamer is the realism-upgrade layer on top of a
procedural tomato.

---

## Prioritized gap roadmap (what to wire, in order)

Filter (user 2.1): only add venues that produce a tomato model we DON'T already have; don't
duplicate the image→3D models already covered.

1. **Procedural — Helios** _(highest value: real built-in tomato, open, headless)_. Wire
   `buildPlant()`→OBJ→GLB→ingest as `source="procedural:helios"`. First gene-controllable faithful entry.
2. **Procedural — AgriGen UnifiedGenerator** _(most on-mission)_. Consume `generate_procedural_geometry`
   read-only (like the scorer); ingest as `source="procedural:agrigen"`. Verify the read-only entry point.
3. **Procedural — L-Py** _(clean open author-it)_. Author `tomato.lpy`; OBJ→GLB; `source="procedural:lpy"`.
4. **Text→3D** _(cheapest: ~1 input-mode flag + new PROVIDERS entries on the existing aggregators)_.
   `source="api:fal:hunyuan3d-v3-text"` etc. Organ generators; flagged as such.
5. **Multi-view / reconstruction** _(new modality)_. Turnkey: synth views → `fal-ai/trellis/multi` → GLB.
   `source="recon:trellis-mv"`. Later: known-pose + GOF for the faithful path.
6. **Frontier — PartCrafter + a GroIMP tomato FSPM** _(research-grade; part-based + botanically grounded)_.
7. **Infinigen tomato** _(low priority, per research)_: custom Bush+fruit factory + realize→decimate→gltf
   exporter, if procedural richness is wanted beyond Helios/L-Py.

Each is its own brainstorm→spec→plan→build increment. Buckets 1–4 are production-usable now (live
runs key-gated where an API key is needed: text→3D + multiview reuse `FAL_KEY`/`REPLICATE_API_TOKEN`).

## AgriGen-leverage note (mission 2.3)

The audit's value to AgriGen: it benchmarks AgriGen's _own_ UnifiedGenerator + tomato PD against the
whole field (commercial image/text→3D, open procedural Helios/L-Py/GroIMP, frontier PartCrafter)
under one scorer (the :8077 GT-band). The thesis above (procedural/FSPM + part-based = faithful;
generative-3D = organ/blob) tells AgriGen where to invest: deepen the procedural PD (richer tomato
compound leaves, already in the phase7b.1 design) and consider part-based generation (PartCrafter)
rather than chasing one-shot generative-3D for faithful whole plants.

## Sources

Procedural: github.com/PlantSimulationLab/Helios · openalea/lpy · openalea/plantgl ·
gitlab.com/grogra/groimp · quantitative-plant.org/model/TomatoGroIMP · github.com/princeton-vl/infinigen.
Text→3D: fal.ai/models (tripo3d, hunyuan3d-v3, hyper3d) · replicate.com/collections/3d-models · meshy.ai ·
lumalabs.ai/api. Multi-view: stability.ai/sv3d · github.com/microsoft/TRELLIS · fal-ai/trellis ·
2411.09693 (CropCraft) · github.com/Tianhang-Cheng/Demeter · 2507.12714 (NeuraLeaf) ·
10.1186/s13007-025-01482-6. Frontier: github.com/wgsxm/PartCrafter · github.com/Tencent-Hunyuan/Hunyuan3D-2.1 ·
github.com/microsoft/TRELLIS.2 · academic.oup.com/insilicoplants (tomato FSPM diaf022, dwarf diaf024) ·
github.com/Lewis-Stuart-11/PlantDreamer · github.com/ahujasid/blender-mcp.
