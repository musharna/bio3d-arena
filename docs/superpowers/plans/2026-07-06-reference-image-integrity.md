# Reference-Image Integrity Subsystem — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the reference images voters see a fair, complete, quality-gated fidelity anchor: stop showing a recon's own input photo as a "reference," gate the CC gallery for fruit-only/wrong-species, and add a probe-validated CLIP/BioCLIP judge.

**Architecture:** A new isolated `app/species_id.py` wraps generic-CLIP + BioCLIP (lazy torch import). `app/reference_qa.py` scores gallery images for organ-coverage (reusing `completeness.derive` — "isolated-organ" == fruit-only) and species-representativeness. A jobd-GPU probe (`scripts/probe_clip_bioclip.py`) measures which mechanism wins where (in-domain vs render-OOD) and gates the render-based follow-ons. `reference_images_for_task` is changed to emit only QA-passed independent gallery images (recon input dropped; barley-MRI exempt).

**Tech Stack:** Python 3, FastAPI, SQLAlchemy 2.0, Anthropic SDK (`claude-sonnet-4-6` VLM), `open_clip_torch` + BioCLIP (torch/CUDA), jobd for GPU.

## Global Constraints

- Branch `reference-image-integrity` already created off `master` @002eff0. Do NOT branch again.
- Two taxon key-spaces, both from `title.split("—")[0].strip()`: **binomial** (`"Solanum lycopersicum"`) for `organ_inventory`/`TraitRubric.taxon`; **slug** (`"solanum_lycopersicum"`, `.lower().replace(" ", "_")`) for gallery dirs / `_gallery_slug`. Never conflate them.
- LLM client = `anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])`; model `JUDGE_MODEL = "claude-sonnet-4-6"` from `app.judge`. Client is always **injected**, never constructed inside `app/` modules (only scripts construct it).
- All GPU work (probe, any BioCLIP inference at scale) submits via jobd: `job submit --project bio3d-arena --cwd $(pwd) --gpu --needs cuda-8gb --wait -- <cmd>`. Never raw `python` for GPU. Probe the full GPU process table first (CLAUDE.md).
- Any script that opens the DB for writes MUST guard: `if not config.is_safe_test_db_target(config.DATABASE_URL): raise SystemExit(...)` before `SessionLocal`, unless it is an explicit production op invoked with `--apply`.
- Advisory flags are non-hiding: `flags.record_flag(db, output_id, session_id, reason, threshold=10**9)` (idempotent per `(output_id, session_id)`; the 10\*\*9 threshold never auto-hides). Caller commits.
- torch/open_clip imports are LAZY (inside functions) so `import app.species_id` stays cheap and the non-GPU app/test paths never import torch.
- Verify the current BioCLIP checkpoint with a web search at build time (BioCLIP-2 `hf-hub:imageomics/bioclip-2` may supersede the CVPR'24 `hf-hub:imageomics/bioclip`); pin whichever is current, prefer bioclip-2.
- YAGNI: build only in-scope components (A–E). #3 wrong_species-on-renders is probe-GATED (build only if the probe clears); #4 perceptual-fidelity and #5 text→3D-alignment are OUT of scope (logged as paper follow-ons).

---

### Task 1: `app/species_id.py` — CLIP/BioCLIP capability module

**Files:**

- Create: `app/species_id.py`
- Modify: `pyproject.toml` (add `open_clip_torch` dependency) — or `requirements.txt` if that is the project's manifest (check which exists; the repo uses `.venv`).
- Test: `tests/test_species_id.py`

**Interfaces:**

- Produces:
  - `MODELS: dict[str, str]` — registry, e.g. `{"clip": "ViT-L-14/laion2b_s32b_b82k", "bioclip": "hf-hub:imageomics/bioclip-2"}`.
  - `load_model(kind: str) -> tuple` — returns `(model, preprocess, tokenizer)`; cached per-process. Lazy-imports `torch`, `open_clip`.
  - `embed_image(bundle, png: bytes) -> "np.ndarray"` — L2-normalized image embedding (1-D float32).
  - `zero_shot(bundle, png: bytes, labels: list[str]) -> dict[str, float]` — softmax similarity per label, sums to 1.0.
  - `species_rep_score(bundle, png: bytes, *, common: str, taxon: str) -> float` — P(image is a good, identifiable photo of the species) via zero-shot against `[f"a clear photo of {common} ({taxon})", "an unrelated or unidentifiable image"]`, returns the first prob.
  - `available() -> bool` — True iff `open_clip` importable (lets tests skip the real-forward path).

- [ ] **Step 1: Write the failing test** (mock the model; test label math is deterministic without torch)

```python
# tests/test_species_id.py
import numpy as np
import pytest
from app import species_id


class _FakeBundle:
    """Stand-in for (model, preprocess, tokenizer): zero_shot() is tested via monkeypatched
    _logits so the label→softmax mapping is exercised without torch/open_clip installed."""


def test_zero_shot_softmax_sums_to_one(monkeypatch):
    monkeypatch.setattr(species_id, "_logits", lambda bundle, png, labels: np.array([2.0, 0.0]))
    out = species_id.zero_shot(_FakeBundle(), b"\x89PNG", ["a", "b"])
    assert set(out) == {"a", "b"}
    assert abs(sum(out.values()) - 1.0) < 1e-6
    assert out["a"] > out["b"]


def test_species_rep_score_is_first_label_prob(monkeypatch):
    monkeypatch.setattr(species_id, "_logits", lambda bundle, png, labels: np.array([3.0, 0.0]))
    s = species_id.species_rep_score(_FakeBundle(), b"\x89PNG", common="tomato", taxon="Solanum lycopersicum")
    assert 0.9 < s <= 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_species_id.py -q`
Expected: FAIL (`AttributeError: module 'app.species_id' has no attribute '_logits'`).

- [ ] **Step 3: Implement `app/species_id.py`**

```python
"""CLIP / BioCLIP species-identity capability. torch + open_clip are imported lazily so the
rest of the app (and non-GPU tests) never pay the import. Models are cached per-process."""
from __future__ import annotations

import functools
import io

MODELS = {
    "clip": "ViT-L-14/laion2b_s32b_b82k",   # generic OpenCLIP — strong compositional prompts
    "bioclip": "hf-hub:imageomics/bioclip-2",  # verify latest at build; prefer bioclip-2
}


def available() -> bool:
    try:
        import open_clip  # noqa: F401
        return True
    except Exception:
        return False


@functools.lru_cache(maxsize=4)
def load_model(kind: str):
    import open_clip
    import torch

    spec = MODELS[kind]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if spec.startswith("hf-hub:"):
        model, _, preprocess = open_clip.create_model_and_transforms(spec)
        tokenizer = open_clip.get_tokenizer(spec)
    else:
        name, pretrained = spec.split("/", 1)
        model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pretrained)
        tokenizer = open_clip.get_tokenizer(name)
    model = model.to(device).eval()
    return (model, preprocess, tokenizer, device)


def _logits(bundle, png: bytes, labels: list[str]):
    """Raw image·text cosine similarities (scaled) as a numpy array, one per label."""
    import numpy as np
    import torch
    from PIL import Image

    model, preprocess, tokenizer, device = bundle
    img = Image.open(io.BytesIO(png)).convert("RGB")
    with torch.no_grad():
        img_t = preprocess(img).unsqueeze(0).to(device)
        txt_t = tokenizer(labels).to(device)
        img_f = model.encode_image(img_t)
        txt_f = model.encode_text(txt_t)
        img_f = img_f / img_f.norm(dim=-1, keepdim=True)
        txt_f = txt_f / txt_f.norm(dim=-1, keepdim=True)
        sims = (100.0 * img_f @ txt_f.T).squeeze(0)
        return sims.cpu().numpy().astype("float64")


def zero_shot(bundle, png: bytes, labels: list[str]) -> dict[str, float]:
    import numpy as np

    z = _logits(bundle, png, labels)
    e = np.exp(z - z.max())
    p = e / e.sum()
    return {lab: float(pi) for lab, pi in zip(labels, p)}


def embed_image(bundle, png: bytes):
    import numpy as np
    import torch
    from PIL import Image

    model, preprocess, _, device = bundle
    img = Image.open(io.BytesIO(png)).convert("RGB")
    with torch.no_grad():
        f = model.encode_image(preprocess(img).unsqueeze(0).to(device))
        f = f / f.norm(dim=-1, keepdim=True)
        return f.squeeze(0).cpu().numpy().astype("float32")


def species_rep_score(bundle, png: bytes, *, common: str, taxon: str) -> float:
    labels = [f"a clear, identifiable photo of {common} ({taxon})", "an unrelated or unidentifiable image"]
    return zero_shot(bundle, png, labels)[labels[0]]
```

Add `open_clip_torch` to the dependency manifest (`pyproject.toml` `[project].dependencies` or `requirements.txt`).

- [ ] **Step 4: Run unit tests**

Run: `python -m pytest tests/test_species_id.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Real-execution smoke test** (only if open_clip installed; skipped in CI)

```python
# append to tests/test_species_id.py
def test_real_forward_pass_if_available():
    if not species_id.available():
        pytest.skip("open_clip not installed")
    import numpy as np
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (224, 224), (0, 128, 0)).save(buf, format="PNG")
    bundle = species_id.load_model("clip")
    v = species_id.embed_image(bundle, buf.getvalue())
    assert v.shape[0] > 0 and abs(float(np.linalg.norm(v)) - 1.0) < 1e-3
