"""Standalone AgriGen crop generator — RUN WITH AGRIGEN'S OWN INTERPRETER, not bio3d-arena's.

bio3d-arena does not depend on AgriGen; `generate_agrigen.py` invokes this script via subprocess
using AgriGen's venv (`<agrigen>/backend/.venv/bin/python`) with `PYTHONPATH=<agrigen>/backend`.
It builds a crop TraitVector from literal values (no Postgres), runs the UnifiedGenerator (which
resolves the per-species plant descriptor — e.g. data/pd/zea_mays.yaml — by scientific name /
growth_form), and writes a GLB. AgriGen is treated read-only — this script only imports it.

Usage: python agrigen_runner.py <out.glb> [seed] [target_points] [--crop tomato|maize]
"""

import argparse
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path


def _tomato_traits():
    # Literal Solanum lycopersicum traits (mirrors AgriGen's test_tomato_pd factory); no DB.
    from agrigen.traits.schema import TraitValue, TraitVector, UniversalTraits

    return TraitVector(
        species_id=3,
        scientific_name="Solanum lycopersicum",
        generated_at=datetime.now(timezone.utc),
        trait_vector_hash="bio3d_tomato",
        universal=UniversalTraits(
            plant_height_cm=TraitValue(value=120.0, unit="cm", confidence=0.9, source="lit"),
            stem_diameter_mm=TraitValue(value=12.0, unit="mm", confidence=0.9, source="lit"),
            leaf_area_cm2=TraitValue(value=40.0, unit="cm²", confidence=0.9, source="lit"),
            growth_form="dicot_crop",
        ),
        overall_confidence=0.9,
        low_confidence_traits=[],
    )


def _maize_traits():
    # Literal Zea mays traits; growth_form monocot_grass resolves AgriGen's canonical zea_mays PD.
    # Maize is a tall single-culm C4 grass with broad strap leaves — the monocot namespace carries
    # the culm/sheath geometry the dicot path lacks.
    from agrigen.traits.schema import (
        MonocotTraits,
        TraitValue,
        TraitVector,
        UniversalTraits,
    )

    return TraitVector(
        species_id=20,
        scientific_name="Zea mays",
        generated_at=datetime.now(timezone.utc),
        trait_vector_hash="bio3d_maize",
        universal=UniversalTraits(
            plant_height_cm=TraitValue(value=250.0, unit="cm", confidence=0.9, source="lit"),
            stem_diameter_mm=TraitValue(value=25.0, unit="mm", confidence=0.9, source="lit"),
            leaf_area_cm2=TraitValue(value=600.0, unit="cm²", confidence=0.9, source="lit"),
            # maize PD also reads these architectural paths (the tomato PD does not):
            internode_length_cm=TraitValue(value=18.0, unit="cm", confidence=0.8, source="lit"),
            branching_angle_degrees=TraitValue(
                value=30.0, unit="deg", confidence=0.8, source="lit"
            ),
            growth_form="monocot_grass",
        ),
        monocot=MonocotTraits(
            tiller_number=TraitValue(value=1.0, unit="count", confidence=0.8, source="lit"),
            culm_height_cm=TraitValue(value=250.0, unit="cm", confidence=0.9, source="lit"),
            leaf_sheath_length_cm=TraitValue(value=15.0, unit="cm", confidence=0.8, source="lit"),
        ),
        overall_confidence=0.9,
        low_confidence_traits=[],
    )


_TRAITS = {"tomato": _tomato_traits, "maize": _maize_traits}


def main() -> int:
    ap = argparse.ArgumentParser(description="AgriGen crop GLB generator (run in AgriGen's venv)")
    ap.add_argument("out", nargs="?", default="tomato.glb")
    ap.add_argument("seed", nargs="?", type=int, default=42)
    ap.add_argument("target_points", nargs="?", type=int, default=20_000)
    ap.add_argument("--crop", default="tomato", choices=sorted(_TRAITS))
    args = ap.parse_args()

    signal.signal(
        signal.SIGALRM,
        lambda *_: (sys.stderr.write("ABORT: agrigen_runner walltime guard\n"), sys.exit(2)),
    )
    signal.alarm(300)

    from agrigen.formats.gltf_writer import write_gltf
    from agrigen.generation.unified.generator import UnifiedGenerator

    traits = _TRAITS[args.crop]()
    geom = UnifiedGenerator().generate(traits, seed=args.seed, target_points=args.target_points)
    path = write_gltf(geom, traits, Path(args.out))
    print(f"WROTE {path} ({Path(args.out).stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
