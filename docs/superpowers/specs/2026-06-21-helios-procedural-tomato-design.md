# Helios procedural tomato — design

> Status: approved (brainstorm 2026-06-21). Roadmap item #1 from the tomato 3D-generation field
> map: the first gene-controllable, botanically-faithful procedural tomato for the spotlight's
> Procedural column. Mirrors the merged Infinigen ingest pattern; build-gate FIRST (the Infinigen
> lesson — verify Helios makes a recognizable tomato before wiring).

## Goal

Generate a real tomato plant with Helios's built-in `TomatoParameters`, convert OBJ→GLB, and
ingest it as `source="procedural:helios"` — scored and critic-gated like every other generator.
Helios is the only open tool with a genuine built-in parametric tomato (verified in source), so
it's the fastest path to a recognizable, gene-controllable procedural tomato.

## Verified feasibility (2026-06-21)

- **Helios = C++/CMake** (github.com/PlantSimulationLab/Helios, GPL-2.0). The PyPI `pyhelios`
  (2.2.4) is a DIFFERENT package (a CFD toolbox by Julien Vanharen, MIT) — a NAME COLLISION; do
  NOT `pip install pyhelios`.
- `TomatoParameters` confirmed in `plugins/canopygenerator/include/CanopyGenerator.h` +
  `plugins/canopygenerator/src/tomato.cpp` — fields `leaf_length`, `leaf_subdivisions`,
  `shoot_color`, `plant_height`, `fruit_radius`, `fruit_color`, `fruit_subdivisions`;
  `CanopyGenerator::buildPlant()` (single plant); `Context::writeOBJ(file, write_normals)`.
- `cmake`/`g++` present. Geometry + OBJ export are **CPU-only** (no GPU; ray-tracing plugins not needed).
- Output is a physiology-styled mesh (leaves as textured patches, tuned for radiation sims) — a
  reproducible parametric tomato; the independent-critic gate judges fitness.

## Decisions (locked)

- **Direct C++ project, not PyHelios.** Helios's own `projects/` pattern (a dir with
  `CMakeLists.txt` + `main.cpp` linking `core` + the `canopygenerator` plugin) is built for this
  and avoids PyHelios's partial-wrapper risk.
- **Build-gate FIRST.** Build Helios + the `tomato_gen` project, generate ONE tomato OBJ, and
  independent-critic-eyeball it BEFORE wiring the ingest. If Helios's tomato isn't recognizable,
  we learn before investing (unlike the Infinigen grind).
- **`procedural:*` prefix convention.** `source_class` returns `"procedural"` for
  `source.startswith("procedural:")` (plus the existing `"infinigen"`). Helios =
  `"procedural:helios"`; future `procedural:agrigen` / `procedural:lpy` just work.
- **Mesh, decimated** via the existing `mesh_convert.to_glb(max_faces=...)` — no new converter.
- Helios is its own build under `~/Helios`, invoked by the adapter via subprocess; NOT a dep of
  the app `.venv`.

## Components

### 1. Build gate (operational, FIRST task)

Permanent-clone Helios to `~/Helios` (with submodules if any). Write a Helios project
`~/Helios/projects/tomato_gen/` = `CMakeLists.txt` (links `core` + `canopygenerator`) + `main.cpp`:

```cpp
#include "Context.h"
#include "CanopyGenerator.h"
int main(int argc, char** argv) {
    helios::Context context;
    CanopyGenerator cgen(&context);
    TomatoParameters params;                 // override fields from argv (seed, plant_height, fruit_radius...)
    params.buildPlant(cgen, helios::make_vec3(0,0,0));
    context.writeOBJ(argv[1], true);         // out path; write_normals=true
    return 0;
}
```

(Verify the exact `buildPlant` signature + how the canopygenerator exposes per-plant build against
the live `CanopyGenerator.h` at build time — it may be `cgen.buildCanopy(params)` for a 1×1 canopy.)
Build via CMake; run → `tomato.obj`; convert to GLB (`mesh_convert.to_glb`, decimated); render +
independent-critic eyeball. GO/NO-GO on a recognizable tomato.

### 2. `source_class` generalization (`app/sourcing.py`)

`source_class` returns `"procedural"` for `source == "infinigen"` OR
`source.startswith("procedural:")`. Existing ai/scan/found/api unchanged; the spotlight Procedural
group (added in the Infinigen increment) renders it with no template change.

### 3. `scripts/generate_helios.py` — the ingest adapter

Mirror of `scripts/generate_infinigen.py`:

- `ingest_helios(db, obj_paths, *, to_glb, score_fn=None, task_title=TOMATO_TITLE, limit=10) -> dict`:
  per OBJ → `to_glb` (skip MeshConvertError → counted) → `register_output(generator_slug="helios",
generator_name="Helios", meta={"depiction":"whole_plant","generator":"helios","render":"mesh"})`
  → set `out.source="procedural:helios"`, `out.license="GPL-2.0 (Helios, UC Davis Bailey Lab)"`,
  `out.attribution="Helios procedural tomato (CanopyGenerator)"`,
  `out.external_url="https://github.com/PlantSimulationLab/Helios"` → per-object commit → isolated
  scoring (guarded inner + outer rollback). Returns `{"hosted","skipped","errors","by_generator"}`.
- `main()`: run the built `~/Helios/projects/tomato_gen` binary for N seeds (subprocess, with a
  wall-clock timeout) → glob the OBJs → `ingest_helios(to_glb=lambda p: mesh_convert.to_glb(p,
max_faces=150_000), score_fn=recon_service.score_and_store)`. If the binary is missing → clear
  "build the Helios tomato_gen project first" message, non-zero exit.

### 4. Render + independent-critic gate (operational)

`render_spotlight.py` (gray-bg) → independent visual critic: does it read as a recognizable tomato
plant? My read is never the terminal gate.

## Error handling

- Unconvertible OBJ → MeshConvertError, skipped + counted. Per-object best-effort; one bad OBJ
  never aborts the batch. Binary/build absent → clear message, non-zero exit. Subprocess failure
  (timeout/non-zero) → clear error, no partial ingest.

## Testing

- **Unit (fixture OBJ, no Helios needed):** `source_class("procedural:helios") == "procedural"`
  (and `procedural:agrigen`, keep `infinigen`); `ingest_helios` with a synthetic `trimesh` box OBJ
  - injected `to_glb` registers a `procedural:helios` ModelOutput with GPL provenance + correct
    meta; uses a UNIQUE generator label in the read-back to avoid the shared-test-DB collision (the
    Infinigen Critical lesson). `build_spotlight` puts it in the `procedural` class; a template-render
    test confirms it lands under "Procedural (...)".
- **Real-execution (build-gated):** the Helios build + tomato generation + critic eyeball
  (operational); the synthetic-tested adapter ships independent of the live build.

## Out of scope (future increments)

- AgriGen UnifiedGenerator (roadmap #2), L-Py (#3), text→3D (#4), multi-view recon (#5), PartCrafter/
  GroIMP-FSPM (#6), Infinigen-tomato (#7) — each its own increment.
- Gene→`TomatoParameters` mapping for breeding control (a later enhancement once the entry is live).
- Helios radiation/texture realism passes.
