<!-- ROOT_CAUSE_OK: implementation plan, not a bug fix -->

# Pre-Publish License & Gate Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public export physically incapable of shipping an un-cleared asset, via a license-string normalizer, a license backfill (own/LLM/procedural→CC0, crops3d relabel, Objaverse per-uid), reference-photo provenance enforcement, and a two-posture export gate (`display` arena vs strict `redistribute` dataset).

**Architecture:** A pure `normalize_license` fn feeds the existing fail-loud `check_licenses`. A backfill driver corrects `model_output.license` on a DB copy. `export_bundle` gains a `posture` param: `redistribute` keeps the strict CC/CC0/own set (+ excludes commercial-model + admissibility-gated); `display` additionally admits commercial-model recon (attribution + AI-label + reference-photo-provenance-enforced), minus the hard-excludes.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, pytest, PIL (EXIF), the `objaverse` package (per-uid license lookup, injected for tests).

## Global Constraints

- Fail-loud, never loosen. Allowed license-passing changes: (1) normalize a string to an already-allowlisted SPDX id, (2) correct a mislabel to the verified license, (3) tag our own assets CC0. Every gate ABORTS rather than silently drop/include.
- `REDISTRIBUTABLE_LICENSES` stays exactly `{CC0-1.0, CC-BY-4.0, CC-BY-SA-4.0, CC-BY-3.0, CC-BY-2.0, PUBLIC-DOMAIN, ODbL-1.0}` — never add NC/ND/commercial.
- Commercial-model recon = source starting `api:`, `recon:`, or `frontier:`. Hard-excludes (both postures) = sources `found:xfrog`, `procedural:demeter`, `procedural:agrigen`.
- Own/procedural/LLM → `CC0-1.0`. crops3d → `CC0-1.0` (verified Figshare data-record).
- Read-only on the real study DB; backfill runs on a COPY. NEVER `BIO3D_DATABASE_URL=study`. Test runner `.venv/bin/pytest`.

---

### Task 1: License-string normalizer + wire into `check_licenses`

**Files:**

- Create: `app/licensing.py`
- Modify: `app/public_export.py` (`check_licenses` calls the normalizer)
- Test: `tests/test_licensing.py`

**Interfaces:**

- Produces: `normalize_license(raw: str | None) -> str | None` — maps loose forms to SPDX; `None`/unrecognized → returned uppercased-normalized (so an unknown string stays non-allowlisted and still fails the gate).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_licensing.py
from app.licensing import normalize_license


def test_space_forms_map_to_spdx():
    assert normalize_license("CC-BY 4.0") == "CC-BY-4.0"
    assert normalize_license("CC BY 4.0") == "CC-BY-4.0"
    assert normalize_license("CC0 1.0") == "CC0-1.0"
    assert normalize_license("CC0") == "CC0-1.0"
    assert normalize_license("CC-BY-SA 4.0") == "CC-BY-SA-4.0"
    assert normalize_license("CC-BY 3.0") == "CC-BY-3.0"


def test_objaverse_codes():
    assert normalize_license("by") == "CC-BY-4.0"
    assert normalize_license("cc0") == "CC0-1.0"
    assert normalize_license("by-sa") == "CC-BY-SA-4.0"


def test_already_spdx_unchanged():
    assert normalize_license("CC-BY-4.0") == "CC-BY-4.0"
    assert normalize_license("CC0-1.0") == "CC0-1.0"


def test_nc_nd_normalize_but_stay_nonredistributable():
    from app.public_export import REDISTRIBUTABLE_LICENSES
    assert normalize_license("CC-BY-NC-ND 4.0") == "CC-BY-NC-ND-4.0"
    assert normalize_license("by-nc") == "CC-BY-NC-4.0"
    assert normalize_license("CC-BY-NC-ND-4.0") not in REDISTRIBUTABLE_LICENSES
    assert normalize_license("CC-BY-NC-4.0") not in REDISTRIBUTABLE_LICENSES


