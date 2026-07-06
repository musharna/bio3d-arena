import json
import pytest
from pathlib import Path
from scripts.add_reference_photo import write_reference_photo


def _img(p: Path) -> Path:
    p.write_bytes(b"\xff\xd8\xff\xe0JFIFdummyjpeg")  # non-empty stand-in
    return p


def test_writes_sidecar_that_clears(tmp_path):
    src = _img(tmp_path / "in.jpg")
    dest = tmp_path / "reference"
    dest.mkdir()
    out = write_reference_photo(
        taxon="basil",
        image_path=src,
        author="Jaret Arnold",
        license_="CC0-1.0",
        source_url="https://example.com/basil",
        download_url="https://example.com/basil.jpg",
        subject="Ocimum basilicum (whole plant)",
        title="Basil plant",
        note="Owner-shot nursery photo.",
        dest_dirs=[dest],
    )
    assert out == dest / "basil_ref_clean.jpg"
    # Sidecar is {taxon}_ref.json (glob-scannable), not _ref_clean.json.
    meta = json.loads((dest / "basil_ref.json").read_text())
    from app.reference_provenance import _REQUIRED

    assert _REQUIRED <= set(meta)
    assert meta["file"] == "basil_ref_clean.jpg"
    assert meta["license"] == "CC0-1.0"
    assert not (dest / "basil_ref_clean.json").exists()  # must NOT use the un-scanned name


def test_ingested_sidecar_actually_clears_the_taxon(tmp_path, monkeypatch):
    # The load-bearing behavior: after ingestion, cleared_reference_taxa() must report the taxon.
    # (The original tests only checked the sidecar's fields, not that the gate scans it.)
    from app import reference_provenance

    src = _img(tmp_path / "in.jpg")
    dest = tmp_path / "reference"
    dest.mkdir()
    monkeypatch.setattr(reference_provenance, "_ref_dir", lambda: dest)
    write_reference_photo(
        taxon="basil",
        image_path=src,
        author="Jaret Arnold",
        license_="CC0-1.0",
        source_url="https://example.com/basil",
        download_url="https://example.com/basil.jpg",
        subject="Ocimum basilicum (whole plant)",
        title="Basil plant",
        note="Owner-shot nursery photo.",
        dest_dirs=[dest],
    )
    assert "basil" in reference_provenance.cleared_reference_taxa()


def test_rejects_non_cc_license(tmp_path):
    src = _img(tmp_path / "in.jpg")
    dest = tmp_path / "reference"
    dest.mkdir()
    with pytest.raises(ValueError, match="license"):
        write_reference_photo(
            taxon="basil",
            image_path=src,
            author="x",
            license_="All Rights Reserved",
            source_url="https://x",
            download_url="https://x.jpg",
            subject="s",
            title="t",
            note="n",
            dest_dirs=[dest],
        )


def test_refuses_overwrite_without_force(tmp_path):
    src = _img(tmp_path / "in.jpg")
    dest = tmp_path / "reference"
    dest.mkdir()
    kw = dict(
        taxon="basil",
        image_path=src,
        author="x",
        license_="CC0-1.0",
        source_url="https://x",
        download_url="https://x.jpg",
        subject="s",
        title="t",
        note="n",
        dest_dirs=[dest],
    )
    write_reference_photo(**kw)
    with pytest.raises(FileExistsError):
        write_reference_photo(**kw)
    write_reference_photo(**kw, force=True)  # ok with force


def test_refuses_to_clobber_existing_sidecar_without_force(tmp_path):
    # A curated original {taxon}_ref.json may exist even when no _ref_clean.jpg does — writing
    # unconditionally would destroy its provenance. Must refuse without force.
    src = _img(tmp_path / "in.jpg")
    dest = tmp_path / "reference"
    dest.mkdir()
    (dest / "morel_ref.json").write_text('{"pre-existing": "curated provenance"}')
    # note: no morel_ref_clean.jpg exists — the old guard (jpg-only) would NOT fire here
    kw = dict(
        taxon="morel",
        image_path=src,
        author="x",
        license_="CC0-1.0",
        source_url="https://x",
        download_url="https://x.jpg",
        subject="s",
        title="t",
        note="n",
        dest_dirs=[dest],
    )
    with pytest.raises(FileExistsError):
        write_reference_photo(**kw)
    # the curated sidecar is untouched
    assert json.loads((dest / "morel_ref.json").read_text()) == {
        "pre-existing": "curated provenance"
    }
    write_reference_photo(**kw, force=True)  # force overwrites intentionally
