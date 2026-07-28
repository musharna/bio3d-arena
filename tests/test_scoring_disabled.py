import pytest
from app import config, recon_service, structure_service


def test_default_scorer_raises_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "SCORING_ENABLED", False)
    with pytest.raises(recon_service.ScoringDisabled):
        recon_service._default_scorer(b"glb", "zea_mays")
    with pytest.raises(structure_service.ScoringDisabled):
        structure_service._default_scorer({"species_slug": "zea_mays"})


def test_default_scorer_enabled_flag_reads_url(monkeypatch):
    monkeypatch.setenv("BIO3D_RECON_SCORER_URL", "")
    # A public deploy also requires a real base URL (2026-07-27 audit guard); this test is
    # about SCORING_ENABLED, so satisfy the unrelated guard rather than trip over it.
    monkeypatch.setenv("BIO3D_PUBLIC_BASE_URL", "https://arena.example")
    import importlib

    importlib.reload(config)
    assert config.SCORING_ENABLED is False
    monkeypatch.setenv("BIO3D_RECON_SCORER_URL", "http://x:8800")
    importlib.reload(config)
    assert config.SCORING_ENABLED is True
