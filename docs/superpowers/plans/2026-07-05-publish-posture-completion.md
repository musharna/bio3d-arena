# Publish-Posture Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete PR#12's two-posture publish-safety system so the DISPLAY arena can show non-CC-_input_-derived recon meshes (never the photo) while REDISTRIBUTE stays provably clean, and close the one real gate gap (bio3d-arena internal recon).

**Architecture:** Extend three existing functions and add two scripts. The input-photo clearance gate (`assert_recon_photos_cleared`) moves from the display path to the redistribute path and widens to cover `bio3d-arena` internal recon; the reference panel suppresses un-cleared input photos; a CLI ingests owned-CC photos; a study-safe script performs the rose/soybean disposition.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, pytest. No new dependencies. No paid APIs in tests.

**Spec:** `docs/superpowers/specs/2026-07-05-publish-posture-completion-design.md`

## Global Constraints

- REDISTRIBUTE stays fail-loud: any un-cleared asset aborts the export. Never weaken it.
- This work only _loosens display_ and _tightens redistribute_.
- All display keeps the existing no-download + 🤖 AI-generated label (already built; not touched here).
- Reuse `app/reference_provenance.cleared_reference_taxa()`, `._taxon_of()`, `_CC_OK`, `_REQUIRED`, and `app/licensing.normalize_license` — never duplicate the allowlist or sidecar schema.
- Never run pytest/scripts against the real study DB (`data/study/arena-study.db`); use copies. Data-op scripts snapshot before mutating.
- Commit trailers on every commit:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01DNJyBTRwHehJDHpgwLia7J`
- Branch: `publish-posture-completion` (already created off master @532e6c6).

---

### Task 1: Move the input-photo gate to redistribute-only (free the display posture)

**Files:**

- Modify: `scripts/export_public.py` — `export_bundle`, the posture gate block (currently: `if posture == "redistribute": check_licenses(...) else: assert_recon_photos_cleared(...) + _for_gold(...)`).
- Test: `tests/test_export_script.py`

**Interfaces:**

- Consumes: `export_bundle(db, storage, *, task_titles, generator_slugs, out_dir, posture="redistribute", dry_run=False) -> dict`; `app.reference_provenance.assert_recon_photos_cleared(db, output_ids)` + `assert_recon_photos_cleared_for_gold(db, gold_output_ids)`; `app.public_export.check_licenses(db, output_ids)`.
- Produces: display bundle that does NOT raise on an un-cleared reference photo. (Redistribute already excludes commercial-model recon via `filter_include_for_posture`, so this move is a no-op for commercial recon until Task 3 widens the gate.)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_export_script.py`:

```python
def test_display_allows_uncleared_input_recon(db_session, tmp_path):
    # A commercial-model recon whose reference photo has NO cleared sidecar must still export
    # in the DISPLAY posture (mesh shows; photo suppressed elsewhere). Previously this raised.
    import json
    from app.models import Category, Generator, ModelOutput, Task
    from app.storage import LocalStorageBackend
    from scripts.export_public import export_bundle

    cat = Category(slug="plants", name="Plants")
    g = Generator(slug="fal-trellis", name="TRELLIS", kind="model", paradigm="image_recon")
    db_session.add_all([cat, g])
    db_session.flush()
    t = Task(category_id=cat.id, title="Rosa — single-image → 3D reconstruction",
             prompt="p", active=True)
    db_session.add(t)
    db_session.flush()
    o = ModelOutput(
        task_id=t.id, generator_id=g.id, asset_path="r.glb", asset_format="glb",
        source="api:fal:trellis", license="TRELLIS (fal) generated-asset terms",
        meta_json=json.dumps({"input_image": "reference/rose_ref.jpg"}),  # NO cleared sidecar
    )
    db_session.add(o)
    db_session.flush()
    store = LocalStorageBackend(tmp_path / "src_assets")
    manifest = export_bundle(
        db_session, store,
        task_titles=["Rosa — single-image → 3D reconstruction"],
        generator_slugs=["fal-trellis"], out_dir=str(tmp_path / "out"),
        posture="display", dry_run=True,
    )
    assert o.id in db_session.execute(
        __import__("sqlalchemy").select(ModelOutput.id).where(ModelOutput.id == o.id)
    ).scalars().all()  # sanity: row exists
    assert manifest["posture"] == "display"
    assert manifest["counts"]["model_output"] == 1  # the uncleared-input recon is INCLUDED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_export_script.py::test_display_allows_uncleared_input_recon -v`
