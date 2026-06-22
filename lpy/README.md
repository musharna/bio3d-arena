# L-Py tomato L-system

`tomato.lpy` is an authored OpenAlea **L-Py / PlantGL** L-system that generates a procedural
_Solanum lycopersicum_ plant: pinnately compound serrated leaves (a rachis bearing separate
alternating large/small leaflets), spiral phyllotaxy up a central stem, and hanging red fruit
trusses with green calyces. It is the geometry source for the `procedural:lpy` spotlight entry —
the first procedural tomato to clear the independent critic gate (Helios and AgriGen did not).

## Environment

L-Py is OpenAlea, installed in a dedicated conda env (PlantGL is not in bio3d-arena's venv):

```sh
conda create -y -n lpy -c openalea3 -c conda-forge python=3.11 openalea.lpy openalea.plantgl
```

`scripts/generate_lpy.py` invokes the env via subprocess (`LPY_ENV_PYTHON`, default
`~/miniconda3/envs/lpy/bin/python`).

## Pipeline

```
lpy/tomato.lpy
  └─(lpy env) scripts/lpy_runner.py  → sets leaf/fruit/stem materials, iterates, saves OBJ+MTL
  └─(bio3d venv) scripts/lpy_glb.obj_to_glb  → Z-up→Y-up, preserve materials, force doubleSided
  └─ scripts/generate_lpy.ingest_lpy  → register as procedural:lpy (no caveat — passed the gate)
```

Run end-to-end: `python scripts/generate_lpy.py` (needs the `lpy` conda env).

## Notes (load-bearing)

- Turtle colour indices in the `.lpy`: `2`=leaf/calyx, `3`=fruit, `5`=stem — bound to botanical
  materials in `lpy_runner.py` (the `.lpy` stays pure geometry/grammar).
- Leaflets are flat single-sided `TriangleSet`s; the GLB step forces `doubleSided` so they show
  from both faces in model-viewer (otherwise back-face culling hides edge-on leaves).
- PlantGL exports Z-up; model-viewer is Y-up — `obj_to_glb` rotates -90° about X.
