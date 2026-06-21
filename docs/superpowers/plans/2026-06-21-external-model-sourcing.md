# External-Model Sourcing (Objaverse) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull license-vetted, depiction-labeled tomato 3D models from Objaverse onto the existing tomato spotlight Task so the grid shows the whole field (AI-recon vs artist-found).

**Architecture:** Pure license/depiction logic in `app/sourcing.py`; a pipeline `scripts/source_objaverse.py` with injectable Objaverse access (so unit tests need no network) that downloads HOST-able models, registers them onto the tomato Task with full provenance, and optionally scores whole-plant ones; `build_spotlight` + the template gain source/depiction grouping. Reuses `ModelOutput`, the recon scorer, and the render pipeline — no new table.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (SQLite), Jinja2, the `objaverse` pip package, pytest.

## Global Constraints

- Python 3.13. Run tests with `.venv/bin/python -m pytest`; lint `ruff check app/ tests/ scripts/`.
- **License policy (private tool):** HOST any Creative-Commons or public-domain license (incl. NC/ND); EXCLUDE all-rights-reserved / proprietary / unmarked. Record the exact license on `ModelOutput.license`.
- Found models reuse `ModelOutput`: `asset_path` stays NOT NULL (host only — no link-only rows in this plan). A SINGLE `objaverse` generator backs all found models; the card label is the output `title` (object name).
- `/spotlight` stays internal (linked from `/admin`, not the public nav) — unchanged by this plan.
- Provenance set AFTER `register_output` returns (it does not set provenance and does not commit): `out.source="objaverse"; out.license=…; out.attribution=…; out.external_url=…; db.commit()`.
- Only `whole_plant` depiction is scored against the GT band; others stay unscored.

---

### Task 1: License + depiction logic (`app/sourcing.py`)

**Files:**

- Create: `app/sourcing.py`
- Test: `tests/test_sourcing.py`

**Interfaces:**