Expected: FAIL — `ReferenceProvenanceError: output ... has no cleared CC provenance sidecar` (the display branch currently asserts).

- [ ] **Step 3: Write minimal implementation**

In `scripts/export_public.py::export_bundle`, replace the posture gate block:

```python
    if posture == "redistribute":
        public_export.check_licenses(db, inc.output_ids)  # fail-loud: nothing non-CC ships
        # Input-photo (derivative) clearance is a REDISTRIBUTION gate: a redistributed recon mesh
        # is a derivative of its input photo, so that photo must be CC-cleared. Displaying the
        # mesh (no download, AI-labeled) does not redistribute the photo, so display is exempt —
        # the un-cleared input photo is instead suppressed from the vote UI
        # (service.reference_images_for_task). "Show the mesh, never the photo."
        assert_recon_photos_cleared(db, inc.output_ids)
        assert_recon_photos_cleared_for_gold(db, inc.gold_output_ids)
    # else display: no input-photo gate.
```

(Keep the existing `from app.reference_provenance import (assert_recon_photos_cleared, assert_recon_photos_cleared_for_gold)` import.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_export_script.py::test_display_allows_uncleared_input_recon -v`
Expected: PASS

- [ ] **Step 5: Run the existing posture/export tests to confirm no regression**

Run: `python -m pytest tests/test_export_script.py tests/test_export_postures.py -q`
Expected: all pass (redistribute behavior unchanged — commercial recon still filtered out of redistribute).

- [ ] **Step 6: Commit**

```bash
git add scripts/export_public.py tests/test_export_script.py
git commit -m "feat(publish-safety): input-photo gate is redistribute-only (free display of non-CC-input recon meshes)"
```

---

### Task 2: Suppress un-cleared input photos from the reference panel ("never the photo")

**Files:**

- Modify: `app/service.py` — `reference_images_for_task`.
- Test: `tests/test_reference_image.py`

**Interfaces:**

- Consumes: `app.reference_provenance.cleared_reference_taxa() -> set[str]`, `._taxon_of(input_image: str|None) -> str|None`.
- Produces: `reference_images_for_task(db, task)` drops any `input_image` whose taxon is not CC-cleared; still shows cleared inputs + the CC species gallery.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reference_image.py`:

```python
def test_reference_suppresses_uncleared_input_photo(monkeypatch):
    # A visible recon output whose input photo taxon is NOT cleared must not surface the photo;
    # a cleared input still shows. (Gallery-less task title → refs == the shown input photos.)
    import json
    from app import reference_provenance
    from app.service import reference_images_for_task
    from app.storage import get_storage

    # only 'tomato' is cleared; 'rose' is not
    monkeypatch.setattr(reference_provenance, "cleared_reference_taxa", lambda: {"tomato"})

    with SessionLocal() as db:
        _clean(db)
        t = _task_with_outputs(
            db,
            [
                json.dumps({"input_image": "reference/tomato_ref_clean.jpg"}),  # cleared → shown
                json.dumps({"input_image": "reference/rose_ref.jpg"}),  # uncleared → suppressed
            ],
        )
        refs = reference_images_for_task(db, t)
        urls = [r["url"] for r in refs]
        assert get_storage().url_for("reference/tomato_ref_clean.jpg") in urls
        assert get_storage().url_for("reference/rose_ref.jpg") not in urls
        _clean(db)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reference_image.py::test_reference_suppresses_uncleared_input_photo -v`
Expected: FAIL — `rose_ref.jpg` URL IS present (no clearance filter yet).

- [ ] **Step 3: Write minimal implementation**

In `app/service.py::reference_images_for_task`, inside the visible-outputs loop, gate each input by clearance. Change the loop body from:

```python
        if img and img not in seen:
            seen.add(img)
            out.append({"url": st.url_for(img), "credit": "reconstruction input photo"})
```

to:

```python
        if img and img not in seen and _taxon_of(img) in cleared_taxa:
            seen.add(img)
            out.append({"url": st.url_for(img), "credit": "reconstruction input photo"})
```

And add, just before the loop (after `seen: set[str] = set()`), the clearance set + imports (add to the existing local import block at the top of the function):

```python
    from .reference_provenance import _taxon_of, cleared_reference_taxa

    cleared_taxa = cleared_reference_taxa()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reference_image.py::test_reference_suppresses_uncleared_input_photo -v`
Expected: PASS

- [ ] **Step 5: Run the reference-image suite (confirm existing cases still pass)**

Run: `python -m pytest tests/test_reference_image.py -q`
Expected: all pass. NOTE: existing tests use inputs like `reference/puffball_ref.jpg`, `reference/rose_ref.jpg` and assert they ARE shown. Those tests rely on the real on-disk sidecars. If a previously-passing test now fails because its input's taxon is not cleared on disk, update that test to `monkeypatch.setattr(reference_provenance, "cleared_reference_taxa", lambda: {<taxon>})` so it exercises the shown-path deterministically (do NOT weaken the new filter).

- [ ] **Step 6: Commit**

```bash
git add app/service.py tests/test_reference_image.py
git commit -m "feat(publish-safety): suppress un-cleared recon input photos from the vote UI (show the mesh, never the photo)"
```

---

### Task 3: Close the Stream-D gap — gate `bio3d-arena` internal recon on redistribute

**Files:**

- Modify: `app/reference_provenance.py` — `assert_recon_photos_cleared`.
- Test: `tests/test_reference_provenance.py`

**Interfaces:**

- Consumes: `app.public_export._COMMERCIAL_MODEL_PREFIXES: tuple[str, ...]`.
- Produces: `assert_recon_photos_cleared(db, output_ids)` now ALSO raises for a `bio3d-arena` output that is a recon (has `meta.input_image`) whose input taxon is un-cleared or unrecorded; a `bio3d-arena` GT/scan output (no `input_image`) stays exempt.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reference_provenance.py` (create fixtures inline; the file uses a `db_session` fixture or `SessionLocal` — match the file's existing style; example assumes `SessionLocal`):

```python
def test_bio3darena_recon_gated_on_redistribute(monkeypatch):
    import json
    from app import reference_provenance as rp
    from app.database import SessionLocal
    from app.models import Category, Generator, ModelOutput, Task

    monkeypatch.setattr(rp, "cleared_reference_taxa", lambda: {"tomato"})  # rose NOT cleared

    with SessionLocal() as db:
        cat = Category(slug="plants2", name="P")
        g = Generator(slug="internal-recon", name="internal", kind="model", paradigm="image_recon")
        db.add_all([cat, g]); db.flush()
        t = Task(category_id=cat.id, title="rp-rose", prompt="p", active=True)
        db.add(t); db.flush()
        # bio3d-arena recon from an UN-cleared photo → must raise
        bad = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="a.glb",
                          source="bio3d-arena",
                          meta_json=json.dumps({"input_image": "reference/rose_ref.jpg"}))
        # bio3d-arena GT mesh (no input_image) → exempt
        gt = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="gt.glb",
                         source="bio3d-arena", meta_json="{}")
        db.add_all([bad, gt]); db.flush()

        import pytest
        with pytest.raises(rp.ReferenceProvenanceError):
            rp.assert_recon_photos_cleared(db, {bad.id})
        rp.assert_recon_photos_cleared(db, {gt.id})  # no raise — no input_image
        db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reference_provenance.py::test_bio3darena_recon_gated_on_redistribute -v`
Expected: FAIL — `bad` does NOT raise (bio3d-arena currently skipped).

- [ ] **Step 3: Write minimal implementation**

In `app/reference_provenance.py::assert_recon_photos_cleared`, replace the skip condition. Change:

```python
    cleared = cleared_reference_taxa()
    for oid in sorted(output_ids):
        o = db.get(ModelOutput, oid)
        if o is None or not (o.source or "").startswith(_COMMERCIAL_MODEL_PREFIXES):
            continue
        img = (json.loads(o.meta_json or "{}")).get("input_image")
        taxon = _taxon_of(img)
        if taxon is None:
            raise ReferenceProvenanceError(...)  # existing message
        if taxon not in cleared:
            raise ReferenceProvenanceError(...)  # existing message
