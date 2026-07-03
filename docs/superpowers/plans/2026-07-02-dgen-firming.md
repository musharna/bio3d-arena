# D-Gen firming (independent cross-judge A/B + multi-model) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Escape D-Gen's same-judge circularity — a different-lab VLM (`gpt-5.1`) blind-compares each taxon's round-0 baseline vs refined-best output and reports a preference rate — plus a small multi-model sweep to test whether rubric-feedback generalizes beyond gemini.

**Architecture:** A new `app/dgen_ab.py` holds pure A/B primitives (composite two contact sheets into one A|B image, prompt/parse a blind pairwise judgment, both-orders verdict, aggregate) + a DB/filesystem enumerator that buckets each (run, taxon) into A/B / repair / no-refinement and resolves the baseline+best GLBs. `app/dgen.py` gains a one-block addition that persists round-0's baseline GLB. A driver renders the sheets, runs the cross-judge via the existing `agentic.vision_complete`, and writes a results doc. All VLM/browser/Blender access is behind injected seams so unit tests use fakes.

**Tech Stack:** Python 3.13, PIL (already used by `judge_render.tile_contact_sheet`), OpenRouter vision via `app/agentic.vision_complete`, the D-Gen tables (`DGenRun`/`DGenIteration`), Playwright capture. Test runner `.venv/bin/pytest`.

## Global Constraints

- **Test runner is `.venv/bin/pytest`** (NOT bare `pytest`). Baseline: 629 passed / 8 skipped — must stay green.
- **NEVER set `BIO3D_DATABASE_URL=study`** when running pytest, and the live driver runs against a **copy** of the study DB only.
- **Reuse verbatim, do not reinvent, do not edit another agent's file:** `app/agentic.vision_complete(post, model_id, prompt, image_png, *, api_key, max_tokens=32000, max_retries=3, sleep_fn=None) -> str` (import only — it is another agent's file); `app/judge_render.tile_contact_sheet(pngs, cols, rows)` + `judge_render.CONDITIONS["multi4"]` (`{"azimuths":[0,90,180,270],"elev":70,"cols":2,"rows":2}`); `scripts/judge_capture.browser_capture_multi_factory()` → `capture_multi(glb_abs, azimuths, elev) -> list[bytes]`; `app/dgen.refine_loop` + `app/dgen._taxon_slug`; `app/commission.SPECIES_COMMON` (taxon→common) + `commission.run_bpy` + `commission.openrouter_complete`; `app/service.dgen_trajectory`; `app/config.ASSET_DIR`.
- **Independent judge disjoint from tested generators + different lab from the `claude-sonnet-4-6` generation judge.** Default judge `openai/gpt-5.1`; tested generators `google/gemini-3.1-pro-preview` (reused) + `anthropic/claude-opus-4.8` + `x-ai/grok-4.3`.
- **The A/B is blind** (no baseline/refined labels reach the judge) and **runs both orders** (position-bias cancel).
- **Injected seams:** every unit test uses fakes for `vision_fn`, `capture_multi`, and `run_fn`. No test touches the network, a browser, Blender, or the VLM.
- **`vision_complete` returns plain text** (OpenRouter chat completion, not tool-use) — parse the answer text for "A"/"B".

---

### Task 1: Pure A/B primitives

**Files:**

- Create: `app/dgen_ab.py`
- Test: `tests/test_dgen_ab.py`

**Interfaces:**

