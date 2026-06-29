"""Rubric authoring: build per-taxon botanical-trait rubrics with mandatory provenance.

Each rubric is a list of traits; every trait carries source_tier ("db"|"llm") + a non-empty
citation (Global Constraints). A structured-DB backbone (fetch_db_traits) and an LLM enrichment
pass (draft_llm_traits) are INJECTED functions so the unit test drives them with stubs; the real
implementations live behind --live (Anthropic client / external DB endpoints). validate_trait +
upsert_rubric are pure and network-free. Uses the same sys.path bootstrap as scripts/judge_vlm.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RECON_TAXA = {  # taxon -> task_id placeholder; resolve real ids in main() via Task lookup
    "Solanum lycopersicum": None,
    "Zea mays": None,
    "Pinus sylvestris": None,
    "Rosa": None,
    "Glycine max": None,
    "Arabidopsis thaliana": None,
}


def validate_trait(t: dict) -> None:
    """Raise ValueError if a trait dict is missing a required field, has a non-scoreable
    trait_class, an empty citation, or a source_tier outside {db, llm}."""
    from app.traits import SCORED_CLASSES

    required = ("key", "trait_class", "type", "expected", "visual", "source_tier", "citation")
    for f in required:
        if f not in t:
            raise ValueError(f"trait missing field {f!r}: {t}")
    if t["trait_class"] not in SCORED_CLASSES:
        raise ValueError(f"trait_class {t['trait_class']!r} not scoreable")
    if t["source_tier"] not in ("db", "llm"):
        raise ValueError(f"bad source_tier {t['source_tier']!r}")
    if not (t.get("citation") or "").strip():
        raise ValueError("trait has empty citation")


def upsert_rubric(db, taxon, task_id, traits):
    """Validate every trait, then insert-or-update the TraitRubric row for taxon."""
    from app.models import TraitRubric

    for t in traits:
        validate_trait(t)
    row = db.query(TraitRubric).filter_by(taxon=taxon).first() or TraitRubric(taxon=taxon)
    row.task_id = task_id
    row.traits_json = json.dumps(traits)
    db.add(row)
    db.commit()
    return row


# --- Live HTTP / subprocess helpers (module-level so monkeypatch can replace them) -------------

import json as _json
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
    """Run ghostcite on one DOI; return {'verified': bool, 'retracted': bool}.

    ghostcite --json (probed 2026-06-29) emits {"summary": {..., "with_doi": N}, "findings": [...]}
    where `findings` lists PROBLEMS (empty = clean). A finding with tier "R" / "RETRACTED" message
    is a retraction; any other finding (e.g. tier "U" "DOI does not resolve") is unverifiable. A
    retracted DOI makes ghostcite exit non-zero, so the verdict is driven off `findings`, NOT the
    exit code. Verified requires a clean result AND a DOI ghostcite actually recognized
    (`summary.with_doi >= 1`), so a bare title/PMID citation is not spuriously trusted.
    Unparseable / wrong-shape output (bad flag, crash) is the real tool error → fail loud + closed."""
    try:
        proc = _subprocess.run(
            ["ghostcite", "--format", "doi", "--json", "-"],
            input=citation,
            capture_output=True,
            text=True,
            timeout=60,
        )
        data = _json.loads(proc.stdout or "")
    except Exception as e:  # noqa: BLE001 — unparseable output = tool error: fail loud + closed
        print(f"ghostcite error on {citation!r}: {e}", file=sys.stderr)
        return {"verified": False, "retracted": False}
    if not isinstance(data, dict) or "findings" not in data:
        print(f"ghostcite unexpected output on {citation!r}: {str(data)[:200]}", file=sys.stderr)
        return {"verified": False, "retracted": False}
    findings = data.get("findings") or []
    if findings:
        retracted = any(
            f.get("tier") == "R" or "RETRACTED" in (f.get("message") or "").upper()
            for f in findings
        )
        return {"verified": False, "retracted": retracted}
    # No findings: verified only if ghostcite recognized a DOI to check (not a bare title).
    verified = bool((data.get("summary") or {}).get("with_doi"))
    return {"verified": verified, "retracted": False}


def _resolve_url(url: str, timeout: int = 20) -> bool:
    try:
        req = _urlrequest.Request(url, method="HEAD", headers={"User-Agent": _UA})
        with _urlrequest.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return 200 <= r.status < 400
    except Exception:  # noqa: BLE001
        return False


# --- Pluggable trait sources (real impls wired behind --live) ---------------------------------


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


def _resolve_task_ids(db, taxa: dict) -> dict:
    """Best-effort: map each taxon to a Task id whose title/prompt mentions it (None if absent)."""
    from app.models import Task

    resolved = dict(taxa)
    for taxon in resolved:
        if resolved[taxon] is not None:
            continue
        row = (
            db.query(Task)
            .filter((Task.title.ilike(f"%{taxon}%")) | (Task.prompt.ilike(f"%{taxon}%")))
            .first()
        )
        resolved[taxon] = row.id if row else None
    return resolved


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--taxa",
        default=",".join(RECON_TAXA),
        help="comma-separated taxa (default: the 6 recon species)",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="use the real structured-DB + Anthropic enrichment sources (network)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="search-only cost report: Wikidata + Europe PMC counts; no LLM calls, no DB writes, no spend",
    )
    args = ap.parse_args()

    if not args.live and not args.dry_run:
        print(
            "refusing to run real sourcing without --live "
            "(performs live Wikidata/Europe PMC/ghostcite/Anthropic calls with API spend); "
            "use --dry-run for a no-spend search-only preview.",
            file=sys.stderr,
        )
        return 2

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


if __name__ == "__main__":
    raise SystemExit(main())
