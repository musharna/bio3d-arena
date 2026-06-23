# Task 3 Report: `volume_convert.volume_to_glb`

## TDD Evidence

### RED phase

```
$ .venv/bin/python -m pytest tests/test_volume_convert.py -q
ERROR collecting tests/test_volume_convert.py
ImportError while importing test module ...
E   ModuleNotFoundError: No module named 'app.volume_convert'
1 error in 0.63s
```

### GREEN phase (after fix — see deviation note)

```
$ .venv/bin/python -m pytest tests/test_volume_convert.py -q
......                                                                   [100%]
6 passed in 0.52s
```

### Intermediate (brief code verbatim, before fix)

```
$ .venv/bin/python -m pytest tests/test_volume_convert.py -q
.....F                                                                   [100%]
FAILED tests/test_volume_convert.py::test_volume_to_glb_decimates
assert 14584 <= 2200
1 failed, 5 passed in 0.95s
```

## Commit

SHA: `3b03c2a`
Subject: `feat(volumetric): volume_to_glb — NIfTI/TIFF volume → marching-cubes mesh GLB`

## Deviation from brief: adaptive step_size for decimation

**Test `test_volume_to_glb_decimates` failed** (5/6 pass on first run) with `assert 14584 <= 2200`.

**Root cause:** `fast_simplification` 0.1.13 (trimesh's `simplify_quadric_decimation` backend)
hits a topological floor at ~14 500 faces on marching-cubes meshes regardless of `target_reduction`.
The VTK decimator cannot collapse MC meshes below ~80% of the original count in a single pass;
even 5 chained passes only reaches ~14 000.

**Fix applied (minimal, correct):** Before the trimesh decimation call, adaptively increase
`step_size` (starting from caller-supplied value, capping at 8) and re-run `marching_cubes` until
the raw face count is already within `max_faces`. This reduces at the source rather than fighting
the decimator floor. The result mesh is coarser but correct; downstream callers that need finer
meshes should pass a larger `max_faces`.

The change is ~12 lines in `volume_to_glb` replacing the single `marching_cubes` call with a
`while` loop. All other code matches the brief verbatim.

## Fix: over-budget warning

Added a loud `print` warning (post-decimation, pre-export) for the case where both the adaptive
`step_size` loop and `simplify_quadric_decimation` hit their floors and the mesh is still over
budget. Per project policy ("fail loud — don't silently swallow"), the warning names the actual
face count, the budget, and `src_path`. No exception is raised — the heavy-but-usable mesh is
still returned.

### Diff

```diff
--- a/app/volume_convert.py
+++ b/app/volume_convert.py
@@ -117,6 +117,11 @@ def volume_to_glb(
     if len(mesh.faces) > max_faces:
         mesh = mesh.simplify_quadric_decimation(face_count=max_faces)
+    if len(mesh.faces) > max_faces:
+        print(
+            f"WARNING: {src_path}: mesh exceeds face budget after decimation "
+            f"({len(mesh.faces)} faces > {max_faces} limit); returning over-budget mesh",
+            flush=True,
+        )
     glb = mesh.export(file_type="glb")
```

### Test run

```
$ cd /home/mjarnold/bio3d-arena/.claude/worktrees/bio3d-arena-mvp && .venv/bin/python -m pytest tests/test_volume_convert.py -q
......                                                                   [100%]
6 passed in 0.55s
```

The warning path is not triggered by the existing tests (all stay within budget) — expected.