- Produces:
  - `composite_ab(sheet_left: bytes, sheet_right: bytes) -> bytes` — one side-by-side PNG, left half labeled "A", right "B".
  - `ab_prompt(taxon: str, common: str) -> str`.
  - `judge_pair(vision_fn, comp_png: bytes, taxon: str, common: str) -> str | None` — `vision_fn(prompt: str, image_png: bytes) -> str`; returns "A"|"B"|None.
  - `verdict_both_orders(pick1: str | None, pick2: str | None) -> str` — "refined"|"baseline"|"inconsistent".
  - `aggregate(rows: list[dict]) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dgen_ab.py
import io

from PIL import Image

from app.dgen_ab import composite_ab, ab_prompt, judge_pair, verdict_both_orders, aggregate


def _png(color, size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_composite_ab_is_wider_side_by_side_png():
    out = composite_ab(_png((0, 128, 0)), _png((128, 0, 0)))
    im = Image.open(io.BytesIO(out))
    assert im.width >= 16  # both 8-wide halves side by side
    assert im.height >= 8


def test_ab_prompt_is_blind_and_asks_A_or_B():
    p = ab_prompt("Glycine max", "soybean")
    assert "soybean" in p
    low = p.lower()
    assert '"a"' in low or " a " in low  # asks for an A/B answer
    assert "baseline" not in low and "refined" not in low and "round" not in low  # no leak


def test_judge_pair_parses_A_B_and_none():
    assert judge_pair(lambda prompt, png: "A", b"x", "Rosa", "rose") == "A"
    assert judge_pair(lambda prompt, png: "The better one is B.", b"x", "Rosa", "rose") == "B"
    assert judge_pair(lambda prompt, png: "neither/unclear", b"x", "Rosa", "rose") is None


def test_verdict_both_orders():
    assert verdict_both_orders("B", "A") == "refined"    # refined won both orders
    assert verdict_both_orders("A", "B") == "baseline"   # baseline won both
    assert verdict_both_orders("A", "A") == "inconsistent"  # flipped
    assert verdict_both_orders("B", None) == "inconsistent"


def test_aggregate_rates_over_ab_denominator():
    rows = [
        {"bucket": "ab", "verdict": "refined"},
        {"bucket": "ab", "verdict": "refined"},
        {"bucket": "ab", "verdict": "baseline"},
        {"bucket": "ab", "verdict": "inconsistent"},
        {"bucket": "repair"},
        {"bucket": "no-refinement"},
    ]
    a = aggregate(rows)
    assert a["n_ab"] == 4
    assert a["refined"] == 2 and a["baseline"] == 1 and a["inconsistent"] == 1
    assert a["refined_rate"] == 0.5  # 2/4
    assert a["repairs"] == 1 and a["no_refinement"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dgen_ab.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.dgen_ab'`.

- [ ] **Step 3: Write `app/dgen_ab.py`**

```python
# app/dgen_ab.py
"""Independent cross-judge A/B for D-Gen firming. A different-lab VLM blind-compares each taxon's
round-0 baseline vs refined-best contact sheet (composited into one A|B image), run in both orders
to cancel position bias, to escape the same-(claude-sonnet)-judge circularity of the D-Gen fidelity
signal. Pure image/parse/verdict/aggregate logic; the VLM call (vision_fn) is injected."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

_LABEL_H = 16


def composite_ab(sheet_left: bytes, sheet_right: bytes) -> bytes:
    """One side-by-side PNG: left half labeled 'A', right half 'B'. Halves are scaled to a common
    height so the judge sees a fair pair."""
    a = Image.open(io.BytesIO(sheet_left)).convert("RGB")
    b = Image.open(io.BytesIO(sheet_right)).convert("RGB")
    h = max(a.height, b.height)
    if a.height != h:
        a = a.resize((round(a.width * h / a.height), h))
    if b.height != h:
        b = b.resize((round(b.width * h / b.height), h))
    canvas = Image.new("RGB", (a.width + b.width, h + _LABEL_H), (255, 255, 255))
    canvas.paste(a, (0, _LABEL_H))
    canvas.paste(b, (a.width, _LABEL_H))
    draw = ImageDraw.Draw(canvas)
    draw.text((a.width // 2, 2), "A", fill=(0, 0, 0))
    draw.text((a.width + b.width // 2, 2), "B", fill=(0, 0, 0))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def ab_prompt(taxon: str, common: str) -> str:
    return (
        f"Two rendered 3D models of a {common} ({taxon}) are shown side by side: model A on the "
        f"left, model B on the right. Judge which is a more complete and botanically accurate whole "
        f"{common} plant (correct organs present, right overall structure). Reply with a single "
        f'letter only: "A" or "B".'
    )


def _parse_ab(resp: str | None) -> str | None:
    t = (resp or "").strip().upper()
    if not t:
        return None
    if t[0] in ("A", "B"):
        return t[0]
    # fall back: last standalone A/B token
    for tok in reversed(t.replace(".", " ").replace(",", " ").split()):
        if tok in ("A", "B"):
            return tok
    return None


def judge_pair(vision_fn, comp_png: bytes, taxon: str, common: str) -> str | None:
    """vision_fn(prompt, image_png) -> str. Returns 'A'|'B'|None."""
    return _parse_ab(vision_fn(ab_prompt(taxon, common), comp_png))


def verdict_both_orders(pick1: str | None, pick2: str | None) -> str:
    """pick1 = pick when baseline=A (refined='B'); pick2 = pick when baseline=B (refined='A')."""
    if pick1 == "B" and pick2 == "A":
        return "refined"
    if pick1 == "A" and pick2 == "B":
        return "baseline"
    return "inconsistent"


def aggregate(rows: list[dict]) -> dict:
    ab = [r for r in rows if r.get("bucket") == "ab"]
    refined = sum(1 for r in ab if r.get("verdict") == "refined")
    baseline = sum(1 for r in ab if r.get("verdict") == "baseline")
    inconsistent = sum(1 for r in ab if r.get("verdict") == "inconsistent")
    n_ab = len(ab)
    return {
        "n_ab": n_ab,
        "refined": refined,
        "baseline": baseline,
        "inconsistent": inconsistent,
        "refined_rate": (refined / n_ab) if n_ab else None,
        "repairs": sum(1 for r in rows if r.get("bucket") == "repair"),
        "no_refinement": sum(1 for r in rows if r.get("bucket") == "no-refinement"),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dgen_ab.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/dgen_ab.py tests/test_dgen_ab.py
git commit -m "feat(dgen-ab): pure A/B primitives (composite, prompt, judge, verdict, aggregate)"
```

