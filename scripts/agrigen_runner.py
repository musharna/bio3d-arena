"""Standalone AgriGen tomato generator — RUN WITH AGRIGEN'S OWN INTERPRETER, not bio3d-arena's.

bio3d-arena does not depend on AgriGen; `generate_agrigen.py` invokes this script via subprocess
using AgriGen's venv (`<agrigen>/backend/.venv/bin/python`) with `PYTHONPATH=<agrigen>/backend`.
It builds a tomato TraitVector from literal values (no Postgres), runs the UnifiedGenerator, and
writes a GLB. AgriGen is treated read-only — this script only imports it.

Usage: python agrigen_runner.py <out.glb> [seed] [target_points]
"""

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


def main() -> int:
    signal.signal(
        signal.SIGALRM,
        lambda *_: (sys.stderr.write("ABORT: agrigen_runner walltime guard\n"), sys.exit(2)),
    )
    signal.alarm(300)

    from agrigen.formats.gltf_writer import write_gltf
    from agrigen.generation.unified.generator import UnifiedGenerator

    out = sys.argv[1] if len(sys.argv) > 1 else "tomato.glb"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    target_points = int(sys.argv[3]) if len(sys.argv) > 3 else 20_000

    traits = _tomato_traits()
    geom = UnifiedGenerator().generate(traits, seed=seed, target_points=target_points)
    path = write_gltf(geom, traits, Path(out))
    print(f"WROTE {path} ({Path(out).stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
