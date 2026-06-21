# External-model sourcing (Objaverse-first) — design

> Status: approved (brainstorm 2026-06-21). Sub-project 2 of the Subject Spotlight.
> Feeds the existing /spotlight grid via the provenance schema built in Phase 1.

## Goal

Make a subject spotlight a grounded audit of the _whole field_ of 3D models for one
organism — not just our 3 reconstruction methods. Pull license-vetted, depiction-labeled
tomato 3D models from Objaverse onto the existing tomato spotlight Task, so the grid shows
AI-recon vs artist-found models side by side.

## Decisions (locked at brainstorm)

- **Source:** Objaverse first (`objaverse` pip package). `load_lvis_annotations()` (1156
  categories → uids), `load_annotations(uids)` (per-object license + name + author +
  source url), `load_objects(uids)` (downloads `.glb`).
- **License policy (private/internal tool — host permissively now, clean up before any
  public exposure):**
  - **HOST** any Creative-Commons or public-domain license, _including_ NC/ND variants
    (cc0, cc-by, cc-by-sa, cc-by-nc, cc-by-nc-sa, cc-by-nc-nd, cc-by-nd, public-domain).
    Internal non-commercial research display is compliant.
  - **EXCLUDE** all-rights-reserved, proprietary, and unmarked/unknown. Hosting clearly
    restricted content is not undone by "clean up later" and is a pointless risk.
  - **Record the exact license** on every model (the `ModelOutput.license` provenance
    column) so the pre-public cleanup is a query.
- **HARD CONSTRAINT:** `/spotlight` stays **internal-only** (linked from `/admin`, NOT in
  the public nav) until a pre-public license re-vet. That re-vet downgrades NC/ND models to
  link-only or removes them and keeps only CC0/CC-BY/CC-BY-SA public. The
  `external_url`/`license` fields make it a filter.
- **Subject scope:** broad + auto-labeled. Ingest any tomato model; label depiction
  (whole_plant / fruit / leaf / other). Only whole-plant models are scored against the GT
  band; fruit/leaf/other are shown as found-reference, unscored. (Objaverse "tomato" is an
  object-detection category → expect mostly fruit; that the field is fruit-not-plant is
  itself a finding.)
- **Representation:** reuse `ModelOutput` (no new table, no link-only path in v1 — link-only
  is part of the future public-cleanup increment). `ModelOutput.asset_path` stays NOT NULL.

## Components

### 1. `app/sourcing.py` (pure, unit-tested — no I/O)

- `classify_license(license_str: str | None) -> "host" | "exclude"`
  - normalize/lower the string; `host` if it matches a CC or public-domain pattern
    (`cc0`, `cc-by`, `cc by`, `creativecommons`, `public domain`, `cc-by-nc`, `cc-by-nd`,
    `cc-by-sa`, and their combinations); else `exclude` (covers "All Rights Reserved",
    empty, None, unknown).
- `public_safe(license_str: str | None) -> bool` — True only for CC0/CC-BY/CC-BY-SA (used
  by the FUTURE pre-public cleanup; not enforced in v1, but defined now so the cleanup is
  trivial).
- `label_depiction(text: str) -> "whole_plant" | "fruit" | "leaf" | "other"` — keyword
  heuristic over the object name + caption: contains plant/vine/bush/seedling/sapling →
  whole_plant; leaf/foliage → leaf; tomato/fruit/cherry/produce (and not a plant word) →
  fruit; else other.

### 2. `scripts/source_objaverse.py` (the pipeline)

1. **Query** — `load_lvis_annotations()`; take the "tomato" category's uids (case-insensitive
   match). Fallback if absent: scan `load_annotations()` names for a "tomato" substring.
   Cap candidates at `--limit` (default 40).
2. **Annotate + filter** — `load_annotations(candidate_uids)`; for each: read `license`,
   `name`, author/attribution, source url; `classify_license` → keep `host`, drop `exclude`
   (count the drops). Relevance: name/caption mentions tomato.
