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
    "botanically accurate ... whole {common} plant", which is why only plants were commissionable.

    The prompt says NOTHING about exporting. It used to end with "export the result as GLB to
    os.environ['OUT_GLB']", and 14% of every failure in the first sweep died right there — on
    invented kwargs to `bpy.ops.export_scene.gltf` (use_selected, export_colors, export_y_up), with
    the organism already fully built in the scene. That is us scoring our own plumbing. The harness
    owns the export now (see RUNNER_SRC); the model is asked for a mesh and judged on the mesh."""
    return (
        f"Write a complete Blender Python (bpy) script that procedurally generates a "
        f"biologically accurate 3D model of a whole {common} ({species}).\n\n"
        "Runtime: the script runs on Blender 4.2.0 — use only bpy APIs valid in Blender 4.2 "
        "(note Blender 4.x renamed several Principled BSDF sockets, e.g. 'Subsurface' -> "
        "'Subsurface Weight'). Keep material setup minimal and defensive so one wrong socket "
        "name cannot abort the whole script (guard optional material tweaks in try/except).\n\n"
        "Avoid these bpy 4.2 API mistakes — each one aborts the whole script:\n"
        "- bmesh.ops has NO create_cylinder/create_cube/create_sphere. Make primitives with "
        "bpy.ops.mesh.primitive_*_add, or bmesh.ops.create_cone (a cone with equal radii and "
        "capped ends is a cylinder) / create_circle / create_icosphere.\n"
        "- After an operator that deletes or recreates an object, any Python variable still "
        "pointing at the old object is dead ('StructRNA of type Object has been removed'); re-fetch "
        "it from bpy.data.objects or bpy.context after such a call.\n"
        "- A node socket's default_value must match its type: a scalar socket wants a float, a "
        "color/vector socket wants a tuple — never assign a tuple to a float socket.\n"
        "- bpy.context.active_object and bpy.context.object can be None; check before touching "
        ".data. Deselect with bpy.ops.object.select_all(action='DESELECT'), not a .clear() call.\n\n"
        "Requirements:\n"
        f"{_body_plan(species)}\n"
        "- Model exactly ONE whole specimen — not a cluster, not a scene, not a detached part.\n"
        "- Leave the finished specimen in the scene. Saving it is handled for you — do not write "
        "any file, and do not clear the scene at the end.\n"
        "- The script must run headless under `blender --background --python` with no user "
        "interaction and no external asset files.\n"
        "- Output ONLY the Python script — no explanation, no markdown prose."
    )


def _final_exception_line(error: str) -> str:
    """The most specific exception line in a Blender traceback, seen THROUGH the runner's
    @@BIO3D_MODEL_ERROR@@ sentinel. Foregrounding it in the repair prompt focuses the fix: every one
    of the 32 residual sweep failures was one of a handful of bpy-API mistakes (a nonexistent
    operator, a stale object ref, a wrong socket value type), and the model repairs the exact call
    faster when told which line blew up than when left to re-read the whole dump."""
    lines = [ln.strip() for ln in (error or "").splitlines() if ln.strip()]
    real = [ln for ln in lines if "@@BIO3D_MODEL_ERROR@@" not in ln]
    for ln in reversed(real):
        head = ln.split(":", 1)[0].strip()
        if ("Error" in head or "Exception" in head) and head.replace(".", "").isalnum():
            return ln
    return real[-1] if real else ""


def repair_prompt(original_prompt: str, script: str, error: str) -> str:
    """Hand the model its own traceback back and ask for a corrected script.

    Why this exists: re-running grok-4.20 under the sweep's exact conditions, on taxa the sweep
    scored as failures, returned ['ok','invalid_mesh'] and ['invalid_mesh','ok'] — coin flips
    published as flat zeros. Its 2/17 was a sampling artifact. Nobody uses an LLM by taking the
    first script and walking away when it throws; they paste the error back. Measuring only the
    unaided shot measures something real but narrow, so the harness now measures BOTH.

    The exact exception is FOREGROUNDED above the raw dump (see _final_exception_line): the residual
    failures are bpy-API mistakes, not wrong approaches, so naming the failing call and telling the
    model to fix that one thing (not rewrite) is what converts a stuck cell into a pass."""
    exc = _final_exception_line(error)
    headline = f"The error was:\n\n    {exc}\n\n" if exc else ""
    return (
        f"{original_prompt}\n\n"
        "---\n\n"
        "Your previous script failed when it was run.\n\n"
        f"{headline}"
        "Here is the script:\n\n"
        f"```python\n{script}\n```\n\n"
        "Here is the full traceback Blender reported:\n\n"
        f"```\n{error.strip()[-3000:]}\n```\n\n"
        "This is almost certainly a bpy API mistake (a nonexistent operator, a stale object "
        "reference after a mutating op, or a wrong socket value type) rather than a wrong overall "
        "approach — fix that specific call and keep the rest of your model. Output the COMPLETE "
        "corrected script. Output ONLY the Python script — no explanation, no markdown prose."
    )


def openrouter_complete(
    post,
    model_id: str,
    prompt: str,
    *,
    api_key: str,
    max_tokens: int = 32000,
    max_retries: int = 3,
    temperature: float | None = None,
    sleep_fn=None,
) -> str:
    """One chat completion via OpenRouter (OpenAI-compatible). `post` injected (httpx.post) for
    testing. Retries up to max_retries times with exponential backoff on any dispatch failure
    (covers transient 429/5xx/transport errors); re-raises the last error if all attempts fail.

    `temperature` is sent only when set. The first sweep sent none at all, so every cell ran at
    whatever default its provider happened to use and the board was partly ranking sampling noise —
    a benchmark picks its own sampler rather than inheriting eleven of them."""
    import time as _time

    sleep = sleep_fn or _time.sleep
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
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


class HarnessError(RuntimeError):
    """The harness itself cannot run. Fatal: never recorded as a model's failed attempt."""


