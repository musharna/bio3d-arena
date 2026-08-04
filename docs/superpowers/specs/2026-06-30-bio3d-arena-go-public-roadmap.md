# Taxon3D — Go-Public Roadmap (scoping)

> 2026-06-30. Decomposition of "make this tool public / competitive / useful / novel."
> Companion deep-dive: [SP1 — Separate Public Instance](2026-06-30-sp1-separate-public-instance-design.md).
> Grounding memo (verified prior art + paper triage): `~/.claude/projects/-home-mjarnold-bio3d-arena/memory/sp4_paper_direction_triage_2026-06-30.md`.

## Locked decisions (from brainstorm)

- **All four axes** are goals, but they are **layered, not parallel**. Agrigen already
  consumes the arena internally as field-infrastructure (axis #4), so going public must not
  break or leak that internal use.
- **Separate public instance** — a distinct deployment (own DB + assets), curated promotion
  from the Agrigen-internal instance. Not a shared/gated single instance.
- **Launch is LAST, not first.** First impressions are one-shot; a thin launch (≈138 votes,
  wide BT CIs) burns the first-mover moment. Build quietly, launch when there's something worth
  launching.
- **Hosting: cheap/near-free now, scale as needed.**
- **"Novel" is already true and verified** — all four kill-queries returned OPEN for the
  biological framing (see grounding memo). Novelty is realized by _publishing + positioning_,
  not by building.

## Sequencing

```
NOW      SP4-lite : paper P-A as a light default (methods/system + the honest
                    "morphological-incompleteness / geometry-GT-blindspot" finding).
                    Runs on existing data. Not the focus — logged, low-invest.

FIRST    SP1      : Separate public instance — sever the Agrigen GT coupling,
   PRODUCT MOVE     cheap deploy. The gate with real unknowns. ← spec'd next.

MID      SP3      : Public submission policy + UI (backend exists) + dataset/API
                    release. Makes it "useful" to others.

SOFT     SP2      : Verified login (the one genuinely-new build) + soft-launch to
LAUNCH             seed votes so BT CIs tighten.

HARD     Launch + SP4 P-B : ride the paper; scaled bias-in-biology result at volume.
LAUNCH             One-shot first impression, now with content + tightened rankings.
```

## The four axes → what each needs (current-state verified)

Most "public" plumbing is **already seamed** — SP1 is mostly _wire + test + harden_, not build.

| Axis            | Sub-project | State                                                                       | Work                                                                                 |
| --------------- | ----------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| **Public**      | SP1         | Postgres/S3/captcha/rate-limit seams exist; GT path couples to Agrigen FS   | Sever Agrigen coupling (promote-don't-recompute); cheap deploy; secrets; legal pages |
| **Competitive** | SP2         | Anonymous-session voting only; no user identity                             | Verified login (HF-OAuth style) — the one real new build; soft-launch to seed votes  |
| **Useful**      | SP3         | `submissions.py` create/approve/reject/list already built                   | Public submission **policy + UI**; dataset/API release (published tasks + GT)        |
| **Novel**       | SP4         | Niche verified unoccupied; `compute_bias`, trait rubrics, κ-judge all built | Publish P-A (now) → P-B (at scale). Not a build.                                     |

## Verified-real seams (grounded, not README claims)

- `app/database.py` — dialect-aware engine, `pool_pre_ping`, pooling (Postgres-ready).
- `app/storage.py` — `S3StorageBackend` (lazy boto3, presigned URLs, CDN base URL).
- `app/integrity.py:verify_captcha` — wired into vote path via `X-Captcha-Token`; **real
  Turnstile/hCaptcha validation is still a no-op stub** (needs impl + keys).
- `app/submissions.py` — external model-submission backend (create/approve/reject/list) exists.
- `app/models.py` — `ModelOutput.license` + `.attribution` columns → provenance filter for
  the promotion boundary.

## The finding that shapes SP1

`app/config.py:59` — `BIO3D_GT_BUNDLE_DIR` defaults to
`/home/mjarnold/agrigen/backend/data/gt_bundle_prod`. The GT render/score path has a **hard
filesystem dependency on Agrigen's machine**. A separate public instance cannot depend on it.
The concrete SP1 task is **severing this coupling** — which _is_ the promotion boundary that
keeps unpublished Agrigen work (and the held-out test set) from leaking. Detailed in the SP1 spec.

## Out of scope (YAGNI for now)

- Real-time distributed scaling (Redis rate-limit seam exists; enable only if traffic demands).
- Live GT scoring on the public instance (promote precomputed scores instead).
- Deep paper work beyond the P-A default.