```

Run (GPU host, via jobd): `job submit --project bio3d-arena --cwd $(pwd) --gpu --needs cuda-8gb --wait -- python -m pytest tests/test_species_id.py::test_real_forward_pass_if_available -q`
Expected: PASS or skipped.

- [ ] **Step 6: Commit** — `git add app/species_id.py tests/test_species_id.py pyproject.toml && git commit -m "feat(species-id): CLIP/BioCLIP capability module (lazy torch, zero-shot + embed)"`

---

### Task 2: `app/reference_qa.py` — organ-coverage (fruit-only) via completeness reuse

**Files:**

- Create: `app/reference_qa.py`
- Test: `tests/test_reference_qa.py`

**Interfaces:**

- Consumes: `completeness.derive`, `completeness.COMPLETENESS_TOOL`, `completeness._parse`, `organ_inventory.inventory_for`, `organ_inventory.TaxonInventory`.
- Produces:
  - `assess_organ_coverage(client, photo_png: bytes, *, inventory) -> dict` — returns `{"category": str, "score": float, "organs_present": list, "note": str, "fruit_only": bool}` where `fruit_only = (category == "isolated-organ")`.
  - `_photo_messages(png: bytes, inventory) -> list[dict]` — reference-PHOTO-framed prompt (NOT the 3D-render-sheet framing of `completeness._build_messages`), reusing `COMPLETENESS_TOOL`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reference_qa.py
from app import reference_qa
from app.organ_inventory import inventory_for


class _Block:
    type = "tool_use"; name = "record_completeness"
    def __init__(self, inp): self.input = inp
class _Resp:
    def __init__(self, inp): self.content = [_Block(inp)]
class _FakeClient:
    def __init__(self, inp): self._r = _Resp(inp); self.messages = self
    def create(self, **kw): return self._r


def test_fruit_only_reference_is_flagged():
    inv = inventory_for("Cucurbita pepo")
    assert inv is not None, "Cucurbita pepo inventory must exist"
    # VLM sees ONLY the fruit organ present -> present_count==1 -> 'isolated-organ' -> fruit_only
    fruit_key = next(o.key for o in inv.organs if not o.required)  # a non-vegetative organ
    present = [{"key": o.key, "status": ("present" if o.key == fruit_key else "absent")} for o in inv.organs]
    client = _FakeClient({"organs_present": present, "note": "only the gourd fruit visible"})
    res = reference_qa.assess_organ_coverage(client, b"\x89PNG", inventory=inv)
    assert res["fruit_only"] is True
    assert res["category"] == "isolated-organ"


def test_whole_plant_reference_not_flagged():
    inv = inventory_for("Solanum lycopersicum")
    present = [{"key": o.key, "status": "present"} for o in inv.organs]
    client = _FakeClient({"organs_present": present, "note": "whole plant"})
    res = reference_qa.assess_organ_coverage(client, b"\x89PNG", inventory=inv)
    assert res["fruit_only"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_reference_qa.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `app/reference_qa.py`**

```python
"""Quality-assessment for reference images. Reuses the completeness VLM machinery: a reference
photo of a single organ (e.g. a lone gourd fruit) maps to `derive`'s 'isolated-organ' category
== fruit_only. Uses a PHOTO-framed prompt, not the 3D-render-sheet framing."""
from __future__ import annotations

