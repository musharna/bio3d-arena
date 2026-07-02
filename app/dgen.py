# app/dgen.py
"""Rubric-in-the-loop self-improving generation (D-Gen).

Closes the loop between the arena's evaluators (trait-morphology rubric + completeness metric)
and the LLM-procedural generator: generate -> render the GLB directly -> score -> build an
actionable critique -> feed the previous script + critique back -> regenerate, up to N rounds
per taxon, keeping the best round. All LLM/Blender/VLM/browser access is behind injected seams."""

from __future__ import annotations

import base64

from app.commission import build_prompt


def fidelity(trait_results: list[dict]) -> tuple[float | None, int, int]:
    """(fidelity, n_correct, n_assessable) from check_traits output. `assessable` excludes
    verdict == 'not_assessable'; fidelity = present_correct / assessable, or None if none assessable."""
    assessable = [t for t in trait_results if t.get("verdict") != "not_assessable"]
    n_correct = sum(1 for t in assessable if t.get("verdict") == "present_correct")
    n_assessable = len(assessable)
    if n_assessable == 0:
        return None, n_correct, 0
    return n_correct / n_assessable, n_correct, n_assessable


def build_critique(
    trait_results: list[dict],
    traits: list[dict],
    completeness: dict,
    run_status: str,
    run_error: str,
) -> str:
    """Actionable instruction list fed into the NEXT round. Empty only if nothing to fix."""
    expected_by_key = {t["key"]: t.get("expected", "") for t in traits}
    lines: list[str] = []
    if run_status and run_status != "ok":
        lines.append(
            f"The script failed to run (status: {run_status}). Fix this error:\n"
            f"{(run_error or '')[:1500]}"
        )
    for t in trait_results:
        v = t.get("verdict")
        if v in ("absent", "present_wrong"):
            key = t.get("trait_key", "")
            problem = "missing" if v == "absent" else "botanically wrong"
            lines.append(f"FIX {key} ({problem}): expected {expected_by_key.get(key, '')}")
    cat = (completeness or {}).get("category")
    if cat and cat != "complete":
        missing = (
            ", ".join((completeness or {}).get("missing_organs") or []) or "the vegetative body"
        )
        lines.append(
            f"The plant is not complete (currently: {cat}); build the whole plant body "
            f"(missing: {missing})."
        )
    return "\n".join(lines)


def build_refine_prompt(species: str, common: str, prev_script: str, critique: str) -> str:
    """base build_prompt + the previous script + the critique + output-only instruction."""
    return (
        build_prompt(species, common) + "\n\n--- REVISION ROUND ---\n"
        "Here is your previous script:\n```python\n"
        + prev_script
        + "\n```\n\nIt was rendered from several angles and evaluated. Fix EXACTLY these issues:\n"
        + critique
        + "\n\nOutput ONLY the revised complete Python script — no explanation, no markdown prose."
    )


def score_glb(
    glb_path,
    *,
    taxon: str,
    prompt: str,
    traits: list[dict],
    capture_multi,
    trait_client,
    completeness_client,
    condition: str = "multi4",
) -> dict:
    """Render the GLB directly (capture_multi -> tile_contact_sheet), then run the trait judge and
    the completeness scorer over the sheet. Returns fidelity + trait_results + completeness. The
    caller records failures; a render/judge exception propagates."""
    from app.completeness import derive, score_completeness
    from app.judge_render import CONDITIONS, tile_contact_sheet
    from app.organ_inventory import inventory_for
    from app.traits import check_traits

    spec = CONDITIONS[condition]
    pngs = capture_multi(str(glb_path), spec["azimuths"], spec["elev"])
    sheet_png = tile_contact_sheet(pngs, spec["cols"], spec["rows"])
    sheet_b64 = base64.b64encode(sheet_png).decode()

    trait_results = check_traits(
        trait_client, species=taxon, prompt=prompt, sheet_b64=sheet_b64, traits=traits
    )
    fid, n_correct, n_assessable = fidelity(trait_results)

    inv = inventory_for(taxon)
    comp = score_completeness(completeness_client, sheet_png, inventory=inv)
    category, score = derive(inv, comp["organs_present"])
    present = {o["key"] for o in comp["organs_present"] if o.get("status") == "present"}
    missing = [o.key for o in inv.organs if o.required and o.key not in present]

    return {
        "fidelity": fid,
        "n_correct": n_correct,
        "n_assessable": n_assessable,
        "trait_results": trait_results,
        "completeness_category": category,
        "completeness_score": score,
        "completeness_missing_organs": missing,
        "completeness_organs_present": comp["organs_present"],
        "sheet_png": sheet_png,
    }


