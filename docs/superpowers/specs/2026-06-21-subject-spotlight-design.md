# Subject Spotlight — design

> Status: approved (brainstorm 2026-06-21). Sub-project 1 of 2; external-model
> sourcing is a separate future spec.

## Goal

A curated, internal **inspection** page that deep-dives one benchmark subject and
shows _every model we have for it_ side-by-side — static render thumbnails with all
metrics, deterministic failure-mode flags, and a qualitative critic note — against
the real reference, with click-to-open live 3D viewers. Built provenance-ready so
externally-sourced models slot in later with no rework.

## Scope

**In:**

- Curated Spotlight pages on our existing data (the recon species: real GT + the
  TRELLIS / Hunyuan3D / InstantMesh outputs).
- Dense, all-metrics-exposed, failure-modes-front layout for internal/audit use.
- Provenance fields on every model (source/license/attribution/external_url) so the
  future external-sourcing pipeline feeds this page unchanged.

**Out (explicitly deferred):**

- External-model sourcing/ingest pipeline (Sketchfab/web/zoos + license capture +
  format conversion + dedup) — its own spec.
- Public-facing polish / editorial narrative — this is an internal tool.
- Live-app LLM calls — the app has no API key wired; qualitative critic notes are
  generated at curation time by the agent and stored.

## Audience & success criteria

Audience = the maintainer auditing model quality. Success = on one page you can see,
for a subject, which models fail and _how_ — fast scanning, every score visible,
failure modes called out — without opening 15 separate viewers.

## Architecture

### Routes

- `GET /spotlight` — index of curated subjects (featured first, then by order).
- `GET /spotlight/<slug>` — the deep-dive page for one subject.
- Linked from `/admin`; **not** in the public top nav (internal tool). No auth gate
  beyond that (page routes in this app are unauthenticated; admin _actions_ use a
  token — Spotlight is read-only so it needs none).

### Curation source

A seed list, consistent with `CATEGORIES`/`TASKS` in `app/seed.py`:

```python
# (subject_task_title, slug, featured, order, blurb)
SPOTLIGHTS = [
    ("Solanum lycopersicum — single-image → 3D reconstruction", "tomato", True, 0, "..."),
    ("Arabidopsis thaliana — single-image → 3D reconstruction", "arabidopsis", False, 1, "..."),
    # ...hand-picked recon species
]
```

`seed_spotlights(db)` is idempotent (get-or-create by slug), called from `seed_all`.
A Spotlight resolves its subject `Task` by title (same pattern as
`synth_task_for_slug`) and shows **all non-gold models on that Task**.

### Schema additions (two, both small)

1. **Provenance on `ModelOutput`** (new nullable columns; SQLite `ALTER TABLE ADD
COLUMN` for the live DB, `create_all` for fresh):
   - `source: str = "bio3d-arena"` — origin of the model.
   - `license: str | None` — license string (null for our own).
   - `attribution: str | None` — credit line.
   - `external_url: str | None` — canonical link; **null ⇒ hosted locally**, non-null
     ⇒ linked-not-hosted (for redistribution-restricted external models later).
2. **New `Critique` table** (one row per output, mirrors `Metric`'s upsert-by-output
   shape):
   - `output_id` (unique FK), `render_path: str | None`, `critic_note: str = ""`,
     `dists: float | None`, `dreamsim: float | None`, `status`, `computed_at`.

No data lived only in templates; both are additive and backward-compatible.

## Components

### 1. Metric-flag deriver (`app/spotlight.py::derive_flags`)

Pure function: `derive_flags(metric: Metric) -> list[Flag]`. Deterministic, uses
existing `Metric` fields only. Rules (initial):

- `chamfer is None / status != "ok"` → `("unscored", "no objective score")`.
- `chamfer > gt_band_hi` (band p75) → `("shape", "outside natural variation")`.
- `gt_band_lo <= chamfer <= gt_band_hi` → `("ok", "within natural variation")`.
- `coverage < 0.5` → `("coverage", "missing geometry")`.
- `fscore < 0.5` (when present) → `("surface", "low F-score@τ")`.
  Each `Flag = (kind, label)`; `kind` drives a CSS severity class. No new infra.

### 2. Render pipeline (`scripts/render_spotlight.py`)

Headless thumbnail capture, reusing the existing renderer:

