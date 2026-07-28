"""Real parent rows for tests that only care about their children.

Many tests used hard-coded ids for rows they never created — `output_id=9001`, `rubric_id=1`
— because SQLite silently accepted a foreign key pointing at nothing. Now that enforcement is
on (see app/database.py and tests/test_fk_enforcement.py) those inserts are refused, which is
the point: the same permissiveness is what let real orphan rows accumulate in the study DB.

These helpers mint the genuine ancestry a child row needs, with per-call unique slugs so rows
from one test never collide with another's on the suite's shared engine.
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import contextmanager

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, text
from sqlalchemy import update as sa_update

from app.models import Category, Criterion, Generator, ModelOutput, Task, TraitRubric


def make_outputs(db, n: int = 1, *, cat_slug: str | None = None) -> list[ModelOutput]:
    """Create `n` committed ModelOutput rows sharing one category/task/generator.

    Returns the outputs; read `.id` for a foreign key that will actually resolve. Pass
    `cat_slug` when the test needs a category the kingdom mapping recognises.
    """
    tag = uuid.uuid4().hex[:10]
    cat = Category(slug=cat_slug or f"f-{tag}", name="Factory")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"Factory {tag}", prompt="p")
    gen = Generator(slug=f"f-{tag}", name=f"FactoryGen {tag}", kind="model")
    db.add_all([task, gen])
    db.flush()
    outs = []
    for i in range(n):
        o = ModelOutput(
            task_id=task.id,
            generator_id=gen.id,
            # NOT "seed/…": several suites bulk-delete outputs by asset_path prefix
            # (e.g. `like("seed/%.glb")`), and factory rows must not be swept up by them.
            asset_path=f"factory/{tag}-{i}.glb",
            asset_format="glb",
        )
        db.add(o)
        db.flush()
        outs.append(o)
    db.commit()
    return outs


@contextmanager
def foreign_keys_suspended(db):
    """Build a fixture that breaks referential integrity ON PURPOSE.

    A handful of tests exist to prove the purge scripts still clean up a database that
    already contains orphans — so they have to manufacture the exact corruption enforcement
    now prevents. That is not a loophole: enforcement is per-connection, so a DB touched by
    the sqlite3 CLI, another tool, or any build predating this change can still arrive with
    dangling rows, and the purge paths must keep working on it.

    Do not reach for this to make an ordinary test pass — build the real parent instead.
    """
    db.commit()  # end any open transaction; the pragma below is ignored inside one
    db.execute(text("PRAGMA foreign_keys=OFF"))
    if db.execute(text("PRAGMA foreign_keys")).scalar() == 0:
        try:
            yield
            db.commit()
        finally:
            db.execute(text("PRAGMA foreign_keys=ON"))
            db.commit()
        return

    # A transaction is already open, so `foreign_keys` is silently ignored — which is why
    # this reads the value back rather than trusting the write. `defer_foreign_keys` is the
    # one that DOES apply mid-transaction: violations are tolerated until the outermost
    # COMMIT. Under the `db_session` fixture that commit never comes (its outer transaction
    # is rolled back on teardown), so the corrupt fixture lives exactly as long as the test.
    db.execute(text("PRAGMA defer_foreign_keys=ON"))
    if db.execute(text("PRAGMA defer_foreign_keys")).scalar() != 1:
        raise RuntimeError("could not suspend FK enforcement by either pragma")
    yield
    db.commit()


def _pk(table):
    return list(table.primary_key.columns)[0]


def _clear_descendants(db, table, ids: list[int]) -> None:
    """Remove everything that transitively references `table`'s primary keys `ids`.

    Two rules, applied by the shape of the FK column rather than by a hand-kept table list,
    so a table added later is handled without anyone remembering this function exists:

    * NOT NULL child column -> the child cannot exist without its parent, so delete it (and
      recurse first, because it may have children of its own — the case that a one-level
      cascade got wrong: deleting a comparison still left its votes behind).
    * NULLABLE child column -> the reference is optional, so clear it and stop. This is also
      what breaks the task <-> model_output cycle: task.reference_asset_id is nullable, so
      recursion terminates there instead of looping.
    """
    from app.database import Base

    pk_name = _pk(table).name
    for child in Base.metadata.tables.values():
        for col in child.columns:
            if not any(
                fk.column.table.name == table.name and fk.column.name == pk_name
                for fk in col.foreign_keys
            ):
                continue
            if col.nullable:
                db.execute(sa_update(child).where(col.in_(ids)).values({col.name: None}))
                continue
            cpk = _pk(child)
            child_ids = [r[0] for r in db.execute(select(cpk).where(col.in_(ids))).all()]
            if child_ids:
                _clear_descendants(db, child, child_ids)
                db.execute(sa_delete(child).where(cpk.in_(child_ids)))


def cascade_delete(db, model, *criteria) -> int:
    """Delete rows of `model` matching `criteria`, plus every row transitively referencing
    them. Returns how many rows of `model` were removed.

    Tests used to bulk-delete parents and leave the children behind. FK enforcement refuses
    that now — the same refusal that stops the app orphaning rows in production — so test
    cleanup has to mean what it says.
    """
    table = model.__table__
    ids = [r[0] for r in db.execute(select(_pk(table)).where(*criteria)).all()]
    if not ids:
        return 0
    _clear_descendants(db, table, ids)
    db.execute(sa_delete(table).where(_pk(table).in_(ids)))
    db.commit()
    return len(ids)


def delete_outputs(db, output_ids: list[int]) -> None:
    """Cascade-delete the given outputs."""
    if output_ids:
        cascade_delete(db, ModelOutput, ModelOutput.id.in_(output_ids))


def delete_outputs_matching(db, *criteria) -> None:
    """`delete_outputs` for the common test idiom of selecting fixtures by a filter
    (usually an asset_path prefix) rather than by id."""
    cascade_delete(db, ModelOutput, *criteria)


def make_rubric(db, task_id: int | None = None, taxon: str = "factory") -> TraitRubric:
    """A committed TraitRubric, so `trait_verdict.rubric_id` resolves."""
    if task_id is None:
        task_id = make_outputs(db, 1)[0].task_id
    now = dt.datetime.now(dt.timezone.utc)
    r = TraitRubric(task_id=task_id, taxon=taxon, traits_json="{}", created=now, updated=now)
    db.add(r)
    db.commit()
    return r


def overall_criterion(db) -> Criterion:
    """The seeded 'overall' criterion, created if this DB has not been seeded yet."""
    c = db.query(Criterion).filter_by(slug="overall").first()
    if c is None:
        c = Criterion(slug="overall", name="Overall")
        db.add(c)
        db.commit()
    return c
