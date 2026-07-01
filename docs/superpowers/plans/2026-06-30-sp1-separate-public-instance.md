# SP1 — Separate Public Instance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a self-contained, publishable Bio 3D Arena instance by adding a curated export/import promotion pipeline plus a scoring-disabled runtime mode — so the public deploy needs no Agrigen filesystem, no live scorer, and cannot leak unpublished work or the held-out test set.

**Architecture:** "Promote, don't recompute." An `export_public.py` script on the internal instance resolves a curated include-set (explicit task/generator allowlist × `active` × license allowlist × non-leaking gold), serializes those rows to JSON, copies their asset blobs + the already-baked GT reference GLBs, and writes a checksummed bundle. `import_public.py` loads a bundle into a fresh public DB + storage. The running server never scores (guarded by `SCORING_ENABLED`) and never reads `GT_BUNDLE_DIR` (already build-time-only).

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x (`Mapped`/`mapped_column`), pytest. Storage via `app/storage.py` (`LocalStorageBackend`/`S3StorageBackend`). No new runtime deps for the pipeline (stdlib `json`, `hashlib`, `tarfile`, `shutil`).

## Global Constraints

- **Fail loud, no silent inclusion:** any included `ModelOutput` with a null/unknown `license` aborts the export naming the id. No silent drop, no silent include. (Global CLAUDE.md: surface full context.)
- **Held-out GT stays internal:** the export ships only _baked_ GT reference GLBs (from `app/gt_render.py`), never raw `.npy` point clouds. The bundle must contain zero `.npy`.
- **No Agrigen path in the public artifact:** neither the emitted bundle nor the documented public config may contain the string `/home/mjarnold/agrigen`.
- **Never run pytest against a real DB:** the suite wipes tables. Tests use temp DBs only (`conftest.py` isolates; incident 2026-06-28). Never set `BIO3D_DATABASE_URL=study`.
- **Explicit promotion:** the schema has no `is_published`/visibility column. Promotion is an explicit allowlist of task titles + generator slugs. Nothing is public unless named.
- **v1 exported tables (arena + leaderboard + benchmark + integrity):** `category`, `criterion`, `generator`, `task`, `model_output`, `comparison`, `vote`, `rating`, `metric`, `gold_pair`, `recon_task`, `task_difficulty`. **Excluded (research-internal):** all Mode-C/judge/trait/calibration tables (`judge_vote`, `judge_rating`, `calibration_pair`, `plant_morphology`, `trait_*`, `organ_metric`, `critique`, `submission`, `voter_session`). Public vote pool + voter trust start clean.

---

### Task 1: Scoring-disabled runtime mode

**Files:**

- Modify: `app/config.py` (add `SCORING_ENABLED` after line 53)
- Modify: `app/recon_service.py:29-33` (`_default_scorer`)
- Modify: `app/structure_service.py:64-66` (`_default_scorer`)
- Test: `tests/test_scoring_disabled.py` (create)

**Interfaces:**

- Produces: `config.SCORING_ENABLED: bool` (True iff `RECON_SCORER_URL` is non-empty). When False, both `_default_scorer` functions raise `ScoringDisabled` (caught by the existing best-effort handlers → `status='error'`, never dials the network).
- Consumes: existing `score_and_store(db, output, *, scorer=_default_scorer)` best-effort try/except.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scoring_disabled.py
import pytest
from app import config, recon_service, structure_service


def test_default_scorer_raises_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "SCORING_ENABLED", False)
    with pytest.raises(recon_service.ScoringDisabled):
        recon_service._default_scorer(b"glb", "zea_mays")
    with pytest.raises(structure_service.ScoringDisabled):
        structure_service._default_scorer({"species_slug": "zea_mays"})


def test_default_scorer_enabled_flag_reads_url(monkeypatch):
    monkeypatch.setenv("BIO3D_RECON_SCORER_URL", "")
    import importlib
    importlib.reload(config)
    assert config.SCORING_ENABLED is False
    monkeypatch.setenv("BIO3D_RECON_SCORER_URL", "http://x:8800")
    importlib.reload(config)
    assert config.SCORING_ENABLED is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_scoring_disabled.py -v`
Expected: FAIL — `AttributeError: module 'app.recon_service' has no attribute 'ScoringDisabled'`

- [ ] **Step 3: Add the config flag**

In `app/config.py`, immediately after the `RECON_SCORER_URL = ...` line (currently line 53):

```python
# Public instances run with an empty scorer URL → scoring disabled (scores are promoted,
# never recomputed). Keeps the public deploy free of the Agrigen scoring microservice.
SCORING_ENABLED = bool(RECON_SCORER_URL.strip())
```

- [ ] **Step 4: Guard both default scorers**

In `app/recon_service.py`, add near the top (after imports) and edit `_default_scorer`:

```python
class ScoringDisabled(RuntimeError):
    """Raised by the default scorer when SCORING_ENABLED is False (public instance)."""


