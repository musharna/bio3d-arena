# Mode-C Rubric-Source Backends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the two rubric-trait-source backends (`fetch_db_traits`, `draft_llm_traits`) that `scripts/build_trait_rubrics.py` currently ships as `NotImplementedError` stubs, so Mode-C rubrics can be authored and Task 7 (the live trait-judge pass) can run.

**Architecture:** A new pure module `app/trait_sources.py` implements two tiers with INJECTED network functions (mirroring how `app/judge.py`/`app/traits.py` isolate the Anthropic client): `wikidata_traits` (`db` tier, SPARQL → Q-ID citations) and `literature_grounded_traits` (`llm` tier, fetched-text → LLM extract-only-what-it-can-quote → DOI citations), plus `verify_citations` (ghostcite + Wikidata-resolve gate). `build_trait_rubrics.py` delegates its stubs to these, merges/dedups/verifies, and the existing `validate_trait`/`upsert_rubric` chain persists. The `--live` wiring binds direct Europe PMC HTTP + Wikidata SPARQL + ghostcite subprocess + Anthropic; everything testable is injected.

**Tech Stack:** Python 3.13, SQLAlchemy/SQLite, Anthropic SDK (forced-tool), Wikidata SPARQL, Europe PMC REST, `ghostcite` CLI. Unit tests use stdlib + injected stubs (no network).

## Global Constraints

- **Trait dict shape (consumed by `app/traits.py` judge + `validate_trait`):** `key, trait_class, type, expected, visual, source_tier, citation`. The judge reads only `key`, `trait_class`, `expected`. Two provenance-only extra fields live in `traits_json`: `quote`, `source_detail` (judge ignores them).
- `trait_class` MUST be in `SCORED_CLASSES` = {habit, organ_shape, phyllotaxy, inflorescence, color, presence, proportion}; `source_tier` ∈ {db, llm}; `citation` non-empty (enforced by existing `validate_trait`).
- **Nothing from model recall:** every `llm`-tier trait's `quote` MUST be a verbatim substring of the retrieved text, or the trait is dropped. Every citation is tool-verified (ghostcite for papers; resolve-check for Wikidata) before admission.
- **Fail loud, never silently degrade:** a SPARQL/HTTP error raises; a taxon ending with zero usable traits is a hard error (NO empty rubric written — an empty rubric makes every output "fully covered" and skips judging).
- **`--live` is operator-run and spends API credits** (LLM extraction). It is gated behind `--live`; `--dry-run` does search-only with no LLM/ghostcite spend and reports counts.
- **Verified Wikidata morphology properties** (probed live 2026-06-29): `P4000` has fruit type, `P12616` leaf morphology, `P3739` inflorescence, `P2827` flower color. Do not invent others.
- **Test hygiene:** run tests with DEFAULT env only — NEVER set `BIO3D_DATABASE_URL`/`BIO3D_DATA_DIR` to the study DB under pytest (it wipes it). Use `.venv/bin/python -m pytest`.
- **No cross-project imports** (no importing the data-aggregator package); data-aggregator stays an agent-side exploration tool, not a runtime dependency.

---

### Task 1: `app/trait_sources.py` scaffold + Wikidata `db` tier

**Files:**

- Create: `app/trait_sources.py`
- Test: `tests/test_trait_sources.py`

**Interfaces:**

- Consumes: `app.traits.SCORED_CLASSES` (the 7-class set).
- Produces: `wikidata_traits(taxon: str, *, sparql_fn) -> list[dict]` where `sparql_fn(taxon) -> {"qid": str, "props": {pid: value}} | None`. `WIKIDATA_PROPERTY_MAP: dict[str, tuple[str,str]]` (pid → (trait_class, key)).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trait_sources.py
from __future__ import annotations

from app import trait_sources