from .completeness import COMPLETENESS_TOOL, _parse, derive
from .judge import JUDGE_MODEL
from .organ_inventory import TaxonInventory


def _photo_messages(png: bytes, inventory: TaxonInventory) -> list[dict]:
    import base64

    lines = "\n".join(f"- {o.key}: {o.visual}" for o in inventory.organs)
    b64 = base64.b64encode(png).decode("ascii")
    text = (
        f"This is a REAL PHOTOGRAPH intended as a reference for the organism {inventory.taxon}. "
        "For EACH expected organ below, mark whether it is visibly present in THIS photo "
        "(present / absent / uncertain). A close-up of a single organ (e.g. only a fruit or only "
        "a cap) should mark the others absent.\n\n"
        f"Expected organs:\n{lines}\n\nThen call record_completeness."
    )
    return [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": text},
    ]}]


def assess_organ_coverage(client, photo_png: bytes, *, inventory: TaxonInventory) -> dict:
    resp = client.messages.create(
        model=JUDGE_MODEL, max_tokens=500,
        tools=[COMPLETENESS_TOOL], tool_choice={"type": "tool", "name": "record_completeness"},
        messages=_photo_messages(photo_png, inventory),
    )
    parsed = _parse(resp)  # {"organs_present": [...], "note": str}
    category, score = derive(inventory, parsed["organs_present"])
    return {
        "category": category, "score": score,
        "organs_present": parsed["organs_present"], "note": parsed["note"],
        "fruit_only": category == "isolated-organ",
    }
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_reference_qa.py -q` → Expected: PASS (2 passed). (If `inventory_for("Cucurbita pepo")` is None, STOP — the taxon inventory is a prerequisite; add it to `organ_inventory.py` mirroring the existing fungi entries, then continue.)

- [ ] **Step 5: Commit** — `git add app/reference_qa.py tests/test_reference_qa.py && git commit -m "feat(reference-qa): fruit-only organ-coverage via completeness.derive reuse"`

---

### Task 3: `app/reference_qa.py` — species-rep gate + combined QA verdict

**Files:**

- Modify: `app/reference_qa.py`
- Test: `tests/test_reference_qa.py`

**Interfaces:**

- Consumes: `species_id.load_model`, `species_id.species_rep_score`, Task 2's `assess_organ_coverage`.
- Produces:
  - `SPECIES_REP_MIN: float = 0.5` (default threshold; tuned by the probe, Task 5).
  - `assess_species_rep(bundle, photo_png, *, common, taxon) -> float` — thin wrapper over `species_rep_score`.
  - `qa_reference_image(*, organ: dict, species_rep: float | None) -> dict` — pure combiner: `{"passed": bool, "reasons": list[str]}`; fails if `organ["fruit_only"]` or `organ["category"] == "fragment"` or (`species_rep is not None and species_rep < SPECIES_REP_MIN`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_reference_qa.py
from app import reference_qa as rq

def test_qa_combiner_fails_fruit_only():
    r = rq.qa_reference_image(organ={"fruit_only": True, "category": "isolated-organ"}, species_rep=0.9)
    assert r["passed"] is False and any("fruit" in x for x in r["reasons"])

def test_qa_combiner_fails_low_species_rep():
    r = rq.qa_reference_image(organ={"fruit_only": False, "category": "complete"}, species_rep=0.1)
    assert r["passed"] is False and any("species" in x for x in r["reasons"])

def test_qa_combiner_passes_good():
    r = rq.qa_reference_image(organ={"fruit_only": False, "category": "complete"}, species_rep=0.9)
    assert r["passed"] is True and r["reasons"] == []
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_reference_qa.py -k qa_combiner -q` → FAIL.

