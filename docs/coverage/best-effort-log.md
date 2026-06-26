# Best-effort coverage sources — Arabidopsis (task 10) + Pine (task 13)

Task 6 of the coverage-parity build. These five sources are **upside, not gates** — both
subjects already meet parity (Arabidopsis 25 outputs / 11 sources; Pine 24 / 10) via the
7 recon APIs + PartCrafter + L-Py + procedural (+ ROMI scan for Arabidopsis). Each source
below was attempted; where the generator/library does not support the species or no clearly
CC-licensed asset exists, it is skipped with a one-line reason (honesty contract: no
fabricated/mislabeled/wrong-species entries — skipping always beats a dishonest entry).

Attempted 2026-06-26.

| Source         | Arabidopsis (task 10) | Pine (task 13)        |
| -------------- | --------------------- | --------------------- |
| agrigen        | SKIPPED               | SKIPPED               |
| Demeter        | SKIPPED               | SKIPPED               |
| Sketchfab      | **FILLED — 3 assets** | **FILLED — 4 assets** |
| Objaverse      | SKIPPED               | SKIPPED               |
| Pine real scan | n/a                   | SKIPPED               |

## Per-source detail

### 1. agrigen

- **arabidopsis: SKIPPED** — no plant descriptor in agrigen repo. AgriGen's `UnifiedGenerator`
  only ships tomato/maize/rose plant descriptors (`AGRIGEN_CROPS` = tomato/maize/rose); the only
  arabidopsis/pinus hits under `~/agrigen` are paper-search test fixtures, not procedural
  descriptors. No code change.
- **pine: SKIPPED** — same reason (no Pinus/conifer plant descriptor in agrigen repo). No code change.

### 2. Demeter

- **arabidopsis: SKIPPED** — Demeter is cereal/broad-leaf-specific; supported `--species` are
  maize / soybean (CropCraft) and tobacco / maize / rose (finetune). No Arabidopsis morphable
  basis. No code change.
- **pine: SKIPPED** — no conifer/Pinus species in Demeter (`fit_single.py` asserts
  `species in ['maize','soybean']`). No code change.

### 3. Sketchfab (found CC game-ready / scan assets)

- **arabidopsis: FILLED — 3 assets.** All verified on the Sketchfab v3 API 2026-06-26 (license
  slug `by` → CC-BY 4.0, `isDownloadable=True`, not age-restricted) and genuinely _Arabidopsis
  thaliana_:
  - `e79922a7f1ea418ab928f2fa8c06cd48` — "Arabidopsis thaliana model" by **lamujer** — CC-BY 4.0
  - `1dc6184c333e44c68efee36eb1922ea0` — "Arabidopsis sauvage" (wild-type) by **evolution.biologique** — CC-BY 4.0
  - `3992abef3af94d78a8344315444044ed` — "Arabidopsis mutant Agamous" by **evolution.biologique** — CC-BY 4.0
- **pine: FILLED — 4 assets.** All verified on the Sketchfab v3 API 2026-06-26 (CC-BY 4.0,
  downloadable, not age-restricted), genus Pinus / conifer whole trees (the c3posw01 set is
  explicitly Scots pine = the subject species _Pinus sylvestris_; the rest are honest
  "pine tree" depictions, mirroring the genus-level maize/rose precedent):
  - `422b961ff3d14e7baa7e9077572b2247` — "Scots Pine Trees Set" by **c3posw01** — CC-BY 4.0
  - `ba7c8f8e1cb549b3a3a30f1221386c8c` — "Pine tree 01" (photoscan) by **POLYSCAN3D** — CC-BY 4.0
  - `99fb6a37547840e3a295689df032ba28` — "Low Poly Pine Tree" by **epicwolfstudio** — CC-BY 4.0
  - `9dfbe65769c840a0ab366c67d8e6762d` — "Pine Tree" by **emarshall** — CC-BY 4.0

  Code: added `PINE_ASSETS` / `ARABIDOPSIS_ASSETS` + `CROPS["pinus"]` / `CROPS["arabidopsis"]`
  to `scripts/generate_sketchfab.py`; ingested via
  `.venv/bin/python scripts/generate_sketchfab.py --crop {pinus,arabidopsis} --no-score`
  (live Sketchfab download + Blender glTF→GLB convert). `--no-score` because the recon scorer
  microservice was not running; objects are hosted regardless and can be backfilled by rescore_all.

### 4. Objaverse

- **arabidopsis: SKIPPED** — no usable LVIS category. Objaverse LVIS (1156 categories) has no
  "arabidopsis"/"thale cress"/generic "plant" category; the only `*plant*` cats are eggplant /
  sugarcane / window-box. Adding a CROPS entry would yield zero on-subject assets. Better skip
  than mislabel. No code change.
- **pine: SKIPPED** — no usable LVIS category. The only pine-adjacent cats are `pineapple`,
  `pinecone` (the cone, not the plant), and `Christmas_tree` (decorated/artificial trees, not
  _Pinus sylvestris_ whole plants). Routing any of these onto the Pinus subject would mislabel
  wrong-species/non-plant assets. No code change.

### 5. Pine real scan

- **pine: SKIPPED — clearly-licensed Pinus sylvestris scan datasets exist, but importing exceeds
  the best-effort budget.** A brief search located CC-licensed candidates that DO contain
  _Pinus sylvestris_ single-tree point clouds — BioDiv-3DTrees (Nature Scientific Data 2025,
  10.1038/s41597-025-06421-7; Scots pine among the 3 most frequent species) and the
  multi-platform German-forest TLS dataset (ESSD 14:2989, 2022; 158 _P. sylvestris_ trees). Both
  are multi-GB terrestrial-laser-scan archives of _mature forest trees_ (7–40 m). Downloading +
  per-tree extraction + point-cloud import would run long (well past the ~8-min best-effort cap),
  and the mature-forest-tree depiction diverges from the "Scots pine sapling" subject framing.
  Deferred — no code change. (If pursued later: register under `SCAN_DATASETS` + add
  `SCAN_TASKS["pinus"]` and import via `scripts/source_scans.py --render points`, mirroring ROMI.)

## Net effect

- Arabidopsis (task 10): 25 → 28 outputs (+3 `found:sketchfab`).
- Pine (task 13): 24 → 28 outputs (+4 `found:sketchfab`), adds a new source class (`found`).
