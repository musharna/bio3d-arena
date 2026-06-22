# Helios `tomato_gen` project

A standalone Helios (UC Davis Bailey Lab, GPL-2.0) project that builds one parametric tomato plant
and writes it to OBJ. This is the geometry source for the `procedural:helios` spotlight entry.

## Build

Helios is a separate C++/CMake codebase (NOT the unrelated PyPI `pyhelios` CFD package). Clone it,
then drop this project into its `projects/` tree so the `BASE_DIRECTORY "../.."` in `CMakeLists.txt`
resolves to the Helios root:

```sh
git clone --recursive https://github.com/PlantSimulationLab/Helios ~/Helios
cp -r helios/tomato_gen ~/Helios/projects/tomato_gen
cd ~/Helios/projects/tomato_gen && mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release && make -j4
```

The binary is `~/Helios/projects/tomato_gen/build/tomato_gen <out.obj> [seed]` — the path
`scripts/generate_helios.py` expects via `HELIOS_TOMATO_BIN`.

## Notes (load-bearing)

- `leaf_subdivisions = (1,1)` is deliberate: one quad per leaf so the post-export Blender step
  (`scripts/helios_glb.py`) maps the full alpha-cutout leaf texture per leaf with clean UVs. The
  Helios default `(4,3)` tiles a single leaflet 12× across one leaf.
- `Context::writeOBJ` drops leaf textures on export (a textured `addTile` writes 0 UVs +
  `Kd 0 0 0` + no `map_Kd`), so the leaf texture is restored downstream in Blender, not here.
- The leaf texture path in `main.cpp` is absolute (`~/Helios/plugins/canopygenerator/textures/
TomatoLeaf_big.png`); the Helios default is relative and only resolves from the Helios root.
