"""CLIP / BioCLIP species-identity capability. torch + open_clip are imported lazily so the
rest of the app (and non-GPU tests) never pay the import. Models are cached per-process."""

from __future__ import annotations

import functools
import io

MODELS = {
    "clip": "ViT-L-14/laion2b_s32b_b82k",  # generic OpenCLIP — strong compositional prompts
    "bioclip": "hf-hub:imageomics/bioclip-2",  # verify latest at build; prefer bioclip-2
}


def available() -> bool:
    try:
        import open_clip  # noqa: F401

        return True
    except Exception:
        return False


@functools.lru_cache(maxsize=4)
def load_model(kind: str):
    import open_clip
    import torch

    spec = MODELS[kind]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if spec.startswith("hf-hub:"):
        model, _, preprocess = open_clip.create_model_and_transforms(spec)
        tokenizer = open_clip.get_tokenizer(spec)
    else:
        name, pretrained = spec.split("/", 1)
        model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pretrained)
        tokenizer = open_clip.get_tokenizer(name)
    model = model.to(device).eval()
    return (model, preprocess, tokenizer, device)


def _logits(bundle, png: bytes, labels: list[str]):
    """Raw image·text cosine similarities (scaled) as a numpy array, one per label."""
    import torch
    from PIL import Image

    model, preprocess, tokenizer, device = bundle
    img = Image.open(io.BytesIO(png)).convert("RGB")
    with torch.no_grad():
        img_t = preprocess(img).unsqueeze(0).to(device)
        txt_t = tokenizer(labels).to(device)
        img_f = model.encode_image(img_t)
        txt_f = model.encode_text(txt_t)
        img_f = img_f / img_f.norm(dim=-1, keepdim=True)
        txt_f = txt_f / txt_f.norm(dim=-1, keepdim=True)
        sims = (100.0 * img_f @ txt_f.T).squeeze(0)
        return sims.cpu().numpy().astype("float64")


def zero_shot(bundle, png: bytes, labels: list[str]) -> dict[str, float]:
    import numpy as np

    z = _logits(bundle, png, labels)
    e = np.exp(z - z.max())
    p = e / e.sum()
    return {lab: float(pi) for lab, pi in zip(labels, p)}


def embed_image(bundle, png: bytes):
    import torch
    from PIL import Image

    model, preprocess, _, device = bundle
    img = Image.open(io.BytesIO(png)).convert("RGB")
    with torch.no_grad():
        f = model.encode_image(preprocess(img).unsqueeze(0).to(device))
        f = f / f.norm(dim=-1, keepdim=True)
        return f.squeeze(0).cpu().numpy().astype("float32")


def species_rep_score(bundle, png: bytes, *, common: str, taxon: str) -> float:
    """DEPRECATED — the binary "is this {species}?" vs "unidentifiable" framing is degenerate
    (2026-07-06 probe: scored ~1.0 for every organism photo incl. deliberate species mismatches,
    because any organism photo beats "unidentifiable" regardless of species). Use
    `classify_species` (multi-class) instead — probe: 13/13 correct. Retained only so the historic
    probe script still imports; do NOT use in new code."""
    labels = [
        f"a clear, identifiable photo of {common} ({taxon})",
        "an unrelated or unidentifiable image",
    ]
    return zero_shot(bundle, png, labels)[labels[0]]


def classify_species(
    bundle, png: bytes, panel: list[str], *, template: str = "a photo of {}."
) -> dict:
    """Multi-class species classification against a `panel` of candidate taxa — BioCLIP's strong
    mode (2026-07-06 probe: 13/13 correct, and it classifies a cross-labeled foil by its TRUE
    content). Returns {"top": taxon, "prob": float, "margin": float, "ranked": [(taxon, p), ...]}
    where margin = top prob minus runner-up prob. A caller verifies a claimed species by checking
    top == claimed (optionally with a margin floor)."""
    labels = [template.format(t) for t in panel]
    probs = zero_shot(bundle, png, labels)
    ranked = sorted(
        ((t, probs[lab]) for t, lab in zip(panel, labels)), key=lambda kv: kv[1], reverse=True
    )
    top_taxon, top_p = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    return {"top": top_taxon, "prob": top_p, "margin": top_p - runner, "ranked": ranked}
