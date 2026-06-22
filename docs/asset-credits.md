# Third-party asset credits

CC-licensed game-ready tomato-plant assets ingested as `found:sketchfab` entries (downloaded via
the Sketchfab API, glTF→GLB converted in Blender to preserve textures; the PolyOne pack's fruiting
stage `SM_Tomato_Lv3` is isolated). Each spotlight card shows its author + license; recorded here
for attribution compliance. Pipeline: `scripts/generate_sketchfab.py`.

| Asset                                        | Author     | License      | Source                                                                                     |
| -------------------------------------------- | ---------- | ------------ | ------------------------------------------------------------------------------------------ |
| Free Pack - Stylized Tomato (fruiting stage) | polyone    | CC-BY 4.0    | https://sketchfab.com/3d-models/free-pack-stylized-tomato-7613f2aec8f54695b7c219946473cb24 |
| Tomato Plant                                 | zvanstone  | CC-BY 4.0    | https://sketchfab.com/3d-models/tomato-plant-e0b559690e384fc0a9f3a05913f609c4              |
| Tomato Plants (Open Brush)                   | lindaman96 | CC-BY-SA 4.0 | https://sketchfab.com/3d-models/tomato-plants-e3293aa133cb439c96d2d7ca412fdcf5             |

> CC-BY-SA (lindaman96) is copyleft — any derivative of that specific asset must be shared alike.
> Confirm these license terms again at the pre-public `/spotlight` license re-vet.

## XfrogPlants (commercial — `found:xfrog`)

Photoreal botanical tomato growth-stage models from the **XfrogPlants Agriculture** library
(AG15 _Solanum lycopersicum_, stages 2/5/8/10), purchased commercially. FBX→GLB converted in
Blender (textures + alpha-cutout foliage, decimated to gallery weight). Pipeline:
`scripts/generate_xfrog.py` (reads the licensed FBX from `XFROG_AGRICULTURE_DIR`; the source files
are **NOT** committed). Source: <https://www.xfrog.net/product-page/library-agriculture>.

> **Commercial license — internal use only.** XfrogPlants terms permit use in renders/projects but
> NOT redistribution of the model files; a public `/spotlight` serves the converted GLB, which is a
> redistribution concern. **Must be cleared at the pre-public license re-vet** (the entries are
> flagged `re-vet before public display` in their stored license string).
