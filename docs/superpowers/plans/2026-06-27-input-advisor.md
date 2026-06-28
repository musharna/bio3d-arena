# Plant Input Advisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An offline advisor that classifies each recon subject's plant growth form, recommends a capture recipe + recon strategy (encoding the multi-view droop caveat), and grades the current reference photo against that recipe.

**Architecture:** A `PlantMorphology` table stores the per-subject growth-form classification (create_all-only); the per-form recipe/strategy lives in code (`app/morphology.py` `STRATEGY`). `app/input_grade.py` grades a photo with deterministic heuristics + a VLM (reusing the `app/judge.py` forced-tool pattern). `scripts/advise_inputs.py` orchestrates and emits a markdown/JSON report. Advisory only — nothing auto-wires into the recon spend path.

**Tech Stack:** Python 3.13, SQLAlchemy (declarative `Mapped`), Pillow + numpy (heuristics), `anthropic` SDK (`claude-sonnet-4-6`), pytest.

## Global Constraints

- `ANTHROPIC_API_KEY` from env, **never logged/pasted**; exception text carries exception _type names_ only (`type(e).__name__`), never key material.
- Schema is **create_all-only** — NEVER ALTER/migrate. New table registers on `Base.metadata` and is created by `init_db()`.
- Human voting/ranking path and existing single-image / multi-view recon remain **untouched**.
- Honest N/A: missing photo, no key, or a VLM error is **skipped + logged**, never faked.
- Subjects are keyed by the **`CROPS` subject slug** (short form: `arabidopsis`, `pinus`, … = `reference/<slug>_ref.jpg` stem), NOT `ReconTask.species_slug` (the binomial `arabidopsis_thaliana`).
- Reuse the VLM call pattern in `app/judge.py` (forced `tool_choice`, `parse` from the `tool_use` block). Model id = `app.judge.JUDGE_MODEL` (`"claude-sonnet-4-6"`).

