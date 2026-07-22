import json
import os
import pytest

# Imported, never restated. These two sets used to be copied here as literals, so this test
# asserted the sidecars matched a SNAPSHOT of the gate rather than the gate itself — which is
# exactly why the allowlist could drift away from public_export's copy without a red test.
from app.licensing import REDISTRIBUTABLE_LICENSES, normalize_license  # noqa: E402
from app.reference_provenance import _REQUIRED as REQUIRED  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_sidecar(d, name, file_field, license="CC-BY-4.0"):
    """Minimal VALID sidecar under `name`, describing the image `file_field`."""
    (d / name).write_text(
        json.dumps(
            {
                "subject": "s",
                "file": file_field,
                "source": "Wikimedia",
                "source_url": "http://example.org/x",
                "download_url": "http://example.org/x.jpg",
                "license": license,
                "author": "a",
                "attribution": "a, CC",
                "title": "t",
                "note": "n",
            }
        )
    )


def test_clearance_is_keyed_by_image_not_by_taxon(tmp_path, monkeypatch):
    """A cleared photo must not launder a DIFFERENT, unrecorded photo of the same taxon.

    Copyright attaches to an individual photograph, but clearance used to be keyed by taxon
    (`_taxon_of` collapses `rose_ref.jpg` and `rose_ref_clean.jpg` to "rose"). That granularity
    mismatch let one sidecar clear every photo sharing its taxon prefix -- production shape:
    `tomato_ref_roma.jpg` (7 outputs) read as cleared purely because `tomato_ref.json` covers a
    different tomato photo."""
    from app import config
    from app.reference_provenance import cleared_reference_images

    ref = tmp_path / "reference"
    ref.mkdir()
    _write_sidecar(ref, "tomato_ref.json", "tomato_ref.jpg")
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)

    cleared = cleared_reference_images()
    assert "tomato_ref.jpg" in cleared
    # A second photo of the SAME taxon with no record of its own must NOT be cleared.
    assert "tomato_ref_roma.jpg" not in cleared


def test_sidecar_clears_the_image_its_file_field_names(tmp_path, monkeypatch):
    """Clearance follows the sidecar's `file` field, not the sidecar's own filename.

    The established convention (dog/mallard/monarch/goldfish) is a `{taxon}_ref.json` sidecar
    whose `file` names the current canonical photo, e.g. `dog_ref_clean.jpg`. rose/soybean were
    re-sourced off that path as `rose_ref_clean.json`, so a taxon-keyed glob silently ignored
    two perfectly valid CC records (21 outputs)."""
    from app import config
    from app.reference_provenance import cleared_reference_images

    ref = tmp_path / "reference"
    ref.mkdir()
    _write_sidecar(ref, "dog_ref.json", "dog_ref_clean.jpg")  # convention: file != sidecar stem
    _write_sidecar(ref, "rose_ref_clean.json", "rose_ref_clean.jpg")  # off-convention filename
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)

    cleared = cleared_reference_images()
    assert "dog_ref_clean.jpg" in cleared
    assert "rose_ref_clean.jpg" in cleared, (
        "a valid record must clear its image whatever it is named"
    )
    # The sidecar's own stem is NOT what gets cleared.
    assert "dog_ref.jpg" not in cleared


def test_invalid_sidecar_clears_nothing(tmp_path, monkeypatch):
    """A record whose licence bars redistribution must not clear its image.

    NC is the exemplar because it genuinely bars redistribution. This test previously used the
    gourd's real CC-BY-2.0 to stand for 'non-allowlisted' -- but that only described the reference
    gate's private copy of the allowlist, which had drifted from the export gate's copy (which
    accepted CC-BY-2.0 all along). Pinning a drifted value as if it were policy is what kept the
    drift invisible; see test_licensing.test_one_redistribution_allowlist_for_outputs_and_
    reference_photos."""
    from app import config
    from app.reference_provenance import cleared_reference_images

    ref = tmp_path / "reference"
    ref.mkdir()
    _write_sidecar(ref, "nc_ref.json", "nc_ref.jpg", license="CC-BY-NC-4.0")
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)

    assert cleared_reference_images() == set()


def test_cc_by_2_0_clears_its_image(tmp_path, monkeypatch):
    """CC-BY-2.0 permits redistribution with attribution, so it clears -- same as CC-BY-3.0/4.0.
    Production case: gourd_ref.jpg (Wikimedia, CC-BY-2.0) and the 10 recon outputs derived from it,
    which the reference gate had been blocking while the export gate accepted the same licence."""
    from app import config
    from app.reference_provenance import cleared_reference_images

    ref = tmp_path / "reference"
    ref.mkdir()
    _write_sidecar(ref, "gourd_ref.json", "gourd_ref.jpg", license="CC-BY-2.0")
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)

    assert cleared_reference_images() == {"gourd_ref.jpg"}


