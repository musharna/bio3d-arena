"""Real-execution (network-gated) check for the Objaverse sourcing path.

Paired with the synthetic-fixture test in tests/test_source_objaverse.py (which exercises
`ingest_found` with injected fakes). This one hits the REAL `objaverse` package to validate
the assumptions that synthetic fakes can't: that the live "tomato" LVIS category exists, that
real annotations carry Sketchfab short-code licenses our `classify_license` maps to "host",
and that a real object downloads to a non-empty `.glb`. Skips cleanly (never fake-passes) when
the package is absent or Objaverse is unreachable.
"""

from __future__ import annotations

import os

import pytest

from app.sourcing import classify_license, label_depiction


def test_objaverse_tomato_real_path():
    objaverse = pytest.importorskip("objaverse")
    try:
        lvis = objaverse.load_lvis_annotations()
        uids = [u for cat in lvis if "tomato" in cat.lower() for u in lvis[cat]]
        if not uids:
            pytest.skip("no 'tomato' LVIS category uids in this Objaverse release")
        anns = objaverse.load_annotations(uids[:8])
    except Exception as e:  # noqa: BLE001 — network/IO: skip, never silently pass
        pytest.skip(f"Objaverse unreachable: {e}")

    # Real licenses are Sketchfab short codes ("by", "by-sa", "cc0", ...) — at least one
    # tomato model must classify as host, or our license mapping is wrong for live data.
    hosts = [u for u, a in anns.items() if classify_license(a.get("license")) == "host"]
    assert hosts, f"no host-licensed tomato model among {[a.get('license') for a in anns.values()]}"

    a = anns[hosts[0]]
    assert a.get("license"), "real annotation must carry a license code"
    assert label_depiction(a.get("name") or "") in {"whole_plant", "fruit", "leaf", "other"}

    # Download one real object and confirm it is a non-empty .glb on disk.
    try:
        path = objaverse.load_objects([hosts[0]])[hosts[0]]
    except Exception as e:  # noqa: BLE001 — download network/IO: skip
        pytest.skip(f"Objaverse object download unreachable: {e}")
    assert path.endswith(".glb")
    assert os.path.getsize(path) > 0
