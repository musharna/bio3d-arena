"""Commissioned-generation arena harness (core).

Give each competing LLM the same organism-generation task, run the bpy script it writes in a
sandbox, and ingest the resulting mesh as an agent-attributed arena output. Pure/core helpers
here (prompt build, script extraction, OpenRouter dispatch, sandbox run, mesh validation, DB
ingestion); scripts/commission_arena.py wires the real HTTP client + Blender.

The roster and every prompt's body plan are driven by ORGAN_INVENTORY (the registry the
completeness metric already scores against), so this harness reaches every kingdom. It used to
iterate a literal 6-plant dict, which is why fungi and animals had zero code-gen outputs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import trimesh

from .organ_inventory import ORGAN_INVENTORY, inventory_for


_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_SECRET_ENV_EXACT = {"BIO3D_DATABASE_URL"}


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Common name per taxon. Every taxon in ORGAN_INVENTORY must appear here — the roster is derived
# from that registry, so an organism it can see but cannot name is a fail-loud error (common_name).
SPECIES_COMMON: dict[str, str] = {
    # Plantae
    "Solanum lycopersicum": "tomato",
    "Zea mays": "maize (corn)",
    "Pinus sylvestris": "Scots pine",
    "Rosa": "rose",
    "Glycine max": "soybean",
    "Arabidopsis thaliana": "Arabidopsis (thale cress)",
    # Fungi
    "Lycoperdon perlatum": "common puffball",
    "Cucurbita pepo": "field pumpkin (gourd)",
    "Hericium erinaceus": "lion's mane mushroom",
    "Boletus edulis": "porcini",
    "Amanita muscaria": "fly agaric",
    "Morchella esculenta": "common morel",
    "Trametes versicolor": "turkey tail",
    # Animalia
    "Canis lupus familiaris": "dog",
    "Anas platyrhynchos": "mallard duck",
    "Danaus plexippus": "monarch butterfly",
    "Carassius auratus": "goldfish",
}


def common_name(taxon: str) -> str:
    """The taxon's common name. Fail loud: the roster comes from ORGAN_INVENTORY, so a taxon the
    generator can reach but cannot name is a registry bug, not something to paper over."""
    try:
        return SPECIES_COMMON[taxon]
    except KeyError:
        raise KeyError(
            f"no common name for {taxon!r} — add it to SPECIES_COMMON; every taxon in "
            "ORGAN_INVENTORY must be nameable"
        ) from None


def _body_plan(species: str) -> str:
    """The 'what must this organism have' block, read from ORGAN_INVENTORY — the same registry the
    completeness metric scores the result against. Asking a fish for the right body plan is what
    makes the ask answerable; this prompt used to demand 'stem/trunk, leaves' of every taxon."""
    inv = inventory_for(species)
    if inv is None:
        return (
            "- Build real geometry for the whole organism: every structure a specimen visibly has."
        )

    lines = ["- Build real geometry for the whole organism. A specimen must show:"]
    for organ in (o for o in inv.organs if o.required):
        count = f" (exactly {organ.complement} of them)" if organ.complement > 1 else ""
        lines.append(f"    - {organ.visual}{count}")
    optional = [o.visual for o in inv.organs if not o.required]
    if optional:
        lines.append("  Where this specimen has them, also include: " + "; ".join(optional) + ".")
    return "\n".join(lines)


def build_prompt(species: str, common: str) -> str:
    """Prompt an LLM for a bpy script building ONE whole organism of `species`.

    Body-plan requirements come from ORGAN_INVENTORY, so a fungus is asked for a cap on a stalk and
    a goldfish for its two pectoral fins. The prompt is kingdom-neutral: it used to say
    "botanically accurate ... whole {common} plant", which is why only plants were commissionable."""
    return (
        f"Write a complete Blender Python (bpy) script that procedurally generates a "
        f"biologically accurate 3D model of a whole {common} ({species}).\n\n"
        "Runtime: the script runs on Blender 4.2.0 — use only bpy APIs valid in Blender 4.2 "
        "(note Blender 4.x renamed several Principled BSDF sockets, e.g. 'Subsurface' -> "
        "'Subsurface Weight'). Keep material setup minimal and defensive so one wrong socket "
        "name cannot abort the whole script (guard optional material tweaks in try/except).\n\n"
        "Requirements:\n"
        f"{_body_plan(species)}\n"
        "- Model exactly ONE whole specimen — not a cluster, not a scene, not a detached part.\n"
        "- Export the result as GLB to the path in the environment variable OUT_GLB "
        "(read it with os.environ['OUT_GLB']).\n"
        "- The script must run headless under `blender --background --python` with no user "
        "interaction and no external asset files.\n"
        "- Output ONLY the Python script — no explanation, no markdown prose."
    )


def openrouter_complete(
    post,
    model_id: str,
    prompt: str,
    *,
    api_key: str,
    max_tokens: int = 32000,
    max_retries: int = 3,
    sleep_fn=None,
) -> str:
    """One chat completion via OpenRouter (OpenAI-compatible). `post` injected (httpx.post) for
    testing. Retries up to max_retries times with exponential backoff on any dispatch failure
    (covers transient 429/5xx/transport errors); re-raises the last error if all attempts fail."""
    import time as _time

    sleep = sleep_fn or _time.sleep
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                },
                timeout=180,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 — bounded retry on transient dispatch failures
            last_exc = e
            if attempt < max_retries - 1:
                sleep(2**attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("openrouter_complete: max_retries must be >= 1")


def _sandbox_env(out_glb, base_env=None) -> dict:
    """Environment for the untrusted bpy subprocess: inherit the parent env MINUS any
    secret-looking vars (name contains KEY/TOKEN/SECRET/PASSWORD, or is BIO3D_DATABASE_URL),
    then inject OUT_GLB. Keeps PATH/HOME/etc. so Blender still runs."""
    src = os.environ if base_env is None else base_env
    out = {
        k: v
        for k, v in src.items()
        if k not in _SECRET_ENV_EXACT and not any(m in k.upper() for m in _SECRET_ENV_MARKERS)
    }
    out["OUT_GLB"] = str(out_glb)
    return out


def is_valid_mesh(glb_path) -> tuple[bool, dict]:
    """Load a GLB and report whether it has real geometry. (False, {}) on any load failure."""
    p = Path(glb_path)
    if not p.exists() or p.stat().st_size == 0:
        return False, {}
    try:
        scene = trimesh.load(str(p), force="scene")
    except Exception:  # noqa: BLE001 — any parse/load failure means invalid mesh
        return False, {}
    geoms = list(getattr(scene, "geometry", {}).values())
    vertices = sum(len(g.vertices) for g in geoms)
    faces = sum(len(g.faces) for g in geoms)
    ok = vertices > 0 and faces > 0
    return ok, {"meshes": len(geoms), "vertices": vertices, "faces": faces}


def run_bpy(
    script_text: str,
    *,
    out_glb,
    timeout_s: int = 120,
    blender_bin: str = "blender",
    sandbox_prefix: list[str] | None = None,
) -> dict:
    """Run an LLM-authored bpy script headless in a throwaway temp cwd with a sandboxed
    environment. Injects OUT_GLB into the environment and strips secret-looking vars
    (containing KEY/TOKEN/SECRET/PASSWORD, or BIO3D_DATABASE_URL). Returns a status dict;
    never raises on script failure. sandbox_prefix lets the caller wrap the command
    (e.g. ["heavy-run"] for a memory cap, ["unshare","-rn"] for no network) — kept
    configurable so tests run bare."""
    out_glb = Path(out_glb)
    prefix = list(sandbox_prefix or [])
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as td:
        script_path = Path(td) / "gen.py"
        script_path.write_text(script_text)
        env = _sandbox_env(out_glb)
        cmd = [*prefix, blender_bin, "--background", "--python", str(script_path)]
        try:
            proc = subprocess.run(
                cmd, env=env, cwd=td, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "stderr": "wall-clock timeout",
                "duration_ms": timeout_s * 1000,
                "glb_path": None,
                "mesh_stats": {},
            }
        except FileNotFoundError:
            return {
                "status": "error",
                "stderr": f"blender binary not found: {blender_bin}",
                "duration_ms": int((time.monotonic() - start) * 1000),
                "glb_path": None,
                "mesh_stats": {},
            }
        dur = int((time.monotonic() - start) * 1000)
        if proc.returncode != 0:
            return {
                "status": "error",
                "stderr": proc.stderr[-4000:],
                "duration_ms": dur,
                "glb_path": None,
                "mesh_stats": {},
            }
        ok, stats = is_valid_mesh(out_glb)
        return {
            "status": "ok" if ok else "invalid_mesh",
            "stderr": proc.stderr[-2000:],
            "duration_ms": dur,
            "glb_path": str(out_glb) if ok else None,
            "mesh_stats": stats,
        }


def extract_script(text: str) -> str:
    """Pull the Python script out of a chat completion. Prefer a CLOSED ```python fenced
    block; if the model opened a fence but never closed it (truncation / unterminated output),
    still strip the opening fence line and take the rest — otherwise the literal ```python line
    lands in the script and it dies on line 1. No nested/ambiguous quantifiers."""
    if not text:
        return ""
    m = re.search(r"```(?:python)?[ \t]*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"```(?:python)?[ \t]*\n(.*)", text, re.DOTALL)
    if m2:
        return m2.group(1).strip()
    return text.strip()


def slug_for_model(model_id: str) -> str:
    return "openrouter-" + re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")


def get_or_create_generator(db, model_id: str):
    from .models import Generator

    slug = slug_for_model(model_id)
    gen = db.query(Generator).filter_by(slug=slug).first()
    if gen is None:
        gen = Generator(
            slug=slug, name=model_id, kind="model", description="commissioned via OpenRouter"
        )
        db.add(gen)
        db.flush()
    return gen


def ingest_attempt(db, *, task_id: int, model_id: str, run: dict, script: str, asset_dir):
    """Persist one attempt. On status 'ok', copy the GLB under asset_dir/commissioned and
    create a ModelOutput(source='commissioned'); always create a CommissionAttempt."""
    from .models import CommissionAttempt, ModelOutput

    gen = get_or_create_generator(db, model_id)
    output_id = None
    if run.get("status") == "ok" and run.get("glb_path"):
        rel = Path("commissioned") / f"{gen.slug}_{task_id}.glb"
        dst = Path(asset_dir) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run["glb_path"], dst)
        out = ModelOutput(
            task_id=task_id,
            generator_id=gen.id,
            title=model_id,
            asset_path=str(rel),
            asset_format="glb",
            source="commissioned",
            meta_json=json.dumps({"model_id": model_id, "mesh_stats": run.get("mesh_stats", {})}),
        )
        db.add(out)
        db.flush()
        output_id = out.id
    att = CommissionAttempt(
        task_id=task_id,
        model_id=model_id,
        generator_id=gen.id,
        output_id=output_id,
        status=run.get("status", "error"),
        error=run.get("stderr", "") or "",
        script=script or "",
        mesh_stats_json=json.dumps(run.get("mesh_stats", {})),
        duration_ms=int(run.get("duration_ms", 0)),
    )
    db.add(att)
    db.commit()
    return att


def resolve_taxon_tasks(db) -> list[tuple[str, int]]:
    """(taxon, task_id) for every organism in ORGAN_INVENTORY that has a TraitRubric.

    Driven by the inventory rather than a hand-kept species literal: registering an organism —
    which you must do anyway for completeness scoring — is what makes it commissionable. Iterating
    the old 6-plant dict is why the 7 fungus and 4 animal tasks were unreachable, leaving the
    procedural_llm and agentic boards empty under a Fungi or Animals filter."""
    from .models import TraitRubric

    out = []
    for taxon in ORGAN_INVENTORY:
        r = db.query(TraitRubric).filter_by(taxon=taxon).first()
        if r is not None and r.task_id:
            common_name(taxon)  # fail loud now, not mid-run: reachable but unnameable is a bug
            out.append((taxon, r.task_id))
    return out


def existing_pairs(db) -> set[tuple[str, int]]:
    """Return set of (model_id, task_id) pairs already attempted."""
    from .models import CommissionAttempt

    return {(a.model_id, a.task_id) for a in db.query(CommissionAttempt).all()}


def run_batch(db, *, complete_fn, run_fn, roster, taxon_tasks, asset_dir, max_calls=None):
    """Run commissioned generation for each un-attempted (model_id, (taxon, task_id)) pair.

    Args:
        db: database session
        complete_fn: (model_id, prompt) -> str (LLM response)
        run_fn: (script, out_glb) -> dict (execution result)
        roster: list of model IDs to try
        taxon_tasks: list of (taxon, task_id) pairs
        asset_dir: root directory for saving assets
        max_calls: optional limit on number of attempts

    Returns:
        dict with counts by status: {"ok", "error", "timeout", "invalid_mesh", "skipped"}
    """
    counts = {"ok": 0, "error": 0, "timeout": 0, "invalid_mesh": 0, "skipped": 0}
    seen = existing_pairs(db)
    made = 0
    for model_id in roster:
        for taxon, task_id in taxon_tasks:
            if (model_id, task_id) in seen:
                counts["skipped"] += 1
                continue
            if max_calls is not None and made >= max_calls:
                return counts
            prompt = build_prompt(taxon, common_name(taxon))
            try:
                text = complete_fn(model_id, prompt)
                script = extract_script(text)
            except Exception as e:  # noqa: BLE001 — transport failure: record + continue
                run = {
                    "status": "error",
                    "stderr": f"dispatch: {e}",
                    "duration_ms": 0,
                    "glb_path": None,
                    "mesh_stats": {},
                }
                script = ""
            else:
                with tempfile.TemporaryDirectory() as td:
                    out_glb = Path(td) / "out.glb"
                    run = run_fn(script, out_glb)
                    if run.get("status") == "ok" and run.get("glb_path"):
                        # ingest copies from glb_path; keep it alive past the tempdir by ingesting now
                        att = ingest_attempt(
                            db,
                            task_id=task_id,
                            model_id=model_id,
                            run=run,
                            script=script,
                            asset_dir=asset_dir,
                        )
                        counts[att.status] = counts.get(att.status, 0) + 1
                        seen.add((model_id, task_id))
                        made += 1
                        continue
            att = ingest_attempt(
                db,
                task_id=task_id,
                model_id=model_id,
                run=run,
                script=script,
                asset_dir=asset_dir,
            )
            counts[att.status] = counts.get(att.status, 0) + 1
            seen.add((model_id, task_id))
            made += 1
    return counts