@pytest.mark.parametrize("slug", ["arabidopsis", "maize", "rose", "soybean", "tomato", "pinus"])
def test_reference_has_image_and_valid_provenance(slug):
    img = os.path.join(REPO_ROOT, f"data/assets/reference/{slug}_ref.jpg")
    if not os.path.exists(img):
        # data/ is gitignored runtime state — absent on a checkout without the runtime volume.
        pytest.skip(f"runtime reference image absent (gitignored): {slug}")
    # The image EXISTS, so a missing/invalid record is a real provenance gap, not an absent
    # runtime volume — fail loud. Skipping on a missing sidecar is how rose and soybean went
    # unchecked: the .jpg was present, the record was not, and this test reported green.
    meta = os.path.join(REPO_ROOT, f"data/assets/reference/{slug}_ref.json")
    assert os.path.exists(meta), f"{slug}_ref.jpg has no {slug}_ref.json provenance record"
    assert os.path.getsize(img) > 5000, img
    with open(meta) as f:
        d = json.load(f)
    assert REQUIRED <= set(d), REQUIRED - set(d)
    # `file` names the photo this record covers; it need not equal {slug}_ref.jpg (the canonical
    # photo may be a re-sourced `_clean` variant), but it MUST name a file that exists.
    covered = os.path.join(REPO_ROOT, "data/assets/reference", d["file"])
    assert os.path.exists(covered), f"{slug}: record covers {d['file']!r}, which is not on disk"
    # Normalize first, exactly as cleared_reference_images() does — comparing the raw string
    # would fail a legitimately-labelled 'CC-BY 4.0' that the real gate accepts.
    assert normalize_license(d["license"]) in REDISTRIBUTABLE_LICENSES, d["license"]
    assert d["source_url"].startswith("http") and d["download_url"].startswith("http")
    for k in ("author", "attribution", "title", "subject"):
        assert d.get(k, "").strip(), k


def test_bio3darena_recon_gated_on_redistribute(monkeypatch):
    import json
    from app import reference_provenance as rp
    from app.database import SessionLocal
    from app.models import Category, Generator, ModelOutput, Task

    # the tomato photo is cleared; rose_ref.jpg is NOT
    monkeypatch.setattr(rp, "cleared_reference_images", lambda: {"tomato_ref.jpg"})

    with SessionLocal() as db:
        cat = Category(slug="plants2", name="P")
        g = Generator(slug="internal-recon", name="internal", kind="model", paradigm="image_recon")
        db.add_all([cat, g])
        db.flush()
        t = Task(category_id=cat.id, title="rp-rose", prompt="p", active=True)
        db.add(t)
        db.flush()
        # bio3d-arena recon from an UN-cleared photo → must raise
        bad = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="a.glb",
            source="bio3d-arena",
            meta_json=json.dumps({"input_image": "reference/rose_ref.jpg"}),
        )
        # bio3d-arena GT mesh (no input_image) → exempt
        gt = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="gt.glb",
            source="bio3d-arena",
            meta_json="{}",
        )
        db.add_all([bad, gt])
        db.flush()

        import pytest

        with pytest.raises(rp.ReferenceProvenanceError):
            rp.assert_recon_photos_cleared(db, {bad.id})
        rp.assert_recon_photos_cleared(db, {gt.id})  # no raise — no input_image


def test_gold_twin_bio3darena_recon_gated(monkeypatch):
    # A gold output aliases a real asset via a shared asset_path; the gate must check the TWIN's
    # provenance. A gold whose twin is a bio3d-arena recon from an un-cleared photo must raise;
    # a gold whose twin is a bio3d-arena GT mesh (no input_image) must not.
    import json

    import pytest

    from app import reference_provenance as rp
    from app.database import SessionLocal
    from app.models import Category, Generator, ModelOutput, Task

    # the tomato photo is cleared; rose_ref.jpg is NOT
    monkeypatch.setattr(rp, "cleared_reference_images", lambda: {"tomato_ref.jpg"})

    with SessionLocal() as db:
        cat = Category(slug="plants-gold", name="P")
        g = Generator(
            slug="internal-recon-gold", name="internal", kind="model", paradigm="image_recon"
        )
        cal = Generator(slug="calib-gold", name="calibration", kind="model", paradigm="image_recon")
        db.add_all([cat, g, cal])
        db.flush()
        t = Task(category_id=cat.id, title="rp-gold-rose", prompt="p", active=True)
        db.add(t)
        db.flush()
        # real bio3d-arena recon twin from an UN-cleared photo, shared asset_path with the gold row
        twin_bad = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="gold_bad.glb",
            source="bio3d-arena",
            meta_json=json.dumps({"input_image": "reference/rose_ref.jpg"}),
        )
        gold_bad = ModelOutput(
            task_id=t.id,
            generator_id=cal.id,
            asset_path="gold_bad.glb",
            is_gold=True,
            source="calibration-decoy",
            meta_json="{}",
        )
        # real bio3d-arena GT twin (no input_image), shared asset_path with a gold row → exempt
        twin_gt = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="gold_gt.glb",
            source="bio3d-arena",
            meta_json="{}",
        )
        gold_gt = ModelOutput(
            task_id=t.id,
            generator_id=cal.id,
            asset_path="gold_gt.glb",
            is_gold=True,
            source="calibration-decoy",
            meta_json="{}",
        )
        db.add_all([twin_bad, gold_bad, twin_gt, gold_gt])
        db.flush()

        with pytest.raises(rp.ReferenceProvenanceError):
            rp.assert_recon_photos_cleared_for_gold(db, {gold_bad.id})
        rp.assert_recon_photos_cleared_for_gold(
            db, {gold_gt.id}
        )  # no raise — twin has no input_image
        db.rollback()