def test_wikidata_traits_maps_known_props_and_ignores_unmapped():
    def fake_sparql(_taxon):
        return {"qid": "Q23501", "props": {"P4000": "berry", "P2827": "yellow", "P9999": "x"}}

    out = trait_sources.wikidata_traits("Solanum lycopersicum", sparql_fn=fake_sparql)
    by = {t["key"]: t for t in out}
    assert by["wd_fruit_type"]["expected"] == "berry"
    assert by["wd_fruit_type"]["trait_class"] == "organ_shape"
    assert by["wd_fruit_type"]["citation"] == "https://www.wikidata.org/wiki/Q23501"
    assert by["wd_fruit_type"]["source_detail"] == "Q23501"
    assert by["wd_flower_color"]["trait_class"] == "color"
    assert len(out) == 2  # P9999 unmapped → ignored


def test_wikidata_traits_empty_when_no_item():
    assert trait_sources.wikidata_traits("Nonexistus", sparql_fn=lambda _t: None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trait_sources.py -v`
Expected: FAIL with `ModuleNotFoundError`/`AttributeError` (no `trait_sources`).

- [ ] **Step 3: Write minimal implementation**

```python
# app/trait_sources.py
"""Rubric trait sources: Wikidata (db tier) + retrieval-grounded literature (llm tier).

Pure cores with INJECTED network functions (mirrors app/judge.py / app/traits.py) so units
test without network. scripts/build_trait_rubrics.py wires the real SPARQL / Europe PMC /
Anthropic clients behind --live. Every emitted trait carries a resolvable citation; no trait
claim or citation comes from model recall."""

from __future__ import annotations

from .traits import SCORED_CLASSES

# Verified Wikidata morphology properties (probed live 2026-06-29) → (trait_class, key).
# Wikidata is a thin, high-confidence backbone; most visual traits come from the llm tier.
WIKIDATA_PROPERTY_MAP: dict[str, tuple[str, str]] = {
    "P4000": ("organ_shape", "wd_fruit_type"),       # has fruit type
    "P12616": ("organ_shape", "wd_leaf_morphology"),  # leaf morphology
    "P3739": ("inflorescence", "wd_inflorescence"),   # inflorescence
    "P2827": ("color", "wd_flower_color"),            # flower color
}


def wikidata_traits(taxon: str, *, sparql_fn) -> list[dict]:
    """db-tier traits from the taxon's Wikidata item. `sparql_fn(taxon)` returns
    {"qid": "Q23501", "props": {"P4000": "berry", ...}} or None when the taxon has no item.
    Maps only WIKIDATA_PROPERTY_MAP entries that are present and non-empty."""
    rec = sparql_fn(taxon)
    if not rec or not rec.get("qid"):
        return []
    qid = rec["qid"]
    citation = f"https://www.wikidata.org/wiki/{qid}"
    out: list[dict] = []
    for pid, value in (rec.get("props") or {}).items():
        mapping = WIKIDATA_PROPERTY_MAP.get(pid)
        if mapping is None or not value:
            continue
        trait_class, key = mapping
        if trait_class not in SCORED_CLASSES:  # defensive: map must stay valid
            continue
        out.append(
            {
                "key": key,
                "trait_class": trait_class,
                "type": "categorical",
                "expected": value,
                "visual": True,
                "citation": citation,
                "source_detail": qid,
                "quote": f"{pid}={value}",
            }
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trait_sources.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/trait_sources.py tests/test_trait_sources.py
git commit -m "feat(mode-c): trait_sources Wikidata db tier (verified property map)"
```

---

### Task 2: Literature `llm` tier — retrieval-grounded extraction

**Files:**

- Modify: `app/trait_sources.py` (append)
- Test: `tests/test_trait_sources.py` (append)

**Interfaces:**

- Consumes: `app.traits.SCORED_CLASSES`, `app.judge.JUDGE_MODEL`.
- Produces:
  - `EXTRACT_TOOL: dict`, `build_extract_messages(taxon, source_text) -> list[dict]`.
  - `parse_extracted(resp, source_text, *, citation, source_detail) -> list[dict]` — keeps only traits whose `quote` ⊂ `source_text` and `trait_class ∈ SCORED_CLASSES`.
  - `literature_grounded_traits(taxon, *, search_fn, resolve_fn, llm_client, max_pubs=5) -> list[dict]` where `search_fn(taxon) -> list[dict]` (each `{"doi"|"pmid"|"title", ...}`), `resolve_fn(pub) -> str | None` (retrieved source text), `llm_client` is Anthropic-like.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trait_sources.py  (append)
class _ExtractClient:
    """Anthropic-like stub returning a forced record_extracted_traits tool_use."""

    class _Block:
        type = "tool_use"
        name = "record_extracted_traits"

        def __init__(self, data):
            self.input = data

    class _Resp:
        def __init__(self, data):
            self.content = [_ExtractClient._Block(data)]

    def __init__(self, payload):
        self._payload = payload
        self.messages = self

    def create(self, **_kw):
        return _ExtractClient._Resp({"traits": self._payload})


def test_literature_grounded_keeps_quoted_drops_unquoted_and_bad_class():
    text = "The corolla is bright red. Leaves are compound."
    payload = [
        {"key": "petal_color", "trait_class": "color", "expected": "red",
         "quote": "The corolla is bright red"},                       # quote ⊂ text → kept
        {"key": "leaf_shape", "trait_class": "organ_shape", "expected": "compound",
         "quote": "Leaves are palmate"},                              # quote NOT in text → dropped
        {"key": "height", "trait_class": "height", "expected": "2m",
         "quote": "Leaves are compound"},                            # bad class → dropped
    ]
    out = trait_sources.literature_grounded_traits(
        "Testus planta",
        search_fn=lambda _t: [{"doi": "10.1/x", "title": "T"}],
        resolve_fn=lambda _p: text,
        llm_client=_ExtractClient(payload),
    )
    assert len(out) == 1
    t = out[0]
    assert t["key"] == "petal_color" and t["trait_class"] == "color"
    assert "source_tier" not in t  # source_tier is stamped later by build_rubric_traits
    assert t["citation"] == "10.1/x" and t["quote"] == "The corolla is bright red"


def test_literature_grounded_skips_pubs_with_no_text():
    out = trait_sources.literature_grounded_traits(
        "Testus",
        search_fn=lambda _t: [{"doi": "10.1/x"}],
        resolve_fn=lambda _p: None,  # unresolvable → skipped, no LLM call
        llm_client=_ExtractClient([]),
    )
    assert out == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trait_sources.py -k literature -v`
Expected: FAIL with `AttributeError: module 'app.trait_sources' has no attribute 'literature_grounded_traits'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/trait_sources.py  (append; add this import at the top with the existing imports)
from .judge import JUDGE_MODEL

EXTRACT_TOOL = {
    "name": "record_extracted_traits",
    "description": "Record botanical traits EXPLICITLY STATED in the provided source text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "traits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "short snake_case id"},
                        "trait_class": {"type": "string", "enum": sorted(SCORED_CLASSES)},
                        "expected": {
                            "type": "string",
                            "description": "the expected visible value, e.g. 'red', 'compound'",
                        },
                        "quote": {
                            "type": "string",
                            "description": "VERBATIM span from the source text stating this trait",
                        },
                    },
                    "required": ["key", "trait_class", "expected", "quote"],
                },
            }
        },
        "required": ["traits"],
    },
}


def build_extract_messages(taxon: str, source_text: str) -> list[dict]:
    text = (
        f"Below is source text about the plant {taxon}. Extract only VISUALLY-OBSERVABLE "
        "morphological traits the text EXPLICITLY states (color, organ shape, leaf arrangement, "
        "inflorescence, presence of structures, relative proportions). For each trait give a "
        "short key, a trait_class, the expected visible value, and a VERBATIM quote from the "
        "text. Do not infer traits the text does not state. Call record_extracted_traits.\n\n"
        f"Source text:\n{source_text}"
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}]}]


def parse_extracted(resp, source_text: str, *, citation: str, source_detail: str) -> list[dict]:
    for block in getattr(resp, "content", []):
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", "") == "record_extracted_traits"
        ):
            out: list[dict] = []
            for r in (block.input or {}).get("traits", []):
                tc = r.get("trait_class")
                quote = r.get("quote", "")
                if tc not in SCORED_CLASSES:
                    continue
                if not quote or quote not in source_text:  # anti-hallucination: must be verbatim
                    continue
                out.append(
                    {
                        "key": r["key"],
                        "trait_class": tc,
                        "type": "categorical",
                        "expected": r.get("expected", ""),
                        "visual": True,
                        "citation": citation,
                        "source_detail": source_detail,
                        "quote": quote,
                    }
                )
            return out
    return []


def literature_grounded_traits(
    taxon: str, *, search_fn, resolve_fn, llm_client, max_pubs: int = 5
) -> list[dict]:
    """llm-tier traits: for each of up to max_pubs publications, resolve its source text and
    have the LLM extract only traits it can quote from that text. Citation = the publication."""
    traits: list[dict] = []
    seen_keys: set[str] = set()
    for pub in (search_fn(taxon) or [])[:max_pubs]:
        text = resolve_fn(pub)
        if not text:
            continue
        citation = pub.get("doi") or pub.get("pmid") or pub.get("title")
        if not citation:
            continue
        resp = llm_client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=1500,
            tools=[EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "record_extracted_traits"},
            messages=build_extract_messages(taxon, text),
        )
        for t in parse_extracted(
            resp, text, citation=str(citation), source_detail=str(citation)
        ):
            if t["key"] in seen_keys:
                continue
            seen_keys.add(t["key"])
            traits.append(t)
    return traits
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trait_sources.py -v`
Expected: PASS (4 passed total).

- [ ] **Step 5: Commit**

```bash
git add app/trait_sources.py tests/test_trait_sources.py
git commit -m "feat(mode-c): trait_sources llm tier (retrieval-grounded extract, quote-substring gate)"
```

---

### Task 3: `verify_citations` — ghostcite + Wikidata-resolve gate

**Files:**

- Modify: `app/trait_sources.py` (append)
- Test: `tests/test_trait_sources.py` (append)

**Interfaces:**

- Produces: `verify_citations(traits, *, ghostcite_fn, resolve_fn) -> list[dict]` where `ghostcite_fn(citation) -> {"verified": bool, "retracted": bool}` and `resolve_fn(url) -> truthy|falsy`. Wikidata-URL citations are gated by `resolve_fn`; all other (paper) citations by `ghostcite_fn`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trait_sources.py  (append)
def _trait(citation):
    return {
        "key": "k", "trait_class": "color", "type": "categorical", "expected": "red",
        "visual": True, "citation": citation, "source_detail": citation, "quote": "q",
    }


def test_verify_citations_gates_papers_and_wikidata():
    traits = [
        _trait("10.1/real"),                                  # paper, verified → kept
        _trait("10.1/fake"),                                  # paper, unverified → dropped
        _trait("10.1/retracted"),                             # paper, retracted → dropped
        _trait("https://www.wikidata.org/wiki/Q23501"),       # wikidata, resolvable → kept
        _trait("https://www.wikidata.org/wiki/Q0"),           # wikidata, unresolvable → dropped
    ]
    gc = {
        "10.1/real": {"verified": True, "retracted": False},
        "10.1/fake": {"verified": False, "retracted": False},
        "10.1/retracted": {"verified": True, "retracted": True},
    }
    kept = trait_sources.verify_citations(
        traits,
        ghostcite_fn=lambda c: gc.get(c, {"verified": False, "retracted": False}),
        resolve_fn=lambda url: url.endswith("Q23501"),
    )
    cites = [t["citation"] for t in kept]
    assert cites == ["10.1/real", "https://www.wikidata.org/wiki/Q23501"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trait_sources.py -k verify -v`
Expected: FAIL with `AttributeError: ... has no attribute 'verify_citations'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/trait_sources.py  (append)
def _is_wikidata(citation: str) -> bool:
    return "wikidata.org" in (citation or "")


def verify_citations(traits, *, ghostcite_fn, resolve_fn) -> list[dict]:
    """Drop any trait whose citation can't be verified. Wikidata URLs must resolve;
    paper citations must be ghostcite-verified AND not retracted."""
    kept: list[dict] = []
    for t in traits:
        cite = t.get("citation") or ""
        if _is_wikidata(cite):
            if resolve_fn(cite):
                kept.append(t)
            continue
        res = ghostcite_fn(cite) or {}
        if res.get("verified") and not res.get("retracted"):
            kept.append(t)
    return kept
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trait_sources.py -v`
Expected: PASS (5 passed total).

- [ ] **Step 5: Commit**

```bash
git add app/trait_sources.py tests/test_trait_sources.py
git commit -m "feat(mode-c): verify_citations ghostcite + wikidata-resolve gate"
```

---

### Task 4: `build_rubric_traits` orchestration — merge/dedup + verify before validate

**Files:**

- Modify: `scripts/build_trait_rubrics.py:79-98` (the `build_rubric_traits` function)
- Test: `tests/test_build_trait_rubrics.py` (append)

**Interfaces:**

- Consumes: `wikidata_traits`/`literature_grounded_traits`/`verify_citations` (Tasks 1-3), existing `validate_trait`.
- Produces: `build_rubric_traits(taxon, *, fetch_db=fetch_db_traits, draft_llm=draft_llm_traits, verify_fn=None) -> list[dict]` — fetches both tiers, stamps `source_tier`, merges/dedups on `(trait_class, expected)` preferring `db` and enforcing unique `key`, applies `verify_fn` (identity when None), then validates each. Adds module-level `_merge_dedup(traits) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_trait_rubrics.py  (append)
def test_build_rubric_traits_merges_dedups_and_verifies():
    import scripts.build_trait_rubrics as b

    def fetch_db(_t):
        return [{
            "key": "wd_flower_color", "trait_class": "color", "type": "categorical",
            "expected": "red", "visual": True,
            "citation": "https://www.wikidata.org/wiki/Q1", "source_detail": "Q1", "quote": "P2827=red",
        }]

    def draft_llm(_t):
        return [
            # same (color, red) as db → deduped out (db preferred)
            {"key": "petal_red", "trait_class": "color", "type": "categorical",
             "expected": "red", "visual": True, "citation": "10.1/a",
             "source_detail": "10.1/a", "quote": "red corolla"},
            # distinct trait → kept
            {"key": "leaf_shape", "trait_class": "organ_shape", "type": "categorical",
             "expected": "compound", "visual": True, "citation": "10.1/b",
             "source_detail": "10.1/b", "quote": "compound leaves"},
        ]

    # verify_fn drops the leaf citation 10.1/b → only the db color trait survives
    def verify_fn(traits):
        return [t for t in traits if t["citation"] != "10.1/b"]

    out = b.build_rubric_traits("X", fetch_db=fetch_db, draft_llm=draft_llm, verify_fn=verify_fn)
    keys = {t["key"] for t in out}
    assert keys == {"wd_flower_color"}  # llm color deduped, llm leaf verify-dropped
    assert out[0]["source_tier"] == "db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_build_trait_rubrics.py -k merges -v`
Expected: FAIL — `build_rubric_traits` has no `verify_fn` kwarg / no dedup (TypeError or assertion failure).

- [ ] **Step 3: Write minimal implementation**

Replace the body of `build_rubric_traits` (currently `scripts/build_trait_rubrics.py:79-98`) and add `_merge_dedup` just above it:

```python
def _merge_dedup(traits: list[dict]) -> list[dict]:
    """Drop duplicate (trait_class, expected) across tiers, preferring db; enforce unique key."""
    ordered = sorted(traits, key=lambda t: 0 if t.get("source_tier") == "db" else 1)
    seen_sig: set[tuple[str, str]] = set()
    used_keys: set[str] = set()
    kept: list[dict] = []
    for t in ordered:
        sig = (t["trait_class"], (t.get("expected") or "").strip().lower())
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        key = t["key"]
        i = 2
        while key in used_keys:
            key = f"{t['key']}_{i}"
            i += 1
        t["key"] = key
        used_keys.add(key)
        kept.append(t)
    return kept


def build_rubric_traits(
    taxon: str,
    *,
    fetch_db=fetch_db_traits,
    draft_llm=draft_llm_traits,
    verify_fn=None,
) -> list[dict]:
    """Assemble + validate one taxon's traits from the injected db backbone + llm enrichment.

    Each source stamps source_tier; we merge/dedup (db preferred), verify every citation
    (verify_fn; identity when None), then re-validate so no uncited/invalid trait gets through."""
    traits: list[dict] = []
    for t in fetch_db(taxon):
        t = dict(t)  # copy: a real fetcher may return shared/cached dicts
        t.setdefault("source_tier", "db")
        traits.append(t)
    for t in draft_llm(taxon):
        t = dict(t)
        t.setdefault("source_tier", "llm")
        traits.append(t)
    traits = _merge_dedup(traits)
    if verify_fn is not None:
        traits = verify_fn(traits)
    for t in traits:
        validate_trait(t)
    return traits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build_trait_rubrics.py -v`
Expected: PASS (existing mutation/validate/upsert tests + the new merge test all green).

- [ ] **Step 5: Commit**

```bash
git add scripts/build_trait_rubrics.py tests/test_build_trait_rubrics.py
git commit -m "feat(mode-c): build_rubric_traits merge/dedup + verify-before-validate"
```

---

### Task 5: `--live` wiring (Wikidata + Europe PMC + ghostcite + Anthropic) + `--dry-run` report

**Files:**

- Modify: `scripts/build_trait_rubrics.py` (replace the two `NotImplementedError` stub bodies at `:61-76`; add live HTTP/ghostcite helpers; rewrite `main()` `--dry-run`/`--live` flow)
- Test: `tests/test_build_trait_rubrics.py` (append a `--dry-run` smoke test)

**Interfaces:**

- Consumes: `app.trait_sources.{wikidata_traits, literature_grounded_traits, verify_citations}`; `anthropic.Anthropic`.
- Produces: live helpers `_live_wikidata_sparql(taxon)`, `_live_lit_search(taxon)`, `_live_lit_resolve(pub)`, `_ghostcite_verify(citation)`, `_resolve_url(url)`, and a `_live_verify_fn` binding `verify_citations`. `main()` `--dry-run` prints per-taxon counts (db traits, candidate pubs, OA-resolvable, est. LLM calls) with NO LLM/ghostcite spend.

- [ ] **Step 1: Write the failing test (dry-run smoke, fully stubbed — no network)**

```python
# tests/test_build_trait_rubrics.py  (append)
def test_dry_run_reports_counts_without_spend(capsys, monkeypatch):
    import scripts.build_trait_rubrics as b

    # Stub the network helpers so dry-run does zero real I/O and zero spend.
    monkeypatch.setattr(b, "_live_wikidata_sparql", lambda taxon: {"qid": "Q1", "props": {"P2827": "red"}})
    monkeypatch.setattr(b, "_live_lit_search", lambda taxon: [{"doi": "10.1/a", "abstractText": "x"}, {"doi": "10.1/b"}])
    monkeypatch.setattr(b, "_live_lit_resolve", lambda pub: pub.get("abstractText"))

    rc = b.dry_run_report(["Solanum lycopersicum"])
    captured = capsys.readouterr().out
    assert rc == 0
    assert "Solanum lycopersicum" in captured
    assert "db traits=1" in captured        # P2827 mapped
    assert "candidate pubs=2" in captured
    assert "OA-resolvable=1" in captured     # only 10.1/a has text
    assert "est. LLM calls=1" in captured
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_build_trait_rubrics.py -k dry_run -v`
Expected: FAIL — `_live_wikidata_sparql` / `dry_run_report` do not exist.

- [ ] **Step 3: Write minimal implementation**

Replace the two stub bodies (`fetch_db_traits`, `draft_llm_traits` at `scripts/build_trait_rubrics.py:61-76`) and add the live helpers + `dry_run_report`. Place this block where the stubs were:

```python
import json as _json
import os as _os
import subprocess as _subprocess
import urllib.parse as _urlparse
import urllib.request as _urlrequest

_UA = "bio3d-arena-rubrics/0.1 (research; contact: operator)"


def _http_json(url: str, timeout: int = 40) -> dict:
    req = _urlrequest.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with _urlrequest.urlopen(req, timeout=timeout) as r:  # noqa: S310 — fixed https hosts
        return _json.loads(r.read().decode())


def _live_wikidata_sparql(taxon: str) -> dict | None:
    """Resolve the taxon's Q-item via P225 and fetch the mapped morphology properties."""
    from app.trait_sources import WIKIDATA_PROPERTY_MAP

    pids = " ".join(f"wdt:{p}" for p in WIKIDATA_PROPERTY_MAP)
    q = (
        "SELECT ?taxon ?p ?vLabel WHERE { ?taxon wdt:P225 %r@en . "
        "VALUES ?p { %s } ?taxon ?p ?v . "
        'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". ?v rdfs:label ?vLabel. } '
        "} LIMIT 50" % (taxon, pids)
    )
    url = "https://query.wikidata.org/sparql?format=json&query=" + _urlparse.quote(q)
    data = _http_json(url)
    rows = data["results"]["bindings"]
    if not rows:
        return None
    qid = rows[0]["taxon"]["value"].rsplit("/", 1)[-1]
    props: dict[str, str] = {}
    for b in rows:
        pid = b["p"]["value"].rsplit("/", 1)[-1]  # e.g. .../prop/direct/P2827 → P2827
        val = b.get("vLabel", {}).get("value")
        if pid and val:
            props.setdefault(pid, val)
    return {"qid": qid, "props": props}


def _live_lit_search(taxon: str, page_size: int = 8) -> list[dict]:
    """Europe PMC core search; returns result dicts incl abstractText/doi/pmid/title."""
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    query = _urlparse.quote(f'"{taxon}" AND (morphology OR description OR floral)')
    url = f"{base}?query={query}&format=json&resultType=core&pageSize={page_size}"
    data = _http_json(url)
    return data.get("resultList", {}).get("result", [])


def _live_lit_resolve(pub: dict) -> str | None:
    """Retrieved source text = the abstract (always honest retrieved text; no extra call)."""
    txt = pub.get("abstractText")
    return txt if txt and txt.strip() else None


def _ghostcite_verify(citation: str) -> dict:
    """Run ghostcite on one DOI; return {'verified': bool, 'retracted': bool}."""
    try:
        proc = _subprocess.run(
            ["ghostcite", "--format", "doi", "--json", "-"],
            input=citation,
            capture_output=True,
            text=True,
            timeout=60,
        )
        data = _json.loads(proc.stdout or "{}")
    except Exception as e:  # noqa: BLE001 — fail closed: unverifiable → not verified
        print(f"ghostcite error on {citation!r}: {e}", file=sys.stderr)
        return {"verified": False, "retracted": False}
    # ghostcite --json reports per-entry results; treat a clean, non-retracted match as verified.
    entries = data.get("results") or data.get("entries") or []
    if not entries:
        return {"verified": False, "retracted": False}
    e0 = entries[0]
    status = (e0.get("status") or "").lower()
    retracted = bool(e0.get("retracted")) or status == "retracted"
    verified = status in ("ok", "verified", "match") and not retracted
    return {"verified": verified, "retracted": retracted}


def _resolve_url(url: str, timeout: int = 20) -> bool:
    try:
        req = _urlrequest.Request(url, method="HEAD", headers={"User-Agent": _UA})
        with _urlrequest.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return 200 <= r.status < 400
    except Exception:  # noqa: BLE001
        return False


def fetch_db_traits(taxon: str) -> list[dict]:
    """db tier: Wikidata. Network-bound — only reachable via --live."""
    from app.trait_sources import wikidata_traits

    return wikidata_traits(taxon, sparql_fn=_live_wikidata_sparql)


def draft_llm_traits(taxon: str) -> list[dict]:
    """llm tier: Europe PMC retrieval + Anthropic extraction. Network + API spend; --live only."""
    import anthropic

    from app.trait_sources import literature_grounded_traits

    client = anthropic.Anthropic()
    return literature_grounded_traits(
        taxon, search_fn=_live_lit_search, resolve_fn=_live_lit_resolve, llm_client=client
    )


def _live_verify_fn(traits: list[dict]) -> list[dict]:
    from app.trait_sources import verify_citations

    return verify_citations(traits, ghostcite_fn=_ghostcite_verify, resolve_fn=_resolve_url)


def dry_run_report(taxa: list[str]) -> int:
    """Search-only: per taxon report db-trait / pub / OA-resolvable / est-LLM-call counts.
    No LLM, no ghostcite, no DB writes, no spend."""
    from app.trait_sources import wikidata_traits

    for taxon in taxa:
        db_traits = wikidata_traits(taxon, sparql_fn=_live_wikidata_sparql)
        pubs = _live_lit_search(taxon)
        resolvable = [p for p in pubs if _live_lit_resolve(p)]
        print(
            f"[dry-run] {taxon}: db traits={len(db_traits)} candidate pubs={len(pubs)} "
            f"OA-resolvable={len(resolvable)} est. LLM calls={min(len(resolvable), 5)}"
        )
    return 0
```

Then update `main()` so `--dry-run` calls `dry_run_report(list(taxa))` (search-only, before any DB session / LLM), and the `--live` write path passes `verify_fn=_live_verify_fn` into `build_rubric_traits` and raises on a zero-trait taxon. Replace the taxa loop in `main()`:

```python
    if args.dry_run:
        return dry_run_report([t.strip() for t in args.taxa.split(",") if t.strip()])

    from app.database import SessionLocal

    taxa = {t.strip(): None for t in args.taxa.split(",") if t.strip()}
    with SessionLocal() as db:
        taxa = _resolve_task_ids(db, taxa)
        written = 0
        for taxon, task_id in taxa.items():
            traits = build_rubric_traits(taxon, verify_fn=_live_verify_fn)
            if not traits:
                raise RuntimeError(
                    f"no usable traits for {taxon!r} after sourcing+verification; "
                    "refusing to write an empty rubric (would skip judging)."
                )
            upsert_rubric(db, taxon, task_id, traits)
            written += 1
            print(f"wrote rubric for {taxon} (task={task_id}): {len(traits)} traits")
    print({"rubrics": written})
    return 0
```

(The pre-`--live` guard at `:139-145` that prints "refusing … without --live" stays; `dry_run_report` runs only under `--dry-run`, which the guard must allow — keep `--dry-run` exempt from the `--live` refusal.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build_trait_rubrics.py -v`
Expected: PASS (dry-run smoke + all prior). Then full suite:
Run: `.venv/bin/python -m pytest -q`
Expected: all pass / prior skips unchanged.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_trait_rubrics.py tests/test_build_trait_rubrics.py
git commit -m "feat(mode-c): --live rubric sourcing (Wikidata+EuropePMC+ghostcite) + --dry-run cost report"
```

- [ ] **Step 6: Operator real-execution probe (NOT pytest — boundary check, no spend)**

Run (study env is fine; `--dry-run` writes nothing and spends nothing):

```bash
.venv/bin/python -m scripts.build_trait_rubrics --taxa "Solanum lycopersicum" --dry-run
```

Expected: a `[dry-run] Solanum lycopersicum: db traits=… candidate pubs=… OA-resolvable=… est. LLM calls=…` line with non-zero candidate pubs (confirms the live Wikidata SPARQL + Europe PMC params actually return data before any `--live` spend). Report the counts to the user; do NOT run `--live` without explicit API-spend approval.

---

## Notes for the executor

- After Task 5, the live `--live` pass itself (authoring real rubrics → running `trait_judge.py` → calibration → flipping classes) remains **Task 7 of the parent Mode-C plan** and is operator-run, gated on the user's API-spend approval of the dry-run count. This plan stops at "the backends exist, tested, and the dry-run probe works."
- `ghostcite --json` output shape: if the real schema differs from the `{"results":[{"status","retracted"}]}` assumed in `_ghostcite_verify`, adjust the parse in that one function — the unit tests inject a stub so they are unaffected; confirm against `ghostcite --json -` on a known DOI during the Task 5 operator probe.
