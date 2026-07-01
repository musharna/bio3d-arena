# Dataset Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the arena's biological-3D benchmark as a citable, versioned, licensed, downloadable release by decorating SP1's export bundle with LICENSE + DATASHEET + VERSION + preference records, served from a `/dataset` page.

**Architecture:** A thin composition layer over SP1's `scripts/export_public.py`. A pure helper module (`app/dataset.py`) builds the release's text/JSON artifacts; a script (`scripts/build_dataset_release.py`) runs the SP1 export then writes the decorations + tarball; a `/dataset` route serves it. No new export pipeline; reuses SP1's fail-loud license gate and leak boundary.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x, pytest. Stdlib `json`, `tarfile`, `pathlib`. Depends on SP1 (`scripts/export_public.py`, `app/public_export.py`) — this plan stacks on the SP1 branch.

## Global Constraints

- **Reuse, don't rebuild:** the benchmark bundle comes from SP1's `export_bundle(db, storage, *, task_titles, generator_slugs, out_dir, dry_run=False) -> dict` (returns a manifest dict with key `"sha256"`). Do not re-implement export/filtering/licensing.
- **Fail loud on license:** an included output lacking a redistributable license aborts the release (inherited from SP1's `check_licenses`, called inside `export_bundle`). No silent inclusion.
- **Held-out GT stays private:** the release ships only baked GT reference GLBs (SP1's `gt/`), never raw `.npy`. The build asserts zero `.npy` and no `/home/mjarnold/agrigen` in the release tree.
- **Version is passed in, never generated:** scripts cannot call time/random. `--version` is a required CLI arg; content hash comes from the bundle manifest's `sha256`.
- **Task allowlist keys on `Task.title`; generator allowlist on `Generator.slug`** (SP1 convention; `Task` has no slug).
- **Never run pytest against a real DB** (temp only; conftest isolates). Tests reuse SP1's `db_session` fixture + the `_mk` helper in `tests/test_public_export.py`.
- **Release lands under `data/releases/<version>/`** (gitignored, like other data); the tarball is the artifact, never committed.

---

### Task 1: Release content helpers (`app/dataset.py`) + DRY the export route

**Files:**

- Create: `app/dataset.py`
- Modify: `app/main.py` (the `/api/export.json` route, currently at `export_dataset`, ~line 854) to call the shared helper
- Test: `tests/test_dataset_helpers.py`

**Interfaces:**

- Produces:
  - `build_preference_records(db) -> dict` — `{"n_votes": int, "votes": [ {comparison_id, task, category, criterion, generator_a, generator_b, asset_a, asset_b, winner, session, voted_at}, … ]}` (exactly today's `/api/export.json` payload).
  - `license_rollup(output_rows: list[dict]) -> list[dict]` — distinct `{"license","attribution","source"}` tuples over the bundle's `model_output` row dicts, sorted, `None`→`""`.
  - `render_license(rollup: list[dict]) -> str` and `render_datasheet(version: str, manifest: dict, rollup: list[dict]) -> str` — the LICENSE and DATASHEET.md text.
- Consumes: `app.models` (Vote, Comparison, ModelOutput, Task, Criterion, Generator).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dataset_helpers.py
from app import dataset
from app.models import Category, Generator, Task, ModelOutput, Comparison, Vote, Criterion
from tests.test_public_export import _mk  # reuse SP1 seed helper


def test_build_preference_records_shape(db_session):
    e = _mk(db_session)
    crit = Criterion(slug="overall", name="Overall"); db_session.add(crit); db_session.flush()
    comp = Comparison(task_id=e["t_pub"].id, output_a_id=e["o_ok"].id, output_b_id=e["o_self"].id,
                      criterion_id=crit.id, session_id="s1")
    db_session.add(comp); db_session.flush()
    db_session.add(Vote(comparison_id=comp.id, winner="a", session_id="s1")); db_session.flush()
    rec = dataset.build_preference_records(db_session)
    assert rec["n_votes"] == 1
    v = rec["votes"][0]
    assert v["winner"] == "a" and v["generator_a"] == "lpy" and v["task"] == "maize-a"


def test_license_rollup_dedupes_and_nullsafe():
    rows = [
        {"license": "CC-BY-4.0", "attribution": "A", "source": "external"},
        {"license": "CC-BY-4.0", "attribution": "A", "source": "external"},
        {"license": None, "attribution": None, "source": "bio3d-arena"},
    ]
    roll = dataset.license_rollup(rows)
    assert {"license": "CC-BY-4.0", "attribution": "A", "source": "external"} in roll
    assert {"license": "", "attribution": "", "source": "bio3d-arena"} in roll
    assert len(roll) == 2


def test_render_license_and_datasheet_include_key_facts():
    roll = [{"license": "CC-BY-4.0", "attribution": "A", "source": "external"}]
    manifest = {"sha256": "abc123", "counts": {"model_output": 5, "task": 2}, "n_outputs": 5}
    lic = dataset.render_license(roll)
    ds = dataset.render_datasheet("2026.07-v1", manifest, roll)
    assert "CC-BY-4.0" in lic and "A" in lic
    assert "2026.07-v1" in ds and "abc123" in ds
    assert "held-out" in ds.lower() and "npy" not in ds.lower()  # GT-private note, no raw GT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dataset_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.dataset'`

- [ ] **Step 3: Implement `app/dataset.py`**

```python
"""Content helpers for a dataset release (SP3-thin).

Pure builders for the release's preference records + LICENSE + DATASHEET text. No filesystem,
no tarball (that's scripts/build_dataset_release.py). The benchmark bundle itself comes from
scripts/export_public.py.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Comparison, Criterion, Generator, ModelOutput, Task, Vote


def build_preference_records(db: Session) -> dict:
    """Every decided comparison with full provenance — the /api/export.json payload."""
    rows = db.execute(
        select(Vote, Comparison).join(Comparison, Vote.comparison_id == Comparison.id)
    ).all()
    records = []
    for vote, comp in rows:
        out_a = db.get(ModelOutput, comp.output_a_id)
        out_b = db.get(ModelOutput, comp.output_b_id)
        task = db.get(Task, comp.task_id)
        crit = db.get(Criterion, comp.criterion_id)
        records.append(
            {
                "comparison_id": comp.id,
                "task": task.title,
                "category": task.category.slug,
                "criterion": crit.slug,
                "generator_a": db.get(Generator, out_a.generator_id).slug,
                "generator_b": db.get(Generator, out_b.generator_id).slug,
                "asset_a": out_a.asset_path,
                "asset_b": out_b.asset_path,
                "winner": vote.winner,
                "session": vote.session_id,
                "voted_at": vote.created.isoformat(),
            }
        )
    return {"n_votes": len(records), "votes": records}


def license_rollup(output_rows: list[dict]) -> list[dict]:
    """Distinct (license, attribution, source) over a bundle's model_output row dicts."""
    seen = set()
    out = []
    for r in output_rows:
        key = (r.get("license") or "", r.get("attribution") or "", r.get("source") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append({"license": key[0], "attribution": key[1], "source": key[2]})
    out.sort(key=lambda d: (d["license"], d["attribution"], d["source"]))
    return out


def render_license(rollup: list[dict]) -> str:
    lines = [
        "Bio 3D Arena — Benchmark Dataset License",
        "",
        "Each 3D asset retains its original license and attribution, listed below. Assets",
        "authored by Bio 3D Arena (source=bio3d-arena) are released CC-BY-4.0. Redistribution",
        "of any asset is bound by its stated license.",
        "",
        "Per-asset provenance (license | attribution | source):",
    ]
    for r in rollup:
        lines.append(f"- {r['license'] or '(bio3d-arena CC-BY-4.0)'} | {r['attribution'] or '-'} | {r['source']}")
    return "\n".join(lines) + "\n"


def render_datasheet(version: str, manifest: dict, rollup: list[dict]) -> str:
    counts = manifest.get("counts", {})
    return "\n".join(
        [
            f"# Bio 3D Arena Benchmark — Datasheet ({version})",
            "",
            f"Content hash (bundle rows.json sha256): `{manifest.get('sha256', '')}`",
            "",
            "## Contents",
            f"- Tasks: {counts.get('task', 0)}",
            f"- Generators: {counts.get('generator', 0)}",
            f"- 3D outputs: {counts.get('model_output', 0)}",
            f"- Objective metrics (chamfer/F-score): {counts.get('metric', 0)}",
            "- `preference_records.json`: human pairwise votes (secondary; volume is still small).",
            "",
            "## How it was built",
            "Biological 3D generations across taxa, evaluated by held-out-scan objective metrics",
            "and human + calibrated-VLM pairwise votes. Chamfer/F-score are REFERENCE signals, not",
            "the sole ranking (morphological completeness matters — geometry alone can mislead).",
            "",
            "## Held-out ground truth",
            "The raw held-out GT point clouds are WITHELD to preserve benchmark integrity. The",
            "release ships baked GT *reference render* GLBs only — no raw scan data.",
            "",
            "## Known limitations",
            "- Human vote volume is low; many generators are provisional (see the live /coverage).",
            "- Coverage is uneven across taxa. Metrics are front-view-biased for some tasks.",
            "",
            "## License",
            f"See LICENSE. {len(rollup)} distinct license/attribution tuples across the assets.",
            "",
        ]
    )
```

- [ ] **Step 4: DRY the route** — in `app/main.py`, replace the body of the `/api/export.json` route (`export_dataset`) so it delegates:

```python
@app.get("/api/export.json")
def export_dataset(db: Session = Depends(get_db)):
    """Reproducible research export: every decided comparison with full provenance."""
    from . import dataset as dataset_mod

    return dataset_mod.build_preference_records(db)
```

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_dataset_helpers.py -v` → PASS (3 passed)
Run: `.venv/bin/pytest tests/test_export_api.py -q 2>/dev/null || .venv/bin/pytest -q -k export` → confirm the `/api/export.json` route still behaves (find its existing test with `grep -rn "export.json" tests/`; if one exists it must still pass).

- [ ] **Step 6: Commit**

```bash
git add app/dataset.py app/main.py tests/test_dataset_helpers.py
git commit -m "feat(dataset): release content helpers + DRY /api/export.json route"
```

---

### Task 2: Release orchestrator (`scripts/build_dataset_release.py`)

**Files:**

- Create: `scripts/build_dataset_release.py`
- Test: `tests/test_build_dataset_release.py`

**Interfaces:**

- Consumes: `scripts.export_public.export_bundle`; `app.dataset` (`build_preference_records`, `license_rollup`, `render_license`, `render_datasheet`); `app.storage.get_storage`.
- Produces: `build_release(db, storage, *, version, task_titles, generator_slugs, out_dir) -> dict` — writes `out_dir/{bundle/…, LICENSE, DATASHEET.md, VERSION, preference_records.json}`, asserts no `.npy` / no agrigen path, tars to `out_dir.parent/<version>.tar.gz`, returns a summary `{"version", "sha256", "n_outputs", "tarball"}`. CLI: `python -m scripts.build_dataset_release --version 2026.07-v1 --tasks "a,b" --generators "lpy,icrisat" --out data/releases/2026.07-v1`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_dataset_release.py
import json
from pathlib import Path
from app.storage import LocalStorageBackend
from scripts.build_dataset_release import build_release
from tests.test_public_export import _mk


def test_build_release_decorates_bundle_and_no_leak(db_session, tmp_path):
    _mk(db_session)
    store = LocalStorageBackend(tmp_path / "src"); store.save("a.glb", b"A"); store.save("b.glb", b"B")
    out = tmp_path / "releases" / "2026.07-v1"
    summary = build_release(db_session, store, version="2026.07-v1",
                            task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out)
    assert (out / "LICENSE").exists()
    assert "2026.07-v1" in (out / "VERSION").read_text()
    assert summary["sha256"] in (out / "VERSION").read_text()
    assert (out / "DATASHEET.md").exists()
    assert json.loads((out / "preference_records.json").read_text())["n_votes"] == 0
    assert (out / "bundle" / "rows.json").exists()
    # leak assertions over the whole release tree
    assert not list(out.rglob("*.npy"))
    for p in out.rglob("*"):
        if p.is_file():
            assert "/home/mjarnold/agrigen" not in p.read_bytes().decode("utf-8", "ignore")
    assert summary["tarball"].endswith(".tar.gz") and Path(summary["tarball"]).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_build_dataset_release.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.build_dataset_release'`

- [ ] **Step 3: Implement `scripts/build_dataset_release.py`**

```python
"""Build a citable dataset release: SP1 export bundle + LICENSE + DATASHEET + VERSION + votes."""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import dataset  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.storage import StorageBackend, get_storage  # noqa: E402
from scripts.export_public import export_bundle  # noqa: E402


def _assert_no_leak(root: Path) -> None:
    if list(root.rglob("*.npy")):
        raise RuntimeError(f"release {root} contains raw .npy GT — refusing to publish")
    for p in root.rglob("*"):
        if p.is_file() and "/home/mjarnold/agrigen" in p.read_bytes().decode("utf-8", "ignore"):
            raise RuntimeError(f"release {root} leaks an agrigen path in {p}")


def build_release(db, storage: StorageBackend, *, version, task_titles, generator_slugs,
                  out_dir) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = export_bundle(
        db, storage, task_titles=task_titles, generator_slugs=generator_slugs,
        out_dir=out / "bundle",
    )
    rows = json.loads((out / "bundle" / "rows.json").read_text())
    rollup = dataset.license_rollup(rows.get("model_output", []))

    (out / "LICENSE").write_text(dataset.render_license(rollup))
    (out / "DATASHEET.md").write_text(dataset.render_datasheet(version, manifest, rollup))
    (out / "VERSION").write_text(f"{version}\nsha256:{manifest.get('sha256', '')}\n")
    (out / "preference_records.json").write_text(json.dumps(dataset.build_preference_records(db)))

    _assert_no_leak(out)

    tarball = out.parent / f"{version}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(out, arcname=version)
    return {
        "version": version,
        "sha256": manifest.get("sha256", ""),
        "n_outputs": manifest.get("n_outputs", 0),
        "tarball": str(tarball),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--tasks", required=True, help="comma-separated task titles")
    ap.add_argument("--generators", required=True, help="comma-separated generator slugs")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    db = SessionLocal()
    try:
        summary = build_release(
            db, get_storage(), version=a.version,
            task_titles=a.tasks.split(","), generator_slugs=a.generators.split(","),
            out_dir=a.out,
        )
    finally:
        db.close()
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_build_dataset_release.py -v` → PASS
Run: `.venv/bin/pytest -q` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_dataset_release.py tests/test_build_dataset_release.py
git commit -m "feat(dataset): build_dataset_release.py — decorate SP1 bundle + tarball + leak guard"
```

---

### Task 3: `/dataset` landing page

**Files:**

- Create: `app/templates/dataset.html`
- Modify: `app/main.py` (add `/dataset` route + `config.RELEASES_DIR`), `app/config.py` (add `RELEASES_DIR`)
- Test: `tests/test_dataset_page.py`

**Interfaces:**

- Consumes: `config.RELEASES_DIR` (`Path`, default `DATA_DIR / "releases"`).
- Produces: `GET /dataset` → 200 HTML. Lists released versions (subdirs of `RELEASES_DIR` that contain a `VERSION` file); shows a "no release published yet" state when none exist. Reads the newest `DATASHEET.md`/`VERSION` if present.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dataset_page.py
from fastapi.testclient import TestClient
from app import config
from app.main import app


def test_dataset_page_no_release(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path / "releases")
    r = TestClient(app).get("/dataset")
    assert r.status_code == 200
    assert "no release" in r.text.lower()


def test_dataset_page_lists_release(monkeypatch, tmp_path):
    rel = tmp_path / "releases" / "2026.07-v1"; rel.mkdir(parents=True)
    (rel / "VERSION").write_text("2026.07-v1\nsha256:abc\n")
    (rel / "DATASHEET.md").write_text("# Datasheet 2026.07-v1\n")
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path / "releases")
    r = TestClient(app).get("/dataset")
    assert r.status_code == 200
    assert "2026.07-v1" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dataset_page.py -v`
Expected: FAIL — 404 for `/dataset`.

- [ ] **Step 3: Add config + route + template**

In `app/config.py`, after `GT_ASSET_SUBDIR`:

```python
# Directory holding built dataset releases (each a <version>/ subdir with VERSION + DATASHEET).
RELEASES_DIR = DATA_DIR / "releases"
```

In `app/main.py`, near the other page routes (match the `templates.TemplateResponse(request, "...")` form — see the `/methodology` route):

```python
@app.get("/dataset", response_class=HTMLResponse)
def dataset_page(request: Request):
    releases_dir = config.RELEASES_DIR
    releases = []
    if releases_dir.is_dir():
        for d in sorted(releases_dir.iterdir(), reverse=True):
            vf = d / "VERSION"
            if d.is_dir() and vf.is_file():
                releases.append({"version": d.name, "version_text": vf.read_text()})
    return templates.TemplateResponse(request, "dataset.html", {"releases": releases})
```

`app/templates/dataset.html`:

```html
{% extends "base.html" %} {% block title %}Dataset · Bio 3D Arena{% endblock %}
{% block content %}
<h1>Benchmark Dataset</h1>
<p>
  A citable, licensed release of the biological-3D generation benchmark: tasks,
  3D outputs, baked GT reference renders, and objective metrics. Held-out raw GT
  is withheld for integrity. Human preference votes ship as a secondary file.
</p>
{% if releases %}
<ul>
  {% for r in releases %}
  <li>
    <b>{{ r.version }}</b> —
    <a href="/api/export.json">preference records (live)</a>
    <pre>{{ r.version_text }}</pre>
  </li>
  {% endfor %}
</ul>
<p>
  See each release's <code>DATASHEET.md</code> and <code>LICENSE</code> for
  contents and citation. API: <a href="/openapi.json">/openapi.json</a>.
</p>
{% else %}
<p>
  <em>No release published yet.</em> The live API is available at
  <a href="/api/export.json">/api/export.json</a> and
  <a href="/api/leaderboard">/api/leaderboard</a>.
</p>
{% endif %} {% endblock %}
```

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_dataset_page.py -v` → PASS (2 passed)
Run: `.venv/bin/pytest -q` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/main.py app/templates/dataset.html tests/test_dataset_page.py
git commit -m "feat(dataset): /dataset landing page (lists releases; no-release state)"
```

---

## Self-Review

**Spec coverage:**

- Reuse SP1 export bundle → Task 2 (`export_bundle`). ✓
- LICENSE + DATASHEET + VERSION + preference_records → Task 1 (helpers) + Task 2 (writes them). ✓
- Held-out GT private / no `.npy` / no agrigen → Task 2 `_assert_no_leak` + reused SP1 gate. ✓
- Version passed in + content hash → Task 2 `VERSION` from `--version` + `manifest.sha256`. ✓
- `/dataset` page (with + without release) → Task 3. ✓
- Fail-loud license → inherited from `export_bundle` (SP1 `check_licenses`); noted in Task 2. ✓
- Preference records as secondary → Task 1 helper + datasheet framing. ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code.

**Type consistency:** `build_release(...)` signature identical in Task 2 interface + test + impl; `build_preference_records`/`license_rollup`/`render_license`/`render_datasheet` names identical across Task 1 def and Task 2 consumption; `export_bundle` signature matches SP1's live source; `config.RELEASES_DIR` used consistently in Task 3.

**Adjust-on-contact (not blockers):** Task 1 Step 5 — find the existing `/api/export.json` test via `grep -rn "export.json" tests/` and keep it green after the DRY refactor; `base.html` block names confirmed (`title`, `content`) from SP1 Task 6.
