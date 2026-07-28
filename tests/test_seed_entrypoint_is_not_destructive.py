"""`python -m app.seed` must not be able to destroy a database by default.

Found while preparing the first real deploy, 2026-07-28. The production Dockerfile booted with:

    CMD ["sh", "-c", "python -m app.seed; uvicorn app.main:app ..."]

and app/seed.py's __main__ was `print(seed_all(force=True))`. force=True wipes every table in
_FORCE_DELETE_MODELS — votes, comparisons, outputs, generators, tasks — and reseeds demo data.

Containers restart routinely (deploys, failed health checks, host migrations, scaling), so the
public instance would have erased every collected vote and replaced the corpus with demo data,
over and over, starting within hours of launch. The votes are the one irreplaceable asset in
this project; nothing else here is unrecoverable.

The Dockerfile's own comment said "Seed is idempotent." That is true of `seed_all()`, whose
default is force=False — and false of the entry point the CMD actually invoked. One module,
two intents: a developer convenience for rebuilding a local demo DB, wired into the production
boot path.

Fixing only the Dockerfile would be tripwire removal: the destructive default would still be
one careless `python -m app.seed` away, on any machine, forever. So the default itself changes
— force is now opt-in — and the boot path stops seeding entirely.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run_module(
    *args: str, env_extra: dict | None = None, db_url: str
) -> subprocess.CompletedProcess:
    import os

    env = {
        **os.environ,
        "BIO3D_DATABASE_URL": db_url,
        "BIO3D_ADMIN_TOKEN": "test-token",
        **(env_extra or {}),
    }
    env.pop("BIO3D_DB_PATH", None)
    return subprocess.run(
        [sys.executable, "-m", "app.seed", *args],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _seeded_db(tmp_path) -> tuple[str, int]:
    """A DB with seed data plus one marker row, and the marker's id."""
    db = tmp_path / "bio3d_test_seedguard.db"
    url = f"sqlite:///{db}"
    proc = _run_module("--force", db_url=url)
    assert proc.returncode == 0, f"initial seed failed: {proc.stderr[-800:]}"

    import sqlite3

    con = sqlite3.connect(db)
    con.execute("INSERT INTO category (slug, name, description) VALUES ('marker','Marker','')")
    con.commit()
    marker = con.execute("SELECT id FROM category WHERE slug='marker'").fetchone()[0]
    con.close()
    return url, marker


def test_bare_module_run_does_not_wipe_the_database(tmp_path):
    """The headline: a plain `python -m app.seed` against a populated DB must leave it alone."""
    import sqlite3

    url, marker = _seeded_db(tmp_path)
    db = url.replace("sqlite:///", "")

    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM category").fetchone()[0]
    proc = _run_module(db_url=url)  # <- no --force, the container's invocation
    assert proc.returncode == 0, proc.stderr[-800:]

    con = sqlite3.connect(db)
    after = con.execute("SELECT COUNT(*) FROM category").fetchone()[0]
    survived = con.execute("SELECT COUNT(*) FROM category WHERE id=?", (marker,)).fetchone()[0]
    con.close()
    assert survived == 1, "a bare `python -m app.seed` destroyed pre-existing rows"
    assert after >= before


def test_force_flag_still_wipes(tmp_path):
    """Positive control. The destructive path must remain available on purpose — otherwise this
    test would pass against a seeder that had simply stopped working."""
    import sqlite3

    url, marker = _seeded_db(tmp_path)
    db = url.replace("sqlite:///", "")

    proc = _run_module("--force", db_url=url)
    assert proc.returncode == 0, proc.stderr[-800:]
    con = sqlite3.connect(db)
    survived = con.execute("SELECT COUNT(*) FROM category WHERE id=?", (marker,)).fetchone()[0]
    con.close()
    assert survived == 0, "--force must still reset the database"


def test_container_boot_does_not_invoke_the_seeder():
    """The other half. Even with a safe default, a production image should not seed on boot: a
    public instance's data arrives via the import bundle, and a demo seed reaching it would be
    wrong even if non-destructive."""
    dockerfile = (REPO / "Dockerfile").read_text()
    cmd_lines = [ln for ln in dockerfile.splitlines() if ln.strip().startswith("CMD")]
    assert cmd_lines, "no CMD in Dockerfile"
    assert not any("app.seed" in ln for ln in cmd_lines), (
        "the container boot command must not run the seeder — the public DB is imported, "
        f"not seeded. Got: {cmd_lines}"
    )