- [ ] **Step 3: Implement** (append to `app/reference_qa.py`)

```python
SPECIES_REP_MIN = 0.5  # probe-tuned (Task 5)


def assess_species_rep(bundle, photo_png: bytes, *, common: str, taxon: str) -> float:
    from .species_id import species_rep_score
    return species_rep_score(bundle, photo_png, common=common, taxon=taxon)


def qa_reference_image(*, organ: dict, species_rep: float | None) -> dict:
    reasons: list[str] = []
    if organ.get("fruit_only"):
        reasons.append("fruit-only / isolated-organ reference")
    if organ.get("category") == "fragment":
        reasons.append("fragment — no expected organ visible")
    if species_rep is not None and species_rep < SPECIES_REP_MIN:
        reasons.append(f"low species-representativeness ({species_rep:.2f} < {SPECIES_REP_MIN})")
    return {"passed": not reasons, "reasons": reasons}
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_reference_qa.py -q` → Expected: PASS (all).

- [ ] **Step 5: Commit** — `git add app/reference_qa.py tests/test_reference_qa.py && git commit -m "feat(reference-qa): species-rep gate + combined QA verdict"`

---

### Task 4: `scripts/probe_clip_bioclip.py` — feasibility probe (Component C)

**Files:**

- Create: `scripts/probe_clip_bioclip.py`
- Create: `docs/superpowers/probe_labels.json` (hand-labeled evaluation set manifest)
- Test: `tests/test_probe_aggregation.py`

**Interfaces:**

