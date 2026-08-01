"""Draco compression for meshes we SERVE, never for meshes we keep.

Why this exists, measured rather than assumed (2026-07-30):

* The votable roster is 311 GLBs / 5.35 GB, of which **68% is raw geometry** and nothing carries
  Draco, meshopt or KTX2 — every mesh ships exactly as the generator emitted it.
* On Chrome "Fast 4G" (4 Mbit/s, CDP-throttled) a **pairwise** ballot takes **83 seconds** before
  a voter can compare anything; a 4-up takes 99.6s. The k-wise default is a ~20% aggravation of a
  problem that was already fatal on mobile, not the cause of it.
* A Draco pilot on two real corpus meshes: 59.6 MB -> 4.34 MB (13.7x) and 50.2 MB -> 9.89 MB
  (5.1x, the lower ratio being 5.8 MB of texture Draco does not touch), at a mean geometric
  deviation of **0.0017%** of the bounding-box diagonal — roughly 0.01 px in a 300 px viewer.

Two invariants, both easy to break silently:

1. **Originals never move.** Compression runs at export, so the internal corpus stays
   byte-identical. Reproducibility, any dataset release, and the votes already cast against those
   exact meshes all depend on that. The compressed file is a serving artifact that happens to
   occupy the same bundle-relative path.
2. **The stimuli must not change.** This is a fidelity benchmark; if compression dropped a UV set
   or a material, voters would judge something other than what was scored, and the board would
   silently stop measuring what it claims to. `structural_signature` is compared on EVERY file,
   not sampled.

Deliberately NOT importable by the public web app: this shells out to a Node toolchain, and
`requirements.txt` is Python-only by design (PR #87 cut the public runtime from 5.6 GB to 173 MB).
Export runs on the internal instance, which has the toolchain; the public host only ever serves
bytes that were already compressed upstream.
"""

from __future__ import annotations

import json
import os
import re
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: The CLI uses import attributes (`with { type: 'json' }`), which Node 18 cannot parse — it dies
#: with a bare SyntaxError several frames from anything meaningful. Refuse before that happens.
MIN_NODE_MAJOR = 20

#: Below this, compressing is not worth a second copy of the asset in the bundle.
MIN_USEFUL_RATIO = 1.05

_GLB_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A


class NotAGlb(ValueError):
    """The bytes handed over are not a binary glTF container."""


class ToolchainUnavailable(RuntimeError):
    """Node (>= MIN_NODE_MAJOR) or the gltf-transform CLI is not usable here."""


class CompressionChangedTheModel(RuntimeError):
    """Compression altered something a voter could perceive. Never ship this file."""


@dataclass(frozen=True)
class CompressionResult:
    source_bytes: int
    output_bytes: int
    kept: bool

    @property
    def ratio(self) -> float:
        return self.source_bytes / self.output_bytes if self.output_bytes else 0.0


# --------------------------------------------------------------------------- container


def glb_json(data: bytes) -> dict:
    """The glTF JSON chunk, without touching the (potentially hundreds of MB) binary payload."""
    if len(data) < 20 or data[:4] != _GLB_MAGIC:
        raise NotAGlb("not a binary glTF container")
    length, chunk_type = struct.unpack("<II", data[12:20])
    if chunk_type != _CHUNK_JSON:
        raise NotAGlb("first GLB chunk is not JSON")
    return json.loads(data[20 : 20 + length])


def is_candidate(asset_path: str) -> bool:
    """Only GLB. Point clouds, volumes and molecular formats go to different viewers entirely,
    and reference photos are not meshes at all."""
    return str(asset_path).lower().endswith(".glb")


# --------------------------------------------------------------------------- fidelity guard


def structural_signature(data: bytes) -> dict:
    """Everything about a mesh that compression must leave alone.

    Vertex positions are *expected* to move — Draco quantizes, and the pilot put that at 0.0017%
    of the bbox diagonal. What must NOT move is the shape of the asset: how many meshes and
    materials it has, whether its textures survived, and which vertex attributes exist. A tool bug
    that dropped TEXCOORD_0 would still produce a file that loads and still looks like a mesh.
    """
    g = glb_json(data)
    attrs: set[str] = set()
    for mesh in g.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs |= set(prim.get("attributes", {}))
    return {
        "meshes": len(g.get("meshes", [])),
        "materials": len(g.get("materials", [])),
        "textures": len(g.get("textures", [])),
        "images": len(g.get("images", [])),
        "attributes": tuple(sorted(attrs)),
    }