**Deviation from spec (author's call):** the spec listed a `--refresh` CLI flag. Grades are recomputed on every run (no view cache exists), so `--refresh` would be a no-op — exactly the smell fixed earlier in #21. It is **omitted**. Flags are `--subject`, `--heuristics-only`, `--json`.

---

### Task 1: Morphology core — table, taxonomy, STRATEGY, seed

**Files:**

- Modify: `app/models.py` (append `PlantMorphology` near `TaskDifficulty` at EOF, ~line 440)
- Create: `app/morphology.py`
- Test: `tests/test_morphology.py`

**Interfaces:**

- Produces:
  - `app.models.PlantMorphology` (columns: `id`, `subject_slug: str`, `growth_form: str`, `notes: str`, `updated: datetime`)
  - `app.morphology` constants: `ROSETTE, ERECT_HERB, GRAMINOID, SHRUB, TREE_CONIFER, VINE_SPRAWLING` (active) + `TREE_BROADLEAF, SUCCULENT` (reserved); `GROWTH_FORMS: set[str]`
  - `app.morphology.StrategyEntry` (frozen dataclass: `capture_view, background, framing, recon_mode, nvs_pose_hint, expected_failure, min_px=1024`)
  - `app.morphology.STRATEGY: dict[str, StrategyEntry]` (one entry per active form)
  - `app.morphology.SEED: dict[str, str]` (subject_slug → growth_form)
  - `app.morphology.seed_morphology(db) -> int` (idempotent upsert; returns rows touched)

- [ ] **Step 1: Write the failing test**

Create `tests/test_morphology.py`:

```python
from __future__ import annotations

from app import morphology
from app.database import SessionLocal, init_db
from app.models import PlantMorphology


def setup_module(_m):
    init_db()


def test_strategy_covers_every_seeded_form():
    # every growth form we actually assign must have a recipe; every recipe key is a valid form
    assert set(morphology.SEED.values()) <= set(morphology.STRATEGY)
    assert set(morphology.STRATEGY) <= morphology.GROWTH_FORMS
    for entry in morphology.STRATEGY.values():
        assert entry.recon_mode in {
            "single",
            "multiview",
            "multiview_preferred",
            "multiview_required",
        }
        assert entry.min_px >= 1024


def test_seed_morphology_is_idempotent():
    db = SessionLocal()
    try:
        morphology.seed_morphology(db)
        morphology.seed_morphology(db)  # second call must not duplicate
        rows = db.query(PlantMorphology).all()
        assert len(rows) == len(morphology.SEED)
        by_slug = {r.subject_slug: r.growth_form for r in rows}
        assert by_slug["arabidopsis"] == morphology.ROSETTE
        assert by_slug["pinus"] == morphology.TREE_CONIFER
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_morphology.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.morphology'` (and `ImportError` for `PlantMorphology`).

- [ ] **Step 3: Add the `PlantMorphology` model**

Append to `app/models.py` (after `TaskDifficulty`, EOF). `Base`, `UniqueConstraint`, `Mapped`, `mapped_column`, `String`, `Text`, `DateTime`, `_utcnow`, `dt` are already imported at the top of the file:

```python
class PlantMorphology(Base):
    """Hand-curated growth-form classification per recon subject. Separate table (not a
    Task/ReconTask column) to honor the create_all-only schema — mirrors TaskDifficulty.
    subject_slug is the CROPS short slug; the per-form recipe lives in morphology.STRATEGY."""

    __tablename__ = "plant_morphology"
    __table_args__ = (UniqueConstraint("subject_slug", name="uq_plant_morphology_subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    growth_form: Mapped[str] = mapped_column(String(32))
    notes: Mapped[str] = mapped_column(Text, default="")
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

- [ ] **Step 4: Create `app/morphology.py`**

```python
"""Plant growth-form taxonomy + per-form capture/recon STRATEGY + the per-subject seed.

The STRATEGY table is the *rules* (recipe per growth form), kept in code so it is versioned
and testable. PlantMorphology (DB) stores only the per-subject classification. Encodes the
multi-view e2e caveat: top-down NVS views make a flat rosette droop, so rosette = single-preferred."""

from __future__ import annotations

from dataclasses import dataclass

# --- growth-form taxonomy (active forms have a STRATEGY entry) ---
ROSETTE = "rosette"
ERECT_HERB = "erect_herb"
GRAMINOID = "graminoid"
SHRUB = "shrub"
TREE_CONIFER = "tree_conifer"
VINE_SPRAWLING = "vine_sprawling"
# reserved for future subjects (no STRATEGY entry yet)
TREE_BROADLEAF = "tree_broadleaf"
SUCCULENT = "succulent"

GROWTH_FORMS = {
    ROSETTE,
    ERECT_HERB,
    GRAMINOID,
    SHRUB,
    TREE_CONIFER,
    VINE_SPRAWLING,
    TREE_BROADLEAF,
    SUCCULENT,
}


@dataclass(frozen=True)
class StrategyEntry:
    capture_view: str
    background: str
    framing: str
    recon_mode: str  # single | multiview | multiview_preferred | multiview_required
    nvs_pose_hint: str
    expected_failure: str
    min_px: int = 1024


_BG = "plain/neutral background"
_FRAME = "subject centered, fills >50% of frame, soft even light"

STRATEGY: dict[str, StrategyEntry] = {
    ROSETTE: StrategyEntry(
        capture_view="top-down (radially flat — natural for a rosette)",
        background=_BG,
        framing=_FRAME,
        recon_mode="single",
        nvs_pose_hint="multi-view droops: top-down NVS views give the recon no flat-ground "
        "constraint, so leaves cascade downward. If multi-view, bias NVS to side/mid elevations.",
        expected_failure="single-image: flat but acceptable; multi-view: over-tall / drooping leaves",
    ),
    ERECT_HERB: StrategyEntry(
        capture_view="three-quarter or front, full height",
        background=_BG,
        framing=_FRAME,
        recon_mode="single",
        nvs_pose_hint="default NVS poses fine; multi-view helps recover occluded stems",
        expected_failure="thin stems/petioles may thin out in single-image",
    ),
    GRAMINOID: StrategyEntry(
        capture_view="front, full height",
        background=_BG,
        framing=_FRAME,
        recon_mode="multiview_preferred",
        nvs_pose_hint="thin vertical blades need lateral views; default NVS azimuths are adequate",
        expected_failure="single-image loses thin blades / collapses the canopy",
    ),
    SHRUB: StrategyEntry(
        capture_view="three-quarter view",
        background=_BG,
        framing=_FRAME,
        recon_mode="single",
        nvs_pose_hint="multi-view recovers the occluded interior of a dense bloom canopy",
        expected_failure="interior occlusion; dense bloom can read as a solid blob",
    ),
    TREE_CONIFER: StrategyEntry(
        capture_view="front, full tree",
        background=_BG,
        framing=_FRAME,
        recon_mode="multiview_required",
        nvs_pose_hint="needles are a fundamental single-image failure; even multi-view is hard — "
        "treat results as low-confidence",
        expected_failure="single-image blobs the needle canopy (confirmed on pine)",
    ),
    VINE_SPRAWLING: StrategyEntry(
        capture_view="isolate one representative section",
        background=_BG,
        framing=_FRAME,
        recon_mode="multiview",
        nvs_pose_hint="sprawling habit is hard to frame as one subject; prefer a bounded section",
        expected_failure="ambiguous extent; recon may fuse separate stems",
    ),
}

# subject_slug (CROPS key) -> growth_form. tomato: indeterminate field tomatoes are vining,
# but our reference is a potted, front-on specimen, so ERECT_HERB.
SEED: dict[str, str] = {
    "arabidopsis": ROSETTE,
    "maize": GRAMINOID,
    "soybean": ERECT_HERB,
    "tomato": ERECT_HERB,
    "rose": SHRUB,
    "pinus": TREE_CONIFER,
}


def seed_morphology(db) -> int:
    """Idempotent upsert of SEED into PlantMorphology. Returns the number of rows created or
    changed. Never overwrites a `notes` field; only sets growth_form to the seed value."""
    from sqlalchemy import select

    from app.models import PlantMorphology

    touched = 0
    for slug, form in SEED.items():
        row = db.execute(
            select(PlantMorphology).where(PlantMorphology.subject_slug == slug)
        ).scalar_one_or_none()
        if row is None:
            db.add(PlantMorphology(subject_slug=slug, growth_form=form))
            touched += 1
        elif row.growth_form != form:
            row.growth_form = form
            touched += 1
    db.commit()
    return touched
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_morphology.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/morphology.py tests/test_morphology.py
git commit -m "feat(advisor): PlantMorphology table + growth-form taxonomy + STRATEGY + seed"
```

---

### Task 2: Grader heuristics + GradeResult + grade_input (heuristics-only path)

**Files:**

- Create: `app/input_grade.py`
- Test: `tests/test_input_grade.py`

**Interfaces:**

- Consumes: `app.morphology.StrategyEntry`, `app.morphology.STRATEGY`
- Produces:
  - `app.input_grade.GradeResult` (dataclass: `width, height, dims_ok, bg_uniformity, bg_ok, vlm, growth_form_match, verdict, reasons`)
  - `app.input_grade.grade_input(image_bytes, *, growth_form, strategy_entry, client=None, heuristics_only=False) -> GradeResult`
  - (internal) `_heuristics(image_bytes, *, min_px) -> tuple[int,int,bool,float,bool]`, `_verdict(dims_ok, bg_ok, vlm) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_input_grade.py`:

```python
from __future__ import annotations

import io

import numpy as np
from PIL import Image

from app import morphology
from app.input_grade import GradeResult, grade_input


def _img_bytes(w, h, *, busy=False):
    if busy:
        arr = (np.random.default_rng(0).integers(0, 256, size=(h, w, 3))).astype("uint8")
        im = Image.fromarray(arr, "RGB")
    else:
        im = Image.new("RGB", (w, h), (255, 255, 255))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


ENTRY = morphology.STRATEGY[morphology.ROSETTE]


def test_heuristics_pass_large_plain():
    r = grade_input(_img_bytes(1200, 1200), growth_form=morphology.ROSETTE,
                    strategy_entry=ENTRY, heuristics_only=True)
    assert isinstance(r, GradeResult)
    assert r.dims_ok and r.bg_ok and r.vlm is None and r.verdict == "good"


def test_small_image_is_reject():
    r = grade_input(_img_bytes(512, 512), growth_form=morphology.ROSETTE,
                    strategy_entry=ENTRY, heuristics_only=True)
    assert not r.dims_ok and r.verdict == "reject"
    assert any("resolution" in s for s in r.reasons)


def test_busy_background_flags_bg():
    r = grade_input(_img_bytes(1200, 1200, busy=True), growth_form=morphology.ROSETTE,
                    strategy_entry=ENTRY, heuristics_only=True)
    assert not r.bg_ok and r.verdict == "marginal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_input_grade.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.input_grade'`.

- [ ] **Step 3: Create `app/input_grade.py` (heuristics + orchestration; VLM branch added in Task 3)**

```python
"""Grade a candidate reference photo as a single-image→3D reconstruction input.

Two layers: deterministic PIL/numpy heuristics (resolution + background uniformity) and a VLM
grader (Task 3, reuses the app/judge.py forced-tool pattern). grade_input combines them. The VLM
branch is skipped when heuristics_only=True or no client is supplied."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

_BG_VARIANCE_THRESHOLD = 0.12  # mean per-channel corner std / 255; below = plain background


@dataclass
class GradeResult:
    width: int
    height: int
    dims_ok: bool
    bg_uniformity: float  # mean corner std / 255 (lower = more uniform background)
    bg_ok: bool
    vlm: dict | None  # None when heuristics_only / no client / VLM error
    growth_form_match: bool | None  # None when no VLM result
    verdict: str  # good | marginal | reject
    reasons: list[str] = field(default_factory=list)


def _heuristics(image_bytes: bytes, *, min_px: int) -> tuple[int, int, bool, float, bool]:
    """Return (width, height, dims_ok, bg_uniformity, bg_ok). Samples the 4 corner regions
    (~10% each) and measures colour spread — a plain background has low spread."""
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = im.size
    dims_ok = min(w, h) >= min_px
    arr = np.asarray(im, dtype=np.float32)
    ch, cw = max(1, h // 10), max(1, w // 10)
    corners = np.concatenate(
        [
            arr[:ch, :cw].reshape(-1, 3),
            arr[:ch, -cw:].reshape(-1, 3),
            arr[-ch:, :cw].reshape(-1, 3),
            arr[-ch:, -cw:].reshape(-1, 3),
        ]
    )
    bg_uniformity = float(corners.std(axis=0).mean() / 255.0)
    bg_ok = bg_uniformity < _BG_VARIANCE_THRESHOLD
    return w, h, dims_ok, bg_uniformity, bg_ok


def _verdict(dims_ok: bool, bg_ok: bool, vlm: dict | None) -> str:
    if not dims_ok:
        return "reject"  # too low-res is disqualifying regardless of content
    if vlm is not None:
        v = vlm["verdict"]
        if v == "good" and not bg_ok:
            return "marginal"  # VLM liked it but corners are busy
        return v
    return "good" if bg_ok else "marginal"


def grade_input(
    image_bytes: bytes,
    *,
    growth_form: str,
    strategy_entry,
    client=None,
    heuristics_only: bool = False,
) -> GradeResult:
    """Grade one photo against the recipe for its growth form. Deterministic heuristics always
    run; the VLM grader runs only when not heuristics_only and a client is supplied (added in
    Task 3). A VLM error is recorded as a reason (type name only) and degrades to heuristics."""
    w, h, dims_ok, bg_uniformity, bg_ok = _heuristics(image_bytes, min_px=strategy_entry.min_px)
    reasons: list[str] = []
    if not dims_ok:
        reasons.append(f"resolution {min(w, h)}px < {strategy_entry.min_px}px")
    if not bg_ok:
        reasons.append("background not plain (high corner colour variance)")

    vlm: dict | None = None
    gf_match: bool | None = None
    # VLM branch is wired in Task 3.

    return GradeResult(
        width=w,
        height=h,
        dims_ok=dims_ok,
        bg_uniformity=bg_uniformity,
        bg_ok=bg_ok,
        vlm=vlm,
        growth_form_match=gf_match,
        verdict=_verdict(dims_ok, bg_ok, vlm),
        reasons=reasons,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_input_grade.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/input_grade.py tests/test_input_grade.py
git commit -m "feat(advisor): input-grade heuristics (resolution + bg uniformity) + GradeResult"
```

---

### Task 3: VLM grader branch

**Files:**

- Modify: `app/input_grade.py` (add `GRADE_TOOL`, `grade_with_vlm`, helpers; wire into `grade_input`)
- Test: `tests/test_input_grade.py` (add VLM tests)

**Interfaces:**

- Consumes: `app.judge.JUDGE_MODEL`, `app.morphology.GROWTH_FORMS`
- Produces: `app.input_grade.grade_with_vlm(client, image_bytes, *, growth_form, strategy_entry) -> dict`
  returning `{growth_form, background_ok, view_matches_recipe, fill_ok, verdict, reasons}`; and the
  wired VLM branch in `grade_input` (sets `vlm`, `growth_form_match`, appends reasons).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_input_grade.py`:

```python
class _FakeBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.name = "record_input_grade"
        self.input = payload


class _FakeResp:
    def __init__(self, payload):
        self.content = [_FakeBlock(payload)]


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    class _Messages:
        def __init__(self, payload):
            self._payload = payload

        def create(self, **_kw):
            return _FakeResp(self._payload)

    @property
    def messages(self):
        return _FakeClient._Messages(self._payload)


_GOOD_VLM = {
    "growth_form": morphology.ROSETTE,
    "background_ok": True,
    "view_matches_recipe": True,
    "fill_ok": True,
    "verdict": "good",
    "reasons": "clean top-down rosette on white",
}


def test_grade_with_vlm_parses_tool_block():
    from app.input_grade import grade_with_vlm

    out = grade_with_vlm(_FakeClient(_GOOD_VLM), _img_bytes(1200, 1200),
                         growth_form=morphology.ROSETTE, strategy_entry=ENTRY)
    assert out["growth_form"] == morphology.ROSETTE and out["verdict"] == "good"


def test_grade_input_flags_growth_form_mismatch():
    payload = dict(_GOOD_VLM, growth_form=morphology.SHRUB)  # disagrees with seed
    r = grade_input(_img_bytes(1200, 1200), growth_form=morphology.ROSETTE,
                    strategy_entry=ENTRY, client=_FakeClient(payload))
    assert r.vlm is not None and r.growth_form_match is False
    assert any("seed says" in s for s in r.reasons)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_input_grade.py -q`
Expected: FAIL — `ImportError: cannot import name 'grade_with_vlm'` and the mismatch test fails (no VLM branch yet).

- [ ] **Step 3: Add the VLM grader and wire it in**

In `app/input_grade.py`, add imports at the top (below the existing `import io`):

```python
import base64

from app.judge import JUDGE_MODEL
from app.morphology import GROWTH_FORMS
```

Add the tool schema + helpers (after `GradeResult`):

```python
GRADE_TOOL = {
    "name": "record_input_grade",
    "description": "Grade a single photo as an input for image-to-3D reconstruction of a plant.",
    "input_schema": {
        "type": "object",
        "properties": {
            "growth_form": {"type": "string", "enum": sorted(GROWTH_FORMS)},
            "background_ok": {"type": "boolean", "description": "Plain/neutral, separable background."},
            "view_matches_recipe": {"type": "boolean", "description": "View matches the recipe for this form."},
            "fill_ok": {"type": "boolean", "description": "Subject centered and fills >50% of frame."},
            "verdict": {"type": "string", "enum": ["good", "marginal", "reject"]},
            "reasons": {"type": "string", "description": "One sentence justification."},
        },
        "required": ["growth_form", "background_ok", "view_matches_recipe", "fill_ok", "verdict"],
    },
}


def _grade_img_block(b64: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def _build_grade_messages(image_b64: str, growth_form: str, strategy_entry) -> list[dict]:
    recipe = (
        f"Expected growth form: {growth_form}. Recommended capture for this form — view: "
        f"{strategy_entry.capture_view}; {strategy_entry.background}; {strategy_entry.framing}; "
        f">={strategy_entry.min_px}px."
    )
    text = (
        "You are grading ONE photo as the input for single-image to 3D reconstruction of a plant.\n"
        f"{recipe}\n\n"
        "First classify the plant's growth form. Then judge the photo AGAINST THE RECIPE FOR THE "
        "GROWTH FORM YOU OBSERVE: is the background plain/separable, does the view match that "
        "recipe, does the subject fill the frame? Do NOT penalize a top-down view for a rosette — "
        "that is correct for a radially-flat plant. Then call record_input_grade."
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _grade_img_block(image_b64)]}]


