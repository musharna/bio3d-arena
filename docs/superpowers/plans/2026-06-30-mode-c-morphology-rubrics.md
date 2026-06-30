# Mode-C Morphology Rubrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 6 study taxa's literature-derived trait rubrics with hand-authored, grounded, visually-judgeable morphology traits, gated by a new `is_visually_judgeable` validator, then re-judge and recalibrate.

**Architecture:** A pure validator (`is_visually_judgeable`) in `app/traits.py` codifies the "visible on a static render of one normal specimen" bar. A new `app/trait_morphology.py` holds the authored per-taxon trait data + an assembler that merges a Wikidata db-tier with the authored ref-tier and validates everything. A new `scripts/author_morphology_rubrics.py` loads the validated rubrics into the study DB (reusing `upsert_rubric`/`_resolve_task_ids`), with resolve-verified citations. The validator is also wired into the legacy `build_trait_rubrics.py` (defense in depth). Re-judge/relabel/calibrate reuse existing tools unchanged.

**Tech Stack:** Python 3.13, SQLAlchemy (SQLite), FastAPI (labeler, unchanged), pytest, urllib (citation resolve), Wikidata SPARQL.

## Global Constraints

- Trait schema is unchanged: `{key, trait_class, type, expected, visual, source_tier, citation}` stored in `TraitRubric.traits_json`. Verdict vocab unchanged (`present_correct|present_wrong|absent|not_assessable`).
- `trait_class` ∈ `app.traits.SCORED_CLASSES` = `{habit, organ_shape, phyllotaxy, inflorescence, color, presence, proportion}`.
- Every authored trait MUST pass BOTH `validate_trait` AND `is_visually_judgeable`, and carry a non-empty `citation`. `source_tier ∈ {db, ref}` for morphology (`llm` retained only for the legacy path).
- No fabricated citations: ref-tier citation = a resolvable Wikispecies URL `https://species.wikimedia.org/wiki/<Taxon>` (spaces→underscores); db-tier citation = the Wikidata Q-ID URL produced by `wikidata_traits`. Both are HTTP-resolve-verified at `--commit`.
- Tests run with DEFAULT env only via `.venv/bin/python -m pytest`. NEVER run pytest with `BIO3D_DATABASE_URL` or `BIO3D_DATA_DIR` pointing at the study DB — it wipes it (prior incident).
- Scripts that touch study data run with `BIO3D_DATABASE_URL=sqlite:///$(pwd)/data/study/arena-study.db` and `BIO3D_DATA_DIR=$(pwd)/.claude/worktrees/bio3d-arena-mvp/data`.
- DB-mutating steps (author `--commit`, re-judge, calibrate) are operator-gated and snapshot the study DB first.
- Commit only when the operator asks (the implementer commits per-task as the SDD skill directs; the transition runbook in Task 5 is operator-run, not auto-executed).

---

### Task 1: `is_visually_judgeable` validator + `ref` source_tier

**Files:**

- Modify: `app/traits.py` (add validator + reject-rule constants)
- Modify: `scripts/build_trait_rubrics.py:38` (allow `source_tier == "ref"`)
- Test: `tests/test_visually_judgeable.py` (create)

**Interfaces:**

- Produces: `is_visually_judgeable(trait: dict) -> bool` and `judgeable_reason(trait: dict) -> str | None` (None when admissible, else the reject reason). Consumed by Tasks 2, 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_visually_judgeable.py
from app.traits import is_visually_judgeable, judgeable_reason


def _t(expected, key="k", trait_class="organ_shape", taxon="Solanum lycopersicum"):
    return {"key": key, "trait_class": trait_class, "expected": expected, "taxon": taxon}


def test_accepts_concrete_static_morphology():
    assert is_visually_judgeable(_t("red berry"))
    assert is_visually_judgeable(_t("alternate", trait_class="phyllotaxy"))
    assert is_visually_judgeable(_t("climber", trait_class="habit"))
    assert is_visually_judgeable(_t("trifoliate"))


def test_rejects_empty_or_unstated():
    assert not is_visually_judgeable(_t(""))
    assert not is_visually_judgeable(_t("not explicitly stated"))
    assert judgeable_reason(_t("unknown")) is not None