def structural_diff(before: dict, after: dict) -> list[str]:
    """Human-readable differences, empty when the model survived intact."""
    out = []
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key), after.get(key)
        if a != b:
            out.append(f"{key}: {a!r} -> {b!r}")
    return out


# --------------------------------------------------------------------------- toolchain


def parse_node_major(raw: str) -> int | None:
    m = re.match(r"^v?(\d+)\.", (raw or "").strip())
    return int(m.group(1)) if m else None


def detect_node_major(binary: str) -> int | None:
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_node_major(out.stdout)


def node_binary() -> str:
    """`BIO3D_NODE_BIN` exists because the system Node is frequently too old while a usable one
    sits in a conda env — pinning it explicitly beats mutating PATH in a release script."""
    return os.environ.get("BIO3D_NODE_BIN", "node")


def require_node(binary: str) -> int:
    major = detect_node_major(binary)
    if major is None:
        raise ToolchainUnavailable(
            f"no usable Node at {binary!r}. Mesh compression needs Node >= {MIN_NODE_MAJOR}; "
            "set BIO3D_NODE_BIN to one, or re-run the export with --no-compress to ship "
            "uncompressed meshes."
        )
    if major < MIN_NODE_MAJOR:
        raise ToolchainUnavailable(
            f"Node {major} at {binary!r} is too old: the gltf-transform CLI needs "
            f">= {MIN_NODE_MAJOR} (it uses import attributes, and older Node fails with a bare "
            "SyntaxError). Set BIO3D_NODE_BIN to a newer Node, or export with --no-compress."
        )
    return major


def draco_command(node: str, cli_entry: str, src: Path, dst: Path) -> list[str]:
    return [node, str(cli_entry), "draco", str(src), str(dst)]


def cli_entry() -> str | None:
    """Path to the gltf-transform CLI entry point, or None when it is not installed here.

    An env var rather than a vendored dependency: the CLI is a 206-package Node tree that only
    the release machine ever needs, and pinning it into this repo would put it on the path of
    every contributor who only wants to run the tests.
    """
    return os.environ.get("BIO3D_GLTF_TRANSFORM_CLI") or None


def toolchain_ready() -> bool:
    """True when this machine can actually compress. Callers use it to decide between
    compressing and failing loudly — never to skip silently."""
    entry = cli_entry()
    if not entry or not Path(entry).exists():
        return False
    return detect_node_major(node_binary()) is not None


# --------------------------------------------------------------------------- policy


def worth_keeping(original_size: int, compressed_size: int) -> bool:
    """Draco can enlarge an already-compressed or trivially small mesh. Serving that is strictly
    worse than serving the original, so the caller keeps what it had."""
    if compressed_size <= 0:
        return False
    return (original_size / compressed_size) >= MIN_USEFUL_RATIO


def compress_glb(
    src: Path, dst: Path, *, node: str, cli_entry: str, timeout: int = 900
) -> CompressionResult:
    """Compress one GLB, refusing any output that changed the model or failed to shrink it.

    Returns a result with `kept=False` when the original should be served instead; `dst` is
    removed in that case so a caller cannot accidentally ship it.
    """
    src, dst = Path(src), Path(dst)
    before = structural_signature(src.read_bytes())
    proc = subprocess.run(
        draco_command(node, cli_entry, src, dst),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0 or not dst.is_file():
        raise RuntimeError(
            f"gltf-transform draco failed for {src.name} (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
        )
    after = structural_signature(dst.read_bytes())
    diff = structural_diff(before, after)
    if diff:
        dst.unlink(missing_ok=True)
        raise CompressionChangedTheModel(
            f"{src.name}: compression altered the model — {'; '.join(diff)}. Refusing to serve it."
        )
    s_bytes, d_bytes = src.stat().st_size, dst.stat().st_size
    if not worth_keeping(s_bytes, d_bytes):
        dst.unlink(missing_ok=True)
        return CompressionResult(s_bytes, d_bytes, kept=False)
    return CompressionResult(s_bytes, d_bytes, kept=True)
