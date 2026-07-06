# `probe_labels.json` — probe evaluation set (SKELETON, not final)

This file is a **schema-valid skeleton**, not the finished evaluation set. It currently holds 4
real, hand-inspected, obviously-correct `"good"` photo-domain examples (verified to exist under
`data/assets/` and visually reviewed — no fabricated labels). Content is `"good"`-only because a
credible fruit_only / wrong_species / poor_exemplar / render example was not confidently
identifiable from a quick visual pass without a labeling session; see Task 4 Step 5 below.

**The controller will expand this to ~20-30 hand-labeled entries** before the probe run (Task 5
in `docs/superpowers/plans/2026-07-06-reference-image-integrity.md`), covering:

- photo domain: `good`, `fruit_only`, `wrong_species`, `poor_exemplar` examples across the
  sourced gallery taxa (and ideally the pre-fix `gourd_ref` wrong-subject photo from git history
  as a real wrong-subject/poor-exemplar case).
- render domain: `right_species` and `wrong_species` (with a `shown_as` foil) contact-sheet
  renders of known-species outputs, plus deliberately mislabeled ones.

## Item schema

```json
{
  "path": "reference/gallery/<slug>/<file>.jpg",   // relative to ASSET_DIR; read via app.storage.get_storage().read(path)
  "taxon": "Binomial name",                          // true rendered/depicted species
  "common": "common name",                           // used in CLIP/BioCLIP prompt text
  "domain": "photo" | "render",
  "label": "good" | "fruit_only" | "wrong_species" | "poor_exemplar"   // photo domain
         | "right_species" | "wrong_species"                           // render domain
  // render-domain "wrong_species" items only:
  "shown_as": "Binomial name"   // the WRONG species this render is claimed/exhibited as
}
```

See `scripts/probe_clip_bioclip.py`'s module docstring for exactly how each mechanism's
prediction is derived from these fields.