def ingest_best(
    db, *, model_id: str, task_id: int, glb_src, best_score: dict, best_iter, asset_dir
) -> int:
    """Promote the best round: copy its GLB, create a votable ModelOutput(source='commissioned')
    under the '<model>-dgen' generator, persist a TraitVerdict per trait result + a Completeness
    row (so it flows through the existing /procedural board with no board change), and mark the
    iteration. Caller commits. Returns the new output id."""
    import json
    import shutil
    from pathlib import Path

    from app.commission import get_or_create_generator
    from app.completeness import upsert_completeness
    from app.judge import JUDGE_MODEL
    from app.models import ModelOutput, TraitRubric, TraitVerdict

    gen = get_or_create_generator(db, f"{model_id}-dgen")
    if gen.paradigm != "procedural_llm":
        gen.paradigm = "procedural_llm"
    rel = Path("dgen") / f"{gen.slug}_{task_id}.glb"
    dst = Path(asset_dir) / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(glb_src, dst)

    out = ModelOutput(
        task_id=task_id,
        generator_id=gen.id,
        asset_path=str(rel),
        asset_format="glb",
        source="commissioned",
        title=f"{model_id} (D-Gen r{best_iter.round})",
        meta_json=json.dumps(
            {
                "model_id": model_id,
                "round": best_iter.round,
                "fidelity": best_iter.fidelity,
                "dgen_run_id": best_iter.run_id,
            }
        ),
    )
    db.add(out)
    db.flush()

    rubric = db.query(TraitRubric).filter_by(task_id=task_id).first()
    if rubric is None:
        raise ValueError(
            f"ingest_best: no TraitRubric for task_id={task_id}; cannot persist trait verdicts"
        )
    for t in best_score.get("trait_results", []):
        db.add(
            TraitVerdict(
                output_id=out.id,
                rubric_id=rubric.id,
                trait_key=t.get("trait_key", ""),
                trait_class=t.get("trait_class", ""),
                verdict=t.get("verdict", ""),
                rationale=t.get("rationale", ""),
                judge_model=JUDGE_MODEL,
            )
        )
    upsert_completeness(
        db,
        out.id,
        category=best_score.get("completeness_category", ""),
        score=best_score.get("completeness_score"),
        checklist={
            "organs_present": best_score.get("completeness_organs_present", []),
            "note": "dgen-best",
        },
        judge_model=JUDGE_MODEL,
        scorer_version="dgen-v1",
    )

    best_iter.output_id = out.id
    best_iter.is_best = True
    return out.id


def _select_best(rounds: list[dict]) -> dict | None:
    """Best valid round: max fidelity; ties -> completeness 'complete' preferred, then earliest round."""
    valid = [
        r
        for r in rounds
        if r["status"] == "ok" and r["score"] and r["score"]["fidelity"] is not None
    ]
    if not valid:
        return None
    return max(
        valid,
        key=lambda r: (
            r["score"]["fidelity"],
            1 if r["comp_cat"] == "complete" else 0,
            -r["round"],
        ),
    )


def _taxon_slug(taxon: str) -> str:
    return taxon.lower().replace(" ", "_")