- Playwright (already installed) loads a minimal page embedding
  `<model-viewer src=<glb> camera-orbit=<fixed> environment-image=neutral>`.
- Waits for `model-viewer`'s `load` event, captures a PNG (fixed orbit ⇒ comparable
  framing across models), writes it under the asset store, sets `Critique.render_path`.
- Batch over a subject's outputs; **commit per output** (same SQLite write-lock
  discipline as `recon_service.rescore_all` — never hold the lock across the
  Playwright render).
- Best-effort: a render failure stores `status="error"` and continues.

### 3. Critic (Phase 2)

Per render, two independent signals:

- **Perceptual (in-app, deterministic):** DISTS + DreamSim distance of the render vs
  the reference image (the rose-eval tech). Stored as `Critique.dists/.dreamsim`.
- **Qualitative note (agent-generated at curation time):** the agent renders +
  critiques each model and writes `Critique.critic_note` ("flat petals", "melted
  core") — what chamfer can't see. Refreshed when data changes. Subject to the
  independent-critic doctrine (a fresh adversarial critic, reference-grounded,
  before notes are trusted). Not a live app dependency.

### 4. Reference panel

Top of each page. Priority order:

- A **real photo** of the subject if curated (public-safe) — preferred.
- An **internal GT render** if GT is accessible: rendered (read-only) from the AgriGen
  `gt_bundle_prod` mesh matched by `nearest_gt_idx`. **D2 constraint:** GT-derived
  images are internal-only and never served on a public route; if Spotlight is ever
  made public, the GT panel hides (same rule `recon_service.reference_for_task`
  already enforces). Fallback if no GT access: show the matched-GT-index metadata
  only, no image.

### 5. Page template (`app/templates/spotlight.html`)

- Reference panel (above).
- Render grid: one card per model — thumbnail, metrics table (chamfer, F@τ, coverage,
  GT band lo/hi, within-variation verdict), flags (from `derive_flags`), critic note,
  provenance line (source · license · attribution, link if `external_url`).
- Click a thumbnail → open one live `<model-viewer>` (camera-synced to the reference).
  At most one live context at a time (the reason we chose render-grid over a live
  grid).

## Data flow

```
seed_spotlights ──► Spotlight rows (curated subjects)
                         │
/spotlight/<slug> ──► resolve Task ──► its ModelOutputs
                         │                  ├─ Metric ──► derive_flags ─► flags
                         │                  ├─ Critique ─► thumbnail + note + DISTS/DreamSim
                         │                  └─ provenance (source/license/...)
                         └─ reference (real photo | internal GT render)
render_spotlight.py (batch) ──► Playwright + model-viewer ──► PNG ──► Critique.render_path
```

## Phasing

**Phase 1 — the tool (ships first):**
schema (provenance + `Critique`) · `seed_spotlights` · `derive_flags` · render
pipeline (thumbnails) · `/spotlight` + `/spotlight/<slug>` · template (grid, metrics,
flags, click-to-live, reference panel). Critic note + perceptual fields exist but may
be empty. This alone is a usable inspection tool on real data.

**Phase 2 — critic enrichment:**
DISTS/DreamSim perceptual scores + agent-generated qualitative notes populate the
`Critique` fields; reference-grounded independent-critic gate.

## Error handling

- Render failure → `Critique.status="error"`, card shows "render failed", page still
  loads (best-effort, never 500 the page).
- Missing `Metric` (unscored output) → flags `("unscored", …)`, metrics show "—".
- Missing GT/reference → reference panel degrades to metadata-only.
- Empty curated subject (no models) → page renders with an "no models yet" notice.

## Testing

- **Unit:** `derive_flags` over crafted `Metric` fixtures (each rule + boundary);
  `seed_spotlights` idempotency + Task resolution; provenance defaults on new
  `ModelOutput` (`source == "bio3d-arena"`, `external_url is None`).
- **Real-execution:** `render_spotlight.py` actually captures a real GLB → non-empty
  PNG via Playwright (boundary check per the real-execution doctrine).
- **Page:** Playwright screenshot of `/spotlight/<slug>` populated; assert the grid
  renders N cards, flags appear, no console errors.
- **Gate:** independent adversarial critic on the rendered page before merge.

## Future (separate specs)

- External-model sourcing pipeline (feeds this page via the provenance schema).
- Making Spotlight public (would require the GT panel to hide and editorial polish).
