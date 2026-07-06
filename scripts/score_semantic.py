# scripts/score_semantic.py
"""Batch-score semantic admissibility for outputs. Renders (or reuses) a turntable contact sheet
per output, runs the semantic VLM judge, persists an Admissibility(predicate='semantic') row.
Persistence is unconditional; advisory flags are emitted only when the configured mode is
'advisory'. Build the Anthropic client from ANTHROPIC_API_KEY (as scripts/judge_vlm.py does).

NEVER set BIO3D_DATABASE_URL=study. For the acceptance run, point BIO3D_DATABASE_URL at a COPY of
the study DB and set BIO3D_SEMANTIC_ADMISSIBILITY_MODE=off (persist verdicts, no advisory flags)."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

# bootstrap: allow `python scripts/<name>.py` without PYTHONPATH (repo root on sys.path)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.judge_render import contact_sheet_path  # noqa: E402
from app.semantic import enumerate_semantic_work, evaluate_outputs  # noqa: E402

CONDITION = "turntable"  # same as completeness -> cached sheets are reused
_RENDER_ONE = str(Path(__file__).resolve().parent / "render_one_sheet.py")
# A pathological GLB can WEDGE the model-viewer/chromium renderer with no exception + no page-timeout
# recovery, stalling the whole batch (observed: 8 min hung on one big recon mesh). So render each
# output in an ISOLATED subprocess (own process group) with a hard OS timeout; a wedge is force-
# killed (whole group, incl. chromium) and the output skipped instead of hanging the run.
_RENDER_TIMEOUT_S = int(os.environ.get("BIO3D_RENDER_TIMEOUT_S", "180"))


def _sheet_provider():
    """Read the turntable contact-sheet PNG bytes for an output, rendering it first (idempotently)
    in a timeout-guarded subprocess if not already cached. Raises on render timeout/failure so
    evaluate_outputs records the output as an error and moves on."""

    def sheet_for(output_id: int) -> bytes:
        path = os.path.join(config.ASSET_DIR, contact_sheet_path(output_id, CONDITION))
        if not (os.path.exists(path) and os.path.getsize(path) > 0):
            proc = subprocess.Popen(
                [sys.executable, _RENDER_ONE, str(output_id), CONDITION],
                start_new_session=True,  # own process group so we can kill chromium grandchildren
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                rc = proc.wait(timeout=_RENDER_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait()
                raise RuntimeError(
                    f"render wedged (> {_RENDER_TIMEOUT_S}s) for output {output_id} — killed + skipped"
                ) from None
            if rc != 0:
                raise RuntimeError(f"render failed (exit {rc}) for output {output_id}")
        with open(path, "rb") as f:
            return f.read()

    return sheet_for


def _build_client():
    import anthropic

    # Per-request timeout + retries: a stalled VLM connection was observed blocking the batch
    # forever in poll() (the SDK's 600s default is too long). 90s + retries recovers on a fresh
    # connection; a persistent failure raises and evaluate_outputs records it as a per-output error.
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=90.0, max_retries=3)


_CHUNK = 25  # commit every N outputs -> durable + resumable (a kill loses at most one chunk)


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-score semantic admissibility.")
    ap.add_argument("--limit", type=int, default=0, help="score at most N outputs (0 = all)")
    args = ap.parse_args()
    init_db()
    total = {"scored": 0, "errors": 0, "flagged": 0}
    with SessionLocal() as db:
        work = enumerate_semantic_work(db)
        if args.limit:
            work = work[: args.limit]
        emit_flags = config.SEMANTIC_ADMISSIBILITY_MODE == "advisory"
        sheet_for = _sheet_provider()
        client = _build_client()
        for i in range(0, len(work), _CHUNK):
            summary = evaluate_outputs(
                db, work[i : i + _CHUNK], client=client, sheet_for=sheet_for, emit_flags=emit_flags
            )
            db.commit()  # durable per chunk: progress survives a kill, run is resumable
            for k in total:
                total[k] += summary[k]
            print(
                f"chunk {i // _CHUNK + 1}: +{summary['scored']} scored, +{summary['errors']} err "
                f"(cum {total['scored']}/{len(work)})",
                flush=True,
            )
    print({"mode": config.SEMANTIC_ADMISSIBILITY_MODE, **total})
    return 0 if total["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
