# Structure Validation Track — Design Spec (Increment 4)

> Taxon3D · 2026-06-20 · audit item **B3** ("CASP-style structure-validation track,
> distinct from the aesthetic vote — our strongest differentiator").

## Goal

Add an **objective, computed** structure-validation track, separate from the human aesthetic
vote, that scores molecular outputs (PDB/SDF/mmCIF) on physical validity (reference-free) and,
when a task has a ground-truth reference, on structural similarity (reference-based). This is the
moat: an objective ranking general image-to-3D arenas cannot produce, and the direct counter to
the presentation confound (pretty-but-physically-impossible structures winning the aesthetic vote).

## Non-goals (YAGNI / explicit boundaries)

- **Not** MolProbity-grade. Ramachandran uses a coarse allowed-region table and is labeled
  "approximate," not the real MolProbity contour kernels. We say "MolProbity-style," never "MolProbity."
- **Not** sequence-independent alignment. Reference-based metrics require equal-length atom
  correspondence (the realistic "model a known target" case). Length mismatch → honest `n/a`,
  not a reimplementation of TM-align.
- **Not** shown inline in the blind A/B vote. Validation is a separate surface so it cannot bias
  the aesthetic vote or leak generator identity.
- **No** external binaries, **no** network calls. Pure Python (numpy already a dependency).

## Architecture

One new module `app/validation.py`, two independent pure functions plus a small parser:

### Parsing

- `parse_atoms(text: str, fmt: str) -> list[Atom]` where `Atom = (element, name, resn, resi, xyz)`.
  - **PDB/ENT/mmCIF-as-PDB**: `ATOM`/`HETATM` fixed-column records (element from cols 77-78, falling
    back to the atom name); φ/ψ needs backbone `N`, `CA`, `C` atom names + residue index.
  - **SDF/MOL** (V2000): counts line → atom block (x,y,z,element) → bond block (a,b,order). Bonds are
    explicit, so bond-geometry uses them directly.
  - Unparseable / zero atoms → raise `ValidationError` (caller converts to `{status:"error"}`).

### 1. Reference-free stereochemistry

`validate_structure(text: str, fmt: str) -> dict`

- **Clashscore**: for all non-bonded atom pairs (exclude 1-2 and 1-3 bonded neighbors when
  connectivity is known; for PDB without CONECT, exclude pairs within the same residue that are
  ≤2 sequential), count pairs with `dist < (vdw[a] + vdw[b] - 0.40 Å)`. Report `clashes_per_1000 =
1000 * n_clash / n_atoms`. VDW radii: small element table (`H 1.10, C 1.70, N 1.55, O 1.52,
P 1.80, S 1.80, default 1.70`).
- **Bond-geometry outliers**: for each explicit bond (SDF) or detected bond (PDB CONECT / ≤1.9 Å),
  z-score the length against an ideal element-pair table (`C-C 1.54, C-N 1.47, C-O 1.43, C-H 1.09,
default 1.50`, σ=0.10). Count `|z| > 4`.
- **Ramachandran outliers** (protein PDB only — needs ≥1 residue with N/CA/C): compute φ/ψ per
  residue; count residues outside a coarse allowed-region table. Skipped (`null`) for non-protein.
- **validity_score** ∈ [0,100]: `100` minus penalties — `min(60, 6*clashes_per_1000)` −
  `min(25, 5*bond_outliers)` − `min(15, 3*rama_outliers)`, floored at 0.
- **tier**: `clean` (≥90), `minor` (70–89), `major` (<70).
- Returns `{status:"ok", n_atoms, clashes_per_1000, bond_outliers, rama_outliers (or null),
validity_score, tier}`.

### 2. Reference-based similarity

`compare_to_reference(out_text: str, ref_text: str, fmt: str) -> dict`

- Extract comparison atoms in order: protein → `CA` atoms; small molecule → all heavy atoms.
- If `len(out) != len(ref)` or either is empty → `{status:"n/a", reason:"length mismatch (out=N,
ref=M) — sequence-independent alignment not implemented"}`.
- Else: **Kabsch superposition** (center both, SVD of covariance, proper-rotation fix) → **RMSD**.
  **TM-score** = `(1/L) * Σ 1/(1+(d_i/d0)^2)` with `d0 = max(0.5, 1.24*(L-15)^(1/3) - 1.8)` (CASP
  formula; clamp L<15 to d0=0.5). Returns `{status:"ok", n_atoms, rmsd, tm_score}`.

### Asset-format gating

- `validate_output(text, fmt)` dispatches: molecular fmt (`pdb,cif,mmcif,ent,sdf,mol`) → run;
  `glb,gltf` or unknown → `{status:"n/a", reason:"non-molecular asset"}`.

## Data flow & storage

- Metrics live in `model_output.meta_json` under key `"validation"`:
  `{"self": <validate_structure result>, "reference": <compare_to_reference result or absent>}`.
- **Compute points**: (a) seed time (new outputs), (b) on submission approval / ingest
  (`app/ingest.py` / submission flow), (c) batch admin re-run.
- **Reading asset bytes**: `get_storage().read(output.asset_path).decode()`.
- **Reference resolution**: if `output.task.reference_asset_id` is set and points to a _different_
  output, fetch the reference bytes and run `compare_to_reference`. A reference output validates
  against itself only for `self` (no `reference` block, or tagged `is_reference`).
- **Batch endpoint** `POST /admin/revalidate` — mirrors `/admin/recompute`: `token: Form`,
  `_require_admin(token)`, iterates all non-gold `ModelOutput`, recomputes `meta_json["validation"]`,
  commits, returns `{status:"revalidated", detail:{outputs, molecular, errors}}`. Idempotent.
- Validation is **best-effort**: a parse failure on one output stores `{status:"error", reason}`
  and never aborts the batch or the ingest of other assets.

## Surfacing — the `/validation` page

New route `GET /validation` + nav link (after Significance). Two sections:

1. **Physical validity leaderboard** (objective, no votes): per-generator aggregate over its
   molecular outputs — mean `validity_score`, mean `clashes_per_1000`, count clean/minor/major,
   n molecular outputs. Sorted by mean validity desc. Generators with zero molecular outputs are
   omitted (with a footnote count).
2. **Reference-based accuracy** (only tasks with a reference): per task, a small table of each
   output's RMSD + TM-score vs the reference, plus the reference labeled. Sorted by TM-score desc.

Copy makes the separation explicit: "Objective structure checks, computed — independent of the
human aesthetic vote." A short methodology note explains each metric + the approximations
(coarse Ramachandran, equal-length-only similarity).

A compact JSON twin `GET /api/validation` returns the same aggregates for transparency/export.

## Demo substrate (seed)

So the page shows real numbers and the reference path is exercised end-to-end, reuse the existing
`crambin-fold` proteins task (already seeded from the benchmark manifest with the real
`app/data/benchmarks/assets/1crn.pdb` = 327 atoms / 46 residues, generator `rcsb-experimental`):

- Set that task's `reference_asset_id` → the existing 1CRN ModelOutput (tag it `is_reference` in meta;
  it gets a `self` validation block but no `reference` block).