def test_rejects_temporal():
    assert not is_visually_judgeable(_t("accelerated floral transition"))
    assert not is_visually_judgeable(_t("earlier flowering", trait_class="presence"))
    assert not is_visually_judgeable(_t("degreening during maturation", trait_class="color"))


def test_rejects_comparative_without_baseline():
    assert not is_visually_judgeable(_t("altered leaf morphology"))
    assert not is_visually_judgeable(_t("reduced height", trait_class="proportion"))
    assert not is_visually_judgeable(_t("thickened"))


def test_rejects_microscopic_or_internal():
    assert not is_visually_judgeable(_t("glandular (multicellular)"))
    assert not is_visually_judgeable(_t("lower RGB brightness", trait_class="color"))
    assert not is_visually_judgeable(_t("variable (mutations affecting morphology)", key="ovary_morphology"))


def test_rejects_wrong_taxon_token():
    assert not is_visually_judgeable(_t("post-genital fusion", key="commelina_erecta_sheath", taxon="Zea mays"))
    assert not is_visually_judgeable(_t("continuous variation in circularity", key="flake_circularity", taxon="Zea mays"))


def test_rejects_vague():
    assert not is_visually_judgeable(_t("diversified inflorescence architecture", trait_class="inflorescence"))
    assert not is_visually_judgeable(_t("highly complex geometrical structures", trait_class="habit"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_visually_judgeable.py -q`
Expected: FAIL with `ImportError: cannot import name 'is_visually_judgeable'`

- [ ] **Step 3: Write the implementation**

Add to `app/traits.py` (after the `VERDICTS` definition):

```python
import re

# Reject patterns derived from the 2026-06-30 judgeability audit. A trait is admissible
# only if it names a static, macroscopic, external, absolute, correctly-attributed,
# concrete morphological feature. Matched against f"{key} {expected}".lower().
_REJECT_EMPTY = {"", "not explicitly stated", "unknown", "n/a", "na", "none", "variable"}
_REJECT_PATTERNS = [
    (r"\baccelerat|transition|recurren|\bonset\b|\btiming\b|rate of|maturation|ripening process|"
     r"senescence|degreening|flowering time|earlier flower|delayed flower|bud stage", "temporal/process"),
    (r"\baltered|\bchange[sd]?\b|reduced|increased|smaller|larger|thicken|prolong|extended|"
     r"superior|affected|malformed|\bdefect|disruption|loss of|abnormal", "comparative without baseline"),
    (r"trichome|glandular|multicellular|\bcellular|stomat|epiderm|\bmicro|ovary|carpel|"
     r"seed coat|seeds? per|abscission|\bpollen\b|meristem|\brgb\b|brightness|reflectan|quantif",
     "microscopic/internal/instrument"),
    (r"\bcommelina|poaceae|\bcannabis|\bflake\b|circularity|lithic|arabidopsis", "wrong-taxon/domain token"),
    (r"diversif|\bcomplex|architecture|substantial variation|depending on|% of individual|"
     r"across .*(combination|cultivar|hybrid|accession)", "vague/population"),
]


def judgeable_reason(trait: dict) -> str | None:
    """Return None if the trait is visually judgeable on a static render of one normal
    specimen, else a short reason string. See the 2026-06-30 morphology-rubrics spec."""
    expected = (trait.get("expected") or "").strip().lower()
    if expected in _REJECT_EMPTY:
        return "no concrete value"
    blob = f"{trait.get('key', '')} {expected}".lower()
    taxon = (trait.get("taxon") or "").lower()
    for pat, reason in _REJECT_PATTERNS:
        m = re.search(pat, blob)
        if m:
            tok = m.group(0).lower()
            # allow an "off-taxon" token only when it actually matches this rubric's taxon
            if reason == "wrong-taxon/domain token" and tok and tok in taxon:
                continue
            return reason
    return None


def is_visually_judgeable(trait: dict) -> bool:
    return judgeable_reason(trait) is None
```

In `scripts/build_trait_rubrics.py`, change the `source_tier` check at line 38:

```python
    if t["source_tier"] not in ("db", "llm", "ref"):
        raise ValueError(f"bad source_tier {t['source_tier']!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_visually_judgeable.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add app/traits.py scripts/build_trait_rubrics.py tests/test_visually_judgeable.py
git commit -m "feat(mode-c): is_visually_judgeable trait validator + ref source_tier"
```

---

### Task 2: Wire the judgeability gate into the legacy rubric path

**Files:**

- Modify: `scripts/build_trait_rubrics.py:27-41` (`validate_trait` calls `is_visually_judgeable`)
- Test: `tests/test_build_trait_rubrics.py` (add one test)

**Interfaces:**

- Consumes: `is_visually_judgeable`, `judgeable_reason` from Task 1.
- Produces: `validate_trait` now also rejects non-judgeable traits (defense in depth for any future literature run).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_build_trait_rubrics.py
import pytest
from scripts.build_trait_rubrics import validate_trait


def test_validate_trait_rejects_non_judgeable():
    bad = {
        "key": "flowering_time_early", "trait_class": "presence", "type": "qualitative",
        "expected": "earlier flowering (promoted)", "visual": "n/a",
        "source_tier": "ref", "citation": "https://example.org",
    }
    with pytest.raises(ValueError, match="not visually judgeable"):
        validate_trait(bad)


def test_validate_trait_accepts_judgeable():
    ok = {
        "key": "fruit_shape", "trait_class": "organ_shape", "type": "qualitative",
        "expected": "globose berry", "visual": "round fleshy fruit",
        "source_tier": "ref", "citation": "https://species.wikimedia.org/wiki/Solanum_lycopersicum",
    }
    validate_trait(ok)  # no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_build_trait_rubrics.py -k judgeable -q`
Expected: FAIL (no "not visually judgeable" raised)

- [ ] **Step 3: Write the implementation**

In `scripts/build_trait_rubrics.py`, extend `validate_trait` (after the citation check at line 41):

```python
    from app.traits import is_visually_judgeable, judgeable_reason

    if not is_visually_judgeable(t):
        raise ValueError(f"trait not visually judgeable ({judgeable_reason(t)}): {t.get('key')}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_build_trait_rubrics.py -q`
Expected: PASS (all, including the 2 new). If any pre-existing fixture trait is now rejected, that fixture used a non-judgeable expected value — update the fixture's `expected` to a concrete one (e.g. `"globose berry"`); do not weaken the gate.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_trait_rubrics.py tests/test_build_trait_rubrics.py
git commit -m "feat(mode-c): gate legacy rubric path on is_visually_judgeable"
```

---

### Task 3: Authored morphology data + assembler (`app/trait_morphology.py`)

**Files:**

- Create: `app/trait_morphology.py`
- Test: `tests/test_trait_morphology.py` (create)

**Interfaces:**

- Consumes: `is_visually_judgeable` (Task 1), `wikidata_traits(taxon, *, sparql_fn)` and `SCORED_CLASSES` from `app.trait_sources`/`app.traits`.
- Produces:
  - `MORPHOLOGY_TRAITS: dict[str, list[dict]]` — taxon → authored ref-tier traits (each WITHOUT `citation`/`source_tier`, which the assembler stamps).
  - `WIKISPECIES_URL(taxon: str) -> str`.
  - `build_morphology_rubric(taxon: str, *, sparql_fn) -> list[dict]` — returns fully-stamped, validated traits (db-tier from Wikidata merged ahead of authored ref-tier, deduped by `(trait_class, expected.lower())`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trait_morphology.py
from app.traits import is_visually_judgeable
from app.trait_morphology import (
    MORPHOLOGY_TRAITS,
    WIKISPECIES_URL,
    build_morphology_rubric,
)

TAXA = ["Solanum lycopersicum", "Zea mays", "Pinus sylvestris", "Rosa",
        "Glycine max", "Arabidopsis thaliana"]


def test_all_six_taxa_present_with_enough_traits():
    assert set(MORPHOLOGY_TRAITS) == set(TAXA)
    for taxon, traits in MORPHOLOGY_TRAITS.items():
        assert 8 <= len(traits) <= 12, f"{taxon} has {len(traits)} traits"


def test_every_authored_trait_is_judgeable_and_scoreable():
    from app.traits import SCORED_CLASSES
    for taxon, traits in MORPHOLOGY_TRAITS.items():
        for t in traits:
            t2 = {**t, "taxon": taxon}
            assert is_visually_judgeable(t2), f"{taxon}/{t['key']} not judgeable"
            assert t["trait_class"] in SCORED_CLASSES


def test_pine_has_no_flower_or_fruit_traits():
    keys = " ".join(t["key"] for t in MORPHOLOGY_TRAITS["Pinus sylvestris"]).lower()
    assert "flower" not in keys and "fruit" not in keys


def test_wikispecies_url():
    assert WIKISPECIES_URL("Solanum lycopersicum") == "https://species.wikimedia.org/wiki/Solanum_lycopersicum"
    assert WIKISPECIES_URL("Rosa") == "https://species.wikimedia.org/wiki/Rosa"


def test_build_merges_db_tier_and_stamps_citations():
    # stub Wikidata: one fruit-type (P4000) value for tomato
    def fake_sparql(taxon):
        return {"qid": "Q23501", "props": {"P4000": "berry"}}
    traits = build_morphology_rubric("Solanum lycopersicum", sparql_fn=fake_sparql)
    assert any(t["source_tier"] == "db" for t in traits)         # Wikidata contributed
    assert all((t.get("citation") or "").strip() for t in traits)  # everything cited
    assert all(t["source_tier"] in ("db", "ref") for t in traits)
    # ref-tier traits cite Wikispecies
    refs = [t for t in traits if t["source_tier"] == "ref"]
    assert all(t["citation"] == "https://species.wikimedia.org/wiki/Solanum_lycopersicum" for t in refs)


def test_build_dedups_db_over_ref():
    # authored ref has an organ_shape fruit; Wikidata also returns a fruit type → one wins, no dup
    def fake_sparql(taxon):
        return {"qid": "Q23501", "props": {"P4000": "berry"}}
    traits = build_morphology_rubric("Solanum lycopersicum", sparql_fn=fake_sparql)
    keys = [(t["trait_class"], t["expected"].lower()) for t in traits]
    assert len(keys) == len(set(keys)), "duplicate (trait_class, expected)"


def test_build_no_wikidata_still_valid():
    traits = build_morphology_rubric("Pinus sylvestris", sparql_fn=lambda taxon: None)
    assert len(traits) >= 8
    assert all(t["source_tier"] == "ref" for t in traits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trait_morphology.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.trait_morphology'`

- [ ] **Step 3: Write the implementation**

Create `app/trait_morphology.py`:

```python
"""Authored, visually-judgeable morphology rubrics for the 6 Mode-C study taxa.

These replace the literature-extraction trait set (which produced genetics/temporal/
microscopic/wrong-organism traits unfit for judging a static 3D model). Every trait here
is a static, macroscopic, externally-visible, absolute morphological feature, authored
from descriptive botany and grounded: db-tier values come from Wikidata structured
morphology (via build_morphology_rubric's sparql_fn); the remaining ref-tier traits cite
the taxon's Wikispecies page (resolve-verified at load). See the 2026-06-30 spec."""

from __future__ import annotations

from .trait_sources import wikidata_traits
from .traits import is_visually_judgeable, judgeable_reason

WIKISPECIES = "https://species.wikimedia.org/wiki/"


def WIKISPECIES_URL(taxon: str) -> str:
    return WIKISPECIES + taxon.replace(" ", "_")


# Authored ref-tier traits. Each: key, trait_class, type, expected, visual.
# (source_tier + citation are stamped by build_morphology_rubric.)
def _t(key, cls, expected, visual):
    return {"key": key, "trait_class": cls, "type": "qualitative",
            "expected": expected, "visual": visual}


MORPHOLOGY_TRAITS: dict[str, list[dict]] = {
    "Solanum lycopersicum": [
        _t("plant_habit", "habit", "sprawling herbaceous plant", "weak sprawling/decumbent stems, not an upright woody tree"),
        _t("leaf_form", "organ_shape", "pinnately compound leaf", "leaf divided into leaflets along a central axis"),
        _t("fruit_form", "organ_shape", "globose berry", "round fleshy fruit"),
        _t("fruit_color_ripe", "color", "red ripe fruit", "ripe fruit is red"),
        _t("flower_color", "color", "yellow flower", "small yellow star-shaped flowers"),
        _t("inflorescence_form", "inflorescence", "branched cyme", "small branched flower clusters off the stem"),
        _t("leaf_arrangement", "phyllotaxy", "alternate leaves", "leaves attach singly, alternating along the stem"),
        _t("fruit_present", "presence", "fruits present", "one or more fruits on the plant"),
        _t("plant_proportion", "proportion", "medium height plant", "knee-to-head height, taller than a seedling, not a tree"),
    ],
    "Zea mays": [
        _t("plant_habit", "habit", "tall single-culm grass", "one thick erect unbranched stalk"),
        _t("leaf_form", "organ_shape", "long linear leaf", "long narrow strap-like blades"),
        _t("leaf_arrangement", "phyllotaxy", "alternate distichous leaves", "leaves alternate in two ranks up the culm"),
        _t("tassel_form", "inflorescence", "terminal branched tassel", "branched spike of male flowers at the very top"),
        _t("ear_present", "presence", "lateral ear present", "a cob/ear emerging from a leaf axil on the side of the stalk"),
        _t("foliage_color", "color", "green foliage", "green leaves"),
        _t("stem_form", "organ_shape", "single solid unbranched culm", "one thick stem, no woody branching"),
        _t("plant_proportion", "proportion", "tall plant", "tall, much taller than wide"),
    ],
    "Pinus sylvestris": [
        _t("plant_habit", "habit", "excurrent conifer tree", "single straight trunk with a conical/columnar crown"),
        _t("needle_form", "organ_shape", "needle leaf", "thin needle-like leaves, not broad blades"),
        _t("needle_arrangement", "phyllotaxy", "needles in fascicles of two", "needles held in paired bundles"),
        _t("cone_form", "organ_shape", "ovoid woody cone", "egg/cone-shaped woody cones"),
        _t("cone_present", "presence", "cones present", "cones on the branches"),
        _t("foliage_color", "color", "blue-green foliage", "glaucous blue-green needles"),
        _t("bark_color", "color", "orange-brown upper bark", "orange/reddish flaky bark on the upper trunk"),
        _t("plant_proportion", "proportion", "tall tree", "a tall tree, much taller than a shrub"),
    ],
    "Rosa": [
        _t("plant_habit", "habit", "woody shrub with arching canes", "woody shrub or climber with arching thorny stems"),
        _t("leaf_form", "organ_shape", "odd-pinnate serrate leaf", "compound leaf with toothed leaflets"),
        _t("fruit_form", "organ_shape", "rose hip", "rounded fleshy hips"),
        _t("flower_pigmentation", "color", "pigmented petals", "showy colored petals (pink/red/white/yellow), not green"),
        _t("inflorescence_form", "inflorescence", "solitary or small corymb", "flowers single or in small clusters at shoot tips"),
        _t("prickles_present", "presence", "prickles present on stems", "thorns/prickles along the canes"),
        _t("leaf_arrangement", "phyllotaxy", "alternate leaves", "leaves attach singly, alternating along the stem"),
        _t("plant_proportion", "proportion", "shrub-sized plant", "shrub scale, not a tall tree or a tiny herb"),
    ],
    "Glycine max": [
        _t("plant_habit", "habit", "erect bushy herb", "upright branched hairy herbaceous plant"),
        _t("leaf_form", "organ_shape", "trifoliate leaf", "leaves with three leaflets"),
        _t("inflorescence_form", "inflorescence", "axillary raceme", "small flower clusters in the leaf axils"),
        _t("pod_present", "presence", "pods present", "fuzzy seed pods on the plant"),
        _t("flower_color", "color", "white to purple flower", "small white or purple pea-like flowers"),
        _t("leaf_arrangement", "phyllotaxy", "alternate leaves", "leaves attach singly, alternating along the stem"),
        _t("pod_form", "organ_shape", "elongate pod", "narrow slightly curved pods"),
        _t("plant_proportion", "proportion", "medium height plant", "knee-to-waist height bush"),
    ],
    "Arabidopsis thaliana": [
        _t("plant_habit", "habit", "rosette with erect flowering stalk", "low basal leaf rosette plus a thin tall flowering stem"),
        _t("rosette_leaf_form", "organ_shape", "spatulate rosette leaf", "small spoon/oblong-shaped basal leaves"),
        _t("inflorescence_form", "inflorescence", "raceme", "flowers spaced along the upper stem"),
        _t("flower_color", "color", "white flower", "small white four-petalled flowers"),
        _t("fruit_form", "organ_shape", "slender silique", "thin elongated seed pods"),
        _t("silique_present", "presence", "siliques present", "narrow upright pods along the stem"),
        _t("leaf_arrangement", "phyllotaxy", "basal rosette leaves", "most leaves in a flat basal rosette"),
        _t("plant_proportion", "proportion", "small plant", "small plant, ankle-to-knee height"),
    ],
}


def build_morphology_rubric(taxon: str, *, sparql_fn) -> list[dict]:
    """Assemble taxon's rubric: Wikidata db-tier traits first, then authored ref-tier
    traits, deduped on (trait_class, expected.lower()) with db preferred. Every trait is
    stamped with source_tier+citation and validated (schema-judgeable). Raises on an
    unknown taxon or a non-judgeable authored trait (author bug)."""
    if taxon not in MORPHOLOGY_TRAITS:
        raise ValueError(f"no authored morphology for {taxon!r}")
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for t in wikidata_traits(taxon, sparql_fn=sparql_fn):  # already stamped source_tier=db + citation
        key = (t["trait_class"], (t["expected"] or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(t)

    for base in MORPHOLOGY_TRAITS[taxon]:
        t = {**base, "source_tier": "ref", "citation": WIKISPECIES_URL(taxon)}
        key = (t["trait_class"], (t["expected"] or "").strip().lower())
        if key in seen:
            continue
        reason = judgeable_reason({**t, "taxon": taxon})
        if reason is not None:
            raise ValueError(f"authored trait {t['key']!r} not judgeable: {reason}")
        seen.add(key)
        out.append(t)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trait_morphology.py -q`
Expected: PASS (7 passed). If `test_every_authored_trait_is_judgeable_and_scoreable` fails, the named trait's `expected` tripped a reject pattern — reword it to a concrete static value (do not relax the validator).

- [ ] **Step 5: Commit**

```bash
git add app/trait_morphology.py tests/test_trait_morphology.py
git commit -m "feat(mode-c): authored grounded morphology rubrics for 6 study taxa"
```

---

### Task 4: Authoring/load script (`scripts/author_morphology_rubrics.py`)

**Files:**

- Create: `scripts/author_morphology_rubrics.py`
- Test: `tests/test_author_morphology_rubrics.py` (create)

**Interfaces:**

- Consumes: `build_morphology_rubric` (Task 3); `upsert_rubric`, `_resolve_task_ids`, `_live_wikidata_sparql`, `_resolve_url` from `scripts/build_trait_rubrics.py`; `MORPHOLOGY_TRAITS`.
- Produces: `assemble_all(*, sparql_fn) -> dict[str, list[dict]]`; `verify_resolves(traits, *, resolve_fn) -> None`; a CLI `--dry-run`/`--commit`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_author_morphology_rubrics.py
import pytest
from scripts.author_morphology_rubrics import assemble_all, verify_resolves


def test_assemble_all_covers_six_taxa():
    res = assemble_all(sparql_fn=lambda taxon: None)
    assert len(res) == 6
    for taxon, traits in res.items():
        assert len(traits) >= 8
        assert all(t["source_tier"] in ("db", "ref") for t in traits)


def test_verify_resolves_passes_when_all_ok():
    traits = [{"key": "a", "citation": "https://x"}, {"key": "b", "citation": "https://y"}]
    verify_resolves(traits, resolve_fn=lambda url: True)  # no raise


def test_verify_resolves_fails_loud_on_dead_link():
    traits = [{"key": "a", "citation": "https://x"}, {"key": "b", "citation": "https://dead"}]
    with pytest.raises(ValueError, match="did not resolve"):
        verify_resolves(traits, resolve_fn=lambda url: url != "https://dead")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_author_morphology_rubrics.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.author_morphology_rubrics'`

- [ ] **Step 3: Write the implementation**

Create `scripts/author_morphology_rubrics.py`:

```python
"""Load the authored morphology rubrics (app/trait_morphology.py) into the study DB,
replacing the literature-derived TraitRubric rows for the 6 study taxa.

--dry-run prints the per-taxon trait table + db/ref tier counts and does NOT write or hit
the network for verification. --commit assembles with the live Wikidata SPARQL backbone,
HTTP-resolve-verifies every citation (fail-loud on a dead link), and upserts the rubrics.
Snapshot the study DB before --commit (operator). Mirrors scripts/build_trait_rubrics.py."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.trait_morphology import MORPHOLOGY_TRAITS, build_morphology_rubric  # noqa: E402
from scripts.build_trait_rubrics import (  # noqa: E402,F401
    _live_wikidata_sparql,
    _resolve_task_ids,
    _resolve_url,
    upsert_rubric,
)


def assemble_all(*, sparql_fn) -> dict:
    """taxon -> assembled+validated trait list, for every authored taxon."""
    return {taxon: build_morphology_rubric(taxon, sparql_fn=sparql_fn) for taxon in MORPHOLOGY_TRAITS}


def verify_resolves(traits, *, resolve_fn) -> None:
    """Fail loud if any trait's citation URL does not resolve (HTTP 200)."""
    for t in traits:
        url = (t.get("citation") or "").strip()
        if not url or not resolve_fn(url):
            raise ValueError(f"citation did not resolve for {t.get('key')!r}: {url!r}")


def _print_table(rubrics: dict) -> None:
    for taxon, traits in rubrics.items():
        tiers = {}
        for t in traits:
            tiers[t["source_tier"]] = tiers.get(t["source_tier"], 0) + 1
        print(f"\n{taxon}  ({len(traits)} traits, tiers={tiers})")
        for t in traits:
            print(f"  [{t['source_tier']}] {t['trait_class']:13} {t['key']:22} = {t['expected']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--commit", action="store_true", help="verify citations live + write to DB")
    args = p.parse_args(argv)

    if not args.commit:
        rubrics = assemble_all(sparql_fn=lambda taxon: None)  # dry: skip network
        _print_table(rubrics)
        total = sum(len(v) for v in rubrics.values())
        print(f"\nDRY RUN — {total} traits across {len(rubrics)} taxa, nothing written. "
              "Re-run with --commit (after snapshotting the study DB) to verify + load.")
        return 0

    from app.database import SessionLocal

    rubrics = assemble_all(sparql_fn=_live_wikidata_sparql)
    for traits in rubrics.values():
        verify_resolves(traits, resolve_fn=_resolve_url)
    db = SessionLocal()
    try:
        task_ids = _resolve_task_ids(db, {taxon: None for taxon in rubrics})
        for taxon, traits in rubrics.items():
            upsert_rubric(db, taxon, task_ids[taxon], traits)
            print(f"wrote {taxon} (task={task_ids[taxon]}): {len(traits)} traits")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_author_morphology_rubrics.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Verify `_resolve_url` exists in build_trait_rubrics.py**

Run: `.venv/bin/python -c "from scripts.build_trait_rubrics import _resolve_url, _resolve_task_ids, _live_wikidata_sparql, upsert_rubric; print('ok')"`
Expected: `ok`. If `_resolve_url` is absent, add to `scripts/build_trait_rubrics.py` (it has `_http_json`/urllib already):

```python
def _resolve_url(url: str, timeout: int = 20) -> bool:
    """True if the URL resolves (HTTP < 400). Used to verify reference citations."""
    req = _urlrequest.Request(url, method="HEAD", headers={"User-Agent": _UA})
    try:
        with _urlrequest.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status < 400
    except _urlerror.HTTPError as e:
        return e.code < 400
    except Exception:
        return False
```

- [ ] **Step 6: Real-execution dry-run (no DB writes, no network)**

Run: `.venv/bin/python scripts/author_morphology_rubrics.py`
Expected: a per-taxon table (~8–9 traits each, all `[ref]` since dry-run skips Wikidata) and `DRY RUN — N traits across 6 taxa, nothing written.`

- [ ] **Step 7: Commit**

```bash
git add scripts/author_morphology_rubrics.py tests/test_author_morphology_rubrics.py
git commit -m "feat(mode-c): morphology-rubric authoring/load script (dry-run + commit)"
```

---

### Task 5: Transition runbook (operator-gated; NOT auto-run)

**Files:**

- Modify: `docs/superpowers/plans/2026-06-30-mode-c-morphology-rubrics.md` (this file — the runbook lives here; nothing to execute during implementation)

This task ships no code and is executed by the operator AFTER Tasks 1–4 are merged and the operator approves the spend. The implementer marks it complete once Tasks 1–4 are green; the steps below are run by the operator with explicit go-ahead (they cost VLM spend and reset labeling).

Runbook (operator runs each line, study env):

```bash
# 0. snapshot
cp data/study/arena-study.db data/backups/arena-study-premorph-$(date +%Y%m%d-%H%M%S).db

# 1. load clean rubrics (verifies citations live, replaces the 6 rubrics)
BIO3D_DATABASE_URL="sqlite:///$(pwd)/data/study/arena-study.db" \
  .venv/bin/python scripts/author_morphology_rubrics.py --commit

# 2. clear stale verdicts/scores for a clean re-judge (Mode-C tables only)
BIO3D_DATABASE_URL="sqlite:///$(pwd)/data/study/arena-study.db" \
  .venv/bin/python -c "from app.database import SessionLocal; from app.models import TraitVerdict, TraitScore, TraitCalibration; db=SessionLocal(); [db.query(m).delete() for m in (TraitVerdict, TraitScore, TraitCalibration)]; db.commit(); print('cleared')"

# 3. re-judge on the clean rubrics (VLM spend ~$3-5)
BIO3D_DATABASE_URL="sqlite:///$(pwd)/data/study/arena-study.db" \
BIO3D_DATA_DIR="$(pwd)/.claude/worktrees/bio3d-arena-mvp/data" \
  .venv/bin/python scripts/trait_judge.py

# 4. reset labeling + regenerate the blind sample
rm -f data/study/calibration_labels_filled.csv
BIO3D_DATABASE_URL="sqlite:///$(pwd)/data/study/arena-study.db" \
BIO3D_DATA_DIR="$(pwd)/.claude/worktrees/bio3d-arena-mvp/data" \
  .venv/bin/python scripts/calibration_labels.py export --out data/study/calibration_labels_2026-07-XX.csv

# 5. restart labeler against the new sample (see scripts/label_server.py header), label, then:
BIO3D_DATABASE_URL="sqlite:///$(pwd)/data/study/arena-study.db" \
  .venv/bin/python scripts/calibration_labels.py ingest <filled.csv> --commit
```

- [ ] **Step 1: Mark complete when Tasks 1–4 are merged and green; surface the runbook to the operator for go/no-go on spend.**

---

## Self-Review

**1. Spec coverage:**

- Trait standard (`is_visually_judgeable`, all 6 reject rules) → Task 1. ✅
- Schema unchanged + `source_tier` ref → Task 1 (validator) + Task 3 (data). ✅
- Wikidata-first + cited gaps grounding → Task 3 `build_morphology_rubric` (db-tier merge) + Wikispecies ref citations; live verify → Task 4. ✅
- Per-taxon sets ~8–12, pine gymnosperm, class balance → Task 3 data + tests. ✅
- Harden legacy path → Task 2. ✅
- Architecture (new module + script, reuse downstream) → Tasks 3–4. ✅
- Transition (snapshot/replace/re-judge/relabel/calibrate) → Task 5 runbook. ✅
- Testing incl real-execution dry-run → Tasks 1–4. ✅

**2. Placeholder scan:** No TBD/TODO; all code blocks complete; the only `<...>` are operator-supplied runtime values in the Task 5 runbook (filled CSV path, dated export name) — appropriate for an operator runbook. ✅

**3. Type consistency:** `is_visually_judgeable(trait)->bool`, `judgeable_reason(trait)->str|None`, `build_morphology_rubric(taxon, *, sparql_fn)->list[dict]`, `assemble_all(*, sparql_fn)`, `verify_resolves(traits, *, resolve_fn)`, `upsert_rubric(db, taxon, task_id, traits)` — used identically across tasks. `sparql_fn` returns `{"qid","props":{pid:val}}|None` matching `wikidata_traits`/`_live_wikidata_sparql`. ✅
