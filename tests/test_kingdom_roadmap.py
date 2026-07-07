"""Task 8: coming-soon kingdoms route to a roadmap screen instead of an empty data page.

seed_all's demo fixture creates the "plants"/"fungi"/"animals" categories but only seeds tasks
under "plants" (see app/seed.py TASKS) — animals is the real coming-soon kingdom, exactly the
case this screen exists for.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


def test_animals_kingdom_with_no_tasks_shows_roadmap():
    r = client.get("/leaderboard?kingdom=animals")
    assert r.status_code == 200
    assert "next on the roadmap" in r.text


def test_plants_kingdom_with_a_task_renders_normal_leaderboard():
    r = client.get("/leaderboard?kingdom=plants")
    assert r.status_code == 200
    assert "next on the roadmap" not in r.text