def test_none_and_freeform():
    assert normalize_license(None) is None
    assert normalize_license("") is None
    # a wordy provider string is not laundered into an allowlisted id
    assert normalize_license("Hunyuan3D v2 (fal) generated-asset terms") not in {"CC-BY-4.0", "CC0-1.0"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_licensing.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.licensing'`).

- [ ] **Step 3: Implement `normalize_license`**

```python
# app/licensing.py
"""License-string normalization to SPDX ids, so the fail-loud allowlist in public_export
passes legitimately-redistributable assets that were merely labelled in a loose/space form
(e.g. 'CC-BY 4.0' -> 'CC-BY-4.0'). Never widens the allowlist: NC/ND/unknown strings normalize
to a non-allowlisted form and still fail the gate."""

from __future__ import annotations

import re

# Objaverse/Sketchfab short codes -> CC family (version 4.0 is the Sketchfab default).
_OBJAVERSE_CODES = {
    "by": "CC-BY-4.0",
    "cc0": "CC0-1.0",
    "by-sa": "CC-BY-SA-4.0",
    "by-nc": "CC-BY-NC-4.0",
    "by-nd": "CC-BY-ND-4.0",
    "by-nc-sa": "CC-BY-NC-SA-4.0",
    "by-nc-nd": "CC-BY-NC-ND-4.0",
}


def normalize_license(raw: str | None) -> str | None:
    """Map a loose license label to an SPDX-style id. None/empty -> None. Deterministic:
    lowercase-match short codes; else uppercase, strip a trailing parenthetical (e.g.
    '... (Sketchfab, author)'), collapse internal whitespace/underscores to '-', map CC0."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    low = s.lower()
    if low in _OBJAVERSE_CODES:
        return _OBJAVERSE_CODES[low]
    # Drop a trailing parenthetical note: "CC-BY 4.0 (Sketchfab, foo)" -> "CC-BY 4.0"
    s = re.sub(r"\s*\(.*\)\s*$", "", s).strip()
    up = s.upper()
    # collapse spaces/underscores between tokens to hyphens
    up = re.sub(r"[\s_]+", "-", up)
    # 'CC0' or 'CC0-1.0' -> 'CC0-1.0'
    if up in ("CC0", "CC0-1.0"):
        return "CC0-1.0"
    if up == "PUBLIC-DOMAIN":
        return "PUBLIC-DOMAIN"
    return up
```

Then modify `app/public_export.py` `check_licenses` to normalize before the allowlist test:

```python
def check_licenses(db: Session, output_ids: set[int]) -> None:
    from .licensing import normalize_license

    for oid in sorted(output_ids):
        o = db.get(ModelOutput, oid)
        if o is None:
            continue
        if o.source == "bio3d-arena":  # our own asset — exempt
            continue
        if normalize_license(o.license) not in REDISTRIBUTABLE_LICENSES:
            raise LicenseError(oid, o.license)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_licensing.py tests/test_public_export.py -v`
Expected: PASS (normalizer green; existing export license tests unaffected — space-form labels now pass, which is the intended behavior).

- [ ] **Step 5: Commit**

```bash
git add app/licensing.py app/public_export.py tests/test_licensing.py
git commit -m "feat(publish-safety): license-string normalizer wired into check_licenses"
```

---

### Task 2: License backfill (own/LLM/procedural→CC0, crops3d relabel, Objaverse per-uid)

**Files:**

- Create: `scripts/backfill_licenses.py`
- Test: `tests/test_backfill_licenses.py`

**Interfaces:**

- Consumes: `normalize_license` (Task 1).
- Produces: `backfill_licenses(db, *, objaverse_license_for) -> dict` — mutates `ModelOutput.license`/`attribution` in place, returns a disposition summary. `objaverse_license_for: Callable[[str], str | None]` maps an `objaverse_uid` → its raw license code (injected; real impl uses the `objaverse` package). Idempotent. Caller commits.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backfill_licenses.py
import json
import uuid
from scripts.backfill_licenses import CC0, backfill_licenses
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _out(db, source, license_=None, meta=None):
    g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g); db.flush()
    t = Task(title=f"t-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t); db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb",
                    source=source, license=license_, meta_json=json.dumps(meta or {}))
    db.add(o); db.flush()
    return o


def test_own_llm_procedural_get_cc0():
    with SessionLocal() as db:
        own = _out(db, "bio3d-arena")
        comm = _out(db, "commissioned")
        agent = _out(db, "agentic:openai/gpt-5.1")
        proc = _out(db, "procedural:lpy", license_="L-Py (CeCILL-C)")
        backfill_licenses(db, objaverse_license_for=lambda uid: None)
        for o in (own, comm, agent, proc):
            assert o.license == CC0
        db.rollback()


