# Blender procedural tomato (`procedural:blender`)

`gen_tomato.py` is an authored **native-Blender (bpy)** procedural _Solanum lycopersicum_ generator:
an upright stem with spiral phyllotaxy, pinnately compound **serrated** leaves built as meshes with
real thickness (Solidify), and hanging red fruit trusses (subdiv ico-spheres) with green calyces. It
exports a **GLB natively** (no OBJ→GLB step). It is the DCC/game-asset procedural representative of
the field, and the second procedural tomato (after L-Py) to clear the independent critic gate, so the
spotlight entry carries **no caveat**.

## Run

```sh
~/blender/blender -b -P blender/gen_tomato.py -- out.glb <seed>
```

End-to-end (generate + ingest): `python scripts/generate_blender.py` (needs a Blender binary;
`BLENDER_BIN` overrides the default `~/blender/blender`).

## Notes

- Native Blender GLB export (`export_scene.gltf`, `export_yup=True`) — model-viewer-ready, no
  rotation/Z-up fix needed (contrast L-Py/PlantGL which needs Z-up→Y-up + double-sided).
- Leaflets get a Solidify modifier (applied before join) so they read solid, not paper-thin — the
  Blender-native advantage over the flat L-Py `TriangleSet` leaflets.
- Morphology choices were tuned against the same critic panel L-Py passed: separate serrated
  compound leaflets (alternating large/small), drooping habit, and **large, well-separated** fruit
  per truss (small/clumped fruit read as "berries" — the panel's lone dissent until enlarged).