---

### Task 2: Persist the round-0 baseline GLB in `refine_loop`

**Files:**

- Modify: `app/dgen.py` (inside `refine_loop`, round-0 handling)
- Test: `tests/test_dgen_loop.py` (add one test)

**Interfaces:**

- Consumes: existing `refine_loop`, `_taxon_slug`.
- Produces: side effect — when round 0 produces a valid GLB, a copy at `{asset_dir}/dgen_baseline/{run_id}_{taxon_slug}.glb`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dgen_loop.py` (it already has `_seed`, `_score`, `_run_fn_ok` fakes and `init_db` setup):

```python
def test_refine_loop_persists_round0_baseline_glb(tmp_path):
    from pathlib import Path
    from app.dgen import _taxon_slug

    with SessionLocal() as db:
        task_id, run_id = _seed(db, "Rosa")
        db.commit()
        asset_dir = tmp_path / "assets"
        refine_loop(
            db, run_id=run_id, taxon="Rosa", task_id=task_id, prompt="p", common="rose",
            model_id="m", traits=[], complete_fn=lambda m, p: "import bpy",
            run_fn=_run_fn_ok, score_fn=lambda g: _score(0.5), asset_dir=str(asset_dir),
            max_rounds=1)
        db.commit()
        baseline = Path(asset_dir) / "dgen_baseline" / f"{run_id}_{_taxon_slug('Rosa')}.glb"
        assert baseline.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dgen_loop.py::test_refine_loop_persists_round0_baseline_glb -v`
Expected: FAIL — the baseline file does not exist.

- [ ] **Step 3: Add the baseline persist to `refine_loop`**

In `app/dgen.py`, inside `refine_loop`'s per-round loop, immediately after the line
`status = run.get("status", "error")` (and before the `score = None` block), insert:

```python
        if n == 0 and status == "ok" and run.get("glb_path"):
            import shutil

            baseline_dir = Path(asset_dir) / "dgen_baseline"
            baseline_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run["glb_path"], baseline_dir / f"{run_id}_{_taxon_slug(taxon)}.glb")
```

(`Path` is already imported in `refine_loop` — it does `from pathlib import Path` at the top of the function; if the import is function-local, keep this line inside that scope. `_taxon_slug` is module-level.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dgen_loop.py -v`
Expected: PASS (existing loop tests + the new baseline test).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q` → 630 passed / 8 skipped (629 baseline + 1 new), no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/dgen.py tests/test_dgen_loop.py
git commit -m "feat(dgen): persist round-0 baseline GLB for the cross-judge A/B"
```