def _default_scorer(glb_bytes: bytes, species_slug: str | None) -> dict:
    if not config.SCORING_ENABLED:
        raise ScoringDisabled("scoring disabled on this instance (empty RECON_SCORER_URL)")
    return score_output(glb_bytes, str(species_slug), base_url=config.RECON_SCORER_URL)
```

In `app/structure_service.py`, mirror it:

```python
class ScoringDisabled(RuntimeError):
    """Raised by the default scorer when SCORING_ENABLED is False (public instance)."""


def _default_scorer(record: dict) -> dict:
    if not config.SCORING_ENABLED:
        raise ScoringDisabled("scoring disabled on this instance (empty RECON_SCORER_URL)")
    return score_structure(record, base_url=config.RECON_SCORER_URL)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_scoring_disabled.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/recon_service.py app/structure_service.py tests/test_scoring_disabled.py
git commit -m "feat(public): SCORING_ENABLED guard — public instance never dials a scorer"
```

---

### Task 2: Include-set resolution + license gate (pure module)

**Files:**

- Create: `app/public_export.py`
- Test: `tests/test_public_export.py`

**Interfaces:**

- Produces:
  - `REDISTRIBUTABLE_LICENSES: frozenset[str]` — normalized allowlist.
  - `resolve_include_ids(db, *, task_titles, generator_slugs) -> IncludeSet` where `IncludeSet` is a dataclass with `generator_ids: set[int]`, `task_ids: set[int]`, `output_ids: set[int]`, `gold_output_ids: set[int]`.
  - `check_licenses(db, output_ids) -> None` — raises `LicenseError(output_id, license)` on the first output whose `license` is null or not in the allowlist (self-authored `source == "bio3d-arena"` outputs and gold decoys are exempt — they are our own assets).
- Consumes: `app.models` (Task, Generator, ModelOutput, GoldPair), a `Session`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_public_export.py
import pytest
from app import public_export as pe
from app.models import Category, Generator, Task, ModelOutput, GoldPair


def _mk(db):
    cat = Category(slug="plant", name="Plant")
    g_ok = Generator(slug="lpy", name="L-Py", kind="model")
    g_hidden = Generator(slug="secret", name="Secret", kind="model")
    db.add_all([cat, g_ok, g_hidden]); db.flush()
    t_pub = Task(category_id=cat.id, title="maize-a", prompt="maize", active=True)
    t_off = Task(category_id=cat.id, title="maize-b", prompt="maize", active=False)
    db.add_all([t_pub, t_off]); db.flush()
    o_ok = ModelOutput(task_id=t_pub.id, generator_id=g_ok.id, asset_path="a.glb",
                       source="external", license="CC-BY-4.0")
    o_self = ModelOutput(task_id=t_pub.id, generator_id=g_ok.id, asset_path="b.glb",
                         source="bio3d-arena", license=None)
    o_bad = ModelOutput(task_id=t_pub.id, generator_id=g_ok.id, asset_path="c.glb",
                        source="external", license=None)
    db.add_all([o_ok, o_self, o_bad]); db.flush()
    return locals()


def test_resolve_respects_allowlist_and_active(db_session):
    e = _mk(db_session)
    inc = pe.resolve_include_ids(db_session, task_titles=["maize-a", "maize-b"],
                                 generator_slugs=["lpy"])
    assert e["t_pub"].id in inc.task_ids
    assert e["t_off"].id not in inc.task_ids          # inactive task excluded
    assert e["g_hidden"].id not in inc.generator_ids  # not in allowlist
    assert e["o_ok"].id in inc.output_ids


def test_check_licenses_fails_loud_on_unknown(db_session):
    e = _mk(db_session)
    inc = pe.resolve_include_ids(db_session, task_titles=["maize-a"], generator_slugs=["lpy"])
    with pytest.raises(pe.LicenseError) as ei:
        pe.check_licenses(db_session, inc.output_ids)
    assert ei.value.output_id == e["o_bad"].id        # external + null license aborts


def test_check_licenses_exempts_self_authored(db_session):
    e = _mk(db_session)
    pe.check_licenses(db_session, {e["o_self"].id})   # bio3d-arena source, no raise
```

