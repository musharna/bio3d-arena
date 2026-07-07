# tests/test_seed_animal_subjects.py
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Category, Task
from app.seed import ANIMAL_SUBJECTS, seed_animal_subjects


def _db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return Session(eng)


def test_seed_animal_subjects_creates_four_tasks_and_flips_category():
    db = _db()
    res = seed_animal_subjects(db)
    assert res == {"subjects": 4}
    cat = db.execute(select(Category).where(Category.slug == "animals")).scalars().first()
    assert cat is not None and cat.name == "Animals"
    titles = {t.title for t in db.execute(select(Task)).scalars()}
    for title, _prompt in ANIMAL_SUBJECTS:
        assert title in titles


def test_seed_animal_subjects_is_idempotent():
    db = _db()
    seed_animal_subjects(db)
    res2 = seed_animal_subjects(db)
    assert res2 == {"subjects": 0}  # second run creates nothing
    assert db.execute(select(Task)).scalars().all().__len__() == 4


def test_animal_titles_match_generator_registries():
    # The seed titles MUST equal the recon CROPS task_titles and text TAXA titles, or generation
    # can't find the subject Task.
    from scripts.generate_api_recon import CROPS
    from scripts.generate_api_text import TAXA

    seed_titles = {t for t, _ in ANIMAL_SUBJECTS}
    recon_titles = {v["task_title"] for v in CROPS.values()}
    text_titles = {t for t, _ in TAXA}
    assert seed_titles <= recon_titles, seed_titles - recon_titles
    assert seed_titles <= text_titles, seed_titles - text_titles