def test_crops3d_relabelled_to_cc0():
    with SessionLocal() as db:
        c = _out(db, "crops3d", license_="CC-BY-NC-ND 4.0")
        backfill_licenses(db, objaverse_license_for=lambda uid: None)
        assert c.license == CC0
        db.rollback()


def test_objaverse_per_uid_and_nc_left_nonredistributable():
    with SessionLocal() as db:
        keep = _out(db, "objaverse", license_="by", meta={"objaverse_uid": "AAA"})
        drop = _out(db, "objaverse", license_="by", meta={"objaverse_uid": "BBB"})
        lookup = {"AAA": "by", "BBB": "by-nc"}
        backfill_licenses(db, objaverse_license_for=lambda uid: lookup.get(uid))
        assert keep.license == "CC-BY-4.0"
        assert drop.license == "CC-BY-NC-4.0"  # normalized but stays non-allowlisted -> gate excludes
        db.rollback()


def test_hard_excludes_untouched():
    with SessionLocal() as db:
        xf = _out(db, "found:xfrog", license_="XfrogPlants commercial (purchased)")
        dm = _out(db, "procedural:demeter", license_="Demeter (NC research)")
        before = (xf.license, dm.license)
        backfill_licenses(db, objaverse_license_for=lambda uid: None)
        assert (xf.license, dm.license) == before  # never relabelled
        db.rollback()


def test_idempotent():
    with SessionLocal() as db:
        o = _out(db, "bio3d-arena")
        backfill_licenses(db, objaverse_license_for=lambda uid: None)
        first = o.license
        backfill_licenses(db, objaverse_license_for=lambda uid: None)
        assert o.license == first
        db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backfill_licenses.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'scripts.backfill_licenses'`).

- [ ] **Step 3: Implement the backfill**

```python
# scripts/backfill_licenses.py
"""Correct/assign model_output.license on a DB COPY before public export (idempotent, fail-loud).
NEVER run against the real study DB. Own/LLM/procedural -> CC0; crops3d -> CC0 (verified Figshare
data-record); Objaverse -> resolved per-uid license; space-form CC -> normalized SPDX. The three
hard-excludes (xfrog/demeter/agrigen) are left untouched (stay non-redistributable)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.licensing import normalize_license  # noqa: E402
from app.models import ModelOutput  # noqa: E402
from sqlalchemy import select  # noqa: E402

CC0 = "CC0-1.0"
HARD_EXCLUDE_SOURCES = {"found:xfrog", "procedural:demeter", "procedural:agrigen"}
_OWN_CC0_PREFIXES = ("bio3d-arena", "commissioned", "agentic:", "procedural:", "infinigen")


def _is_own_cc0(source: str | None) -> bool:
    s = source or ""
    if s in HARD_EXCLUDE_SOURCES:
        return False
    return s == "bio3d-arena" or s == "commissioned" or s == "infinigen" or s.startswith(
        ("agentic:", "procedural:")
    )


def backfill_licenses(db, *, objaverse_license_for: Callable[[str], str | None]) -> dict:
    disp: Counter = Counter()
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    for o in outs:
        src = o.source or ""
        if src in HARD_EXCLUDE_SOURCES:
            disp["hard_exclude_untouched"] += 1
            continue
        if src == "objaverse":
            uid = (json.loads(o.meta_json or "{}")).get("objaverse_uid")
            code = objaverse_license_for(uid) if uid else None
            norm = normalize_license(code) if code else None
            if norm:
                o.license = norm
                disp[f"objaverse:{norm}"] += 1
            else:
                disp["objaverse_unresolved"] += 1  # stays as-is -> non-allowlisted -> gate excludes
            continue
        if src == "crops3d":
            o.license = CC0
            o.attribution = (o.attribution or "") or "Crops3D — Figshare data-record CC0 (art. 27313272)"
            disp["crops3d->CC0"] += 1
            continue
        if _is_own_cc0(src):
            o.license = CC0
            disp["own_or_llm_or_procedural->CC0"] += 1
            continue
        # everything else (external CC datasets, sketchfab, api:* commercial-model): normalize only
        norm = normalize_license(o.license)
        if norm and norm != o.license:
            o.license = norm
            disp[f"normalized:{norm}"] += 1
    return dict(disp)


def _objaverse_lookup(uids: set[str]) -> Callable[[str], str | None]:
    try:
        import objaverse  # type: ignore
    except Exception:
        raise SystemExit("objaverse package not installed; `pip install objaverse` to resolve per-uid licenses")
    anns = objaverse.load_annotations(list(uids))
    return lambda uid: (anns.get(uid) or {}).get("license")


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill model_output.license on a DB COPY.")
    ap.add_argument("--commit", action="store_true", help="persist changes (default: dry-run)")
    args = ap.parse_args()
    if "study" in (config.DATABASE_URL or "").lower():
        raise SystemExit("refusing to run against a 'study' DB — use a copy")
    with SessionLocal() as db:
        uids = {
            json.loads(o.meta_json or "{}").get("objaverse_uid")
            for o in db.execute(
                select(ModelOutput).where(ModelOutput.source == "objaverse")
            ).scalars().all()
        }
        uids = {u for u in uids if u}
        lookup = _objaverse_lookup(uids) if uids else (lambda uid: None)
        disp = backfill_licenses(db, objaverse_license_for=lookup)
        if args.commit:
            db.commit()
        print(json.dumps({"committed": args.commit, "disposition": disp}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backfill_licenses.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_licenses.py tests/test_backfill_licenses.py
git commit -m "feat(publish-safety): license backfill (own/LLM/procedural CC0, crops3d, Objaverse per-uid)"
```

---

### Task 3: Reference-photo provenance enforcement

**Files:**

- Create: `app/reference_provenance.py`
- Modify: `tests/test_reference_provenance.py` (all recon taxa)
- Create: `scripts/source_reference_sidecars.py` (best-effort sourcing helper)
- Test: `tests/test_reference_gate.py`

**Interfaces:**

- Produces: `ReferenceProvenanceError(Exception)`; `cleared_reference_taxa() -> set[str]` (taxa whose `{taxon}_ref.json` sidecar exists with a CC license + required fields); `assert_recon_photos_cleared(db, output_ids)` — raises `ReferenceProvenanceError` if any recon (`api:`/`recon:`/`frontier:`) output's `meta_json.input_image` names a taxon whose sidecar is missing/non-CC.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reference_gate.py
import json, uuid, os
import pytest
from app.reference_provenance import ReferenceProvenanceError, assert_recon_photos_cleared
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _recon(db, input_image):
    g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g); db.flush()
    t = Task(title=f"t-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t); db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb",
                    source="api:fal:trellis", meta_json=json.dumps({"input_image": input_image}))
    db.add(o); db.flush()
    return o


def test_raises_when_reference_sidecar_missing():
    with SessionLocal() as db:
        o = _recon(db, "reference/zzz_ref.jpg")  # 'zzz' has no sidecar
        with pytest.raises(ReferenceProvenanceError):
            assert_recon_photos_cleared(db, {o.id})
        db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_reference_gate.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.reference_provenance'`).

- [ ] **Step 3: Implement the gate + all-taxa test + sourcing helper**

```python
# app/reference_provenance.py
"""Enforce that every recon input reference photo has a cleared CC provenance sidecar before it
may be published (even display). A render of a derivative of a copyrighted photo is still a display
of that photo's work — so an uncleared reference photo blocks its recon outputs."""

