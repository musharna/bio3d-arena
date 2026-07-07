from sqlalchemy import select

from app import kingdoms as K
from app.models import Category


def test_static_map_covers_buckets():
    assert K.KINGDOM_OF["plants"] == "plants"
    assert K.KINGDOM_OF["synthetic-plants"] == "plants"  # procedural plants stay in plants
    assert K.KINGDOM_OF["fungi"] == "fungi"
    assert K.KINGDOM_OF["animals"] == "animals"
    assert (
        "plants" in K.CATEGORY_SLUGS_IN["plants"]
        and "synthetic-plants" in K.CATEGORY_SLUGS_IN["plants"]
    )


def test_normalize():
    assert K.normalize_kingdom(None) == "all"
    assert K.normalize_kingdom("bogus") == "all"
    assert K.normalize_kingdom("PLANTS") == "plants"
    assert K.normalize_kingdom("fungi") == "fungi"


def test_category_ids_for_kingdom(db_session):
    db = db_session
    for slug, name in [
        ("plants", "Plants"),
        ("synthetic-plants", "Synthetic Plants"),
        ("fungi", "Fungi"),
    ]:
        db.add(Category(slug=slug, name=name))
    db.flush()
    assert K.category_ids_for_kingdom(db, "all") is None
    plant_ids = K.category_ids_for_kingdom(db, "plants")
    got = {db.execute(select(Category.slug).where(Category.id == i)).scalar() for i in plant_ids}
    assert got == {"plants", "synthetic-plants"}
    assert K.category_ids_for_kingdom(db, "animals") == set()  # none seeded here
