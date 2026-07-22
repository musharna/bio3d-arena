from pathlib import Path


def test_public_env_has_no_agrigen_and_disables_scoring():
    env = Path("deploy/.env.public.example").read_text()
    assert "/home/mjarnold/agrigen" not in env
    assert "BIO3D_GT_BUNDLE_DIR" not in env
    assert "BIO3D_RECON_SCORER_URL=" in env
    # scorer URL must be empty on the public instance
    line = next(ln for ln in env.splitlines() if ln.startswith("BIO3D_RECON_SCORER_URL="))
    assert line.strip() == "BIO3D_RECON_SCORER_URL="
    assert "changeme-admin-token" not in env
