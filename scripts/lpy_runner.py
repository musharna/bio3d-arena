"""Run an L-Py tomato model and write its geometry to OBJ — RUN WITH THE L-PY CONDA ENV,
not bio3d-arena's venv. `generate_lpy.py` invokes this via subprocess using the `lpy` conda
env python (it has openalea.lpy + openalea.plantgl). Sets botanical materials (leaf/fruit/stem),
iterates the L-system, and saves an OBJ (+MTL).

Usage: python lpy_runner.py <model.lpy> <out.obj>
"""

import signal
import sys


def main() -> int:
    signal.signal(
        signal.SIGALRM,
        lambda *_: (sys.stderr.write("ABORT: lpy_runner walltime guard\n"), sys.exit(2)),
    )
    signal.alarm(180)

    from openalea.lpy import Lsystem
    import openalea.plantgl.all as pgl

    lpy_file = sys.argv[1] if len(sys.argv) > 1 else "tomato.lpy"
    out_obj = sys.argv[2] if len(sys.argv) > 2 else "tomato.obj"

    ls = Lsystem(lpy_file)
    ctx = ls.context()
    # turtle colour indices used in the .lpy: 2=leaf/calyx-sepal, 3=fruit, 5=stem
    ctx.turtle.setMaterial(2, pgl.Material("leaf", (34, 82, 26)))
    ctx.turtle.setMaterial(3, pgl.Material("fruit", (204, 36, 24), diffuse=1.0, shininess=0.15))
    ctx.turtle.setMaterial(5, pgl.Material("stem", (60, 92, 40)))
    scene = ls.sceneInterpretation(ls.iterate())
    scene.save(out_obj)
    print(f"WROTE {out_obj} ({len(scene)} shapes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