---

### Task 3: A/B work enumeration + sheet rendering

**Files:**

- Modify: `app/dgen_ab.py` (add `render_sheet`, `ab_work`)
- Test: `tests/test_dgen_ab_work.py`

**Interfaces:**

- Consumes: `DGenIteration`, `ModelOutput` from `app.models`; `commission.SPECIES_COMMON`; `config.ASSET_DIR`; `judge_render.tile_contact_sheet` + `CONDITIONS`.
- Produces:
  - `render_sheet(glb_abs: str, capture_multi, condition: str = "multi4") -> bytes` — capture_multi + tile.
  - `ab_work(db, run_id: int, asset_dir) -> list[dict]` — one row per taxon of the run: `{"taxon","common","bucket","best_glb","baseline_glb","best_round"}`. Bucket: `"ab"` if best_round>0 and the baseline GLB exists; `"repair"` if the baseline GLB is missing but a best output exists; `"no-refinement"` if best_round==0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dgen_ab_work.py
from pathlib import Path

from app.database import SessionLocal, init_db
from app.models import (Category, Generator, ModelOutput, Task, DGenRun, DGenIteration)
from app.dgen_ab import ab_work, render_sheet


def setup_module(_m):
    init_db()


def _seed_output(db, taxon, asset_rel):
    cat = Category(slug=f"{taxon[:6].lower()}-abw", name=taxon)
    gen = Generator(slug=f"gen-abw-{taxon[:6].lower()}", name="g", paradigm="procedural_llm")
    db.add_all([cat, gen])
    db.flush()
    task = Task(category_id=cat.id, title="t", prompt="p")
    db.add(task)
    db.flush()
    out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path=asset_rel,
                      asset_format="glb", source="commissioned")
    db.add(out)
    db.flush()
    return out.id


def test_ab_work_buckets_taxa(tmp_path):
    asset_dir = tmp_path / "assets"
    (asset_dir / "dgen_baseline").mkdir(parents=True)
    with SessionLocal() as db:
        run = DGenRun(model_id="m")
        db.add(run)
        db.flush()
        # Rosa: best_round=2 (refined) + baseline present -> "ab"
        oid = _seed_output(db, "Rosa", "best/rosa.glb")
        (Path(asset_dir) / "best").mkdir(exist_ok=True)
        (Path(asset_dir) / "best" / "rosa.glb").write_bytes(b"GLB")
        (asset_dir / "dgen_baseline" / f"{run.id}_rosa.glb").write_bytes(b"GLB")
        db.add(DGenIteration(run_id=run.id, taxon="Rosa", round=0, status="ok", fidelity=0.3))
        db.add(DGenIteration(run_id=run.id, taxon="Rosa", round=2, status="ok", fidelity=0.8,
                             is_best=True, output_id=oid))
        # Zea mays: best_round=0 -> "no-refinement"
        oid2 = _seed_output(db, "Zea mays", "best/zea.glb")
        db.add(DGenIteration(run_id=run.id, taxon="Zea mays", round=0, status="ok", fidelity=0.9,
                             is_best=True, output_id=oid2))
        db.commit()

        work = {w["taxon"]: w for w in ab_work(db, run.id, str(asset_dir))}
        assert work["Rosa"]["bucket"] == "ab"
        assert work["Rosa"]["common"] == "rose"
        assert work["Zea mays"]["bucket"] == "no-refinement"


def test_render_sheet_uses_capture_and_tiles():
    import io
    from PIL import Image

    def fake_capture(glb_abs, azimuths, elev):
        out = []
        for _ in azimuths:
            buf = io.BytesIO()
            Image.new("RGB", (4, 4), (0, 100, 0)).save(buf, format="PNG")
            out.append(buf.getvalue())
        return out

    png = render_sheet("/x.glb", fake_capture)
    assert isinstance(png, bytes)
    assert Image.open(io.BytesIO(png)).width >= 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dgen_ab_work.py -v`
Expected: FAIL — `ImportError: cannot import name 'ab_work'`.

- [ ] **Step 3: Add `render_sheet` + `ab_work` to `app/dgen_ab.py`**

Append:

```python
def render_sheet(glb_abs: str, capture_multi, condition: str = "multi4") -> bytes:
    """Render one GLB to a contact-sheet PNG (capture_multi + tile), mirroring dgen.score_glb."""
    from app.judge_render import CONDITIONS, tile_contact_sheet

    spec = CONDITIONS[condition]
    pngs = capture_multi(str(glb_abs), spec["azimuths"], spec["elev"])
    return tile_contact_sheet(pngs, spec["cols"], spec["rows"])


