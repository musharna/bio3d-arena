"""Verify a recon INPUT photo actually depicts its claimed species — the in-domain, strong use
of BioCLIP (2026-07-06 probe: multi-class species-ID 13/13, and it classifies a mislabeled photo
by its TRUE content). A mismatch raises a NON-HIDING advisory flag for human triage; it never
auto-hides (precision-first — a human confirms). Uses `reference_qa.species_matches` (multi-class),
NOT the retired binary species_rep_score."""

from __future__ import annotations

import json

INPUT_SUBJECT_SESSION = "input-subject-v1"


def candidate_panel() -> list[str]:
    """The universe of plausible taxa to classify against — every taxon with an organ inventory."""
    from .organ_inventory import ORGAN_INVENTORY

    return list(ORGAN_INVENTORY.keys())


def verify_input_subject(
    bundle,
    png: bytes,
    *,
    claimed_taxon: str,
    panel: list[str] | None = None,
    min_margin: float = 0.0,
) -> dict:
    """Return species_matches() for the input photo against the candidate panel: ok iff BioCLIP's
    top-1 IS the claimed taxon. `panel` defaults to the full inventory universe."""
    from .reference_qa import species_matches

    panel = panel or candidate_panel()
    if claimed_taxon not in panel:
        panel = [claimed_taxon, *panel]
    return species_matches(
        bundle, png, claimed_taxon=claimed_taxon, panel=panel, min_margin=min_margin
    )


def scan_and_flag(
    db, *, bundle, resolve_png, taxon_of, apply: bool, min_margin: float = 0.0
) -> list[dict]:
    """Scan every visible ModelOutput carrying a meta.input_image; classify the input photo and,
    on a species mismatch, record a non-hiding advisory flag. `resolve_png(rel)->bytes|None` reads
    the asset; `taxon_of(output)->str|None` maps an output to its claimed binomial (None -> skip).
    Returns a triage list. Never auto-hides (threshold 10**9)."""
    from sqlalchemy import select

    from . import flags
    from .models import ModelOutput

    ADVISORY = 10**9
    triage: list[dict] = []
    for o in db.execute(select(ModelOutput).where(ModelOutput.hidden_at.is_(None))).scalars():
        img = (json.loads(o.meta_json or "{}") or {}).get("input_image")
        if not img:
            continue
        claimed = taxon_of(o)
        if claimed is None:
            continue
        png = resolve_png(img)
        if png is None:
            continue
        r = verify_input_subject(bundle, png, claimed_taxon=claimed, min_margin=min_margin)
        if not r["ok"]:
            triage.append(
                {
                    "output_id": o.id,
                    "input_image": img,
                    "claimed": claimed,
                    "reads_as": r["top"],
                    "prob": round(r["prob"], 3),
                }
            )
            if apply:
                flags.record_flag(
                    db,
                    o.id,
                    INPUT_SUBJECT_SESSION,
                    f"input subject reads as {r['top']!r}, not claimed {claimed!r}",
                    ADVISORY,
                )
    if apply:
        db.commit()
    return triage