from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import ModelOutput

_CC_OK = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-3.0", "CC-BY-SA-3.0"}
_REQUIRED = {"subject", "file", "source", "source_url", "download_url", "license",
             "author", "attribution", "title", "note"}
_RECON_PREFIXES = ("api:", "recon:", "frontier:")


class ReferenceProvenanceError(RuntimeError):
    pass


def _ref_dir():
    return config.ASSET_DIR / "reference"


def cleared_reference_taxa() -> set[str]:
    """Taxa with a valid CC sidecar {taxon}_ref.json (all required fields, allowlisted license)."""
    ok: set[str] = set()
    d = _ref_dir()
    if not d.exists():
        return ok
    from .licensing import normalize_license
    for meta in d.glob("*_ref.json"):
        try:
            data = json.loads(meta.read_text())
        except Exception:
            continue
        if _REQUIRED <= set(data) and normalize_license(data.get("license")) in _CC_OK:
            taxon = meta.name[: -len("_ref.json")]
            ok.add(taxon)
    return ok


def _taxon_of(input_image: str | None) -> str | None:
    if not input_image:
        return None
    m = re.search(r"([a-z0-9]+)_ref", input_image.lower())
    return m.group(1) if m else None


def assert_recon_photos_cleared(db: Session, output_ids: set[int]) -> None:
    """Raise if any recon output in the set uses a reference photo whose taxon lacks a cleared sidecar."""
    cleared = cleared_reference_taxa()
    for oid in sorted(output_ids):
        o = db.get(ModelOutput, oid)
        if o is None or not (o.source or "").startswith(_RECON_PREFIXES):
            continue
        img = (json.loads(o.meta_json or "{}")).get("input_image")
        taxon = _taxon_of(img)
        if taxon is not None and taxon not in cleared:
            raise ReferenceProvenanceError(
                f"output {oid}: recon input '{img}' (taxon {taxon!r}) has no cleared CC provenance sidecar"
            )