3. **Download + register** — `load_objects([uid])` → local `.glb`; copy into the asset store;
   `ingest.register_output(db, task_id=<tomato>, generator_slug="objaverse", generator_name=
"Objaverse", data=…, ext="glb", title=<object name>, meta={"depiction": <label>,
"objaverse_uid": uid, "found": True})` and set provenance on the row: `source="objaverse"`,
   `license=<raw license>`, `attribution=<author>`, `external_url=<source url>`. A SINGLE
   `objaverse` generator backs all found models (no per-object Generator rows); the card label
   comes from the output `title` (the object name), not the generator. Idempotent
   (content-dedup via register_output; re-runs skip existing).
4. **Score** (whole_plant only) — call the existing recon scorer
   (`recon_service.score_and_store` / `recon_client.score_output`) so found whole-plant
   models get a `Metric` vs the tomato GT band, exactly like AI recons. Fruit/leaf/other are
   left unscored (`derive_flags` already yields the "unscored" flag).
5. **Render** — reuse `scripts/render_spotlight.py` to capture thumbnails for the new hosted
   models (it operates per-output; just include them in the slug's outputs).
6. **Report** — print: hosted N (by depiction), excluded N (by reason: ARR/unmarked),
   so nothing is silently dropped.

### 3. Spotlight grid grouping (extend `app/spotlight.py` + `spotlight.html`)

- `build_spotlight` adds to each model dict: `found = (source != "bio3d-arena")`,
  `depiction = meta.get("depiction")`, a `label` (= the output `title`/object name for found
  models, else the generator name), and the existing provenance.
- The template uses `label` as the card heading (so the 40 found models read as their object
  names, not a repeated "Objaverse"), and groups cards into sections by **class**: "AI
  reconstruction" (source=bio3d-arena) and "Found — <depiction>" (whole plant / fruit / leaf
  / other), each with a small source badge and the attribution/license line. Hosted found
  models render exactly like AI ones (thumbnail + click-to-live); they already carry
  `derive_flags` (scored whole-plant) or the unscored flag (fruit/leaf).

## Data flow

```
objaverse LVIS "tomato" ─► candidate uids
   └─ load_annotations ─► license/name/author/url
        └─ classify_license: host? ──no──► count excluded (ARR/unmarked)
              └─yes─► load_objects ─► GLB ─► register_output (tomato Task,
                        source=objaverse, license, attribution, external_url,
                        meta.depiction)
                        └─ whole_plant? ─► recon scorer ─► Metric
                        └─ render_spotlight ─► thumbnail
/spotlight/tomato ─► build_spotlight groups AI-recon vs Found×depiction
```

## Error handling

- A candidate that fails download/convert/score is logged and skipped (best-effort; the run
  reports failures). One bad object never aborts the batch.
- `objaverse` network failure → the script exits non-zero with a clear message; nothing
  half-registered (register only after a successful download).
- No "tomato" LVIS category → fall back to name-substring scan; if still empty, report and
  exit 0 (nothing to add).

## Testing

- **Unit:** `classify_license` over every CC variant + "All Rights Reserved" + "" + None
  (host/exclude verdicts); `public_safe` (CC0/BY/SA true, NC/ND false); `label_depiction`
  over representative names ("tomato plant in pot"→whole_plant, "ripe tomato"→fruit,
  "tomato leaf"→leaf, "tomato soup can"→other).
- **Real-execution (paired with the synthetic tests, per doctrine):** pull ONE real
  CC0/CC-BY tomato object from Objaverse end-to-end against a temp DB — assert a
  `ModelOutput` is registered on the tomato Task with `source="objaverse"`, a non-empty
  `license`, an `external_url`, a `depiction` in meta, and a real on-disk GLB. Network-gated;
  if Objaverse is unreachable, the test skips with a clear reason (not a silent pass).
- **Page:** after a real pull, `GET /spotlight/tomato` returns 200 and the grid shows a
  "Found" group; independent-critic gate on the rendered page.

## Out of scope (future increments)

- Link-only cards for NC/ND + the pre-public license cleanup (downgrade/remove) — driven by
  the recorded `license`/`public_safe`.
- Other sources (Sketchfab-CC, academic scans).
- API-generation of new models (Meshy/Tripo/Rodin) — needs the user's keys/authorization.
