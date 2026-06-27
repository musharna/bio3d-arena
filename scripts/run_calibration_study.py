"""Robust operational runner for the VLM calibration study (calibration-only scope).

Two phases, run as SEPARATE invocations so the browser is gone during the long
API phase (the render-heavy chromium is the memory/availability-sensitive part;
the judge phase is pure network):

    python scripts/run_calibration_study.py render               # pre-render all ladder sheets, then exit
    python scripts/run_calibration_study.py judge [--max N]      # judge from cache, NO browser

Both phases are idempotent/resumable: renders skip cached files; judge skips
swap-group/order rows already in JudgeVote. Run the judge phase in bounded
chunks (--max ~120) if the host reaps long-lived background jobs — each vote is
committed immediately, so re-running resumes cleanly.

Scope is the calibration ladder only (each CalibrationPair × all view conditions),
via judge_vlm's calibration_only path — NOT the full pairwise grid.

Env: BIO3D_DATA_DIR (assets), BIO3D_DB_PATH (DB), ANTHROPIC_API_KEY (judge phase).
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config, judge, judge_render  # noqa: E402
from app.models import CalibrationPair  # noqa: E402
from scripts import judge_vlm  # noqa: E402


def _ladder_sheets(db) -> list[tuple[int, str]]:
    """Every (output_id, condition) the calibration ladder will need."""
    need: set[tuple[int, str]] = set()
    for cp in db.execute(select(CalibrationPair)).scalars():
        for oid in (cp.output_a_id, cp.output_b_id):
            for cond in judge_render.CONDITIONS:
                need.add((oid, cond))
    return sorted(need)


def phase_render() -> int:
    from app.database import SessionLocal
    from scripts.judge_capture import browser_capture_multi_factory

    with SessionLocal() as db:
        need = _ladder_sheets(db)
        todo = [
            (oid, cond)
            for oid, cond in need
            if not (Path(config.ASSET_DIR) / judge_render.contact_sheet_path(oid, cond)).exists()
        ]
        print(f"ladder sheets: {len(need)} needed, {len(todo)} to render", flush=True)
        if not todo:
            print("all sheets cached", flush=True)
            return 0
        capture_multi = browser_capture_multi_factory()
        done = errors = 0
        for oid, cond in todo:
            try:
                judge_render.render_contact_sheets(db, [oid], cond, capture_multi=capture_multi)
                done += 1
            except Exception as e:  # noqa: BLE001 — best-effort; resume re-renders the gap
                errors += 1
                print(f"render error oid={oid} cond={cond}: {e}", file=sys.stderr, flush=True)
        print(f"RENDER DONE rendered={done} errors={errors}", flush=True)
    return 0


def phase_judge(max_votes: int | None) -> int:
    import anthropic

    from app.database import SessionLocal

    client = anthropic.Anthropic()

    def cache_only_sheet_b64(output_id: int, condition: str) -> str:
        path = Path(config.ASSET_DIR) / judge_render.contact_sheet_path(output_id, condition)
        if not path.exists():
            raise FileNotFoundError(f"sheet not pre-rendered (run render phase first): {path}")
        return base64.b64encode(path.read_bytes()).decode()

    def judge_fn(species, prompt, cname, cdesc, a_b64, b_b64):
        return judge.judge_pair(
            client,
            species=species,
            prompt=prompt,
            criterion_name=cname,
            criterion_desc=cdesc,
            sheet_a_b64=a_b64,
            sheet_b_b64=b_b64,
        )

    with SessionLocal() as db:
        res = judge_vlm.run_batch(
            db,
            judge_fn=judge_fn,
            sheet_b64=cache_only_sheet_b64,
            calibration_only=True,
            max_votes=max_votes,
        )
    print("JUDGE RESULT:", res, flush=True)
    return 0


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("phase", choices=("render", "judge"))
    ap.add_argument("--max", type=int, default=None, help="judge: cap votes written this run")
    args = ap.parse_args()
    return phase_render() if args.phase == "render" else phase_judge(args.max)


if __name__ == "__main__":
    raise SystemExit(main())