def refine_loop(
    db,
    *,
    run_id,
    taxon,
    task_id,
    prompt,
    common,
    model_id,
    traits,
    complete_fn,
    run_fn,
    score_fn,
    asset_dir,
    max_rounds: int = 3,
) -> dict:
    """Refine ONE taxon over <= max_rounds; plateau-stop on a valid-but-not-better round (a failed
    round never stops the loop); persist a DGenIteration per round; promote the best via ingest_best."""
    from pathlib import Path

    from app.commission import extract_script
    from app.models import DGenIteration

    tmp_dir = Path(asset_dir) / "dgen_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    rounds: list[dict] = []
    best_fid: float | None = None
    prev: dict | None = None

    for n in range(max_rounds):
        if n == 0 or prev is None:
            prompt_text = build_prompt(taxon, common)
            critique = ""
        else:
            critique = build_critique(
                (prev["score"] or {}).get("trait_results", []),
                traits,
                {"category": prev["comp_cat"], "missing_organs": prev["missing"]},
                prev["status"],
                prev["error"],
            )
            prompt_text = build_refine_prompt(taxon, common, prev["script"], critique)

        try:
            script = extract_script(complete_fn(model_id, prompt_text))
        except Exception as e:  # noqa: BLE001 — dispatch failure recorded, loop continues
            script = ""
            run = {"status": "error", "stderr": f"dispatch: {e}", "glb_path": None}
        else:
            out_glb = tmp_dir / f"{run_id}_{_taxon_slug(taxon)}_r{n}.glb"
            run = run_fn(script, out_glb)

        status = run.get("status", "error")
        if n == 0 and status == "ok" and run.get("glb_path"):
            import shutil

            baseline_dir = Path(asset_dir) / "dgen_baseline"
            baseline_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run["glb_path"], baseline_dir / f"{run_id}_{_taxon_slug(taxon)}.glb")
        score = None
        fid = None
        cat = ""
        cscore = None
        missing: list[str] = []
        if status == "ok" and run.get("glb_path"):
            try:
                score = score_fn(run["glb_path"])
                fid = score["fidelity"]
                cat = score["completeness_category"]
                cscore = score["completeness_score"]
                missing = score["completeness_missing_organs"]
            except Exception as e:  # noqa: BLE001 — render/judge failure recorded, loop continues
                status = "render_error"
                run["stderr"] = f"score: {e}"

        it = DGenIteration(
            run_id=run_id,
            taxon=taxon,
            round=n,
            fidelity=fid,
            n_correct=(score or {}).get("n_correct", 0),
            n_assessable=(score or {}).get("n_assessable", 0),
            completeness_category=cat or "",
            completeness_score=cscore,
            critique=critique,
            script=script,
            status=status,
        )
        db.add(it)
        db.flush()

        rec = {
            "round": n,
            "score": score,
            "glb_path": run.get("glb_path"),
            "status": status,
            "error": run.get("stderr", ""),
            "script": script,
            "comp_cat": cat,
            "missing": missing,
            "iter": it,
        }
        rounds.append(rec)
        prev = rec

        if status == "ok" and fid is not None:
            if best_fid is not None and fid <= best_fid:
                break  # plateau: a valid round that didn't beat the best
            best_fid = fid if best_fid is None else max(best_fid, fid)

    best = _select_best(rounds)
    if best is not None:
        ingest_best(
            db,
            model_id=model_id,
            task_id=task_id,
            glb_src=best["glb_path"],
            best_score=best["score"],
            best_iter=best["iter"],
            asset_dir=asset_dir,
        )

    fid0 = rounds[0]["score"]["fidelity"] if (rounds and rounds[0]["score"]) else None
    return {
        "taxon": taxon,
        "best_round": best["round"] if best else None,
        "fidelity_0": fid0,
        "fidelity_best": best_fid,
        "n_rounds": len(rounds),
    }