- Add two generator outputs for the task, built by perturbing the 1CRN coordinates: `near` (σ=0.3 Å
  jitter → low RMSD / high TM) and `far` (σ=2.5 Å jitter → high RMSD / low TM). A small seed helper
  `perturb_pdb(text, sigma, seed)` rewrites the ATOM x/y/z columns.
- This makes both validators light up on real biology with the reference path fully exercised.

## Error handling

| Condition                     | Result                                                     |
| ----------------------------- | ---------------------------------------------------------- |
| GLB/GLTF/unknown fmt          | `{status:"n/a", reason:"non-molecular asset"}`             |
| Empty / unparseable structure | `{status:"error", reason:<msg>}` (logged; batch continues) |
| Reference length mismatch     | `reference:{status:"n/a", reason:"length mismatch …"}`     |
| Non-protein for Ramachandran  | `rama_outliers: null` (not an error)                       |

## Testing

**Unit (synthetic fixtures):**

- `parse_atoms` on a hand-written 3-atom PDB and a 3-atom SDF → correct elements/coords.
- Clashscore: two atoms 0.5 Å apart (non-bonded) → ≥1 clash; same atoms 3 Å apart → 0.
- Bond outlier: a 3.0 Å "C-C" bond → flagged; a 1.54 Å C-C → clean.
- Kabsch RMSD: a structure vs a rotated+translated copy of itself → RMSD ≈ 0; vs a known
  per-atom displacement → expected RMSD within tol.
- TM-score: identical → 1.0; length mismatch → `status:"n/a"`.
- `validate_output` on a GLB stub → `n/a`.

**Real-execution check (per the real-execution-testing doctrine — synthetic fixtures alone are
insufficient at this boundary):**

- Run `validate_structure` on the **real bundled `app/data/benchmarks/assets/1crn.pdb`** → assert it
  parses 327 atoms (46 CA) and scores `tier in {clean, minor}` with low `clashes_per_1000` (a real
  1.5 Å crystal structure must not read as garbage). This catches parser/column-offset bugs that
  synthetic fixtures would miss.
- Run `compare_to_reference(perturbed_1crn, 1crn)` for σ=0.3 and σ=2.5 → assert RMSD(0.3) <
  RMSD(2.5) and TM(0.3) > TM(2.5), with RMSD(0.3) in a sane sub-Å band. Validates the similarity
  math against known ground truth.

**Route tests:** `/admin/revalidate` requires token (401 without); after revalidate, `/validation`
renders the validity leaderboard and (with the seeded reference task) the reference table;
`/api/validation` returns matching aggregates.

## Files

- **Create** `app/validation.py` — parser + `validate_structure` + `compare_to_reference` +
  `validate_output` + element tables.
- **Create** `app/templates/validation.html` — the `/validation` page.
- **Create** `tests/test_validation.py` — unit + real-execution + route tests.
- **Modify** `app/main.py` — `GET /validation`, `GET /api/validation`, `POST /admin/revalidate`,
  an aggregation helper (or put aggregation in a `validation_service` function).
- **Modify** `app/templates/base.html` — add "Validation" nav link.
- **Modify** `app/seed.py` — compute validation on seeded molecular outputs; seed the 1CRN
  reference task + perturbed outputs; `perturb_pdb` helper.
- **Modify** `app/ingest.py` / submission approval — compute validation when an output is created
  (best-effort).
- **Modify** `app/templates/admin.html` — a "Revalidate structures" button (mirrors Recompute).

## Global constraints

- Pure Python + numpy; no new dependency, no network, no binaries.
- Test env: `.venv/bin/python -m pytest`. ruff clean (`ruff check app tests`).
- Validation never crashes ingest/seed; failures are captured per-output.
- Language discipline: "MolProbity-style" / "approximate Ramachandran," never "MolProbity."
- Screenshot-verify the `/validation` page with the existing Playwright harness before merge.