```

Modify `tests/test_reference_provenance.py`: change the parametrize line to cover all recon taxa:

```python
@pytest.mark.parametrize("slug", ["arabidopsis", "maize", "rose", "soybean", "tomato", "pinus"])
def test_reference_has_image_and_valid_provenance(slug):
    # ... body unchanged (still skips when the gitignored runtime asset is absent) ...
```

Create `scripts/source_reference_sidecars.py` — a best-effort helper that reports EXIF + old-sidecar hints for each reference photo so a human/agent can fill the sidecar; it never fabricates provenance:

```python
# scripts/source_reference_sidecars.py
"""Best-effort: surface any embedded provenance hints (EXIF Artist/Copyright, matching old MVP
sidecar) for each current reference photo, to help fill its CC provenance sidecar. Fabricates
nothing — untraceable photos are reported so they can be swapped or hand-sourced."""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402


def main() -> int:
    ref = config.ASSET_DIR / "reference"
    for img in sorted(glob.glob(str(ref / "*_ref.jpg"))):
        name = os.path.basename(img)
        hint = "no EXIF"
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS
            ex = Image.open(img)._getexif() or {}
            tags = {TAGS.get(k, k): v for k, v in ex.items()
                    if TAGS.get(k, k) in ("Artist", "Copyright", "ImageDescription")}
            hint = str(tags) if tags else "no source EXIF"
        except Exception as e:
            hint = f"unreadable: {e}"
        sidecar = img[:-4] + ".json"
        print(f"{name}: sidecar={'present' if os.path.exists(sidecar) else 'MISSING'} | {hint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_reference_gate.py tests/test_reference_provenance.py -v`
Expected: PASS (gate raises on missing sidecar; provenance test skips absent runtime assets as before).

- [ ] **Step 5: Commit**

```bash
git add app/reference_provenance.py tests/test_reference_provenance.py tests/test_reference_gate.py scripts/source_reference_sidecars.py
git commit -m "feat(publish-safety): reference-photo provenance gate (all taxa, fail-loud) + sourcing helper"
```

---

### Task 4: Two-posture export gate (`display` vs `redistribute`) + admissibility exclusion

**Files:**

- Modify: `app/public_export.py` (posture filter + hard-excludes + commercial-model predicate)
- Modify: `scripts/export_public.py` (`export_bundle` gains `posture`; applies admissibility + reference gate; `--posture` CLI)
- Modify: `scripts/build_dataset_release.py` (call with `posture="redistribute"`)
- Test: `tests/test_export_postures.py`

**Interfaces:**

- Consumes: `normalize_license` (T1), `assert_recon_photos_cleared` (T3), `admissibility.non_admitted_output_ids`.
- Produces: `public_export.filter_include_for_posture(db, inc, posture, gated) -> None` (narrows `inc.output_ids` in place); `is_commercial_model(source) -> bool`; `HARD_EXCLUDE_SOURCES`. `export_bundle(..., posture: str = "redistribute")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export_postures.py
import uuid
from app import public_export
from app.public_export import IncludeSet, filter_include_for_posture, is_commercial_model
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _o(db, source, license_):
    g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g); db.flush()
    t = Task(title=f"t-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t); db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb",
                    source=source, license=license_)
    db.add(o); db.flush()
    return o


def test_commercial_model_predicate():
    assert is_commercial_model("api:fal:trellis")
    assert is_commercial_model("recon:trellis-mv")
    assert is_commercial_model("frontier:partcrafter")
    assert not is_commercial_model("plant3d")
    assert not is_commercial_model("bio3d-arena")


def test_redistribute_drops_commercial_and_keeps_cc():
    with SessionLocal() as db:
        cc = _o(db, "plant3d", "CC0-1.0")
        comm = _o(db, "api:fal:trellis", "TRELLIS (fal) generated-asset terms")
        xf = _o(db, "found:xfrog", "XfrogPlants commercial")
        inc = IncludeSet(output_ids={cc.id, comm.id, xf.id})
        filter_include_for_posture(db, inc, "redistribute", gated=set())
        assert inc.output_ids == {cc.id}
        db.rollback()


def test_display_keeps_commercial_drops_hardexclude_and_gated():
    with SessionLocal() as db:
        cc = _o(db, "plant3d", "CC0-1.0")
        comm = _o(db, "api:fal:trellis", "TRELLIS (fal) generated-asset terms")
        xf = _o(db, "found:xfrog", "XfrogPlants commercial")
        inc = IncludeSet(output_ids={cc.id, comm.id, xf.id})
        filter_include_for_posture(db, inc, "display", gated={cc.id})  # cc gated out
        assert inc.output_ids == {comm.id}   # commercial kept, xfrog + gated dropped
        db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_export_postures.py -v`
Expected: FAIL (`ImportError: cannot import name 'filter_include_for_posture'`).

- [ ] **Step 3: Implement the posture filter + wire into export_bundle**

Add to `app/public_export.py`:

```python
HARD_EXCLUDE_SOURCES = frozenset({"found:xfrog", "procedural:demeter", "procedural:agrigen"})
_COMMERCIAL_MODEL_PREFIXES = ("api:", "recon:", "frontier:")


def is_commercial_model(source: str | None) -> bool:
    return (source or "").startswith(_COMMERCIAL_MODEL_PREFIXES)


def filter_include_for_posture(db: Session, inc: "IncludeSet", posture: str, gated: set[int]) -> None:
    """Narrow inc.output_ids in place per posture. redistribute = strict redistributable, no
    commercial-model. display = redistributable OR commercial-model recon. Both drop the hard-
    excludes and admissibility-gated. Attribution/labeling is carried by the row export."""
    from .licensing import normalize_license

    keep: set[int] = set()
    for oid in inc.output_ids:
        if oid in gated:
            continue
        o = db.get(ModelOutput, oid)
        if o is None or o.source in HARD_EXCLUDE_SOURCES:
            continue
        redistributable = o.source == "bio3d-arena" or normalize_license(o.license) in REDISTRIBUTABLE_LICENSES
        if posture == "redistribute":
            if redistributable and not is_commercial_model(o.source):
                keep.add(oid)
        elif posture == "display":
            if redistributable or is_commercial_model(o.source):
                keep.add(oid)
        else:
            raise ValueError(f"unknown posture {posture!r}")
    inc.output_ids = keep
```

Modify `scripts/export_public.py` `export_bundle` to take `posture`, apply the admissibility gate + posture filter, and (redistribute) keep the fail-loud `check_licenses`, (display) enforce reference photos:

```python
def export_bundle(
    db, storage: StorageBackend, *, task_titles, generator_slugs, out_dir,
    posture: str = "redistribute", dry_run: bool = False,
) -> dict:
    from app import admissibility
    from app.reference_provenance import assert_recon_photos_cleared

    inc = public_export.resolve_include_ids(
        db, task_titles=task_titles, generator_slugs=generator_slugs
    )
    gated = admissibility.non_admitted_output_ids(db)  # structural ∪ completeness ∪ semantic(gate)
    public_export.filter_include_for_posture(db, inc, posture, gated)
    if posture == "redistribute":
        public_export.check_licenses(db, inc.output_ids)  # fail-loud: nothing non-CC ships
    else:  # display
        assert_recon_photos_cleared(db, inc.output_ids)   # fail-loud: no uncleared reference photo
    all_out = inc.output_ids | inc.gold_output_ids
    tables = _filtered_rows(db, inc)
    # ... rest unchanged (manifest, dry_run, asset copy) ...
    manifest["posture"] = posture
    ...
```

(Keep the remaining body of `export_bundle` exactly as-is from line 130 onward; only add `manifest["posture"] = posture` into the manifest dict.)

Add `--posture` to `main()`'s argparser (`ap.add_argument("--posture", default="redistribute", choices=["display", "redistribute"])`) and pass `posture=a.posture` into `export_bundle`.

Modify `scripts/build_dataset_release.py` — its `export_bundle(...)` call passes `posture="redistribute"` (the bulk downloadable dataset is strict).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_export_postures.py tests/test_public_export.py tests/test_dataset_release.py -v`
Expected: PASS (posture filter correct; existing export/release tests still pass — default posture `redistribute` preserves prior strict behavior).

- [ ] **Step 5: Commit**

```bash
git add app/public_export.py scripts/export_public.py scripts/build_dataset_release.py tests/test_export_postures.py
git commit -m "feat(publish-safety): two-posture export (display vs redistribute) + admissibility exclusion"
```

---

### Task 5: Arena display — machine-generated label + no download affordance

**Files:**

- Modify: `app/main.py` (`_serialize_output` / the pair+kwise payloads carry `machine_generated` + `attribution`)
- Modify: `app/templates/arena.html` + `app/static/arena.js` (surface an "AI-generated" badge; confirm no download link is emitted)
- Test: `tests/test_display_labeling.py`

**Interfaces:**

- Consumes: `is_commercial_model` (T4).
- Produces: each display output payload includes `machine_generated: bool` (true for commercial-model recon) + `attribution: str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_display_labeling.py
import uuid
from app.main import _serialize_output
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def test_serialize_flags_machine_generated_and_carries_attribution():
    with SessionLocal() as db:
        g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
        db.add(g); db.flush()
        t = Task(title=f"t-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
        db.add(t); db.flush()
        o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb",
                        source="api:fal:trellis", attribution="Generated by TRELLIS")
        db.add(o); db.flush()
        d = _serialize_output(o)
        assert d["machine_generated"] is True
        assert d["attribution"] == "Generated by TRELLIS"
        assert "generator" not in d and "paradigm" not in d  # no identity leak (unchanged invariant)
        db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_display_labeling.py -v`
Expected: FAIL (`KeyError: 'machine_generated'`).

- [ ] **Step 3: Add the fields + badge**

In `app/main.py`, extend `_serialize_output(o)` to add the two fields (keep the existing anonymized `{output_id, url, format}` — do NOT add generator identity):

```python
def _serialize_output(o) -> dict:
    from .public_export import is_commercial_model

    return {
        "output_id": o.id,
        "url": storage.url_for(o.asset_path),
        "format": o.asset_format,
        "machine_generated": is_commercial_model(o.source),
        "attribution": o.attribution or None,
    }
```

(Grep the existing `_serialize`/pair payload; add the same two keys wherever a single output is serialized for display so the pairwise + kwise paths both carry them.)

In `app/templates/arena.html` / `app/static/arena.js`: when a shown output has `machine_generated`, render a small "AI-generated" badge on its card; surface `attribution` as a caption/tooltip. Confirm no `<a download>` / download button is emitted for any asset (grep; the arena is display-only).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_display_labeling.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/templates/arena.html app/static/arena.js tests/test_display_labeling.py
git commit -m "feat(publish-safety): AI-generated label + attribution on display outputs, no download affordance"
```

---

## Self-Review

**Spec coverage:** normalizer (T1) ✓; CC0 backfill + crops3d + Objaverse per-uid (T2) ✓; reference-photo enforcement + sourcing (T3) ✓; two-posture export + admissibility wiring + fail-loud gates (T4) ✓; machine-generated label + no-download + attribution carriage (T5) ✓. Out-of-scope legal items correctly excluded. Objaverse network call isolated behind an injected `objaverse_license_for` (testable offline). Reference-photo _content_ sourcing is best-effort with a fail-loud backstop (per approved decision).

**Placeholder scan:** `_serialize_output` grep-and-extend is a concrete instruction, not a TBD; the `export_bundle` body-preservation note names the exact line (130) and the one added key. No "add error handling"/"handle edge cases" placeholders.

**Type consistency:** `normalize_license(str|None)->str|None` used identically in T1/T2/T3/T4; `is_commercial_model`/`HARD_EXCLUDE_SOURCES` defined in T4 (public_export) and imported in T5; `filter_include_for_posture(db, inc, posture, gated)` signature matches its test and its export_bundle call; `assert_recon_photos_cleared(db, output_ids)` matches T3 def + T4 use; `CC0="CC0-1.0"` consistent. Note: T2's backfill also defines a local `HARD_EXCLUDE_SOURCES` (set) and T4 defines it in public_export (frozenset) — both hold the same three sources; T4's is the canonical one, T2 predates the import and keeps its own to stay a standalone script (acceptable duplication of a 3-element constant; flagged for the final review).