- Consumes: `species_id`, `reference_qa`, `completeness`.
- Produces:
  - `confusion(records: list[dict]) -> dict` — pure aggregation: for each (mechanism, defect) count TP/FP/TN/FN from `records` (each `{"true": str, "pred_<mech>": str}`), returns nested dict. This is the unit-tested core.
  - `main() -> int` — loads `probe_labels.json`, runs generic-CLIP + BioCLIP + completeness-VLM over each image, writes `docs/superpowers/probe_results_<runid>.md` + `.csv`, prints the decision table (chosen mechanism per in-domain defect; render species-separation GO/NO-GO for #3).

**probe_labels.json shape** (hand-authored, ~20–30 items from existing assets):

```json
[
  {
    "path": "reference/gallery/cucurbita_pepo/2.jpg",
    "taxon": "Cucurbita pepo",
    "common": "pumpkin/gourd",
    "domain": "photo",
    "label": "fruit_only"
  },
  {
    "path": "reference/gallery/zea_mays/1.jpg",
    "taxon": "Zea mays",
    "common": "maize",
    "domain": "photo",
    "label": "good"
  },
  {
    "path": "renders/<output>.png",
    "taxon": "Solanum lycopersicum",
    "common": "tomato",
    "domain": "render",
    "label": "right_species"
  },
  {
    "path": "renders/<output>.png",
    "taxon": "Solanum lycopersicum",
    "common": "tomato",
    "domain": "render",
    "label": "wrong_species",
    "shown_as": "Pinus sylvestris"
  }
]
```

- [ ] **Step 1: Write the failing test** (aggregation is deterministic; the GPU run is not tested here)

```python
# tests/test_probe_aggregation.py
from scripts.probe_clip_bioclip import confusion

def test_confusion_counts_tp_fp():
    recs = [
        {"true": "fruit_only", "pred_clip": "fruit_only"},   # TP
        {"true": "good",       "pred_clip": "fruit_only"},   # FP
        {"true": "good",       "pred_clip": "good"},         # TN
        {"true": "fruit_only", "pred_clip": "good"},         # FN
    ]
    c = confusion(recs)["clip"]["fruit_only"]
    assert (c["tp"], c["fp"], c["tn"], c["fn"]) == (1, 1, 1, 1)
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_probe_aggregation.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `scripts/probe_clip_bioclip.py`** — the `confusion()` aggregator (pure) plus `main()` that: reads labels, loads each PNG via storage, for photo-domain items runs generic-CLIP composition zero-shot + BioCLIP species-rep + completeness-VLM organ-coverage; for render-domain items runs BioCLIP species classification (right vs `shown_as` wrong); records `pred_<mech>` per item; calls `confusion()`; writes markdown + csv; prints a decision table. Guard: no DB writes (read-only). GPU forwards use `species_id.load_model`.

```python
def confusion(records):
    out = {}
    mechs = sorted({k[5:] for r in records for k in r if k.startswith("pred_")})
    defects = sorted({r["true"] for r in records if r["true"] != "good"})
    for m in mechs:
        out[m] = {}
        for d in defects:
            tp = fp = tn = fn = 0
            for r in records:
                pred = r.get(f"pred_{m}")
                if pred is None:
                    continue
                actual_pos, pred_pos = (r["true"] == d), (pred == d)
                tp += actual_pos and pred_pos
                fp += (not actual_pos) and pred_pos
                fn += actual_pos and (not pred_pos)
                tn += (not actual_pos) and (not pred_pos)
            out[m][d] = {"tp": tp, "fp": fp, "tn": tn, "fn": fn}
    return out
```

- [ ] **Step 4: Run unit test** — `python -m pytest tests/test_probe_aggregation.py -q` → PASS.

- [ ] **Step 5: Author `docs/superpowers/probe_labels.json`** — hand-label ~20–30 real images: gallery photos (good / fruit_only / wrong_species / poor_exemplar), a few recon input photos incl. the pre-fix gourd_ref wrong-subject (from git history), and render-sheets of known-species outputs plus deliberately mislabeled ones. Commit the label file.

- [ ] **Step 6: Commit code** — `git add scripts/probe_clip_bioclip.py tests/test_probe_aggregation.py docs/superpowers/probe_labels.json && git commit -m "feat(probe): CLIP/BioCLIP feasibility probe harness + labeled set"`

---

### Task 5: RUN the probe + record the decision (controller milestone — not a code task)

**This is a controller-executed step, not a subagent implementation task.** No TDD.

- [ ] **Step 1:** Probe the full GPU process table (CLAUDE.md), then submit: `job submit --project bio3d-arena --cwd $(pwd) --gpu --needs cuda-8gb --wait -- python scripts/probe_clip_bioclip.py`
- [ ] **Step 2:** Read `docs/superpowers/probe_results_<runid>.md`. Record into the SDD ledger + a memory memo:
  - chosen production mechanism for organ-coverage (completeness-VLM vs generic-CLIP) and for species-rep (BioCLIP vs VLM);
  - `SPECIES_REP_MIN` threshold from the ROC (update Task 3's constant if the default 0.5 is wrong);
  - the **render species-separation GO/NO-GO for #3** (revive `wrong_species`). If NO-GO, #3/#4/#5 stay dead — note it and do not build them.
- [ ] **Step 3:** If the probe changes the default mechanism/threshold, make that one-line config edit + commit. Otherwise proceed.

---

### Task 6: `scripts/qa_reference_gallery.py` + manifest QA integration (Component B)

**Files:**

- Create: `scripts/qa_reference_gallery.py`
- Modify: `app/service.py` (`reference_images_for_task` — filter unpassed items)
- Test: `tests/test_reference_gallery_qa.py`

**Interfaces:**

- Consumes: `reference_qa.assess_organ_coverage`, `reference_qa.assess_species_rep`, `reference_qa.qa_reference_image`, `organ_inventory.inventory_for`.
- Produces: writes a `"passed_qa": bool` + `"qa_reasons": list[str]` field onto each manifest item in-place. `reference_images_for_task` emits only items where `item.get("passed_qa", True)` is truthy (default-true so un-scored legacy manifests are unaffected until scored).

- [ ] **Step 1: Write the failing test** (manifest-level filter — no VLM/GPU)

```python
# tests/test_reference_gallery_qa.py
import json
from app import config, service


def test_reference_images_skips_qa_failed(tmp_path, monkeypatch):
    slug = "solanum_lycopersicum"
    gdir = config.ASSET_DIR / "reference" / "gallery" / slug
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "manifest.json").write_text(json.dumps([
        {"file": "1.jpg", "attribution": "x", "passed_qa": True},
        {"file": "2.jpg", "attribution": "y", "passed_qa": False, "qa_reasons": ["fruit-only"]},
    ]))

    class T: id = -999; title = "Solanum lycopersicum — single-image → 3D reconstruction"
    # no ModelOutput rows for this task id -> only gallery contributes
    from app.database import SessionLocal
    with SessionLocal() as db:
        urls = [r["url"] for r in service.reference_images_for_task(db, T())]
    assert any("1.jpg" in u for u in urls)
    assert not any("2.jpg" in u for u in urls)