def _parse_grade(response) -> dict:
    for block in getattr(response, "content", []):
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", "") == "record_input_grade"
        ):
            d = block.input or {}
            if d.get("growth_form") not in GROWTH_FORMS:
                raise ValueError(f"invalid growth_form: {d.get('growth_form')!r}")
            if d.get("verdict") not in {"good", "marginal", "reject"}:
                raise ValueError(f"invalid verdict: {d.get('verdict')!r}")
            return {
                "growth_form": d["growth_form"],
                "background_ok": bool(d["background_ok"]),
                "view_matches_recipe": bool(d["view_matches_recipe"]),
                "fill_ok": bool(d["fill_ok"]),
                "verdict": d["verdict"],
                "reasons": d.get("reasons", ""),
            }
    raise ValueError("no record_input_grade tool_use block in response")


def grade_with_vlm(client, image_bytes: bytes, *, growth_form: str, strategy_entry) -> dict:
    """One forced-tool VLM call grading the photo against the recipe. Mirrors app.judge.judge_pair."""
    b64 = base64.b64encode(image_bytes).decode()
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=400,
        tools=[GRADE_TOOL],
        tool_choice={"type": "tool", "name": "record_input_grade"},
        messages=_build_grade_messages(b64, growth_form, strategy_entry),
    )
    return _parse_grade(resp)
