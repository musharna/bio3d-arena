# Taxon3D — Design Doc

> Date: 2026-06-20
> Status: MVP design, approved-by-default (background build; user can redirect)

## 1. Purpose

A public, Chatbot-Arena–style platform for **anonymous pairwise comparison and
voting on biological 3D model generations**. Users see a biological task and two
anonymous 3D outputs side-by-side, inspect/rotate/zoom both, and vote for the
better one. Votes feed Elo + Bradley–Terry rankings and leaderboards for the
underlying generators.

Designed to grow into:

1. A community-driven benchmark platform for biological 3D generation.
2. A repository of biological 3D assets + benchmark tasks.
3. A research platform for evaluating biological realism, morphology, structural
   accuracy, visual quality, and scientific usefulness.

## 2. Architecture

Single FastAPI application, server-rendered (Jinja2) + vanilla JS frontend,
SQLite via SQLAlchemy ORM, 3D rendered client-side by Google `<model-viewer>`.
Packaged as one Docker container with a mounted volume for the DB + asset blobs.

```
Browser (model-viewer + vanilla JS)
        │  HTML + JSON
        ▼
FastAPI app  ── ranking.py (Elo + Bradley–Terry)
        │      ── matchmaking.py (pair selection)
        │      ── admin routes (CRUD)
        ▼
SQLAlchemy ORM ──> SQLite file  (data/arena.db)
Static assets   ──> data/assets/*.glb
```

**Why this stack** (balancing simplicity / scalability / maintainability / cost /
extensibility):

- **FastAPI** — async, typed, auto OpenAPI docs, Python-native (fits the user's
  conda toolchain). Easy to expose a JSON API for future SPA/clients.
- **SQLite + SQLAlchemy** — zero-cost, single file, trivial deploy. ORM means a
  one-line engine swap to Postgres when write concurrency demands it.
- **`<model-viewer>`** — battle-tested GLB/GLTF viewer with built-in
  orbit/zoom/pan, no build step. A **viewer registry** keyed on asset format lets
  us add Mol\*/3Dmol.js (proteins, mmCIF/PDB) and point-cloud viewers later
  without touching the voting flow.
- **Jinja2 + vanilla JS** — no frontend build pipeline; one deployable artifact.
  Prioritizes functionality over polish, as requested.

## 3. Data Model

Extensible taxonomy + multi-axis criteria are first-class.

| Table          | Key fields                                                                | Purpose                                                                                                             |
| -------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `category`     | id, slug, name, description                                               | Taxonomy: plants, flowers, crops, roots, fungi, cells, tissues, organs, proteins, molecules… (seedable, extensible) |
| `criterion`    | id, slug, name, description                                               | Evaluation axes: overall, realism, morphology, structural_accuracy, visual_quality, scientific_usefulness           |
| `task`         | id, category_id, title, prompt, criteria_note, reference_asset_id, active | A benchmark task / prompt                                                                                           |
| `generator`    | id, slug, name, description, is_anonymous, kind                           | A model/system under evaluation (anonymized in the arena)                                                           |
| `model_output` | id, task_id, generator_id, asset_path, asset_format, title, meta_json     | One 3D asset produced by a generator for a task                                                                     |
| `comparison`   | id, task_id, output_a_id, output_b_id, criterion_id, session_id, created  | A pair shown to a voter (audit trail)                                                                               |
| `vote`         | id, comparison_id, winner ('a'/'b'/'tie'/'bad'), session_id, created      | The recorded judgment                                                                                               |
| `rating`       | id, generator_id, category_id(null=global), criterion_id, elo, bt_score,  | Cached ranking per (generator × scope × criterion)                                                                  |
|                | bt_lower, bt_upper, n_games, updated                                      |                                                                                                                     |

Notes:

- `category_id`/`criterion_id` nullable on `rating` → supports global + sliced
  leaderboards from the same table.
- `meta_json` on `model_output` holds free-form provenance (generator version,
  prompt seed, vertex count, source) without schema churn.
- `session_id` is an anonymous cookie/uuid for light dedup + per-session history;
  no accounts in the MVP.

## 4. Ranking Methodology

Two complementary systems, mirroring Chatbot Arena:

1. **Online Elo** (`ranking.elo_update`) — updated on every vote for instant
   feedback. K=32 default, ties = 0.5 score. Cheap, drifts with order.
2. **Bradley–Terry MLE** (`ranking.bradley_terry`) — authoritative leaderboard.
   Fits logistic strengths to the full pairwise win matrix via iterative
   minorization-maximization (no SciPy dependency). **Bootstrap resampling of the
   vote list** yields 95% confidence intervals → rank generators with CIs, not
   just point estimates. Computed globally and per (category, criterion) slice.

Ties and "both bad" votes are recorded but, in the MVP, excluded from BT fitting
(counted in Elo as draws). Leaderboard = BT score (desc) with CI; Elo shown as a
secondary live column.

## 5. Matchmaking

`matchmaking.pick_pair(task)` selects two distinct outputs for a task,
preferring under-sampled outputs (fewest prior comparisons) to maximize ranking
information per vote, with random tie-breaking. Falls back to a random active
task when none is specified.

## 6. Admin Tools

Minimal token-gated admin UI + JSON endpoints (`/admin`, `ADMIN_TOKEN` env):

- Create categories, criteria, tasks, generators.
- Upload a `model_output` (GLB file + task + generator + metadata).
- Trigger a Bradley–Terry leaderboard recompute.
  No auth framework in the MVP — a shared bearer token kept out of logs.

## 7. Deployment

`Dockerfile` builds the app; `docker run -v $PWD/data:/app/data` persists DB +
assets. `uvicorn app.main:app`. Works on Fly.io / Railway / a VPS / the homelab.
SQLite + a volume = lowest possible operating cost for the MVP.

## 8. Extensibility Hooks (built in, not yet exercised)

- **Viewer registry** keyed on `asset_format` → molecular/point-cloud viewers.
- **Nullable scope columns** on `rating` → arbitrary leaderboard slices.
- **`meta_json`** provenance bag → research metadata without migrations.
- **Criteria table** → add evaluation axes by inserting a row.
- **JSON API** alongside HTML → future SPA, programmatic submission, dataset export.

## 9. Roadmap / Build Stages

1. Data model + DB + seed (procedural GLB demo assets via trimesh).
2. Pairwise voting: matchmaking + arena page + dual `<model-viewer>` + vote API.
3. Elo update on vote + leaderboard page.
4. Admin: CRUD + GLB upload + recompute trigger.
5. Bradley–Terry + bootstrap CIs + per-category/criterion slices.
6. Anti-abuse polish: session dedup, rate hints, "report bad asset".

## 10. Testing

- `tests/test_ranking.py` — Elo symmetry/monotonicity; BT recovers known
  strength ordering on synthetic data; CI bounds sane.
- `tests/test_api.py` — seed → request a pair → vote → rating moves; admin CRUD;
  leaderboard renders.
  Real-execution check: app boots under uvicorn and serves the arena + a real GLB.

## 11. Out of Scope (YAGNI for MVP)

User accounts/OAuth, real-time websockets, CDN, moderation queue, model auto-
submission API hardening, molecular-format viewers (hooks only), multi-tenancy.