- Produces: `classify_license(license_str: str | None) -> str` (`"host"|"exclude"`); `public_safe(license_str: str | None) -> bool`; `label_depiction(text: str) -> str` (`"whole_plant"|"fruit"|"leaf"|"other"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sourcing.py
from app.sourcing import classify_license, label_depiction, public_safe


def test_classify_license_hosts_cc_and_public_domain():
    for lic in ["CC0", "CC-BY 4.0", "CC BY-SA", "cc-by-nc", "CC-BY-NC-ND",
                "CC-BY-ND", "Creative Commons Attribution", "Public Domain"]:
        assert classify_license(lic) == "host", lic


def test_classify_license_excludes_arr_and_unmarked():
    for lic in ["All Rights Reserved", "", None, "Standard", "Proprietary"]:
        assert classify_license(lic) == "exclude", lic


def test_public_safe_only_cc0_by_sa():
    assert public_safe("CC0") and public_safe("CC-BY") and public_safe("CC-BY-SA")
    for lic in ["CC-BY-NC", "CC-BY-ND", "CC-BY-NC-SA", "All Rights Reserved", None]:
        assert not public_safe(lic), lic


def test_label_depiction():
    assert label_depiction("Tomato plant in a pot") == "whole_plant"
    assert label_depiction("tomato seedling") == "whole_plant"
    assert label_depiction("Ripe red tomato") == "fruit"
    assert label_depiction("cherry tomatoes") == "fruit"
    assert label_depiction("tomato leaf closeup") == "leaf"
    assert label_depiction("tomato soup can") == "other"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sourcing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sourcing'`.

- [ ] **Step 3: Implement**

```python
# app/sourcing.py
"""License classification + depiction labeling for externally-sourced 3D models.

Pure functions, no I/O. See docs/superpowers/specs/2026-06-21-external-model-sourcing-design.md.
Private-tool policy: host any CC/public-domain license (incl. NC/ND); exclude
all-rights-reserved/unmarked. `public_safe` marks the stricter set for the future
pre-public cleanup.
"""

from __future__ import annotations

_CC_MARKERS = ("cc0", "cc-by", "cc by", "creativecommons", "creative commons", "public domain")
_PUBLIC_SAFE_BAD = ("nc", "nd")  # non-commercial / no-derivatives → not public-safe


def classify_license(license_str: str | None) -> str:
    """'host' for any CC/public-domain license; 'exclude' for ARR/proprietary/unmarked."""
    if not license_str:
        return "exclude"
    s = license_str.strip().lower()
    return "host" if any(m in s for m in _CC_MARKERS) else "exclude"


def public_safe(license_str: str | None) -> bool:
    """True only for CC0 / CC-BY / CC-BY-SA (no NC, no ND). For the future public re-vet."""
    if classify_license(license_str) != "host":
        return False
    s = license_str.strip().lower()
    # split tokens on non-alpha so 'nc'/'nd' match as license components, not substrings
    parts = set(filter(None, (p for p in __import__("re").split(r"[^a-z0-9]+", s))))
    return not any(bad in parts for bad in _PUBLIC_SAFE_BAD)


def label_depiction(text: str) -> str:
    """Coarse subject label from an object's name/caption."""
    s = (text or "").lower()
    if any(w in s for w in ("plant", "vine", "bush", "seedling", "sapling", "potted")):
        return "whole_plant"
    if "leaf" in s or "foliage" in s:
        return "leaf"
    if any(w in s for w in ("tomato", "fruit", "cherry", "produce")) and "can" not in s and "soup" not in s:
        return "fruit"
    return "other"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sourcing.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/sourcing.py tests/test_sourcing.py
git commit -m "feat(sourcing): license classification + depiction labeling"
```

---

### Task 2: Objaverse ingest pipeline (`scripts/source_objaverse.py`)

**Files:**

- Create: `scripts/source_objaverse.py`
- Test: `tests/test_source_objaverse.py`

**Interfaces:**

- Consumes: `app.sourcing.{classify_license, label_depiction}`; `app.ingest.register_output(db, task_id, generator_slug, data, ext, title, meta, generator_name) -> (ModelOutput, bool)`; the tomato Task (title `"Solanum lycopersicum — single-image → 3D reconstruction"`).
- Produces: `ingest_found(db, uids, *, fetch_annotations, fetch_objects, score_fn=None, task_title=TOMATO_TITLE) -> dict` where `fetch_annotations(uids)->{uid:{"license","name","uri"}}`, `fetch_objects(uids)->{uid:glb_path}`, optional `score_fn(db, output)->None`. Returns `{"hosted": int, "excluded": int, "by_depiction": {label:int}, "excluded_licenses": {license:int}}`.

- [ ] **Step 1: Write the failing test** (injected fakes — no network)

```python
# tests/test_source_objaverse.py
import json
from pathlib import Path

from app.assets_gen import build_asset
from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task
from scripts.source_objaverse import ingest_found

TOMATO = "Solanum lycopersicum — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _tomato_task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="Plants")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=TOMATO, prompt="p")
    db.add(t)
    db.commit()
    return t


def test_ingest_found_hosts_cc_excludes_arr(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        glb = tmp_path / "obj.glb"
        build_asset("flower", 1, glb)  # a real, trimesh-valid GLB
        annotations = {
            "u_cc": {"license": "CC-BY 4.0", "name": "Tomato plant in pot",
                     "uri": "https://sketchfab.com/u_cc"},
            "u_arr": {"license": "All Rights Reserved", "name": "Ripe tomato",
                      "uri": "https://sketchfab.com/u_arr"},
        }
        report = ingest_found(
            db, ["u_cc", "u_arr"],
            fetch_annotations=lambda uids: {u: annotations[u] for u in uids},
            fetch_objects=lambda uids: {u: str(glb) for u in uids},
            score_fn=None,
        )
        assert report["hosted"] == 1
        assert report["excluded"] == 1
        out = db.query(ModelOutput).filter(ModelOutput.source == "objaverse").one()
        assert out.license == "CC-BY 4.0"
        assert out.external_url == "https://sketchfab.com/u_cc"
        assert out.title == "Tomato plant in pot"
        assert json.loads(out.meta_json)["depiction"] == "whole_plant"
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_objaverse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.source_objaverse'`.

- [ ] **Step 3: Implement**

```python
# scripts/source_objaverse.py
"""Source license-vetted tomato 3D models from Objaverse onto the tomato spotlight Task.

`ingest_found` is the testable core (Objaverse access + scorer injected). `main()` wires
the real `objaverse` package + the recon scorer. Hosts any CC/public-domain license,
excludes all-rights-reserved/unmarked (see app.sourcing). Commits per object.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.models import Task  # noqa: E402
from app.sourcing import classify_license, label_depiction  # noqa: E402

TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"


def ingest_found(db, uids, *, fetch_annotations, fetch_objects, score_fn=None,
                 task_title=TOMATO_TITLE) -> dict:
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "excluded": 0, "by_depiction": {}, "excluded_licenses": {}}
    anns = fetch_annotations(list(uids))
    for uid in uids:
        ann = anns.get(uid) or {}
        lic = ann.get("license")
        if classify_license(lic) != "host":
            report["excluded"] += 1
            key = (lic or "unmarked")
            report["excluded_licenses"][key] = report["excluded_licenses"].get(key, 0) + 1
            continue
        name = ann.get("name") or uid
        depiction = label_depiction(name)
        try:
            glb_path = fetch_objects([uid]).get(uid)
            data = Path(glb_path).read_bytes()
            out, _created = ingest.register_output(
                db, task_id=task.id, generator_slug="objaverse", generator_name="Objaverse",
                data=data, ext="glb", title=name,
                meta={"depiction": depiction, "objaverse_uid": uid, "found": True},
            )
            out.source = "objaverse"
            out.license = lic
            out.attribution = ann.get("author") or ann.get("user", {}).get("displayName")
            out.external_url = ann.get("uri")
            db.commit()  # per-object: short write lock
            if score_fn is not None and depiction == "whole_plant":
                score_fn(db, out)
                db.commit()
            report["hosted"] += 1
            report["by_depiction"][depiction] = report["by_depiction"].get(depiction, 0) + 1
        except Exception as e:  # noqa: BLE001 — best-effort; one bad object never aborts
            print(f"  skip {uid}: {e}")
            db.rollback()
    return report


def main() -> int:
    import argparse

    import objaverse

    from app import recon_service
    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--no-score", action="store_true", help="skip GT scoring of whole-plant")
    args = ap.parse_args()

    lvis = objaverse.load_lvis_annotations()
    uids = []
    for cat, cat_uids in lvis.items():
        if "tomato" in cat.lower():
            uids.extend(cat_uids)
    uids = uids[: args.limit]
    if not uids:
        print("no 'tomato' LVIS category uids found")
        return 0

    db = SessionLocal()
    report = ingest_found(
        db, uids,
        fetch_annotations=objaverse.load_annotations,
        fetch_objects=lambda u: objaverse.load_objects(u),
        score_fn=None if args.no_score else recon_service.score_and_store,
    )
    print(report)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_objaverse.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite + lint**

Run: `.venv/bin/python -m pytest -q && ruff check app/ tests/ scripts/`
Expected: all pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add scripts/source_objaverse.py tests/test_source_objaverse.py
git commit -m "feat(sourcing): Objaverse ingest pipeline (license-vetted, depiction-labeled)"
```

---

### Task 3: Found-model grouping in the spotlight grid

**Files:**

- Modify: `app/spotlight.py` (the per-model dict in `build_spotlight`)
- Modify: `app/templates/spotlight.html` (group cards by class)
- Test: `tests/test_spotlight_found.py`

**Interfaces:**

- Consumes: `build_spotlight(db, slug)` from Task-4 of the Phase-1 plan.
- Produces: each model dict gains `found: bool`, `label: str` (object name for found, else generator name), `depiction: str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spotlight_found.py
import json

from app import ingest, spotlight
from app.database import SessionLocal, init_db
from app.assets_gen import build_asset
from app.models import Category, Task


def setup_module(_m):
    init_db()


def test_build_spotlight_marks_found_and_label(tmp_path, monkeypatch):
    db = SessionLocal()
    try:
        cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="Plants")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="Found Subject", prompt="p")
        db.add(task)
        db.flush()
        glb = tmp_path / "x.glb"
        build_asset("flower", 2, glb)
        out, _ = ingest.register_output(
            db, task_id=task.id, generator_slug="objaverse", generator_name="Objaverse",
            data=glb.read_bytes(), ext="glb", title="Ripe tomato",
            meta={"depiction": "fruit", "found": True},
        )
        out.source = "objaverse"
        db.commit()
        monkeypatch.setattr(spotlight, "SPOTLIGHTS", [
            {"slug": "f", "task_title": "Found Subject", "featured": True, "order": 0,
             "blurb": "b", "reference_image": None},
        ])
        m = spotlight.build_spotlight(db, "f")["models"][0]
        assert m["found"] is True
        assert m["label"] == "Ripe tomato"        # object name, not "Objaverse"
        assert m["depiction"] == "fruit"
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_spotlight_found.py -v`
Expected: FAIL — `KeyError: 'found'`.

- [ ] **Step 3: Extend `build_spotlight`'s model dict**

In `app/spotlight.py`, inside the `for o in outs:` loop, after `gen = db.get(Generator, o.generator_id)`, compute:

```python
        import json as _json
        found = o.source != "bio3d-arena"
        depiction = _json.loads(o.meta_json or "{}").get("depiction")
        label = o.title if (found and o.title) else (gen.name if gen else "?")
```

and add these three keys to the appended dict (alongside the existing keys):

```python
                "found": found,
                "label": label,
                "depiction": depiction,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_spotlight_found.py -v`
Expected: PASS.

- [ ] **Step 5: Group the grid in `app/templates/spotlight.html`**

Replace the single `<div class="spotlight-grid"> … {% for m in s.models %} … {% endfor %} … </div>` block with grouped sections. Add this Jinja above the grid to bucket models, then render a titled grid per non-empty group:

```html
{% set ai = s.models | selectattr('found', 'equalto', false) | list %} {% set
found = s.models | selectattr('found', 'equalto', true) | list %} {% set groups
= [('AI reconstruction', ai), ('Found — whole plant', found |
selectattr('depiction','equalto','whole_plant') | list), ('Found — fruit', found
| selectattr('depiction','equalto','fruit') | list), ('Found — leaf', found |
selectattr('depiction','equalto','leaf') | list), ('Found — other', found |
selectattr('depiction','equalto','other') | list)] %} {% for gname, gmodels in
groups %} {% if gmodels %}
<h3 class="group-title">
  {{ gname }} <span class="subtle">({{ gmodels|length }})</span>
</h3>
<div class="spotlight-grid">
  {% for m in gmodels %} {# ...the existing card markup, but use m.label for the
  heading... #} {% endfor %}
</div>
{% endif %} {% endfor %}
```

Inside the card markup, change the heading line from `{{ m.generator_name }}` to `{{ m.label }}`, and add the license under provenance when present:

```html
<div class="card-gen">
  {{ m.label }} <span class="subtle">#{{ m.id }}</span>
</div>
...
<div class="provenance subtle">
  {{ m.provenance.source }}{% if m.provenance.license %} · {{
  m.provenance.license }}{% endif %}{% if m.provenance.external_url %} ·
  <a href="{{ m.provenance.external_url }}" target="_blank" rel="noopener"
    >source</a
  >{% endif %}
</div>
```

(Keep the rest of the card — thumbnail, metric table, flags, critic note — unchanged.)

- [ ] **Step 6: Add the `.group-title` style to `app/static/style.css`** (after the spotlight block)

```css
.group-title {
  margin: 1.2rem 0 0.4rem;
  font-size: 1rem;
}
```

- [ ] **Step 7: Run tests + a render check**

Run: `.venv/bin/python -m pytest tests/test_spotlight_found.py tests/test_spotlight_page.py -v`
Expected: PASS. Then manually load `/spotlight/tomato` against the dev server and confirm the page still renders (the "AI reconstruction" group shows the 15 existing models; no "Found" group yet until Task 4 runs).

- [ ] **Step 8: Commit**

```bash
git add app/spotlight.py app/templates/spotlight.html app/static/style.css tests/test_spotlight_found.py
git commit -m "feat(spotlight): group grid by source class + depiction; found-model labels"
```

---

### Task 4: Install Objaverse + real pull, score, render, verify (operational)

**Files:** none (operational; controller-run).

- [ ] **Step 1: Install the Objaverse package into the venv**

Run: `.venv/bin/pip install objaverse`
Expected: installs cleanly.

- [ ] **Step 2: Real-execution test (network-gated)**

Add `tests/test_source_objaverse_live.py` that imports `objaverse`, pulls the first CC-licensed uid from the "tomato" LVIS category through `ingest_found` against a temp DB, and asserts a `ModelOutput` with `source="objaverse"`, a non-empty `license`, an `external_url`, a `depiction` in meta, and a real on-disk GLB. Guard with `pytest.importorskip("objaverse")` AND a try/except around the network call that `pytest.skip(...)`s with a clear reason if Objaverse is unreachable (never a silent pass). Run it: `.venv/bin/python -m pytest tests/test_source_objaverse_live.py -v` — expect PASS or a clear SKIP.

- [ ] **Step 3: Pull tomato models onto the live DB**

Run: `BIO3D_DATABASE_URL="sqlite:///data/arena.db" .venv/bin/python scripts/source_objaverse.py --limit 40`
Expected: prints a report `{"hosted": N, "excluded": M, "by_depiction": {...}, "excluded_licenses": {...}}` with N ≥ 1.

- [ ] **Step 4: Render thumbnails for the new found models**

Run: `BIO3D_DATABASE_URL="sqlite:///data/arena.db" .venv/bin/python scripts/render_spotlight.py --slug tomato`
Expected: `errors: 0`; the new found models get thumbnails.

- [ ] **Step 5: Verify the page + independent-critic gate**

Restart the dev server; confirm `GET /spotlight/tomato` is 200 and now shows a "Found — fruit" (and possibly "whole plant") group alongside "AI reconstruction". Screenshot it and run a fresh independent adversarial critic (per the independent-critic doctrine) before declaring done.

- [ ] **Step 6: Full suite + lint + commit any fixes**

Run: `.venv/bin/python -m pytest -q && ruff check app/ tests/ scripts/`. Commit the live test + any fixes: `git add -A && git commit -m "test(sourcing): live Objaverse pull + operational verification"`.

---

## Self-Review

**Spec coverage:** `classify_license`/`public_safe`/`label_depiction` (Task 1) ✓; Objaverse query + license filter + download + provenance register + per-object commit + scoring hook + report (Task 2) ✓; single `objaverse` generator + object-name label (Task 2 register call) ✓; grid grouping by source/depiction + found labels + license/source link (Task 3) ✓; install + real pull + render + verify + critic gate (Task 4) ✓; real-execution test paired with synthetic (Task 4 Step 2, network-gated skip) ✓; whole-plant-only scoring (Task 2 `score_fn` gated on `depiction == "whole_plant"`) ✓. Link-only/public-cleanup explicitly out of scope (spec) — not planned. ✓

**Placeholder scan:** no "TBD"/"handle errors"/"similar to". The Task-3 template step shows the exact Jinja and the one-line card change; the card body is explicitly "unchanged from Phase 1" (not a placeholder — it's a reuse instruction).

**Type consistency:** `ingest_found(db, uids, *, fetch_annotations, fetch_objects, score_fn=None, task_title=...)` matches the test and `main()` caller. `register_output(... generator_slug, generator_name, data, ext, title, meta)` matches `app/ingest.py`. `build_spotlight` model dict gains `found`/`label`/`depiction`, consumed by the template. `classify_license`/`label_depiction` signatures consistent across Tasks 1–2. `score_fn(db, out)` matches `recon_service.score_and_store(db, output)`.