```

Then replace the placeholder VLM branch in `grade_input` (the `# VLM branch is wired in Task 3.` line) with:

```python
    if not heuristics_only and client is not None:
        try:
            vlm = grade_with_vlm(
                client, image_bytes, growth_form=growth_form, strategy_entry=strategy_entry
            )
            gf_match = vlm["growth_form"] == growth_form
            if not gf_match:
                reasons.append(f"VLM sees {vlm['growth_form']}, seed says {growth_form}")
            for key in ("background_ok", "view_matches_recipe", "fill_ok"):
                if not vlm[key]:
                    reasons.append(f"VLM: {key} is false")
        except Exception as e:  # noqa: BLE001 — degrade to heuristics; key-safe message
            reasons.append(f"vlm_error: {type(e).__name__}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_input_grade.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/input_grade.py tests/test_input_grade.py
git commit -m "feat(advisor): VLM grader (forced-tool, claude-sonnet-4-6) + grade_input wiring"
```

---

### Task 4: CLI + report (`scripts/advise_inputs.py`)

**Files:**

- Create: `scripts/advise_inputs.py`
- Test: `tests/test_advise_inputs.py`

**Interfaces:**

- Consumes: `app.morphology` (`seed_morphology`, `STRATEGY`), `app.input_grade.grade_input`, `app.models.PlantMorphology`, `app.config.ASSET_DIR`
- Produces:
  - `scripts.advise_inputs.advise(db, *, subjects, asset_dir, client=None, heuristics_only=False) -> list[dict]`
    (per-subject result dicts: `{subject, growth_form?, entry?, grade?, skipped?}`)
  - `scripts.advise_inputs.build_report(results) -> str` (markdown)
  - `scripts.advise_inputs.main() -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_advise_inputs.py`:

