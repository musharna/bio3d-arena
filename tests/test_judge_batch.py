from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import CalibrationPair, Category, Criterion, Generator, JudgeVote, ModelOutput, Task
from tests.factories import cascade_delete, delete_outputs_matching


def setup_module(_m):
    init_db()


def _seed(db):
    # Clean previous run's jb-prefixed rows (Category+Generator slugs are unique).
    db.query(JudgeVote).delete()
    db.query(CalibrationPair).delete()
    delete_outputs_matching(db, ModelOutput.asset_path.like("seed/%.glb"))
    cascade_delete(db, Task, Task.title == "jb-task")
    cascade_delete(db, Generator, Generator.slug.like("jb-g%"))
    db.query(Category).filter_by(slug="jb-cat").delete(synchronize_session=False)
    db.commit()
    cat = Category(slug="jb-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    gens = [Generator(slug=f"jb-g{i}", name=f"G{i}") for i in range(3)]
    db.add_all(gens)
    db.flush()
    task = Task(category_id=cat.id, title="jb-task", prompt="p")
    db.add(task)
    db.flush()
    for g in gens:
        db.add(ModelOutput(task_id=task.id, generator_id=g.id, asset_path=f"seed/{g.id}.glb"))
    db.commit()
    return task, crit


def test_run_batch_writes_both_orders_and_resumes():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        task, _crit = _seed(db)

        def judge_fn(species, prompt, cname, cdesc, a_b64, b_b64):
            return "a", "stub rationale"

        def sheet_b64(output_id, condition):
            return "QQ=="  # 1-byte PNG stub; not actually decoded by the stub judge

        jv.run_batch(
            db,
            judge_fn=judge_fn,
            sheet_b64=sheet_b64,
            grid_condition="multi4",
            criteria_slugs=["overall"],
        )
        # 3 generators → C(3,2)=3 logical pairs × 2 orders = 6 votes for THIS task.
        # Scope to the jb-seeded task so any active tasks/pairs another test file
        # left behind in the shared persistent DB can't perturb the count.
        votes = db.query(JudgeVote).filter_by(task_id=task.id).all()
        assert len(votes) == 6
        groups = {v.swap_group for v in votes}
        assert len(groups) == 3  # each logical pair shares one swap_group
        for g in groups:
            assert db.query(JudgeVote).filter_by(swap_group=g).count() == 2

        # Resume: a second run writes nothing new (resume key matches written rows).
        res2 = jv.run_batch(
            db,
            judge_fn=judge_fn,
            sheet_b64=sheet_b64,
            grid_condition="multi4",
            criteria_slugs=["overall"],
        )
        assert res2["written"] == 0
        assert db.query(JudgeVote).filter_by(task_id=task.id).count() == 6


def test_calibration_enumerates_all_conditions():
    import scripts.judge_vlm as jv
    from app.judge_render import CONDITIONS

    with SessionLocal() as db:
        task, crit = _seed(db)
        # Disable the GRID branch for this task so only the CALIBRATION branch
        # produces its rows — the calibration branch processes CalibrationPair
        # regardless of Task.active. (Grid would otherwise duplicate the multi4 row.)
        task.active = False
        outs = sorted(o.id for o in task.outputs)
        a, b = outs[0], outs[1]
        db.add(CalibrationPair(task_id=task.id, output_a_id=a, output_b_id=b, criterion_id=crit.id))
        db.commit()

        items = jv.enumerate_work(db, criteria_slugs=["overall"])
        cal = [it for it in items if it["task_id"] == task.id]
        # 1 calibration pair × 3 conditions × 2 orders = 6 rows.
        assert len(cal) == 6
        # All three perception conditions fire (single, multi4, turntable).
        assert {it["condition"] for it in cal} == set(CONDITIONS)
        groups = {it["swap_group"] for it in cal}
        assert len(groups) == 3  # one swap_group per condition
        for g in groups:
            rows = [it for it in cal if it["swap_group"] == g]
            assert len(rows) == 2  # both orders share the swap_group
            assert {(r["output_a_id"], r["output_b_id"]) for r in rows} == {(a, b), (b, a)}


def test_max_votes_caps_writes():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        _seed(db)
        res = jv.run_batch(
            db,
            judge_fn=lambda *a: ("b", "r"),
            sheet_b64=lambda oid, cond: "QQ==",
            grid_condition="multi4",
            criteria_slugs=["overall"],
            max_votes=2,
        )
        assert res["written"] == 2


# ---- Message Batches API path (50% cheaper, async) --------------------------------------------
# A fake that mirrors anthropic client.messages.batches: create() records the submitted requests,
# retrieve() reports processing_status, results() streams one result per request.


class _FakeToolUse:
    type = "tool_use"

    def __init__(self, name, input_):
        self.name = name
        self.input = input_


class _FakeMessage:
    def __init__(self, blocks):
        self.content = blocks


class _FakeResultBody:
    def __init__(self, type_, message=None):
        self.type = type_
        self.message = message


class _FakeResult:
    def __init__(self, custom_id, result):
        self.custom_id = custom_id
        self.result = result


class _FakeBatch:
    def __init__(self, id_, status="ended"):
        self.id = id_
        self.processing_status = status


class _FakeBatches:
    """create → capture requests; retrieve → 'ended'; results → a succeeded verdict per request."""

    def __init__(self, winner="a", rationale="r", result_type="succeeded"):
        self.winner, self.rationale, self.result_type = winner, rationale, result_type
        self.create_calls = 0
        self._reqs = []

    def create(self, requests):
        self.create_calls += 1
        self._reqs = list(requests)
        return _FakeBatch("batch_1")

    def retrieve(self, batch_id):
        return _FakeBatch(batch_id, "ended")

    def results(self, batch_id):
        for r in self._reqs:
            if self.result_type == "succeeded":
                msg = _FakeMessage(
                    [
                        _FakeToolUse(
                            "record_verdict", {"winner": self.winner, "rationale": self.rationale}
                        )
                    ]
                )
                yield _FakeResult(r["custom_id"], _FakeResultBody("succeeded", msg))
            else:
                yield _FakeResult(r["custom_id"], _FakeResultBody(self.result_type))


def test_build_judge_request_replicates_judge_pair_call():
    """A batch request carries the same model/tool/forced-choice as judge_pair, images in A→B
    order, and a custom_id unique to the ordered pair so results map back."""
    import scripts.judge_vlm as jv
    from app import judge

    with SessionLocal() as db:
        task, crit = _seed(db)
        item = {
            "task_id": task.id,
            "output_a_id": 101,
            "output_b_id": 202,
            "criterion_id": crit.id,
            "condition": "multi4",
            "swap_group": "deadbeefdeadbeef",
        }
        cid, params = jv.build_judge_request(
            db.get(type(task), task.id), crit, item, "AAAA", "BBBB"
        )
        assert params["model"] == judge.JUDGE_MODEL
        assert params["tool_choice"] == {"type": "tool", "name": "record_verdict"}
        assert params["tools"] == [judge.VERDICT_TOOL]
        imgs = [b for b in params["messages"][0]["content"] if b["type"] == "image"]
        assert [i["source"]["data"] for i in imgs] == ["AAAA", "BBBB"]  # A then B
        # custom_id is order-specific: swapping A/B yields a different id (both orders coexist).
        cid2, _ = jv.build_judge_request(
            db.get(type(task), task.id),
            crit,
            {**item, "output_a_id": 202, "output_b_id": 101},
            "BBBB",
            "AAAA",
        )
        assert cid != cid2 and len(cid) <= 64


def test_submit_batch_polls_until_ended():
    """submit_batch creates the batch, polls retrieve() until 'ended' (sleeping between), then
    returns {custom_id: result}."""
    import scripts.judge_vlm as jv

    class Polling(_FakeBatches):
        def __init__(self):
            super().__init__()
            self._n = 0

        def retrieve(self, batch_id):
            self._n += 1
            return _FakeBatch(batch_id, "ended" if self._n >= 3 else "in_progress")

    slept = []
    fake = Polling()
    results = jv.submit_batch(
        fake,
        [{"custom_id": "x-1-2", "params": {}}],
        sleep_fn=slept.append,
        poll_interval=7,
    )
    assert "x-1-2" in results and results["x-1-2"].type == "succeeded"
    assert slept == [7, 7]  # slept on the two in_progress polls, not after 'ended'


def test_submit_batch_retries_transient_error():
    """A transient connection blip during create/poll/collect must not abort a multi-hour run —
    submit_batch retries the injected transient exceptions (with a backoff sleep) and recovers."""
    import scripts.judge_vlm as jv

    class Flaky(_FakeBatches):
        def __init__(self):
            super().__init__()
            self.create_attempts = 0

        def create(self, requests):
            self.create_attempts += 1
            if self.create_attempts == 1:
                raise ValueError("transient blip")  # injected as retryable below
            return super().create(requests)

    slept, fake = [], Flaky()
    results = jv.submit_batch(
        fake,
        [{"custom_id": "x-1-2", "params": {}}],
        sleep_fn=slept.append,
        retryable=(ValueError,),
    )
    assert fake.create_attempts == 2  # retried the first failure
    assert results["x-1-2"].type == "succeeded"
    assert slept and slept[0] > 0  # backed off before the retry


def test_run_batch_api_writes_votes_and_resumes():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        task, _crit = _seed(db)
        fake = _FakeBatches(winner="a")
        res = jv.run_batch_api(
            db,
            sheet_b64=lambda oid, cond: "QQ==",
            batches_client=fake,
            grid_condition="multi4",
            criteria_slugs=["overall"],
            sleep_fn=lambda *_: None,
        )
        assert res["written"] == 6 and res["errors"] == 0
        votes = db.query(JudgeVote).filter_by(task_id=task.id).all()
        assert len(votes) == 6 and {v.winner for v in votes} == {"a"}
        assert len({v.swap_group for v in votes}) == 3

        # Resume: nothing new, and no second batch submitted (all rows already seen).
        fake2 = _FakeBatches(winner="a")
        res2 = jv.run_batch_api(
            db,
            sheet_b64=lambda oid, cond: "QQ==",
            batches_client=fake2,
            grid_condition="multi4",
            criteria_slugs=["overall"],
            sleep_fn=lambda *_: None,
        )
        assert res2["written"] == 0 and fake2.create_calls == 0


def test_run_batch_api_errored_result_counts_error_no_vote():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        task, _crit = _seed(db)
        fake = _FakeBatches(result_type="errored")
        res = jv.run_batch_api(
            db,
            sheet_b64=lambda oid, cond: "QQ==",
            batches_client=fake,
            grid_condition="multi4",
            criteria_slugs=["overall"],
            sleep_fn=lambda *_: None,
        )
        assert res["written"] == 0 and res["errors"] == 6
        assert db.query(JudgeVote).filter_by(task_id=task.id).count() == 0
