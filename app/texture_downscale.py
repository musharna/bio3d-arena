"""Downscale embedded GLB textures to the resolution the arena can actually display.

Stage 2 of the mesh-weight work; stage 1 was Draco geometry compression (`app/mesh_compress`).

Measured on the corpus 2026-07-31: 452 embedded textures totalling 1.70 GB, of which **422 are
>= 2048px on a side** (117 of them 4096²) — while the arena renders meshes at ~300px on mobile and
~500px on desktop. That resolution cannot reach a viewer's eye; it is paid for in download time
on a site where a pairwise ballot already takes 83 seconds to become comparable on 4G.

Why this is hand-rolled rather than `gltf-transform resize`:

That command re-encodes, and its encoder is badly tuned for this content. Measured against the
original at the viewer's real render size (the only honest comparison — at source resolution the
larger image trivially "wins" detail nobody can display):

    gltf-transform resize  ->  0.52 MB   28.5 dB   (artifacts visible)
    PIL Lanczos + WebP q92 ->  0.53 MB   42.2 dB   (imperceptible)

Same size, 14 dB apart. It is not the filter (its default is already lanczos3) and not the
resolution drop (resizing without re-encoding scores 56 dB). It is purely the encode. On a
benchmark whose whole purpose is judging visual fidelity, shipping the first row would mean
voters scoring compression artifacts.

The GLB rewrite below is deliberately explicit about buffer bookkeeping. Every accessor addresses
its data by bufferView offset, so an offset that shifts by one byte does not raise — it renders
garbage geometry, silently.
"""

from __future__ import annotations

import io
import json
import struct

#: Long-edge cap and WebP quality, both set from a measured sweep on a real 4096² RGBA corpus
#: texture rather than picked by feel. PSNR is computed over VISIBLE pixels (alpha > 250) at the
#: viewer's render size — lossy WebP legitimately discards colour under fully transparent pixels,
#: and counting those drags the number down by ~13 dB while changing nothing anyone can see:
#:
#:     max_dim  quality   PSNR(visible)
#:        1024       92       38.0 dB      marginal
#:        1024       97       39.0 dB      marginal
#:        1536       92       41.7 dB
#:        1536       97       43.5 dB   <- chosen: >40 with 3.5 dB of margin
#:        2048       92       45.0 dB
#:
#: 1024 cannot reach 40 dB at ANY quality — the resolution is the binding constraint, not the
#: encoder. 1536 is the smallest cap that clears it, and it still shrinks all 422 oversized
#: textures in the corpus; a 2048 cap would leave the 305 already-at-2048 ones untouched and
#: give up most of the saving.
DEFAULT_MAX_DIM = 1536
DEFAULT_QUALITY = 97

_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942


def target_size(size: tuple[int, int], max_dim: int) -> tuple[int, int] | None:
    """New (w, h) capped at `max_dim` on the long edge, or None when already small enough.

    Aspect ratio is preserved: a squashed texture would slide across the model's UV mapping,
    which reads as a smeared or misplaced pattern rather than as a resolution change.
    """
    w, h = size
    longest = max(w, h)
    if longest <= max_dim:
        return None
    scale = max_dim / longest
    return (max(1, round(w * scale)), max(1, round(h * scale)))


def _split(glb: bytes) -> tuple[dict, bytes]:
    if len(glb) < 20 or glb[:4] != b"glTF":
        raise ValueError("not a binary glTF container")
    jlen, jtype = struct.unpack("<II", glb[12:20])
    if jtype != _CHUNK_JSON:
        raise ValueError("first GLB chunk is not JSON")
    gltf = json.loads(glb[20 : 20 + jlen])
    rest = glb[20 + jlen :]
    blob = b""
    if len(rest) >= 8:
        blen, btype = struct.unpack("<II", rest[:8])
        if btype == _CHUNK_BIN:
            blob = rest[8 : 8 + blen]
    return gltf, blob


def _assemble(gltf: dict, blob: bytes) -> bytes:
    raw = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    raw += b" " * (-len(raw) % 4)
    body = blob + b"\x00" * (-len(blob) % 4)
    chunks = struct.pack("<II", len(raw), _CHUNK_JSON) + raw
    chunks += struct.pack("<II", len(body), _CHUNK_BIN) + body
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def downscale_glb(
    glb: bytes, *, max_dim: int = DEFAULT_MAX_DIM, quality: int = DEFAULT_QUALITY
) -> tuple[bytes, dict]:
    """Return (rewritten glb, stats). Textures already within `max_dim` are left byte-identical.

    Returns the input unchanged when nothing needed resizing, so a texture-free or
    already-small mesh is never rewritten — rewriting costs fidelity and buys no bytes.
    """
    from PIL import Image

    gltf, blob = _split(glb)
    views = gltf.get("bufferViews", [])
    images = gltf.get("images", [])
    if not images or not views:
        return glb, {"resized": 0, "skipped": len(images), "bytes_saved": 0}

    replacement: dict[int, bytes] = {}
    skipped = 0
    for image in images:
        bv = image.get("bufferView")
        if bv is None or bv >= len(views):
            skipped += 1
            continue
        v = views[bv]
        off = v.get("byteOffset", 0)
        data = blob[off : off + v.get("byteLength", 0)]
        try:
            im = Image.open(io.BytesIO(data))
            new_size = target_size(im.size, max_dim)
            if new_size is None:
                skipped += 1
                continue
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            out = io.BytesIO()
            # method=6 is the slowest/best WebP search. This runs offline at release time, so
            # encode cost is irrelevant next to every future visitor's download.
            im.resize(new_size, Image.LANCZOS).save(out, "WEBP", quality=quality, method=6)
        except Exception:
            skipped += 1  # unreadable or unsupported codec: ship the original bytes
            continue
        replacement[bv] = out.getvalue()
        image["mimeType"] = "image/webp"

    if not replacement:
        return glb, {"resized": 0, "skipped": skipped, "bytes_saved": 0}

    # Rebuild the binary chunk. Views are re-laid-out in their existing index order, each 4-byte
    # aligned, with offsets rewritten — the only safe way to change a view's length, since every
    # later view would otherwise overlap it.
    new_blob = bytearray()
    for i, v in enumerate(views):
        pad = -len(new_blob) % 4
        new_blob.extend(b"\x00" * pad)
        if i in replacement:
            data = replacement[i]
        else:
            off = v.get("byteOffset", 0)
            data = blob[off : off + v.get("byteLength", 0)]
        v["byteOffset"] = len(new_blob)
        v["byteLength"] = len(data)
        new_blob.extend(data)

    if gltf.get("buffers"):
        gltf["buffers"][0]["byteLength"] = len(new_blob)

    return _assemble(gltf, bytes(new_blob)), {
        "resized": len(replacement),
        "skipped": skipped,
        "bytes_saved": max(0, len(glb) - (12 + len(new_blob))),
    }