```python
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from app import config, morphology
from app.database import SessionLocal, init_db
from app.models import PlantMorphology
from scripts.advise_inputs import advise, build_report


def setup_module(_m):
    init_db()


def _write_ref(slug, w=1200, h=1200):
    p = Path(config.ASSET_DIR) / "reference" / f"{slug}_ref.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    b = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(b, "JPEG")
    p.write_bytes(b.getvalue())


def test_advise_grades_present_ref_and_skips_missing():
    _write_ref("arabidopsis")
    # ensure pinus ref is absent
    miss = Path(config.ASSET_DIR) / "reference" / "pinus_ref.jpg"
    if miss.exists():
        miss.unlink()
    db = SessionLocal()
    try:
        results = advise(
            db,
            subjects=["arabidopsis", "pinus"],
            asset_dir=config.ASSET_DIR,
            heuristics_only=True,
        )
        by = {r["subject"]: r for r in results}
        assert by["arabidopsis"]["growth_form"] == morphology.ROSETTE
        assert by["arabidopsis"]["grade"].verdict == "good"
        assert "skipped" in by["pinus"]  # missing ref
        # morphology rows were seeded/upserted
        assert db.query(PlantMorphology).filter_by(subject_slug="arabidopsis").one()
        # report renders the key fields
        md = build_report(results)
        assert "arabidopsis" in md and "rosette" in md and "single" in md
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_advise_inputs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.advise_inputs'`.

