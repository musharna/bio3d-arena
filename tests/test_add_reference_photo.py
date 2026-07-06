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
    meta = json.loads((dest / "basil_ref_clean.json").read_text())
    from app.reference_provenance import _REQUIRED

    assert _REQUIRED <= set(meta)
    assert meta["file"] == "basil_ref_clean.jpg"
    assert meta["license"] == "CC0-1.0"


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
