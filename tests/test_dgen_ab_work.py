# tests/test_dgen_ab_work.py
from pathlib import Path

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task, DGenRun, DGenIteration
from app.dgen_ab import ab_work, render_sheet


def setup_module(_m):
    init_db()


def _seed_output(db, taxon, asset_rel):
    cat = Category(slug=f"{taxon[:6].lower()}-abw", name=taxon)
    gen = Generator(slug=f"gen-abw-{taxon[:6].lower()}", name="g", paradigm="procedural_llm")
    db.add_all([cat, gen])
    db.flush()
    task = Task(category_id=cat.id, title="t", prompt="p")
    db.add(task)
    db.flush()
    out = ModelOutput(
        task_id=task.id,
        generator_id=gen.id,
        asset_path=asset_rel,
        asset_format="glb",
        source="commissioned",
    )
    db.add(out)
    db.flush()
    return out.id


def test_ab_work_buckets_taxa(tmp_path):
    asset_dir = tmp_path / "assets"
    (asset_dir / "dgen_baseline").mkdir(parents=True)
    with SessionLocal() as db:
        run = DGenRun(model_id="m")
        db.add(run)
        db.flush()
        # Rosa: best_round=2 (refined) + baseline present -> "ab"
        oid = _seed_output(db, "Rosa", "best/rosa.glb")
        (Path(asset_dir) / "best").mkdir(exist_ok=True)
        (Path(asset_dir) / "best" / "rosa.glb").write_bytes(b"GLB")
        (asset_dir / "dgen_baseline" / f"{run.id}_rosa.glb").write_bytes(b"GLB")
        db.add(DGenIteration(run_id=run.id, taxon="Rosa", round=0, status="ok", fidelity=0.3))
        db.add(
            DGenIteration(
                run_id=run.id,
                taxon="Rosa",
                round=2,
                status="ok",
                fidelity=0.8,
                is_best=True,
                output_id=oid,
            )
        )
        # Zea mays: best_round=0 -> "no-refinement"
        oid2 = _seed_output(db, "Zea mays", "best/zea.glb")
        db.add(
            DGenIteration(
                run_id=run.id,
                taxon="Zea mays",
                round=0,
                status="ok",
                fidelity=0.9,
                is_best=True,
                output_id=oid2,
            )
        )
        db.commit()

        work = {w["taxon"]: w for w in ab_work(db, run.id, str(asset_dir))}
        assert work["Rosa"]["bucket"] == "ab"
        assert work["Rosa"]["common"] == "rose"
        assert work["Zea mays"]["bucket"] == "no-refinement"


def test_render_sheet_uses_capture_and_tiles():
    import io
    from PIL import Image

    def fake_capture(glb_abs, azimuths, elev):
        out = []
        for _ in azimuths:
            buf = io.BytesIO()
            Image.new("RGB", (4, 4), (0, 100, 0)).save(buf, format="PNG")
            out.append(buf.getvalue())
        return out

    png = render_sheet("/x.glb", fake_capture)
    assert isinstance(png, bytes)
    assert Image.open(io.BytesIO(png)).width >= 4