```

to (widen the "is this a recon we must check" test to include bio3d-arena outputs that carry an input_image):

```python
    cleared = cleared_reference_taxa()
    for oid in sorted(output_ids):
        o = db.get(ModelOutput, oid)
        if o is None:
            continue
        img = (json.loads(o.meta_json or "{}")).get("input_image")
        is_commercial = (o.source or "").startswith(_COMMERCIAL_MODEL_PREFIXES)
        # A recon derives from its input photo regardless of who ran it. Commercial-model recon
        # is always a recon; a bio3d-arena output is a recon iff it recorded an input_image (a
        # held-out GT mesh / scan has none) — those internal recons must ALSO be input-cleared to
        # ship in the dataset, else an internal recon from a non-CC photo would slip through.
        is_internal_recon = (o.source == "bio3d-arena") and (img is not None)
        if not (is_commercial or is_internal_recon):
            continue
        taxon = _taxon_of(img)
        if taxon is None:
            raise ReferenceProvenanceError(
                f"output {oid}: recon input '{img}' has no identifiable reference-photo taxon —"
                " cannot verify provenance"
            )
        if taxon not in cleared:
            raise ReferenceProvenanceError(
                f"output {oid}: recon input '{img}' (taxon {taxon!r}) has no cleared CC provenance sidecar"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reference_provenance.py::test_bio3darena_recon_gated_on_redistribute -v`
Expected: PASS

- [ ] **Step 5: Run the provenance + export suites**

Run: `python -m pytest tests/test_reference_provenance.py tests/test_export_script.py tests/test_export_postures.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/reference_provenance.py tests/test_reference_provenance.py
git commit -m "fix(publish-safety): gate bio3d-arena internal recon on its input photo in redistribute (Stream-D gap)"
```

---

### Task 4: `add_reference_photo.py` — owned-CC photo ingestion

**Files:**

- Create: `scripts/add_reference_photo.py`
- Test: `tests/test_add_reference_photo.py`

**Interfaces:**

- Consumes: `app.reference_provenance._REQUIRED: set[str]`, `app.licensing.normalize_license`, `app.reference_provenance._CC_OK`.
- Produces: `write_reference_photo(*, taxon, image_path, author, license_, source_url, download_url, subject, title, note, dest_dirs, force=False) -> Path` — copies image to `{taxon}_ref_clean.jpg` and writes `{taxon}_ref_clean.json` (all `_REQUIRED` fields) in each dest dir; raises `ValueError` on non-CC license or missing field; refuses to overwrite without `force`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_add_reference_photo.py`:

```python
import json
import pytest
from pathlib import Path
from scripts.add_reference_photo import write_reference_photo


def _img(p: Path) -> Path:
    p.write_bytes(b"\xff\xd8\xff\xe0JFIFdummyjpeg")  # non-empty stand-in
    return p


def test_writes_sidecar_that_clears(tmp_path):
    src = _img(tmp_path / "in.jpg")
    dest = tmp_path / "reference"
    dest.mkdir()
    out = write_reference_photo(
        taxon="basil", image_path=src, author="Jaret Arnold", license_="CC0-1.0",
        source_url="https://example.com/basil", download_url="https://example.com/basil.jpg",
        subject="Ocimum basilicum (whole plant)", title="Basil plant",
        note="Owner-shot nursery photo.", dest_dirs=[dest],
    )
    assert out == dest / "basil_ref_clean.jpg"
    meta = json.loads((dest / "basil_ref_clean.json").read_text())
    from app.reference_provenance import _REQUIRED
    assert _REQUIRED <= set(meta)
    assert meta["file"] == "basil_ref_clean.jpg"
    assert meta["license"] == "CC0-1.0"


def test_rejects_non_cc_license(tmp_path):
    src = _img(tmp_path / "in.jpg")
    dest = tmp_path / "reference"; dest.mkdir()
    with pytest.raises(ValueError, match="license"):
        write_reference_photo(
            taxon="basil", image_path=src, author="x", license_="All Rights Reserved",
            source_url="https://x", download_url="https://x.jpg", subject="s", title="t",
            note="n", dest_dirs=[dest],
        )


def test_refuses_overwrite_without_force(tmp_path):
    src = _img(tmp_path / "in.jpg")
    dest = tmp_path / "reference"; dest.mkdir()
    kw = dict(taxon="basil", image_path=src, author="x", license_="CC0-1.0",
              source_url="https://x", download_url="https://x.jpg", subject="s", title="t",
              note="n", dest_dirs=[dest])
    write_reference_photo(**kw)
    with pytest.raises(FileExistsError):
        write_reference_photo(**kw)
    write_reference_photo(**kw, force=True)  # ok with force
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_add_reference_photo.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.add_reference_photo`.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/add_reference_photo.py`:

```python
# scripts/add_reference_photo.py
"""Ingest an OWNED / CC-licensed reference photo as a recon input: copy it to
{taxon}_ref_clean.jpg and write a provenance sidecar that clears reference_provenance's gate.
Does NOT touch the DB or call any paid regen — after this, run:
  scripts/generate_api_recon.py --crop {taxon} --force   (then completeness scoring)
and point CROPS[{taxon}] at {taxon}_ref_clean.jpg if not already."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from app.licensing import normalize_license
from app.reference_provenance import _CC_OK, _REQUIRED


def write_reference_photo(
    *,
    taxon: str,
    image_path: Path,
    author: str,
    license_: str,
    source_url: str,
    download_url: str,
    subject: str,
    title: str,
    note: str,
    dest_dirs: list[Path],
    force: bool = False,
) -> Path:
    norm = normalize_license(license_)
    if norm not in _CC_OK:
        raise ValueError(
            f"license {license_!r} normalizes to {norm!r}, not in the CC allowlist {sorted(_CC_OK)}"
        )
    sidecar = {
        "subject": subject,
        "file": f"{taxon}_ref_clean.jpg",
        "source": source_url.split("/")[2] if "//" in source_url else source_url,
        "source_url": source_url,
        "download_url": download_url,
        "license": norm,
        "author": author,
        "attribution": f"{title} by {author}, {norm}",
        "title": title,
        "note": note,
    }
    missing = _REQUIRED - set(sidecar)
    if missing:
        raise ValueError(f"sidecar missing required fields: {sorted(missing)}")
    if not all(str(sidecar[k]).strip() for k in ("author", "attribution", "title", "subject")):
        raise ValueError("author/attribution/title/subject must all be non-empty")

    first_jpg = None
    for d in dest_dirs:
        d.mkdir(parents=True, exist_ok=True)
        jpg = d / f"{taxon}_ref_clean.jpg"
        if jpg.exists() and not force:
            raise FileExistsError(f"{jpg} exists (use force=True to overwrite)")
        shutil.copyfile(image_path, jpg)
        (d / f"{taxon}_ref_clean.json").write_text(json.dumps(sidecar, indent=2))
        first_jpg = first_jpg or jpg
    assert first_jpg is not None
    return first_jpg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--taxon", required=True)
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--author", required=True)
    ap.add_argument("--license", dest="license_", required=True)
    ap.add_argument("--source-url", required=True)
    ap.add_argument("--download-url", required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--note", default="Owner-supplied CC reference photo.")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    from app import config

    main_ref = config.ASSET_DIR / "reference"
    dest_dirs = [main_ref]
    out = write_reference_photo(
        taxon=a.taxon, image_path=a.image, author=a.author, license_=a.license_,
        source_url=a.source_url, download_url=a.download_url, subject=a.subject,
        title=a.title, note=a.note, dest_dirs=dest_dirs, force=a.force,
    )
    print(f"wrote {out} + sidecar. Next: point CROPS[{a.taxon!r}] at {a.taxon}_ref_clean.jpg, "
          f"then `python scripts/generate_api_recon.py --crop {a.taxon} --force` + score completeness.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_add_reference_photo.py -v`
Expected: PASS (all three)

- [ ] **Step 5: Commit**

```bash
git add scripts/add_reference_photo.py tests/test_add_reference_photo.py
git commit -m "feat(publish-safety): add_reference_photo.py — ingest owned-CC recon inputs (sidecar clears the gate)"
```

---

### Task 5: `disposition_rose_soybean.py` — un-hide good recon, hide weak CC recon (study-safe)

**Files:**

- Create: `scripts/disposition_rose_soybean.py`
- Test: `tests/test_disposition_rose_soybean.py`

**Interfaces:**

- Consumes: `app.models.ModelOutput`, a DB session.
- Produces: `plan_disposition(db) -> dict` returning `{"unhide": [output_ids], "hide": [output_ids]}` computed by rule (never hard-coded IDs), and `apply_disposition(db, plan) -> None` that sets/clears `hidden_at`. Rule: for tasks titled `Rosa …reconstruction` and `Glycine max …reconstruction`, UN-HIDE the visible-quality good recon (the api:\* recon whose `meta.input_image` is the ORIGINAL non-CC photo `{taxon}_ref.jpg`), HIDE the recon whose input is the `{taxon}_ref_clean.jpg` CC swap. (The script refuses to run against a study path directly — operate on a copy — mirroring `is_safe_test_db_target`.)

- [ ] **Step 1: Write the failing test**

Create `tests/test_disposition_rose_soybean.py`:

```python
import json
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from scripts.disposition_rose_soybean import plan_disposition


def setup_module(_m):
    init_db()


def test_plan_unhides_original_input_recon_and_hides_clean_swap():
    with SessionLocal() as db:
        cat = Category(slug="plants-d", name="P")
        g = Generator(slug="d-recon", name="r", kind="model", paradigm="image_recon")
        db.add_all([cat, g]); db.flush()
        t = Task(category_id=cat.id, title="Rosa — single-image → 3D reconstruction",
                 prompt="p", active=True)
        db.add(t); db.flush()
        import datetime as dt
        good = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="good.glb",
                           source="api:fal:trellis",
                           meta_json=json.dumps({"input_image": "reference/rose_ref.jpg"}),
                           hidden_at=dt.datetime(2026, 7, 5, tzinfo=dt.timezone.utc))  # currently hidden
        weak = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="weak.glb",
                           source="api:fal:trellis",
                           meta_json=json.dumps({"input_image": "reference/rose_ref_clean.jpg"}))  # visible
        db.add_all([good, weak]); db.flush()

        plan = plan_disposition(db)
        assert good.id in plan["unhide"]
        assert weak.id in plan["hide"]
        db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_disposition_rose_soybean.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.disposition_rose_soybean`.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/disposition_rose_soybean.py`:

```python
# scripts/disposition_rose_soybean.py
"""Disposition after the display-gate loosening: SHOW the good non-CC-input rose/soybean recon
(un-hide) and HIDE the weak CC-swap recon. Rule-based (no hard-coded ids). Study-safe: refuses
to run against the study DB path directly — snapshot + operate on a copy, then promote."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ModelOutput, Task

_TAXA = {
    "rose": ("Rosa — single-image → 3D reconstruction", "rose"),
    "soybean": ("Glycine max — single-image → 3D reconstruction", "soybean"),
}


def _input_of(o: ModelOutput) -> str | None:
    try:
        return (json.loads(o.meta_json or "{}") or {}).get("input_image")
    except (ValueError, TypeError):
        return None


def plan_disposition(db) -> dict:
    unhide: list[int] = []
    hide: list[int] = []
    for _key, (title, slug) in _TAXA.items():
        t = db.execute(select(Task).where(Task.title == title)).scalars().first()
        if t is None:
            continue
        for o in db.execute(select(ModelOutput).where(ModelOutput.task_id == t.id)).scalars():
            img = _input_of(o)
            if img is None:
                continue
            if img.endswith(f"{slug}_ref_clean.jpg"):
                hide.append(o.id)  # weak CC-swap recon → hide
            elif img.endswith(f"{slug}_ref.jpg"):
                unhide.append(o.id)  # good original-input recon → show
    return {"unhide": sorted(unhide), "hide": sorted(hide)}


def apply_disposition(db, plan: dict) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    for oid in plan["unhide"]:
        o = db.get(ModelOutput, oid)
        if o is not None:
            o.hidden_at = None
    for oid in plan["hide"]:
        o = db.get(ModelOutput, oid)
        if o is not None and o.hidden_at is None:
            o.hidden_at = now
    db.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="apply (default: dry-run print)")
    ap.parse_args()
    url = os.environ.get("BIO3D_DATABASE_URL", "")
    if "arena-study.db" in url and "PRE-" not in url and "copy" not in url:
        print("refusing to run against the study DB directly — copy it first", file=sys.stderr)
        return 2
    db = SessionLocal()
    plan = plan_disposition(db)
    print(json.dumps(plan, indent=2))
    if "--apply" in sys.argv:
        apply_disposition(db, plan)
        print(f"APPLIED: un-hid {len(plan['unhide'])}, hid {len(plan['hide'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_disposition_rose_soybean.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/disposition_rose_soybean.py tests/test_disposition_rose_soybean.py
git commit -m "feat(publish-safety): disposition_rose_soybean.py — show good non-CC recon, hide weak CC-swap (rule-based, study-safe)"
```

---

### Task 6: Full-suite gate + push

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (baseline was 774 pass, 12 skipped on master; expect ~780+ with the new tests).

- [ ] **Step 2: Push the branch**

```bash
git push -u origin publish-posture-completion
```

- [ ] **Step 3: Open a draft PR**

```bash
gh pr create --draft --base master --head publish-posture-completion \
  --title "Publish-posture completion: display shows non-CC-input recon meshes; redistribute closes the Stream-D gap" \
  --body "Implements docs/superpowers/specs/2026-07-05-publish-posture-completion-design.md. See plan 2026-07-05-publish-posture-completion.md."
```

---

## Notes for the executor

- **Data-op (Component 4) is NOT run by this plan.** Task 5 only builds + unit-tests the
  disposition script. Running it against the real study DB (snapshotted copy → promote) is a
  separate operator step after merge, per the spec's rollout section.
- **Component 5 (probe-informed redistribute expansion) is out of scope** — documented follow-on.
- If Task 2 Step 5 surfaces a pre-existing reference-image test that assumed an un-cleared input
  was shown, fix it by monkeypatching `cleared_reference_taxa` (do not weaken the filter).