def ab_work(db, run_id: int, asset_dir) -> list[dict]:
    """Bucket each taxon of a DGenRun into ab / repair / no-refinement and resolve the baseline+best
    GLB paths. baseline GLB = {asset_dir}/dgen_baseline/{run_id}_{taxon_slug}.glb; best GLB =
    ASSET_DIR/<best iteration's ModelOutput.asset_path>."""
    import collections
    import os

    from app.commission import SPECIES_COMMON
    from app.config import ASSET_DIR
    from app.dgen import _taxon_slug
    from app.models import DGenIteration, ModelOutput

    by_taxon = collections.defaultdict(list)
    for it in db.query(DGenIteration).filter_by(run_id=run_id).all():
        by_taxon[it.taxon].append(it)

    rows = []
    for taxon, iters in by_taxon.items():
        iters.sort(key=lambda i: i.round)
        best = next((i for i in iters if i.is_best), None)
        best_round = best.round if best else None
        best_glb = None
        if best is not None and best.output_id is not None:
            mo = db.get(ModelOutput, best.output_id)
            if mo is not None:
                best_glb = os.path.join(str(ASSET_DIR), mo.asset_path)
        baseline_glb = os.path.join(str(asset_dir), "dgen_baseline", f"{run_id}_{_taxon_slug(taxon)}.glb")
        has_baseline = os.path.exists(baseline_glb)

        if best is None or best_glb is None:
            continue  # no promoted output for this taxon (all rounds failed) — nothing to compare
        if best_round == 0:
            bucket = "no-refinement"
        elif not has_baseline:
            bucket = "repair"
        else:
            bucket = "ab"
        rows.append({
            "taxon": taxon,
            "common": SPECIES_COMMON.get(taxon, taxon),
            "bucket": bucket,
            "best_glb": best_glb,
            "baseline_glb": baseline_glb if has_baseline else None,
            "best_round": best_round,
        })
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dgen_ab_work.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q` → 632 passed / 8 skipped (630 + 2 new), no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/dgen_ab.py tests/test_dgen_ab_work.py
git commit -m "feat(dgen-ab): ab_work bucketing + render_sheet"
```

---

### Task 4: Driver `scripts/run_dgen_ab.py`

**Files:**

- Create: `scripts/run_dgen_ab.py`
- Test: none (live-only path; import-clean check in Step 3)

**Interfaces:**

- Consumes: `ab_work`, `render_sheet`, `composite_ab`, `judge_pair`, `verdict_both_orders`, `aggregate` (Tasks 1+3); `agentic.vision_complete`; `judge_capture.browser_capture_multi_factory`; `service.dgen_trajectory`; `DGenRun`.
- Produces: `scripts/run_dgen_ab.py` CLI — runs the A/B over every `DGenRun` (or a given `--run-id`), writes `docs/results/2026-07-02-dgen-firming-results.md`.

- [ ] **Step 1: Write `scripts/run_dgen_ab.py`**