(Assumes a `db_session` fixture giving a temp-DB `Session` with tables created. If absent, add to `conftest.py`: a fixture that builds `create_engine("sqlite://")`, `Base.metadata.create_all`, yields a `Session`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_public_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.public_export'`

- [ ] **Step 3: Implement `app/public_export.py`**

```python
"""Curated promotion boundary for the public instance (SP1).

Resolves the exact row-id sets that may be published, and enforces the license gate.
Pure DB reads; no filesystem, no serialization (that's scripts/export_public.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GoldPair, Generator, ModelOutput, Task

REDISTRIBUTABLE_LICENSES = frozenset({
    "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-3.0", "CC-BY-2.0",
    "PUBLIC-DOMAIN", "ODbL-1.0",
})


class LicenseError(RuntimeError):
    def __init__(self, output_id: int, license_: str | None):
        self.output_id = output_id
        self.license = license_
        super().__init__(f"output {output_id}: non-redistributable license {license_!r}")


@dataclass
class IncludeSet:
    generator_ids: set[int] = field(default_factory=set)
    task_ids: set[int] = field(default_factory=set)
    output_ids: set[int] = field(default_factory=set)
    gold_output_ids: set[int] = field(default_factory=set)


def resolve_include_ids(db: Session, *, task_titles: list[str],
                        generator_slugs: list[str]) -> IncludeSet:
    inc = IncludeSet()
    inc.generator_ids = {
        g.id for g in db.execute(
            select(Generator).where(Generator.slug.in_(generator_slugs))
        ).scalars()
    }
    inc.task_ids = {
        t.id for t in db.execute(
            select(Task).where(Task.title.in_(task_titles), Task.active.is_(True))
        ).scalars()
    }
    rows = db.execute(
        select(ModelOutput).where(
            ModelOutput.task_id.in_(inc.task_ids),
            ModelOutput.generator_id.in_(inc.generator_ids),
        )
    ).scalars().all()
    for o in rows:
        (inc.gold_output_ids if o.is_gold else inc.output_ids).add(o.id)
    # Gold decoys referenced by GoldPairs on included tasks travel too (integrity checks).
    for gp in db.execute(select(GoldPair)).scalars():
        for oid in (gp.good_output_id, gp.bad_output_id):
            o = db.get(ModelOutput, oid)
            if o and o.task_id in inc.task_ids:
                inc.gold_output_ids.add(oid)
    return inc


def check_licenses(db: Session, output_ids: set[int]) -> None:
    for oid in sorted(output_ids):
        o = db.get(ModelOutput, oid)
        if o is None:
            continue
        if o.source == "bio3d-arena":       # our own asset — exempt
            continue
        if o.license not in REDISTRIBUTABLE_LICENSES:
            raise LicenseError(oid, o.license)
```

(Confirmed against live schema: `GoldPair` columns are `good_output_id` + `bad_output_id`; `ModelOutput` cols `is_gold`/`source`/`license` per `app/models.py:92-116`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_public_export.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/public_export.py tests/test_public_export.py conftest.py
git commit -m "feat(public): include-set resolution + fail-loud license gate"
```

---

### Task 3: Export script — serialize rows + assets + baked GT + manifest

**Files:**

- Create: `scripts/export_public.py`
- Test: `tests/test_export_script.py`

**Interfaces:**

- Consumes: `app.public_export.resolve_include_ids`, `check_licenses`; `app.storage.get_storage`; `app.models`.
- Produces: `export_bundle(db, storage, *, task_titles, generator_slugs, out_dir) -> dict` writing `out_dir/{rows.json, assets/…, gt/…, manifest.json}` and returning the manifest dict (with `sha256` over `rows.json`). CLI: `python -m scripts.export_public --tasks a,b --generators lpy,icrisat --out public_bundle/v1 [--dry-run]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export_script.py
import json
from pathlib import Path
from scripts.export_public import export_bundle
from app.storage import LocalStorageBackend
# reuse the _mk seed from test_public_export
from tests.test_public_export import _mk


def test_export_writes_bundle_and_no_agrigen_leak(db_session, tmp_path):
    e = _mk(db_session)
    store = LocalStorageBackend(tmp_path / "src_assets")
    store.save("a.glb", b"GLBDATA-a")
    store.save("b.glb", b"GLBDATA-b")
    out = tmp_path / "bundle"
    manifest = export_bundle(db_session, store, task_titles=["maize-a"],
                             generator_slugs=["lpy"], out_dir=out)
    rows = json.loads((out / "rows.json").read_text())
    assert "model_output" in rows and len(rows["model_output"]) >= 1
    assert (out / "assets" / "a.glb").read_bytes() == b"GLBDATA-a"
    # Leak assertions (Global Constraints):
    blob = (out / "rows.json").read_text() + json.dumps(manifest)
    assert "/home/mjarnold/agrigen" not in blob
    assert not list(out.rglob("*.npy"))
    assert manifest["sha256"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_export_script.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.export_public'`

- [ ] **Step 3: Implement `scripts/export_public.py`**

```python
"""Export a curated public bundle from the internal instance (SP1).

Emits out_dir/{rows.json, assets/<path>, gt/<species>.glb, manifest.json}. The single
leak chokepoint: license-gated, allowlist-only, baked-GT-only (no raw .npy), fail-loud.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.inspection import inspect as sqla_inspect  # noqa: E402

from app import config, public_export  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.storage import StorageBackend, get_storage  # noqa: E402
from app.models import (  # noqa: E402
    Category, Criterion, Generator, Task, ModelOutput, Comparison, Vote,
    Rating, Metric, GoldPair, ReconTask, TaskDifficulty,
)

# Serialization order = FK-safe insert order for import.
EXPORT_MODELS = [
    Category, Criterion, Generator, Task, ModelOutput, Comparison, Vote,
    Rating, Metric, GoldPair, ReconTask, TaskDifficulty,
]


def _row_to_dict(obj) -> dict:
    cols = sqla_inspect(obj).mapper.column_attrs
    out = {}
    for c in cols:
        v = getattr(obj, c.key)
        out[c.key] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


def _filtered_rows(db, inc: public_export.IncludeSet) -> dict[str, list[dict]]:
    all_out = inc.output_ids | inc.gold_output_ids
    tables: dict[str, list[dict]] = {}
    for model in EXPORT_MODELS:
        name = model.__tablename__
        q = select(model)
        rows = [r for r in db.execute(q).scalars()]
        keep = []
        for r in rows:
            d = _row_to_dict(r)
            if name == "task" and r.id not in inc.task_ids:
                continue
            if name == "generator" and r.id not in inc.generator_ids:
                continue
            if name == "model_output" and r.id not in all_out:
                continue
            if name in ("comparison", "recon_task", "task_difficulty") and \
                    getattr(r, "task_id", None) not in inc.task_ids:
                continue
            if name == "metric" and getattr(r, "output_id", None) not in all_out:
                continue
            if name == "rating" and getattr(r, "generator_id", None) not in inc.generator_ids:
                continue
            keep.append(d)
        tables[name] = keep
    return tables


def export_bundle(db, storage: StorageBackend, *, task_titles, generator_slugs,
                  out_dir, dry_run: bool = False) -> dict:
    inc = public_export.resolve_include_ids(
        db, task_titles=task_titles, generator_slugs=generator_slugs)
    public_export.check_licenses(db, inc.output_ids)  # fail-loud before writing anything
    all_out = inc.output_ids | inc.gold_output_ids
    tables = _filtered_rows(db, inc)

    licenses: dict[str, int] = {}
    for d in tables["model_output"]:
        licenses[str(d.get("license"))] = licenses.get(str(d.get("license")), 0) + 1

    manifest = {
        "version": 1,
        "counts": {k: len(v) for k, v in tables.items()},
        "licenses": licenses,
        "n_outputs": len(all_out),
    }
    if dry_run:
        manifest["dry_run"] = True
        return manifest

    out = Path(out_dir)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "gt").mkdir(parents=True, exist_ok=True)
    rows_bytes = json.dumps(tables, indent=0, sort_keys=True).encode()
    (out / "rows.json").write_bytes(rows_bytes)
    manifest["sha256"] = hashlib.sha256(rows_bytes).hexdigest()

    # Asset blobs for every included output.
    for d in tables["model_output"]:
        rel = d["asset_path"]
        (out / "assets" / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / "assets" / rel).write_bytes(storage.read(rel))

    # Baked GT reference GLBs only (never raw .npy). Copy whatever exists under gt/.
    for d in tables["recon_task"]:
        slug = d.get("species_slug")
        rel = f"{config.GT_ASSET_SUBDIR}/{slug}.glb"
        if slug and storage.exists(rel):
            (out / "gt" / f"{slug}.glb").write_bytes(storage.read(rel))

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True, help="comma-separated task titles")
    ap.add_argument("--generators", required=True, help="comma-separated generator slugs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        m = export_bundle(
            db, get_storage(),
            task_titles=a.tasks.split(","), generator_slugs=a.generators.split(","),
            out_dir=a.out, dry_run=a.dry_run,
        )
    finally:
        db.close()
    print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_export_script.py -v`
Expected: PASS. If a filter references a column that differs from the schema (e.g. `recon_task.task_id`), fix the attribute name per `grep -n "task_id\|species_slug" app/models.py` and re-run.

- [ ] **Step 5: Commit**

```bash
git add scripts/export_public.py tests/test_export_script.py
git commit -m "feat(public): export_public.py — curated, license-gated, no-.npy bundle"
```

---

### Task 4: Import script + real-execution round-trip leak test

**Files:**

- Create: `scripts/import_public.py`
- Test: `tests/test_import_roundtrip.py`

**Interfaces:**

- Consumes: the bundle written by Task 3; `app.database` (engine/Base), `app.storage`.
- Produces: `import_bundle(bundle_dir, *, database_url, storage) -> dict` — verifies `sha256(rows.json)` against manifest (raises `BundleChecksumError` on mismatch), `create_all` on the target engine, inserts rows in `EXPORT_MODELS` order, copies `assets/*` and `gt/*` into `storage`. Returns counts.

- [ ] **Step 1: Write the failing round-trip test**

```python
# tests/test_import_roundtrip.py
from pathlib import Path
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.database import Base
from app.models import ModelOutput, Task
from app.storage import LocalStorageBackend
from scripts.export_public import export_bundle
from scripts.import_public import import_bundle, BundleChecksumError
from tests.test_public_export import _mk
import pytest


def test_roundtrip_matches_and_no_leak(db_session, tmp_path):
    _mk(db_session)
    src = LocalStorageBackend(tmp_path / "src"); src.save("a.glb", b"A"); src.save("b.glb", b"B")
    out = tmp_path / "bundle"
    export_bundle(db_session, src, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out)

    dst_url = f"sqlite:///{tmp_path/'public.db'}"
    dst_store = LocalStorageBackend(tmp_path / "dst")
    counts = import_bundle(out, database_url=dst_url, storage=dst_store)

    eng = create_engine(dst_url)
    with Session(eng) as s:
        assert s.execute(select(Task)).scalars().all()          # tasks landed
        outs = s.execute(select(ModelOutput)).scalars().all()
        assert outs and all(dst_store.exists(o.asset_path) for o in outs)
    # leak grep over the whole bundle tree
    for p in out.rglob("*"):
        if p.is_file():
            assert "/home/mjarnold/agrigen" not in p.read_bytes().decode("utf-8", "ignore")
    assert not list(out.rglob("*.npy"))


def test_import_rejects_tampered_bundle(db_session, tmp_path):
    _mk(db_session)
    src = LocalStorageBackend(tmp_path / "src"); src.save("a.glb", b"A"); src.save("b.glb", b"B")
    out = tmp_path / "bundle"
    export_bundle(db_session, src, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out)
    (out / "rows.json").write_bytes(b'{"tampered": []}')
    with pytest.raises(BundleChecksumError):
        import_bundle(out, database_url=f"sqlite:///{tmp_path/'p.db'}",
                      storage=LocalStorageBackend(tmp_path / "dst"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_import_roundtrip.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.import_public'`

- [ ] **Step 3: Implement `scripts/import_public.py`**

```python
"""Load a curated public bundle into a fresh public DB + storage (SP1)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import Base, engine_kwargs  # noqa: E402
from app.storage import StorageBackend, get_storage  # noqa: E402
from scripts.export_public import EXPORT_MODELS  # noqa: E402

_BY_TABLE = {m.__tablename__: m for m in EXPORT_MODELS}


class BundleChecksumError(RuntimeError):
    pass


def import_bundle(bundle_dir, *, database_url: str, storage: StorageBackend) -> dict:
    b = Path(bundle_dir)
    manifest = json.loads((b / "manifest.json").read_text())
    rows_bytes = (b / "rows.json").read_bytes()
    if hashlib.sha256(rows_bytes).hexdigest() != manifest.get("sha256"):
        raise BundleChecksumError(f"rows.json checksum != manifest for {b}")
    tables = json.loads(rows_bytes)

    eng = create_engine(database_url, future=True, **engine_kwargs(database_url))
    Base.metadata.create_all(eng)
    counts = {}
    with Session(eng) as s:
        for model in EXPORT_MODELS:                      # FK-safe order
            name = model.__tablename__
            for d in tables.get(name, []):
                s.merge(model(**d))                      # merge = idempotent by PK
            counts[name] = len(tables.get(name, []))
        s.commit()

    for sub in ("assets", "gt"):
        base = b / sub
        for p in base.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(b / "assets")) if sub == "assets" \
                    else f"gt/{p.name}"
                storage.save(rel, p.read_bytes())
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    a = ap.parse_args()
    counts = import_bundle(a.bundle, database_url=__import__("app.config",
                           fromlist=["DATABASE_URL"]).DATABASE_URL, storage=get_storage())
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_import_roundtrip.py -v`
Expected: PASS (2 passed). Datetime columns arrive as ISO strings; if SQLAlchemy rejects a string for a `DateTime`, add a coercion in `import_bundle` that parses ISO strings back to `datetime` for known datetime columns before `merge`.

- [ ] **Step 5: Commit**

```bash
git add scripts/import_public.py tests/test_import_roundtrip.py
git commit -m "feat(public): import_public.py + real-execution round-trip leak test"
```

---

### Task 5: Real captcha verification (Turnstile/hCaptcha)

**Files:**

- Modify: `app/config.py` (add `CAPTCHA_PROVIDER`, `CAPTCHA_SECRET`)
- Modify: `app/integrity.py:80` (`verify_captcha`)
- Test: `tests/test_captcha.py`

**Interfaces:**

- Consumes: `config.REQUIRE_CAPTCHA`, `config.CAPTCHA_SECRET`, `config.CAPTCHA_PROVIDER`.
- Produces: `verify_captcha(token) -> bool` — no-op `True` when `REQUIRE_CAPTCHA` is False (unchanged); otherwise POSTs the token to the provider's siteverify endpoint via an injectable `_post` for testability.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_captcha.py
from app import config, integrity


def test_captcha_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", False)
    assert integrity.verify_captcha(None) is True


def test_captcha_calls_provider_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", True)
    monkeypatch.setattr(config, "CAPTCHA_SECRET", "sek")
    monkeypatch.setattr(config, "CAPTCHA_PROVIDER", "turnstile")
    calls = {}
    def fake_post(url, data):
        calls["url"] = url; calls["data"] = data
        return {"success": True}
    assert integrity.verify_captcha("tok", _post=fake_post) is True
    assert "challenges.cloudflare.com" in calls["url"]
    assert calls["data"]["response"] == "tok"


def test_captcha_rejects_missing_token_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", True)
    monkeypatch.setattr(config, "CAPTCHA_SECRET", "sek")
    assert integrity.verify_captcha(None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_captcha.py -v`
Expected: FAIL — `verify_captcha() got an unexpected keyword argument '_post'` / provider constants missing.

- [ ] **Step 3: Implement**

In `app/config.py` after the `REQUIRE_CAPTCHA` line:

```python
CAPTCHA_PROVIDER = os.environ.get("BIO3D_CAPTCHA_PROVIDER", "turnstile").lower()  # turnstile|hcaptcha
CAPTCHA_SECRET = os.environ.get("BIO3D_CAPTCHA_SECRET", "")
```

Replace `verify_captcha` in `app/integrity.py`:

```python
import json as _json
import urllib.parse as _urlparse
import urllib.request as _urlreq

_SITEVERIFY = {
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "hcaptcha": "https://api.hcaptcha.com/siteverify",
}


def _post_form(url: str, data: dict) -> dict:
    body = _urlparse.urlencode(data).encode()
    with _urlreq.urlopen(_urlreq.Request(url, data=body), timeout=10) as r:
        return _json.loads(r.read().decode())


def verify_captcha(token: str | None, *, _post=_post_form) -> bool:
    """No-op True unless REQUIRE_CAPTCHA; else validate against the provider."""
    if not config.REQUIRE_CAPTCHA:
        return True
    if not token:
        return False
    url = _SITEVERIFY.get(config.CAPTCHA_PROVIDER, _SITEVERIFY["turnstile"])
    try:
        res = _post(url, {"secret": config.CAPTCHA_SECRET, "response": token})
    except Exception:
        return False
    return bool(res.get("success"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_captcha.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/integrity.py tests/test_captcha.py
git commit -m "feat(public): real Turnstile/hCaptcha siteverify in verify_captcha"
```

---

### Task 6: Legal pages — terms, privacy, licenses

**Files:**

- Create: `app/templates/terms.html`, `app/templates/privacy.html`, `app/templates/licenses.html`
- Modify: `app/main.py` (add 3 GET routes near the other page routes)
- Test: `tests/test_legal_pages.py`

**Interfaces:**

- Produces: `GET /terms`, `GET /privacy`, `GET /licenses` → 200 HTML. `/licenses` lists distinct `(license, attribution, source)` from `ModelOutput` for the current instance.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_legal_pages.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_legal_pages_serve():
    for path in ("/terms", "/privacy", "/licenses"):
        r = client.get(path)
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_legal_pages.py -v`
Expected: FAIL — 404 for `/terms`.

- [ ] **Step 3: Create templates + routes**

Minimal templates (match the existing Jinja base — check `app/templates/` for the base template name, e.g. `{% extends "base.html" %}`). Example `app/templates/terms.html`:

```html
{% extends "base.html" %} {% block content %}
<h1>Terms of Use</h1>
<p>
  Bio 3D Arena is a research platform for comparing biological 3D generations.
  Votes are anonymous and used for aggregate rankings and research. Do not
  submit content you do not have the right to share. Provided "as is", without
  warranty.
</p>
{% endblock %}
```

`privacy.html` (same structure): state that only an anonymous session id, votes, and coarse rate-limit metadata are stored; no accounts in v1; no third-party sale.

`licenses.html`:

```html
{% extends "base.html" %} {% block content %}
<h1>Asset Licenses & Attribution</h1>
<ul>
  {% for row in licenses %}
  <li>
    {{ row.license or "—" }} · {{ row.attribution or "—" }} · {{ row.source }}
  </li>
  {% endfor %}
</ul>
{% endblock %}
```

In `app/main.py`, near the existing page routes (e.g. the `/methodology` route — grep `@app.get("/methodology"`), add:

```python
# NB: match the codebase's TemplateResponse form — main.py uses (request, "name.html"[, ctx])
# (e.g. main.py:266 `templates.TemplateResponse(request, "arena.html")`), not the legacy
# ("name.html", {"request": request}) form.
@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return templates.TemplateResponse(request, "terms.html")


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html")


@app.get("/licenses", response_class=HTMLResponse)
def licenses(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(
        select(ModelOutput.license, ModelOutput.attribution, ModelOutput.source).distinct()
    ).all()
    return templates.TemplateResponse(request, "licenses.html", {"licenses": rows})
```

(Confirm the actual `templates` object + `Request`/`HTMLResponse`/`select`/`ModelOutput` imports already exist in `main.py` — they do for the other page routes; reuse them.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_legal_pages.py -v`
Expected: PASS. Add footer links to the 3 pages in `base.html` if the base has a footer.

- [ ] **Step 5: Commit**

```bash
git add app/templates/terms.html app/templates/privacy.html app/templates/licenses.html app/main.py tests/test_legal_pages.py
git commit -m "feat(public): terms/privacy/licenses pages (licenses from ModelOutput provenance)"
```

---

### Task 7: Deploy config + public-instance docs

**Files:**

- Create: `deploy/.env.public.example`, `deploy/README.md`
- Modify: `Dockerfile` (only if it hardcodes a dev command; else leave)
- Test: `tests/test_public_env_no_agrigen.py`

**Interfaces:**

- Produces: a documented public env (`.env.public.example`) that sets `BIO3D_RECON_SCORER_URL=` (empty → scoring disabled), `BIO3D_STORAGE_BACKEND=s3`, `BIO3D_DATABASE_URL=postgresql+psycopg://…`, `BIO3D_REQUIRE_CAPTCHA=true`, a rotated `BIO3D_ADMIN_TOKEN`, and **no `BIO3D_GT_BUNDLE_DIR`**.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_public_env_no_agrigen.py
from pathlib import Path


def test_public_env_has_no_agrigen_and_disables_scoring():
    env = Path("deploy/.env.public.example").read_text()
    assert "/home/mjarnold/agrigen" not in env
    assert "BIO3D_GT_BUNDLE_DIR" not in env
    assert "BIO3D_RECON_SCORER_URL=" in env
    # scorer URL must be empty on the public instance
    line = next(l for l in env.splitlines() if l.startswith("BIO3D_RECON_SCORER_URL="))
    assert line.strip() == "BIO3D_RECON_SCORER_URL="
    assert "changeme-admin-token" not in env
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_public_env_no_agrigen.py -v`
Expected: FAIL — file missing.

- [ ] **Step 3: Create `deploy/.env.public.example`**

```bash
# Public Bio 3D Arena — env (fill secrets from the host's secret store; never commit real values)
BIO3D_DATABASE_URL=postgresql+psycopg://USER:PASS@HOST/DBNAME
BIO3D_STORAGE_BACKEND=s3
BIO3D_S3_BUCKET=bio3d-public
BIO3D_S3_PUBLIC_BASE_URL=https://assets.example.org
BIO3D_ADMIN_TOKEN=SET_A_LONG_RANDOM_SECRET
BIO3D_REQUIRE_CAPTCHA=true
BIO3D_CAPTCHA_PROVIDER=turnstile
BIO3D_CAPTCHA_SECRET=SET_FROM_CLOUDFLARE
# Scoring OFF on the public instance — scores are promoted, never recomputed:
BIO3D_RECON_SCORER_URL=
# (BIO3D_GT_BUNDLE_DIR intentionally unset — public serves pre-baked GT GLBs from assets.)
```

- [ ] **Step 4: Write `deploy/README.md`**

Document the promote→deploy runbook: (1) on internal, `python -m scripts.export_public --tasks … --generators … --out public_bundle/vN` (review the printed manifest — license breakdown, counts); (2) transfer `public_bundle/vN` to the public host; (3) `python -m scripts.import_public --bundle public_bundle/vN` with the public env loaded; (4) boot `uvicorn app.main:app` on Fly/Render with `deploy/.env.public.example` values; (5) smoke `/`, `/leaderboard`, `/benchmark`, `/coverage`, `/terms`, `/licenses`. Note free-tier targets: Fly.io/Render (app), Neon/Supabase (Postgres), Cloudflare R2 (assets).

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_public_env_no_agrigen.py -v`
Expected: PASS

- [ ] **Step 6: Full suite + commit**

Run: `.venv/bin/pytest -q`
Expected: PASS (all SP1 tests green; pre-existing suite unaffected).

```bash
git add deploy/.env.public.example deploy/README.md tests/test_public_env_no_agrigen.py
git commit -m "docs(public): deploy env + promote→deploy runbook (scoring off, no GT bundle)"
```

---

## Self-Review

**Spec coverage:**

- Sever Agrigen GT coupling → Tasks 1 (scoring off) + 3 (baked-GT-only export) + 7 (no `GT_BUNDLE_DIR`). ✓
- Promote-don't-recompute (no scorer, promoted scores) → Task 1 + Metric export in Task 3 + import in Task 4. ✓
- Export leak chokepoint (license fail-loud, allowlist, no `.npy`) → Tasks 2 + 3. ✓
- Import + checksum verify → Task 4. ✓
- Real-execution round-trip + leak grep → Task 4. ✓
- Captcha real impl → Task 5. ✓ Secret rotation → Task 7 env. ✓
- Legal pages → Task 6. ✓ Cheap deploy stack → Task 7. ✓
- Open decisions (GT private / clean vote pool / Fly-vs-Render) → honored: no raw GT exported, `voter_session` excluded (clean pool), host documented not hardcoded. ✓

**Placeholder scan:** no TBD/TODO; every code step shows code. Two flagged verification points (GoldPair field names in Task 2; datetime-string coercion in Task 4) are explicit "confirm/adjust" instructions with the exact grep, not placeholders.

**Type consistency:** `IncludeSet` fields (`generator_ids`/`task_ids`/`output_ids`/`gold_output_ids`) used identically in Tasks 2–3; `EXPORT_MODELS` defined in Task 3 and imported in Task 4; `export_bundle`/`import_bundle` signatures match their call sites in tests.

**Schema confirmed against live source:** `GoldPair.good_output_id`/`bad_output_id`, `ReconTask.task_id`/`species_slug`, `ModelOutput.is_gold`/`source`/`license`/`asset_path`, `base.html` present, `TemplateResponse(request, name[, ctx])` form. **Remaining adjust-on-contact (not blockers):** datetime ISO-string round-trip on import (Task 4 Step 4 note); `base.html` block name (`{% block content %}` assumed — confirm).