```

- [ ] **Step 2: Run to verify it fails** — the current loop has no `passed_qa` filter → FAIL (2.jpg present).

- [ ] **Step 3: Implement the filter** in `reference_images_for_task` — change the manifest loop so a falsy `passed_qa` skips the item:

```python
            for item in json.loads(manifest.read_text()):
                if not item.get("passed_qa", True):
                    continue  # QA-failed reference (fruit-only / wrong-species) — do not show
                rel = f"reference/gallery/{_gallery_slug(task.title)}/{item['file']}"
                out.append({"url": st.url_for(rel), "credit": item.get("attribution", "iNaturalist")})
```

- [ ] **Step 4: Implement `scripts/qa_reference_gallery.py`** — for each gallery dir (or `--slug`), for each manifest item: load the JPEG, resolve binomial from the slug (reverse of `_gallery_slug`; the script takes `--taxon "Binomial"` per slug or maps via a small dict), get `inventory_for(binomial)`, run `assess_organ_coverage` (client from `anthropic.Anthropic(...)`) and, if `species_id.available()`, `assess_species_rep` on a `load_model(<probe-chosen>)` bundle; combine via `qa_reference_image`; write `passed_qa`/`qa_reasons` back into the manifest. Fail-loud if an inventory is missing. Read-only w.r.t. DB (no DB writes → no guard needed, but do not open the study DB).

- [ ] **Step 5: Run** — `python -m pytest tests/test_reference_gallery_qa.py -q` → PASS. Clean up the tmp gallery dir the test created if it wrote under the real ASSET_DIR (prefer monkeypatching `config.ASSET_DIR` to `tmp_path` in the test — do that instead of writing real dirs).

- [ ] **Step 6: Commit** — `git add scripts/qa_reference_gallery.py app/service.py tests/test_reference_gallery_qa.py && git commit -m "feat(reference-qa): gallery QA scorer + passed_qa filter in reference_images_for_task"`

---

### Task 7: Component A — source galleries for the 5 uncovered taxa + QA (controller milestone)

**Not a TDD code task — an execution step** (reuses existing `scripts/source_reference_gallery.py`). Barley (`Hordeum vulgare`) is EXEMPT — no gallery (Task 8 handles its exemption).

- [ ] **Step 1:** Probe GPU table; then source galleries: `python scripts/source_reference_gallery.py --taxa "Solanum lycopersicum,Rosa,Cucurbita pepo,Hericium erinaceus,Morchella esculenta" --n 4`
- [ ] **Step 2:** QA them: `ANTHROPIC_API_KEY=... python scripts/qa_reference_gallery.py --slug solanum_lycopersicum,rosa,cucurbita_pepo,hericium_erinaceus,morchella_esculenta` (GPU host if species-rep enabled, via jobd).
- [ ] **Step 3:** Verify each of the 5 slugs has a `manifest.json` with ≥1 `passed_qa: true` item; licenses pass `check_licenses`. If a taxon has zero passing images (e.g. only fruit-only available), log it and leave that task input-exempt like barley rather than shipping a bad anchor. Record which taxa cleared.
- [ ] **Step 4: Commit** the new manifests + images (respect `.gitignore` — `data/` may be gitignored; if so, the assets live on the runtime volume and only the sourcing/QA record is committed).

---

### Task 8: Component D — recon input subject-verification gate

**Files:**

- Create: `scripts/verify_input_subjects.py`
- Create: `app/input_verify.py` (the reusable predicate)
- Test: `tests/test_input_verify.py`

**Interfaces:**

- Consumes: `species_id`, `flags.record_flag`, `reference_provenance._taxon_of`, `models.ModelOutput`.
- Produces:
  - `INPUT_SUBJECT_SESSION = "input-subject-v1"`.
  - `verify_input_subject(bundle, png: bytes, *, common: str, taxon: str, min_score: float = 0.5) -> tuple[bool, float]` — `(ok, score)` via `species_id.species_rep_score`.
  - `scan_and_flag(db, *, bundle, resolve_png, apply: bool) -> list[dict]` — over every visible `ModelOutput` with a `meta.input_image`, verify; on mismatch record a non-hiding advisory flag (`record_flag(db, oid, INPUT_SUBJECT_SESSION, reason, 10**9)`); return a triage list. Never auto-hides.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_input_verify.py
from app import input_verify

def test_verify_flags_mismatch(monkeypatch):
    monkeypatch.setattr("app.species_id.species_rep_score", lambda *a, **k: 0.05)
    ok, s = input_verify.verify_input_subject(object(), b"\x89PNG", common="tomato", taxon="Solanum lycopersicum")
    assert ok is False and s == 0.05

def test_verify_passes_match(monkeypatch):
    monkeypatch.setattr("app.species_id.species_rep_score", lambda *a, **k: 0.92)
    ok, s = input_verify.verify_input_subject(object(), b"\x89PNG", common="tomato", taxon="Solanum lycopersicum")
    assert ok is True
```