```python
# scripts/run_dgen_ab.py
"""Independent cross-judge A/B for D-Gen firming. For each DGenRun, blind-compare round-0 baseline
vs refined-best per taxon with a DIFFERENT-lab VLM (default openai/gpt-5.1) via OpenRouter vision,
both orders, and write a results doc. Build the judge from OPENROUTER_API_KEY. Renders via Playwright.
NEVER set BIO3D_DATABASE_URL=study — point at a COPY of the study DB.

Prereq: the D-Gen runs exist in the target DB (run scripts/run_dgen.py for the tested models first),
and round-0 baseline GLBs exist under {ASSET_DIR}/dgen_baseline/. For the pre-existing gemini run whose
baselines predate the baseline-persist, pass --backfill-from-tmp to copy its dgen_tmp/<run>_<taxon>_r0.glb
into dgen_baseline/ first."""

from __future__ import annotations

import argparse
import functools
import os
import sys

from app.database import SessionLocal, init_db

RESULTS = "docs/results/2026-07-02-dgen-firming-results.md"


def _backfill_from_tmp(asset_dir: str) -> int:
    """Copy dgen_tmp/<run>_<taxon>_r0.glb -> dgen_baseline/<run>_<taxon>.glb (for pre-persist runs)."""
    import glob
    import os.path
    import re
    import shutil

    tmp = os.path.join(asset_dir, "dgen_tmp")
    base = os.path.join(asset_dir, "dgen_baseline")
    os.makedirs(base, exist_ok=True)
    n = 0
    for p in glob.glob(os.path.join(tmp, "*_r0.glb")):
        m = re.match(r"(.+)_r0\.glb$", os.path.basename(p))
        if not m:
            continue
        dst = os.path.join(base, m.group(1) + ".glb")
        if not os.path.exists(dst):
            shutil.copyfile(p, dst)
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Independent cross-judge A/B for D-Gen firming.")
    ap.add_argument("--judge-model", default="openai/gpt-5.1", help="independent A/B judge (OpenRouter id)")
    ap.add_argument("--run-id", type=int, default=None, help="limit to one DGenRun (default: all)")
    ap.add_argument("--backfill-from-tmp", action="store_true",
                    help="seed dgen_baseline/ from dgen_tmp round-0 GLBs first")
    args = ap.parse_args()

    import httpx

    from app.agentic import vision_complete
    from app.config import ASSET_DIR
    from app.dgen_ab import ab_work, aggregate, composite_ab, judge_pair, render_sheet, verdict_both_orders
    from app.models import DGenRun
    from app import service
    from scripts.judge_capture import browser_capture_multi_factory

    or_key = os.environ["OPENROUTER_API_KEY"]
    capture_multi = browser_capture_multi_factory()
    vision_fn = functools.partial(vision_complete, httpx.post, args.judge_model, api_key=or_key)
    # vision_complete(post, model_id, prompt, image_png, *, api_key): partial leaves (prompt, image_png).

    asset_dir = str(ASSET_DIR)
    if args.backfill_from_tmp:
        print(f"backfilled {_backfill_from_tmp(asset_dir)} baseline GLBs from dgen_tmp", flush=True)

    init_db()
    all_rows = []
    with SessionLocal() as db:
        runs = ([db.get(DGenRun, args.run_id)] if args.run_id else db.query(DGenRun).all())
        runs = [r for r in runs if r is not None]
        for run in runs:
            for w in ab_work(db, run.id, asset_dir):
                row = {"run_id": run.id, "model_id": run.model_id, **w}
                if w["bucket"] == "ab":
                    sheet_base = render_sheet(w["baseline_glb"], capture_multi)
                    sheet_best = render_sheet(w["best_glb"], capture_multi)
                    comp_ab = composite_ab(sheet_base, sheet_best)  # baseline=A
                    comp_ba = composite_ab(sheet_best, sheet_base)  # baseline=B
                    pick1 = judge_pair(vision_fn, comp_ab, w["taxon"], w["common"])
                    pick2 = judge_pair(vision_fn, comp_ba, w["taxon"], w["common"])
                    row["verdict"] = verdict_both_orders(pick1, pick2)
                    row["picks"] = [pick1, pick2]
                all_rows.append(row)
                print(f"{run.model_id} {w['taxon']}: {row.get('bucket')} {row.get('verdict','')}", flush=True)
        traj = service.dgen_trajectory(db)

    agg = aggregate(all_rows)
    lines = [
        "# D-Gen firming — independent cross-judge A/B + multi-model results",
        "",
        f"Independent judge: `{args.judge_model}` (different lab from the claude-sonnet-4-6 generation judge).",
        "",
        "## Independent cross-judge A/B (blind, both orders)",
        f"- A/B pairs (best_round>0, valid baseline): **{agg['n_ab']}**",
        f"- **refined preferred: {agg['refined']}/{agg['n_ab']}** (rate {agg['refined_rate']})",
        f"- baseline preferred: {agg['baseline']}/{agg['n_ab']}",
        f"- inconsistent (position-flip/tie): {agg['inconsistent']}/{agg['n_ab']}",
        f"- repairs (invalid baseline -> valid best, not A/B'd): {agg['repairs']}",
        f"- no-refinement (best == round 0): {agg['no_refinement']}",
        "",
        "### Per (model, taxon)",
    ]
    for r in all_rows:
        lines.append(f"- {r['model_id']} / {r['taxon']}: {r['bucket']} {r.get('verdict','')} {r.get('picks','')}")
    lines += ["", "## Multi-model same-judge fidelity lift (sonnet judge, from D-Gen runs)"]
    for t in traj:
        lines.append(f"- {t['model_id']} / {t['taxon']}: r0={t['fidelity_0']} best={t['fidelity_best']} lift={t['lift']}")
    lines += [
        "",
        "## Caveats",
        "- The A/B tests only where refinement CHANGED the output (best_round>0); repairs + no-refinement are",
        "  reported separately so the denominator is honest.",
        "- Per-taxon n is small. Chamfer is intentionally NOT the primary axis (geometry != morphology).",
    ]
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {RESULTS}: {agg}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/pytest -q` → 632 passed / 8 skipped (the driver adds no unit tests; must not break collection).