# HTTP statuses that mean OUR ACCOUNT is the problem, not the model: no credit (402), bad or
# missing key (401), forbidden (403). Mid-sweep the OpenRouter balance ran out and every subsequent
# call 402'd — each one recorded as status="error" against the model, giving qwen3.6-plus a 0/17
# record it had not earned, feeding /procedural's pass@1, and (UNIQUE(model_id, task_id)) burning
# those pairs permanently. An account failure is a STOP, not a result.
ACCOUNT_FAILURE_STATUSES = frozenset({401, 402, 403})


def _is_account_failure(exc: Exception) -> bool:
    """True if this dispatch exception is our account failing rather than the model.

    Duck-typed on purpose: the HTTP client is injected (httpx today), so commission.py never
    imports it. Falls back to the status code in the message, which is how httpx renders it
    ("Client error '402 Payment Required' for url ...")."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code in ACCOUNT_FAILURE_STATUSES
    return any(f"'{s} " in str(exc) or f" {s} " in str(exc) for s in ACCOUNT_FAILURE_STATUSES)


def preflight_sandbox(
    *, sandbox_prefix: list[str] | None = None, blender_bin: str = "blender"
) -> None:
    """Prove the sandbox can actually run Blender, BEFORE any LLM call or attempt row.

    run_bpy shells out to [*sandbox_prefix, blender, ...] and maps every non-zero exit to
    status="error" against the MODEL. So when the WRAPPER fails — heavy-run is a systemd user
    scope and exits 1 inside a detached session — three valid bpy scripts got recorded as three
    models failing the task, in 12ms, with an empty stderr. /procedural computes pass@1 from those
    rows, so a broken sandbox would publish itself as a model's score. Fail loud here instead."""
    cmd = [*(sandbox_prefix or []), blender_bin, "--version"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as e:
        raise HarnessError(f"sandbox preflight: cannot execute {cmd!r} — {e}") from None
    except subprocess.TimeoutExpired:
        raise HarnessError(f"sandbox preflight: {cmd!r} hung past 60s") from None
    if proc.returncode != 0:
        raise HarnessError(
            f"sandbox preflight FAILED (exit {proc.returncode}) for {cmd!r}. The wrapper cannot "
            "run Blender, so every attempt would be recorded as the MODEL failing. "
            f"stderr: {proc.stderr[-500:]!r}. "
            "(heavy-run needs a systemd user session — it exits 1 under a detached setsid. Run "
            "via jobd, or pass --sandbox-prefix '' to drop the memory cap.)"
        )


EXIT_MODEL_ERROR = 3  # the model's script raised — its fault, recorded as script_error
EXIT_HARNESS_EXPORT_ERROR = 4  # OUR export raised — our fault, never the model's

# The harness runs THIS, not the model's script. It execs the model's code, then exports the scene
# itself.
#
# Two bugs die here.
#
# (1) The export used to be the model's job, so a model could build a flawless organism and still
#     score zero by mis-remembering a kwarg on `bpy.ops.export_scene.gltf`. 14% of the first
#     sweep's failures were exactly that.
#
# (2) Blender EXITS 0 WHEN A --python SCRIPT RAISES. (Verified against Blender 4.2.0: an uncaught
#     exception prints its traceback and the process returns 0.) run_bpy checked only the exit
#     code, so every crashed script fell through to the mesh check, found no GLB, and was filed as
#     `invalid_mesh` — which is why 98% of "invalid_mesh" rows carry a Python traceback. Catching
#     the model's exception here and exiting 3 makes "the model wrote broken code" (script_error)
#     distinguishable from "the script ran and built nothing" (invalid_mesh).
#
# compile(..., "gen.py", ...) keeps the model's filename and line numbers in the traceback, which
# is what the repair loop hands back to the model.
RUNNER_SRC = """\
import os, sys, traceback

import bpy

GEN, OUT = os.environ["GEN_SCRIPT"], os.environ["OUT_GLB"]

try:
    src = open(GEN).read()
    exec(compile(src, "gen.py", "exec"), {"__name__": "__main__", "__file__": GEN})
except SystemExit:
    pass  # a script calling sys.exit() after building its scene is fine
except BaseException:
    traceback.print_exc()
    sys.stderr.write("\\n@@BIO3D_MODEL_ERROR@@\\n")
    sys.exit(3)

try:
    bpy.ops.export_scene.gltf(filepath=OUT, export_format="GLB")
except BaseException:
    traceback.print_exc()
    sys.stderr.write("\\n@@BIO3D_HARNESS_EXPORT_ERROR@@\\n")
    sys.exit(4)

sys.exit(0)
"""


def classify_exit(returncode: int, *, stderr: str) -> str | None:
    """Map the runner's exit code to a status. None means "defer to the mesh check".

    HarnessError on our own export failing is the whole point. This is the third time our
    infrastructure has been recorded as a model's result — the sandbox wrapper exiting 1, then a
    402 from our billing, now the export. Each one published OUR failure as the model's pass@1, and
    UNIQUE(model_id, task_id) made it permanent. A harness failure is a STOP, not a score."""
    if returncode == EXIT_HARNESS_EXPORT_ERROR:
        raise HarnessError(
            "OUR GLB export failed on a script that ran to completion — the model built its "
            f"organism and we lost it. Nothing recorded. stderr: {stderr[-1500:]!r}"
        )
    if returncode == EXIT_MODEL_ERROR:
        return "script_error"
    if returncode != 0:
        return "error"  # blender itself died (segfault, OOM kill): not a clean model traceback
    return None


def run_bpy(
    script_text: str,
    *,
    out_glb,
    timeout_s: int = 120,
    blender_bin: str = "blender",
    sandbox_prefix: list[str] | None = None,
) -> dict:
    """Run an LLM-authored bpy script headless in a throwaway temp cwd with a sandboxed
    environment. The model's script is exec'd by RUNNER_SRC, which owns the GLB export.

    Strips secret-looking vars from the child env (containing KEY/TOKEN/SECRET/PASSWORD, or
    BIO3D_DATABASE_URL). Returns a status dict; never raises on script failure — but DOES raise
    HarnessError when the failure is ours. sandbox_prefix lets the caller wrap the command
    (e.g. ["heavy-run"] for a memory cap) — kept configurable so tests run bare.

    Status is one of: ok | script_error (the model's code raised) | invalid_mesh (it ran and built
    nothing) | timeout | error (Blender itself died)."""
    out_glb = Path(out_glb)
    prefix = list(sandbox_prefix or [])
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as td:
        script_path = Path(td) / "gen.py"
        script_path.write_text(script_text)
        runner_path = Path(td) / "runner.py"
        runner_path.write_text(RUNNER_SRC)
        env = _sandbox_env(out_glb)
        env["GEN_SCRIPT"] = str(script_path)
        cmd = [*prefix, blender_bin, "--background", "--python", str(runner_path)]
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
        status = classify_exit(proc.returncode, stderr=proc.stderr)  # raises on OUR export failing
        if status is not None:
            return {
                "status": status,
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


def run_with_repair(
    *,
    complete_fn,
    run_fn,
    model_id: str,
    prompt: str,
    out_glb,
    max_repairs: int = 2,
) -> dict:
    """One model, one task: the unaided attempt, then up to `max_repairs` rounds with the traceback.

    Returns {"run", "script", "rounds", "status_oneshot"} — BOTH outcomes, on purpose. pass@1 has
    always claimed to be the unaided number and `status_oneshot` is finally exactly that; `run` is
    what the model gets to when you do what everyone actually does and paste the error back.
    Neither one alone is the honest measure, so the row carries both.

    A repair round is only ever spent on a script that FAILED, so a strong model costs one call."""
    script = extract_script(complete_fn(model_id, prompt))
    run = run_fn(script, out_glb)
    status_oneshot = run.get("status", "error")
    rounds = 1

    for _ in range(max_repairs):
        if run.get("status") == "ok":
            break
        fix = repair_prompt(prompt, script, run.get("stderr", "") or "")
        script = extract_script(complete_fn(model_id, fix))
        run = run_fn(script, out_glb)
        rounds += 1

    return {"run": run, "script": script, "rounds": rounds, "status_oneshot": status_oneshot}


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


def get_or_create_generator(db, model_id: str, *, paradigm: str = "procedural_llm"):
    """Fetch (or create) the generator for a commissioned model, asserting its paradigm.

    The paradigm is KNOWN at creation — the commission harness only makes procedural_llm entrants
    (an LLM authoring Blender-Python), and /procedural filters on paradigm == 'procedural_llm', so a
    generator born blank is invisible to its own board until a manual backfill. The creator states
    the paradigm it knows instead of deferring to a classify pass. `paradigm` defaults to the common
    case; a caller building a different kind of entrant passes its own. A pre-existing row with a
    blank paradigm is healed here (the harness now telling it what it is); a row that already carries
    a deliberate, non-blank paradigm is left untouched.
    """
    from .models import Generator

    slug = slug_for_model(model_id)
    gen = db.query(Generator).filter_by(slug=slug).first()
    if gen is None:
        gen = Generator(
            slug=slug,
            name=model_id,
            kind="model",
            description="commissioned via OpenRouter",
            paradigm=paradigm,
        )
        db.add(gen)
        db.flush()
    elif not gen.paradigm:
        gen.paradigm = paradigm
    return gen


def ingest_attempt(
    db,
    *,
    task_id: int,
    model_id: str,
    run: dict,
    script: str,
    asset_dir,
    protocol: str = "repair",
    status_oneshot: str = "",
    rounds: int = 1,
):
    """Persist one attempt. On status 'ok', copy the GLB under asset_dir/commissioned and
    create a ModelOutput(source='commissioned') — UNLESS this cell already has one; always create a
    CommissionAttempt.

    `protocol` is passed explicitly by every caller: the model's "legacy" default exists only so
    that PRE-EXISTING rows self-heal to the truth about themselves when the column is added, and a
    new row silently inheriting it would quietly poison the scorecard's exclusion filter.

    An attempt is a MEASUREMENT of a cell; a ModelOutput is the model's ENTRANT in the arena on that
    task, and the arena's invariant is one entrant per (task, generator). Those used to be the same
    thing, because UNIQUE(model_id, task_id) meant a cell could only ever be attempted once. Now
    that a cell can be re-measured under a new protocol, they part company: re-measuring must not
    mint a second entrant (that is the same-generator BT pollution we already fixed once) and must
    not copy its mesh over the entrant's — which would silently rewrite the very asset earlier votes
    were cast on. So a re-run of a covered cell records the measurement in full and leaves the arena
    alone; `status` is what says the mesh was valid, and output_id NULL here means "no entrant slot
    to fill", not "no mesh". A cell the old harness scored as a flat failure has no entrant, so a
    model that now succeeds does enter the pool — which is the whole point of the re-run."""
    from .models import CommissionAttempt, ModelOutput

    gen = get_or_create_generator(db, model_id)
    output_id = None
    covered = (
        db.query(ModelOutput.id).filter_by(task_id=task_id, generator_id=gen.id).first() is not None
    )
    if run.get("status") == "ok" and run.get("glb_path") and not covered:
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
        protocol=protocol,
        status_oneshot=status_oneshot,
        rounds=rounds,
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


def existing_pairs(db, protocol: str = "repair") -> set[tuple[str, int]]:
    """(model_id, task_id) pairs already attempted UNDER THIS PROTOCOL.

    Protocol-scoped so the legacy rows do not block the re-run. They record what a different (and
    broken) harness measured; a pair being "done" under that harness says nothing about whether it
    has been measured under this one."""
    from .models import CommissionAttempt

    rows = db.query(CommissionAttempt).filter_by(protocol=protocol).all()
    return {(a.model_id, a.task_id) for a in rows}


def failed_pairs(db, protocol: str) -> set[tuple[str, int]]:
    """(model_id, task_id) pairs whose attempt under `protocol` did NOT end 'ok'.

    The re-measurement set for a harness improvement: an already-passing cell has an entrant and
    re-running it is a wasted (billed) call, so a lift run touches only the ones that failed."""
    from .models import CommissionAttempt

    rows = (
        db.query(CommissionAttempt.model_id, CommissionAttempt.task_id)
        .filter(CommissionAttempt.protocol == protocol, CommissionAttempt.status != "ok")
        .all()
    )
    return {(m, t) for m, t in rows}


def run_batch(
    db,
    *,
    complete_fn,
    run_fn,
    roster,
    taxon_tasks,
    asset_dir,
    max_calls=None,
    max_repairs: int = 2,
    protocol: str = "repair",
    on_progress=None,
    pairs=None,
):
    """Run commissioned generation for each un-attempted (model_id, (taxon, task_id)) pair.

    Each pair gets the unaided script and, if it failed, up to `max_repairs` rounds with the
    traceback handed back (see run_with_repair). The row keeps BOTH outcomes.

    Args:
        db: database session
        complete_fn: (model_id, prompt) -> str (LLM response)
        run_fn: (script, out_glb) -> dict (execution result)
        roster: list of model IDs to try
        taxon_tasks: list of (taxon, task_id) pairs
        asset_dir: root directory for saving assets
        max_calls: optional limit on the number of PAIRS attempted (not LLM calls)
        max_repairs: repair rounds allowed after a failing script
        protocol: recorded on every row; the scorecard groups by it
        pairs: optional set of (model_id, task_id) to restrict the run to — used to re-measure JUST
            the cells that failed under a prior protocol without re-generating (and re-billing) the
            whole roster. None means the full roster x taxon_tasks cross product.
        on_progress: optional (done, total, model_id, task_id, status) -> None, called once per
            ATTEMPTED cell (never for a skipped one). The batch runner passes a callback that prints
            and flushes a heartbeat line: without it, run_batch is silent between the plan line and
            the final summary, and a jobd worker's stdout-idle watchdog SIGTERMs the (productive) job
            at its idle timeout. It also makes the log track real progress.

    Returns:
        counts by final status, plus "pass_oneshot" (how many passed with no help at all)
    """
    # dispatch_failed: the call never returned a script (timeout, 429, 5xx). Counted, NOT recorded
    # as an attempt — a row would burn the (model, task) pair forever under the UNIQUE constraint.
    counts = {
        "ok": 0,
        "error": 0,
        "script_error": 0,
        "timeout": 0,
        "invalid_mesh": 0,
        "skipped": 0,
        "dispatch_failed": 0,
        "pass_oneshot": 0,
    }

    def in_scope(m, tid):
        return pairs is None or (m, tid) in pairs

    seen = existing_pairs(db, protocol)
    total = sum(
        1 for m in roster for _, tid in taxon_tasks if in_scope(m, tid) and (m, tid) not in seen
    )
    processed = 0  # attempted (non-skipped) cells so far — the heartbeat's numerator
    made = 0
    for model_id in roster:
        for taxon, task_id in taxon_tasks:
            if not in_scope(model_id, task_id):
                continue  # out of the targeted re-measurement set — not counted at all
            if (model_id, task_id) in seen:
                counts["skipped"] += 1
                continue
            if max_calls is not None and made >= max_calls:
                return counts
            prompt = build_prompt(taxon, common_name(taxon))
            with tempfile.TemporaryDirectory() as td:
                out_glb = Path(td) / "out.glb"
                try:
                    res = run_with_repair(
                        complete_fn=complete_fn,
                        run_fn=run_fn,
                        model_id=model_id,
                        prompt=prompt,
                        out_glb=out_glb,
                        max_repairs=max_repairs,
                    )
                except HarnessError:
                    raise  # our sandbox or our export — never a model's result
                except Exception as e:  # noqa: BLE001 — dispatch failure, see below
                    # A model is judged ONLY on a script it actually returned. A dispatch failure is
                    # ours or the network's, never the model's — and recording one would both
                    # publish it as the model's pass@1 AND burn the pair forever under
                    # UNIQUE(model_id, task_id).
                    if _is_account_failure(e):
                        raise HarnessError(
                            f"account failure calling {model_id!r}: {e}. Nothing was recorded. "
                            "This is OUR account, not the model — recording it would publish a "
                            "billing problem as the model's score. Top up / fix credentials and "
                            "re-run (the run is resumable)."
                        ) from e
                    counts["dispatch_failed"] += 1  # transient: no row, so the pair stays retryable
                    processed += 1
                    if on_progress is not None:
                        on_progress(processed, total, model_id, task_id, "dispatch_failed")
                    continue
                # ingest copies from glb_path, so it must happen before the tempdir is torn down
                att = ingest_attempt(
                    db,
                    task_id=task_id,
                    model_id=model_id,
                    run=res["run"],
                    script=res["script"],
                    asset_dir=asset_dir,
                    protocol=protocol,
                    status_oneshot=res["status_oneshot"],
                    rounds=res["rounds"],
                )
            counts[att.status] = counts.get(att.status, 0) + 1
            if res["status_oneshot"] == "ok":
                counts["pass_oneshot"] += 1
            seen.add((model_id, task_id))
            made += 1
            processed += 1
            if on_progress is not None:
                on_progress(processed, total, model_id, task_id, att.status)
    return counts
