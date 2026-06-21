#!/usr/bin/env python3
"""Real-execution check for /spotlight routes.

Starts a transient uvicorn on a random port against a fresh temp DB, curls
/spotlight and /spotlight/<slug>, asserts HTTP 200 + grid HTML presence.
Optionally takes a Playwright screenshot if --screenshot is passed.

Usage:
    python scripts/shoot_spotlight.py
    python scripts/shoot_spotlight.py --screenshot  # also saves a PNG
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point at a fresh temp dir so we don't hit the stale persistent arena.db
# (which may predate the provenance columns added in Task 1). Must happen
# BEFORE any app import so config reads the env var at module init time.
_tmp_data = tempfile.mkdtemp(prefix="bio3d_shoot_")
os.environ["BIO3D_DATA_DIR"] = _tmp_data
os.environ.setdefault("BIO3D_ADMIN_TOKEN", "test-token")
os.environ.setdefault("BIO3D_GOLD_RATE", "0")

from app import spotlight  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Category, Metric, Task  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _seed_test_subject() -> str:
    """Seed a spotlight subject using the real SPOTLIGHTS list; returns the slug.

    We seed the DB with the task_title from the first real SPOTLIGHTS entry so
    the uvicorn subprocess (which loads its own copy of spotlight.SPOTLIGHTS)
    can resolve it without any monkeypatching.
    """
    init_db()
    db = SessionLocal()
    try:
        from app import ingest
        from app.assets_gen import build_asset

        # Use the first real curated entry so the subprocess route resolves it.
        entry = spotlight.SPOTLIGHTS[0]
        slug = entry["slug"]
        task_title = entry["task_title"]

        cat = db.query(Category).filter_by(slug="plants").first()
        if cat is None:
            cat = Category(slug="plants", name="Plants")
            db.add(cat)
            db.flush()

        task = db.query(Task).filter_by(title=task_title).first()
        if task is None:
            task = Task(category_id=cat.id, title=task_title, prompt="p")
            db.add(task)
            db.flush()

            tmp = Path(tempfile.mkdtemp(prefix="bio3d_glb_"))
            for gslug, ch in [("shoot-gen-a", 0.11), ("shoot-gen-b", 0.19)]:
                glb = tmp / f"{gslug}.glb"
                build_asset("flower", abs(hash(gslug)) % 2**31, glb)
                out, _ = ingest.register_output(
                    db,
                    task_id=task.id,
                    generator_slug=gslug,
                    data=glb.read_bytes(),
                    ext="glb",
                    title=f"out {gslug}",
                )
                db.add(
                    Metric(
                        output_id=out.id,
                        status="ok",
                        chamfer=ch,
                        gt_band_lo=0.10,
                        gt_band_hi=0.14,
                        coverage=0.85,
                        fscore=0.85,
                    )
                )
        db.commit()
    finally:
        db.close()

    return slug


def _wait_ready(url: str, retries: int = 20, delay: float = 0.3) -> None:
    for _ in range(retries):
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:  # noqa: BLE001
            time.sleep(delay)
    raise RuntimeError(f"Server did not become ready at {url}")


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read().decode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--screenshot",
        action="store_true",
        help="Also save a Playwright PNG screenshot.",
    )
    args = parser.parse_args()

    slug = _seed_test_subject()
    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    env = {**os.environ, "BIO3D_DATA_DIR": _tmp_data}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(f"{base}/healthz")

        # --- /spotlight index ---
        idx = _fetch(f"{base}/spotlight")
        assert "Subject Spotlight" in idx, f"/spotlight missing header\n{idx[:500]}"
        print("[OK] GET /spotlight → 200, contains spotlight header")

        # --- /spotlight/<slug> grid ---
        page = _fetch(f"{base}/spotlight/{slug}")
        assert "spotlight-card" in page, f"/spotlight/{slug} missing .spotlight-card\n{page[:500]}"
        assert "shoot-gen-a" in page and "shoot-gen-b" in page, (
            f"Generator slugs not found\n{page[:1000]}"
        )
        n_cards = page.count("spotlight-card")
        assert n_cards >= 2, f"Expected >=2 .spotlight-card, got {n_cards}"
        print(f"[OK] GET /spotlight/{slug} → 200, {n_cards} .spotlight-card elements")

        # --- 404 for unknown slug ---
        try:
            _fetch(f"{base}/spotlight/does-not-exist-xyz")
            raise AssertionError("Expected 404 for unknown slug")
        except urllib.error.HTTPError as e:
            assert e.code == 404, f"Expected 404, got {e.code}"
        print("[OK] GET /spotlight/does-not-exist-xyz → 404")

        if args.screenshot:
            try:
                from playwright.sync_api import sync_playwright

                out_path = Path(tempfile.mkdtemp(prefix="bio3d_shot_")) / "spotlight.png"
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    ctx = browser.new_context()
                    errors: list[str] = []
                    ctx.on(
                        "console",
                        lambda m: errors.append(m.text) if m.type == "error" else None,
                    )
                    pg = ctx.new_page()
                    pg.goto(f"{base}/spotlight/{slug}", wait_until="domcontentloaded")
                    pg.screenshot(path=str(out_path))
                    browser.close()
                if errors:
                    print(f"[WARN] Console errors: {errors}")
                else:
                    print("[OK] No console errors")
                print(f"[OK] Screenshot saved: {out_path}")
            except ImportError:
                print("[SKIP] Playwright not available for screenshot")

        print("\nAll real-execution checks passed.")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
