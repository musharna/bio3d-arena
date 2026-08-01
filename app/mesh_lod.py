"""A low-detail mesh to START a ballot with, never the mesh a voter finishes judging on.

Measured 2026-08-01, on the 525-mesh serving pool after Draco (PR #124) and the texture pass
(PR #127):

* median served mesh 0.20 MB, mean 0.85 MB — but the tail is fat: 19 files exceed 4 MB and carry
  19% of all bytes, every one of them geometry-dominated at 1.4-1.6M triangles.
* a simulated 4-up ballot (20k draws) is 3.45 MB mean / 6.51 MB p90, i.e. 6.9s / 13.0s of transfer
  on Fast 4G. Capping meshes at 1 MB takes that to 3.8s / 6.1s.

So the win is real but bounded — Draco already took the corpus from 99.6s to 20.9s, and geometry
is only ~7s of what remains. That ceiling is why this ships as an LOD rather than as decimation in
place: a modest speedup does not justify altering what a fidelity benchmark asks people to judge.

**The invariant that makes this safe.** The arena viewer grants `camera-controls` with zoom
enabled and says so on screen ("drag to rotate · scroll to zoom", `viewer.js`). A voter can look
closely, and at close range a decimated mesh IS distinguishable — they would attribute our
faceting to the generator. So the LOD is only ever the *opening* frame: the viewer swaps in the
full mesh the moment anyone zooms or goes fullscreen. Nothing here is allowed to assume the
difference is imperceptible, because it is not.

**Why this needs its own gate.** `mesh_compress.structural_signature` is sufficient for Draco,
which quantizes positions and keeps every triangle. Decimation deletes triangles, and the
signature cannot see that: a pass that dropped 99% of the geometry has the same mesh, material,
texture and attribute counts. `check_lod` therefore adds a triangle floor on top of the signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.mesh_compress import glb_json, structural_diff, structural_signature

#: Suffix marking a file as the reduced companion of its neighbour.
LOD_SUFFIX = ".lod.glb"

#: Below this a second artifact costs more (bundle bytes, an import row, a cache entry) than the
#: transfer it saves. Set from the measured distribution: it excludes the 0.20 MB median outright
#: and targets the ~196 files that carry most of the weight.
LOD_MIN_SOURCE_BYTES = 1_000_000

#: Fraction of triangles meshoptimizer is asked to keep.
LOD_TARGET_RATIO = 0.25

#: Simplification error bound, as a fraction of mesh extent.
LOD_TARGET_ERROR = 0.001

#: An LOD must be at least this much smaller than its source, or serving two files is a worse
#: trade than serving one.
LOD_MIN_USEFUL_RATIO = 1.5

#: A decimation may not leave fewer than this fraction of the original triangles. Catches a
#: simplify pass that collapsed the model into a blob — which `structural_signature` cannot see.
LOD_MIN_TRIANGLE_FRACTION = 0.02

#: ...except on meshes already so coarse that the fraction is meaningless. A 200-triangle source
#: has no meaningful floor to enforce.
LOD_TRIANGLE_FLOOR_EXEMPT_BELOW = 1_000


class LodChangedTheModel(RuntimeError):
    """The LOD lost a material, texture or vertex attribute. Never serve it."""


class LodCollapsed(RuntimeError):
    """The LOD kept its structure but lost nearly all of its geometry."""


@dataclass(frozen=True)
class LodResult:
    source_bytes: int
    lod_bytes: int
    source_triangles: int
    lod_triangles: int
    kept: bool

    @property
    def ratio(self) -> float:
        return self.source_bytes / self.lod_bytes if self.lod_bytes else 0.0


# --------------------------------------------------------------------------- naming


def is_lod_path(asset_path: str | Path) -> bool:
    return str(asset_path).lower().endswith(LOD_SUFFIX)


def lod_path(asset_path: str | Path) -> str:
    """`a.glb` -> `a.lod.glb`, and idempotent so a re-run cannot produce `a.lod.lod.glb`."""
    text = str(asset_path)
    if is_lod_path(text):
        return text
    return text[: -len(".glb")] + LOD_SUFFIX if text.lower().endswith(".glb") else text


# --------------------------------------------------------------------------- candidacy


def is_lod_candidate(asset_path: str | Path, size_bytes: int) -> bool:
    """Only large GLBs. Not point clouds or volumes (different viewers), not LODs themselves."""
    text = str(asset_path).lower()
    if not text.endswith(".glb") or is_lod_path(text):
        return False
    return size_bytes >= LOD_MIN_SOURCE_BYTES


# --------------------------------------------------------------------------- geometry


def triangle_count(data: bytes) -> int:
    """Triangles across every primitive, read from the JSON chunk alone.

    Deliberately does not touch the binary payload: these files reach 60 MB and the export walks
    the whole corpus.
    """
    g = glb_json(data)
    accessors = g.get("accessors", [])
    total = 0
    for mesh in g.get("meshes", []):
        for prim in mesh.get("primitives", []):
            if prim.get("mode", 4) != 4:  # only TRIANGLES
                continue
            idx = prim.get("indices")
            if idx is not None and idx < len(accessors):
                total += accessors[idx].get("count", 0) // 3
            else:
                pos = prim.get("attributes", {}).get("POSITION")
                if pos is not None and pos < len(accessors):
                    total += accessors[pos].get("count", 0) // 3
    return total


# --------------------------------------------------------------------------- the gate


def worth_keeping(source_bytes: int, lod_bytes: int) -> bool:
    if lod_bytes <= 0:
        return False
    return (source_bytes / lod_bytes) >= LOD_MIN_USEFUL_RATIO


def check_lod(before: bytes, after: bytes) -> None:
    """Raise unless `after` is a safe reduced stand-in for `before`.

    Model integrity only; whether the LOD is *worth serving* is a separate question with a
    separate answer (`worth_keeping`).

    Two independent failures, because they fail differently: losing a material is a toolchain bug
    that changes what is rendered, while losing the geometry is a simplify parameter that produced
    a blob. The signature catches the first and only the first.
    """
    diff = structural_diff(structural_signature(before), structural_signature(after))
    if diff:
        raise LodChangedTheModel(
            f"LOD altered the model — {'; '.join(diff)}. Refusing to serve it."
        )
    src_tris = triangle_count(before)
    lod_tris = triangle_count(after)
    if src_tris >= LOD_TRIANGLE_FLOOR_EXEMPT_BELOW:
        floor = int(src_tris * LOD_MIN_TRIANGLE_FRACTION)
        if lod_tris < floor:
            raise LodCollapsed(
                f"LOD kept only {lod_tris:,} of {src_tris:,} triangles "
                f"({100 * lod_tris / src_tris:.2f}%), below the {floor:,} floor. "
                "The structure survived but the geometry did not — refusing to serve it."
            )


# --------------------------------------------------------------------------- toolchain


def weld_command(node: str, cli_entry: str, src: str | Path, dst: str | Path) -> list[str]:
    """Merge vertices that are identical in every attribute.

    Not optional. The CLI says so ("For best results, apply a 'weld' operation before
    simplification") and the reason is mechanical: meshoptimizer collapses EDGES, and a split
    vertex means the two triangles sharing that position are not topologically adjacent, so there
    is no edge there to collapse. Welding is lossless — only exact duplicates merge — so it cannot
    move a surface or change shading.
    """
    return [node, str(cli_entry), "weld", str(src), str(dst)]


def simplify_command(
    node: str,
    cli_entry: str,
    src: str | Path,
    dst: str | Path,
    *,
    ratio: float = LOD_TARGET_RATIO,
    error: float = LOD_TARGET_ERROR,
) -> list[str]:
    """Reduce triangle count, bounded by `error` as a fraction of mesh radius.

    `--lock-border false` is deliberate and was measured. Locking borders pins every boundary edge
    in place; on a mesh split into hundreds of primitives that is nearly every edge, and
    simplification does essentially nothing (observed: 400 of 1,625,148 triangles removed on a
    422-primitive mesh, even with the error bound removed entirely). The borders worth protecting
    on a connected mesh are protected by `error`, not by pinning.
    """
    return [
        node,
        str(cli_entry),
        "simplify",
        str(src),
        str(dst),
        "--ratio",
        str(ratio),
        "--error",
        str(error),
        "--lock-border",
        "false",
    ]


def draco_command(node: str, cli_entry: str, src: str | Path, dst: str | Path) -> list[str]:
    """Re-compress after simplifying.

    Also not optional, and the failure is spectacular without it: `simplify` DECODES any incoming
    KHR_draco_mesh_compression and writes plain geometry, so a 5.35 MB Draco'd source came back out
    at 123.68 MB — 23x LARGER. `worth_keeping` would then reject every LOD and the export would
    produce nothing while reporting success.
    """
    return [node, str(cli_entry), "draco", str(src), str(dst)]


def generate_lod(
    src: Path,
    dst: Path,
    *,
    node: str,
    cli_entry: str,
    ratio: float = LOD_TARGET_RATIO,
    error: float = LOD_TARGET_ERROR,
    timeout: int = 900,
) -> LodResult:
    """Produce one LOD via weld -> simplify -> draco, refusing any output that changed the model
    or failed to earn its place.

    All three stages are required; see each command's docstring for the measurement that says so.
    The intermediates are uncompressed and can reach ~124 MB for a 5 MB source, so they live in a
    temporary directory and are discarded — only `dst` survives.

    `dst` is removed when the result is not kept, so a caller cannot accidentally ship it.
    """
    import subprocess
    import tempfile

    src, dst = Path(src), Path(dst)
    before = src.read_bytes()

    def run(cmd: list[str], stage: str) -> None:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        # Every one of the three commands is [node, cli, verb, src, dst, ...opts].
        out = Path(cmd[4])
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError(
                f"gltf-transform {stage} failed for {src.name} (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
            )

    with tempfile.TemporaryDirectory(prefix="bio3d-lod-") as tmp:
        welded = Path(tmp) / "welded.glb"
        reduced = Path(tmp) / "reduced.glb"
        run(weld_command(node, cli_entry, src, welded), "weld")
        run(
            simplify_command(node, cli_entry, welded, reduced, ratio=ratio, error=error), "simplify"
        )
        run(draco_command(node, cli_entry, reduced, dst), "draco")

    after = dst.read_bytes()
    s_bytes, d_bytes = src.stat().st_size, dst.stat().st_size
    try:
        check_lod(before, after)
    except (LodChangedTheModel, LodCollapsed):
        dst.unlink(missing_ok=True)
        raise
    result = LodResult(
        source_bytes=s_bytes,
        lod_bytes=d_bytes,
        source_triangles=triangle_count(before),
        lod_triangles=triangle_count(after),
        kept=worth_keeping(s_bytes, d_bytes),
    )
    if not result.kept:
        dst.unlink(missing_ok=True)
    return result


__all__ = [
    "LOD_SUFFIX",
    "LodChangedTheModel",
    "LodCollapsed",
    "LodResult",
    "check_lod",
    "generate_lod",
    "is_lod_candidate",
    "is_lod_path",
    "lod_path",
    "simplify_command",
    "triangle_count",
    "structural_signature",
    "worth_keeping",
]
