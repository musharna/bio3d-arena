"""A reference photo nobody has judged must not be shown to a voter.

`scripts/qa_reference_gallery.py` scores every gallery photo and writes `passed_qa` into the
manifest. `app/reference_qa.py` carries the rule it scores against, and that rule is *specific* —
`MORPHOTYPE` says, in as many words, "NOT a wild five-petalled dog-rose or a bramble", "NOT a
dingo, dhole, coyote or other wild canid", "NOT one crushed in a predator's beak", "NOT the
striped caterpillar (larva) or the green chrysalis (pupa)".

On 2026-08-04 every one of those was live. A sweep of all 16 tasks found the rose gallery showing
a wild bramble ID series, the dog gallery showing dingo-type canids beside an input of a hairless
street dog, the goldfish gallery showing dead silver fish and one in a heron's beak, and the
monarch gallery showing a caterpillar and a chrysalis. Nine of sixteen galleries did not depict
the subject their task's model was given, across ~61% of all votes cast.

`reference_images_for_task` read the verdict as `item.get("passed_qa", True)` — default TRUE — so
"never judged" and "judged and passed" were the same value. A gate whose absence means pass is not
a gate; it is a comment. That default is the mechanism, and this file is what holds it closed.

What the default actually cost is narrower than it first looked, and the measurement is worth
recording because the first two readings of it were wrong. Of 172 shipped entries, 129 carry
`passed_qa: True`, 35 carry `False`, and 8 carry nothing — and all 8 are `cucurbita_pepo`, the
pumpkin retired corpus-wide on 2026-07-25, whose slug maps to no `ORGAN_INVENTORY` taxon and which
`qa_reference_gallery.py` therefore skips outright. So closing the default does not black out the
arena; it hides one dead task's gallery.

The live problem is staleness, not absence. The manifests were scored on 2026-07-29 between 03:45
and 20:34; the `MORPHOTYPE` rules that name the bramble, the dingoes, the fish in a heron's beak
and the caterpillar were committed at 21:09 the same evening. The verdicts predate the rule they
are supposed to encode, which is why photos those rules describe in as many words are sitting at
`passed_qa: True`. Re-running the scorer is what resolves it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config, service
from app.storage import LocalStorageBackend


def _write_gallery(root: Path, slug: str, items: list[dict]) -> None:
    d = root / "reference" / "gallery" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(items))
    for it in items:
        (d / it["file"]).write_bytes(b"\xff\xd8\xff jpeg-ish")


class _Task:
    def __init__(self, title, id=1):
        self.title = title
        self.id = id


@pytest.fixture
def gallery(tmp_path, monkeypatch):
    """Serve a gallery from storage with the local asset dir pointed elsewhere, matching
    production topology (see test_reference_gallery_publish for why that split matters)."""
    store = tmp_path / "store"
    store.mkdir()

    def _install(slug, items):
        _write_gallery(store, slug, items)
        monkeypatch.setattr(config, "ASSET_DIR", tmp_path / "empty")
        monkeypatch.setattr(
            service, "get_storage", lambda: LocalStorageBackend(store), raising=False
        )
        service.reference_gallery_cache_clear()

    yield _install
    service.reference_gallery_cache_clear()


def test_an_unjudged_photo_is_not_shown(gallery):
    """The defect, stated directly: no verdict is not a pass.

    Every photo that reached a voter on 2026-08-04 looked exactly like this — `file`,
    `attribution`, `license`, `photo_id`, `url`, and no verdict of any kind.
    """
    gallery("rosa", [{"file": "1.jpg", "attribution": "a", "photo_id": 1, "license": "cc0"}])
    assert service.reference_images_for_task(None, _Task("Rosa — x")) == [], (
        "a reference photo nobody has judged was shown to a voter; `passed_qa` must be "
        "required, not defaulted"
    )


def test_an_explicitly_passed_photo_is_still_shown(gallery):
    """POSITIVE CONTROL. Fail-closed must not be satisfied by showing nothing ever — that would
    pass the test above and silently delete the reference gallery the arena is built around."""
    gallery("rosa", [{"file": "1.jpg", "attribution": "a", "passed_qa": True}])
    refs = service.reference_images_for_task(None, _Task("Rosa — x"))
    assert len(refs) == 1 and "1.jpg" in refs[0]["url"]


def test_an_explicitly_failed_photo_is_not_shown(gallery):
    """The behaviour that already existed, pinned so the rewrite does not lose it."""
    gallery("rosa", [{"file": "1.jpg", "attribution": "a", "passed_qa": False}])
    assert service.reference_images_for_task(None, _Task("Rosa — x")) == []


def test_a_non_boolean_verdict_does_not_count_as_a_pass(gallery):
    """`passed_qa: "pending"` is truthy. Requiring the literal True keeps a half-written or
    hand-edited manifest from reading as approval."""
    gallery("rosa", [{"file": "1.jpg", "attribution": "a", "passed_qa": "pending"}])
    assert service.reference_images_for_task(None, _Task("Rosa — x")) == []


def test_mixed_manifest_shows_only_the_judged_and_passed(gallery):
    gallery(
        "rosa",
        [
            {"file": "1.jpg", "attribution": "a", "passed_qa": True},
            {"file": "2.jpg", "attribution": "b", "passed_qa": False},
            {"file": "3.jpg", "attribution": "c"},
        ],
    )
    refs = service.reference_images_for_task(None, _Task("Rosa — x"))
    assert [r["url"].rsplit("/", 1)[-1] for r in refs] == ["1.jpg"]


# --------------------------------------------------------------------------- the real corpus


def _shipped_galleries() -> list[Path]:
    """The galleries as they sit in the REPO, deliberately not via `config.ASSET_DIR`.

    `conftest.py` sets `BIO3D_DATA_DIR` to a fresh temp dir before the app is imported, so under
    pytest `config.ASSET_DIR` is *always* a temp tree — it seeds a handful of `*_ref.jpg` input
    photos and no galleries at all. A corpus check written against it therefore finds nothing in
    every environment and skips forever: a test that cannot fail, wearing a skip message that
    blames CI. This walks up from the test file the same way conftest does, so the check reads the
    real thing where it exists and genuinely reports absence where it does not.
    """
    root = Path(__file__).resolve().parent.parent / "data" / "assets" / "reference" / "gallery"
    return sorted(root.glob("*/manifest.json")) if root.is_dir() else []


def test_every_shipped_gallery_entry_carries_an_explicit_verdict():
    """The corpus check, and the reason the fix is not just the one-line default.

    Skipped only where the repo genuinely has no `data/` — CI excludes it, which is precisely why
    the 2026-08-04 state survived a green pipeline. The skip is therefore LOUD: it names what was
    not checked rather than reporting a pass.

    Where `data/` DOES exist this is expected to FAIL until `scripts/qa_reference_gallery.py` has
    run, and that red is the point: fail-closed serving plus an unjudged corpus means voters see
    no references at all, so the code change and the scoring run have to ship together. A green
    here is the signal that the bundle is safe to publish.
    """
    manifests = _shipped_galleries()
    if not manifests:
        pytest.skip(
            "no galleries under config.ASSET_DIR — this environment (CI) cannot verify gallery "
            "QA verdicts at all; run this where data/ exists before publishing a bundle"
        )
    unjudged = []
    for m in manifests:
        try:
            items = json.loads(m.read_text())
        except ValueError:
            unjudged.append(f"{m.parent.name}: unreadable manifest")
            continue
        for it in items:
            # JUDGED, not PASSED. An explicit `False` is the gate working — 35 of 172 shipped
            # entries are rejects, and demanding they all pass would make this unsatisfiable
            # forever. What must not exist is an entry the scorer never reached, because
            # fail-closed serving turns that into an invisible reference rather than a loud one.
            if isinstance(it, dict) and "file" in it and not isinstance(it.get("passed_qa"), bool):
                unjudged.append(f"{m.parent.name}/{it['file']}")
    assert not unjudged, (
        f"{len(unjudged)} gallery photo(s) carry no QA verdict at all and will therefore be "
        f"hidden from voters without anyone having judged them. Run "
        f"`scripts/qa_reference_gallery.py` for these galleries and ship the rescored manifests "
        f"— or, if the task is retired, delete the gallery. First few: {unjudged[:8]}"
    )
