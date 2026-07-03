"""seed_all(force=True) must delete every table with a FK to model_output.id, or a force
reseed orphans it -> SQLite rowid reuse -> UNIQUE collision / stale association. This guards
against the delete-list drifting out of sync with the schema again."""

from __future__ import annotations

from app import seed as seed_mod
from app.database import Base


def test_force_delete_list_covers_all_model_output_children():
    children = set()
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        for col in cls.__table__.columns:
            for fk in col.foreign_keys:
                if fk.column.table.name == "model_output":
                    children.add(cls)
    missing = {c.__name__ for c in children} - {m.__name__ for m in seed_mod._FORCE_DELETE_MODELS}
    assert not missing, f"seed_all(force=True) would orphan model_output children: {missing}"