- [ ] **Step 2: Run to verify it fails** — module missing → FAIL.

- [ ] **Step 3: Implement `app/input_verify.py`**

```python
"""Verify a recon INPUT photo actually depicts the claimed species (in-domain for BioCLIP —
input photos are real photos). Mismatch -> non-hiding advisory flag for human triage."""
from __future__ import annotations

INPUT_SUBJECT_SESSION = "input-subject-v1"


def verify_input_subject(bundle, png: bytes, *, common: str, taxon: str, min_score: float = 0.5):
    from .species_id import species_rep_score
    s = species_rep_score(bundle, png, common=common, taxon=taxon)
    return (s >= min_score, s)


def scan_and_flag(db, *, bundle, resolve_png, apply: bool, common_for, min_score: float = 0.5):
    import json
    from sqlalchemy import select
    from . import flags
    from .models import ModelOutput

    ADVISORY = 10**9
    triage = []
    for o in db.execute(select(ModelOutput).where(ModelOutput.hidden_at.is_(None))).scalars():
        img = (json.loads(o.meta_json or "{}") or {}).get("input_image")
        if not img:
            continue
        common, taxon = common_for(o)  # caller maps output -> (common, binomial); None -> skip
        if taxon is None:
            continue
        png = resolve_png(img)
        if png is None:
            continue
        ok, s = verify_input_subject(bundle, png, common=common, taxon=taxon, min_score=min_score)
        if not ok:
            triage.append({"output_id": o.id, "input_image": img, "taxon": taxon, "score": round(s, 3)})
            if apply:
                flags.record_flag(db, o.id, INPUT_SUBJECT_SESSION, f"input subject mismatch ({s:.2f})", ADVISORY)
    if apply:
        db.commit()
    return triage
```

- [ ] **Step 4: Implement `scripts/verify_input_subjects.py`** — builds the `bundle` (`species_id.load_model(<probe-chosen>)`), a `resolve_png` (via storage), a `common_for` mapper (task title → binomial via `title.split("—")[0]`; common name from a small dict), guards the DB (`is_safe_test_db_target` unless `--apply`), calls `scan_and_flag`, prints the triage list. GPU via jobd.

- [ ] **Step 5: Run** — `python -m pytest tests/test_input_verify.py -q` → PASS. Add a DB-level test for `scan_and_flag` mirroring `tests/test_semantic_batch.py`'s advisory-flag test (fake bundle via monkeypatched `species_rep_score`; assert one `OutputFlag`, `hidden_at is None`); use unique slugs + a safe test DB.

- [ ] **Step 6: Commit** — `git add app/input_verify.py scripts/verify_input_subjects.py tests/test_input_verify.py && git commit -m "feat(input-verify): recon input subject-verification advisory gate (non-hiding)"`

---

### Task 9: Component E — exclude recon input from the vote UI (barley-MRI exempt)

**Files:**

- Modify: `app/service.py` (`reference_images_for_task`)
- Modify: `app/config.py` (add exemption set)
- Test: `tests/test_reference_exclusion.py`

**Interfaces:**

- Consumes: `_gallery_slug`, `config.INPUT_REFERENCE_EXEMPT_SLUGS`.
- Produces: `reference_images_for_task` no longer surfaces `meta.input_image` photos EXCEPT for tasks whose gallery-slug is in `INPUT_REFERENCE_EXEMPT_SLUGS` (barley-MRI). Independent gallery (QA-passed) is the fidelity anchor for everything else.