- [ ] **Step 3: Create `scripts/advise_inputs.py`**

```python
"""Offline plant input advisor: per recon subject, classify growth form (seeded), look up the
capture/recon STRATEGY, and grade the current reference photo. Emits a markdown (+ optional JSON)
report. Advisory only — does not touch the recon pipeline. ANTHROPIC_API_KEY from env, never logged."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config, morphology  # noqa: E402
from app import input_grade as ig  # noqa: E402
from app.models import PlantMorphology  # noqa: E402


def advise(db, *, subjects, asset_dir, client=None, heuristics_only=False) -> list[dict]:
    """Seed morphology, then per subject grade reference/<slug>_ref.jpg against its STRATEGY.
    Missing ref / unknown subject / no-strategy → skip-and-log dict; others get a grade."""
    morphology.seed_morphology(db)
    results: list[dict] = []
    for slug in subjects:
        row = db.execute(
            select(PlantMorphology).where(PlantMorphology.subject_slug == slug)
        ).scalar_one_or_none()
        if row is None:
            results.append({"subject": slug, "skipped": "unknown subject (no morphology row)"})
            continue
        entry = morphology.STRATEGY.get(row.growth_form)
        if entry is None:
            results.append(
                {"subject": slug, "growth_form": row.growth_form, "skipped": "no strategy for form"}
            )
            continue
        ref = Path(asset_dir) / "reference" / f"{slug}_ref.jpg"
        if not ref.exists():
            results.append(
                {"subject": slug, "growth_form": row.growth_form, "skipped": f"missing ref {ref}"}
            )
            continue
        grade = ig.grade_input(
            ref.read_bytes(),
            growth_form=row.growth_form,
            strategy_entry=entry,
            client=client,
            heuristics_only=heuristics_only,
        )
        results.append(
            {"subject": slug, "growth_form": row.growth_form, "entry": entry, "grade": grade}
        )
    return results


def build_report(results: list[dict]) -> str:
    lines = ["# Plant Input Advisor — report", ""]
    for r in results:
        lines.append(f"## {r['subject']}")
        if "skipped" in r:
            lines.append(f"- SKIPPED: {r['skipped']}")
            lines.append("")
            continue
        e = r["entry"]
        g = r["grade"]
        lines.append(f"- growth form: **{r['growth_form']}**")
        lines.append(f"- recon mode: **{e.recon_mode}**")
        lines.append(f"- capture recipe: {e.capture_view}; {e.background}; {e.framing}; >={e.min_px}px")
        lines.append(f"- expected failure: {e.expected_failure}")
        lines.append(f"- nvs hint: {e.nvs_pose_hint}")
        lines.append(
            f"- photo grade: **{g.verdict}** ({g.width}x{g.height}, dims_ok={g.dims_ok}, "
            f"bg_ok={g.bg_ok}, bg_uniformity={g.bg_uniformity:.3f})"
        )
        if g.vlm is not None:
            lines.append(f"- VLM: {g.vlm} | growth_form_match={g.growth_form_match}")
        if g.reasons:
            lines.append(f"- reasons: {'; '.join(g.reasons)}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    import argparse
    import datetime as dt
    import json
    import os

    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subject", choices=sorted(morphology.SEED), default=None)
    ap.add_argument("--heuristics-only", action="store_true", help="skip the VLM grader")
    ap.add_argument("--json", action="store_true", help="also write a JSON sidecar")
    args = ap.parse_args()

    subjects = [args.subject] if args.subject else list(morphology.SEED)

    client = None
    heuristics_only = args.heuristics_only
    note = ""
    if not heuristics_only:
        if os.environ.get("ANTHROPIC_API_KEY"):
            import anthropic

            client = anthropic.Anthropic()
        else:
            heuristics_only = True
            note = "ANTHROPIC_API_KEY not set — heuristics-only run."

    with SessionLocal() as db:
        results = advise(
            db,
            subjects=subjects,
            asset_dir=config.ASSET_DIR,
            client=client,
            heuristics_only=heuristics_only,
        )

    md = build_report(results)
    if note:
        md = f"> {note}\n\n" + md
    out_dir = Path("docs/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    (out_dir / f"{stamp}-input-advisor.md").write_text(md)
    if args.json:
        serializable = [
            {k: (vars(v) if hasattr(v, "__dict__") else v) for k, v in r.items() if k != "entry"}
            for r in results
        ]
        (out_dir / f"{stamp}-input-advisor.json").write_text(json.dumps(serializable, indent=2, default=str))
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_advise_inputs.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Run the full suite + lint**

Run: `python -m pytest -q && ruff check app/morphology.py app/input_grade.py scripts/advise_inputs.py tests/test_morphology.py tests/test_input_grade.py tests/test_advise_inputs.py`
Expected: full suite PASS (no regressions), ruff `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add scripts/advise_inputs.py tests/test_advise_inputs.py
git commit -m "feat(advisor): advise_inputs CLI + markdown/JSON report"
```

---

## Real-execution check (key-gated, deferred — controller decides)

After the four tasks land, one live VLM grade verifies the grader against reality (spends a small
`ANTHROPIC_API_KEY` budget):

```bash
python scripts/advise_inputs.py --subject arabidopsis   # requires ANTHROPIC_API_KEY
```

Expected: report shows arabidopsis classified `rosette` with a `good`/`marginal` verdict and
`growth_form_match=True`. This is the analogue of #21's key-gated e2e — surface the spend decision to
the controller rather than running it automatically.

## Notes for the SDD controller

- Tasks are sequential: 1 (data+rules) → 2 (heuristics+orchestration) → 3 (VLM branch) → 4 (CLI). Task 3 edits the file Task 2 created; Task 4 consumes all prior.
- `numpy` and `Pillow` are already in the env (trimesh/Pillow present). `anthropic` is already a dep (used by `judge_vlm.py`).
- Minor accepted by design: `bg_uniformity` is a coarse proxy; framing/view are deferred to the VLM (the spec's explicit choice). Don't let a reviewer escalate "heuristic fill-ratio missing" — it's intentional.

```

```