- [ ] **Step 3: Verify the driver imports cleanly**

Run: `.venv/bin/python -c "import scripts.run_dgen_ab"`
Expected: exits 0 with no output (import-time must not require a browser/keys — `httpx`/`vision_complete`/browser/keys are all inside `main()`).

- [ ] **Step 4: Commit**

```bash
git add scripts/run_dgen_ab.py
git commit -m "feat(dgen-ab): run_dgen_ab.py driver — cross-judge A/B + results doc"
```

---

## Self-Review

**Spec coverage:**

- Independent cross-judge blind A/B, both orders, composited A|B image, reusing `vision_complete` → Tasks 1 (primitives) + 4 (driver wiring). ✓
- Round-0 baseline artifact for the A/B → Task 2 (persist in refine_loop) + Task 4 `--backfill-from-tmp` for the pre-existing gemini run. ✓
- Bucketing (ab / repair / no-refinement) with an honest denominator → Task 3 `ab_work` + Task 1 `aggregate`. ✓
- Multi-model same-judge lift table → Task 4 uses `service.dgen_trajectory` (the multi-model D-Gen runs are the operational live step, not code). ✓
- Judge disjoint + different lab (default `gpt-5.1`) → Task 4 `--judge-model` default + Global Constraints. ✓
- Chamfer secondary-only, caveated → results-doc caveats (Task 4); not implemented as a code axis (YAGNI, deferred). ✓
- Injected seams; no network/browser/Blender/VLM in unit tests → every test uses fakes. ✓
- Do not edit `app/agentic.py` → Task 4 imports `vision_complete`, never edits it. ✓

**Placeholder scan:** No TBD/TODO. The multi-model D-Gen runs (opus, grok) are an operational invocation of the existing `scripts/run_dgen.py` (documented in the spec), not a code task — correctly out of the plan's code scope.

**Type/name consistency:** `composite_ab(left,right)->bytes`, `ab_prompt(taxon,common)`, `judge_pair(vision_fn,comp_png,taxon,common)->str|None`, `verdict_both_orders(pick1,pick2)`, `aggregate(rows)->{n_ab,refined,baseline,inconsistent,refined_rate,repairs,no_refinement}`, `render_sheet(glb_abs,capture_multi,condition)`, `ab_work(db,run_id,asset_dir)->rows{taxon,common,bucket,best_glb,baseline_glb,best_round}` — used identically across Tasks 1/3/4. `vision_fn(prompt,image_png)->str` matches `functools.partial(vision_complete, httpx.post, judge_model, api_key=...)`. Baseline path `{asset_dir}/dgen_baseline/{run_id}_{_taxon_slug(taxon)}.glb` consistent between Task 2 (writer), Task 3 (`ab_work` reader), and Task 4 (`--backfill-from-tmp`).