- [ ] **Step 1: Add exemption config** — `app/config.py`: `INPUT_REFERENCE_EXEMPT_SLUGS = {"hordeum_vulgare"}  # barley-MRI: root-system stand-in has no whole-plant gallery`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_reference_exclusion.py
import json
from app import config, service
from app.database import SessionLocal
from app.models import Category, Generator, ModelOutput, Task


def _mk(db, title, slug):
    import uuid; t = uuid.uuid4().hex[:8]
    cat = Category(slug=f"c-{t}", name="c"); gen = Generator(slug=f"g-{t}", name="g", kind="model", paradigm="image_recon")
    db.add_all([cat, gen]); db.flush()
    task = Task(category_id=cat.id, title=title, prompt="p", active=True); db.add(task); db.flush()
    o = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="a.glb", source="bio3d-arena",
                    meta_json=json.dumps({"input_image": f"reference/{slug}_ref.jpg"}))
    db.add(o); db.flush()
    return task


def test_non_exempt_task_drops_input_photo(monkeypatch):
    monkeypatch.setattr("app.reference_provenance.cleared_reference_taxa", lambda: {"tomato"})
    with SessionLocal() as db:
        task = _mk(db, "Solanum lycopersicum — single-image → 3D reconstruction", "tomato")
        urls = [r["url"] for r in service.reference_images_for_task(db, task)]
        assert not any("_ref.jpg" in u for u in urls)  # input photo excluded
        db.rollback()


def test_barley_task_keeps_input_photo(monkeypatch):
    monkeypatch.setattr("app.reference_provenance.cleared_reference_taxa", lambda: {"hordeum"})
    with SessionLocal() as db:
        task = _mk(db, "Hordeum vulgare — barley root system (3D MRI)", "hordeum")
        urls = [r["url"] for r in service.reference_images_for_task(db, task)]
        assert any("_ref.jpg" in u for u in urls)  # barley exempt: input retained
        db.rollback()
```

- [ ] **Step 3: Run to verify it fails** — current code shows input for both → `test_non_exempt_task_drops_input_photo` FAILS.

- [ ] **Step 4: Implement** — gate the input-photo loop on the exemption in `reference_images_for_task`:

```python
    cleared_taxa = cleared_reference_taxa()
    exempt = _gallery_slug(task.title) in config.INPUT_REFERENCE_EXEMPT_SLUGS
    if exempt:
        for o in db.execute(
            select(ModelOutput).where(ModelOutput.task_id == task.id, ModelOutput.hidden_at.is_(None))
        ).scalars():
            try:
                img = (json.loads(o.meta_json or "{}") or {}).get("input_image")
            except (ValueError, TypeError):
                continue
            if img and img not in seen and _taxon_of(img) in cleared_taxa:
                seen.add(img)
                out.append({"url": st.url_for(img), "credit": "reconstruction input photo"})
    # non-exempt tasks: recon input is NOT a reference (bias/circularity); gallery is the anchor.
```

- [ ] **Step 5: Run** — `python -m pytest tests/test_reference_exclusion.py -q` → PASS (2 passed). Update any existing test that asserted input photos appear for non-exempt tasks (grep `reconstruction input photo` in tests/).

- [ ] **Step 6: Full-suite gate** — `python -m pytest -q -p no:cacheprovider` → Expected: all pass. Fix any reference-gallery tests that assumed the old input-in-gallery behavior.

- [ ] **Step 7: Commit** — `git add app/service.py app/config.py tests/test_reference_exclusion.py && git commit -m "feat(reference): exclude recon input from vote UI; barley-MRI exempt (Component E)"`

---

## Self-Review

- **Spec coverage:** A→Task 7; B→Tasks 2,3,6; C→Tasks 4,5; D→Task 8; E→Task 9; species-rep judge (both CLIP+BioCLIP, prefer BioCLIP)→Tasks 1,4,5; #3 probe-gate→Task 5 Step 2; #4/#5 out of scope (Global Constraints). Covered.
- **Placeholder scan:** the two controller milestones (Tasks 5, 7) are execution steps by design, not code tasks — their "no TDD" is intentional, not a placeholder. All code tasks carry real code + tests.
- **Type consistency:** `bundle` = `load_model()`'s 4-tuple throughout; `assess_organ_coverage` returns the dict consumed by `qa_reference_image(organ=...)`; `species_rep_score` signature identical in `species_id`, `reference_qa`, `input_verify`. Manifest `passed_qa` written by Task 6 script, read by Task 6 service filter. `_taxon_of` / `cleared_reference_taxa` reused unchanged in Task 9.
- **Known prerequisite risk:** Tasks 2/6 require `inventory_for(binomial)` non-None for tomato/rose/gourd/hericium/morel — verified at Task 2 Step 4 (fail-loud). Barley intentionally has no gallery/inventory dependency (exempt).
