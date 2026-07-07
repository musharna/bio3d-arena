# Handoff: Bio 3D Arena

## Overview
**Bio 3D Arena** is a web application for benchmarking AI 3D-generation models on *biological* subjects (plants, fungi, and — on the roadmap — animals). Its core loop is a blind **pairwise arena**: two anonymous model reconstructions of the same real organism are shown side by side, the user votes for the better one, and votes feed a **Bradley–Terry** ranking. Around that loop sits a full analytics suite — leaderboard, per-model pages, difficulty analysis, statistical significance, dataset/task catalogs, coverage, spotlight, and methodology.

The design is a **single-page app** with a persistent left sidebar, a sticky "kingdom scope" bar, and ~13 routable screens. It supports **light and dark themes** and a **tweakable accent color**, all driven by CSS custom properties.

---

## About the Design Files
The files in this bundle are **design references authored in HTML** — a working prototype that demonstrates the intended look, layout, motion, and behavior. **They are not production code to copy verbatim.**

The prototype is built as a "Design Component" (`.dc.html`) that runs on a small custom runtime (`support.js`). That runtime is a *prototyping* tool, **not** something to ship. Your task is to **recreate these designs in the target codebase's own environment** (React, Vue, Svelte, SwiftUI, etc.) using its established component patterns, routing, and state management. If no codebase exists yet, choose the most appropriate modern framework (React + TypeScript is a safe default) and implement there.

The one file worth reusing directly is **`viewer.js`** — see the "3D Viewer" section; it is framework-agnostic and wraps `<model-viewer>` / 3Dmol.js.

### How to open the prototype
Open `Bio 3D Arena v2.dc.html` in a browser (it loads `support.js` and `viewer.js` from the same folder, plus `<model-viewer>`, fonts, and 3Dmol.js from CDNs). Navigate via the sidebar; toggle theme via the button at the sidebar bottom.

---

## Fidelity
**High-fidelity (hifi).** Final colors, typography, spacing, motion, and interactions are all specified. Recreate the UI pixel-accurately using the codebase's own libraries. All colors are expressed in **OKLCH** — keep them in OKLCH if the target supports it (all evergreen browsers do), otherwise convert to the nearest sRGB hex.

---

## Design Tokens

All theming is done with CSS custom properties set on the app root. There are two full palettes (dark = default, light) plus a set of shared accent/semantic tokens layered on top. **Colors are OKLCH.** The accent is user-tweakable; the default is a chlorophyll green.

### Shared / accent tokens (both themes)
| Token | Value (dark → light) | Use |
|---|---|---|
| `--accent` | `oklch(0.72 0.14 150)` → `oklch(0.5 0.13 150)` | Primary brand green (tweakable). Active nav, primary buttons, scores, links |
| `--accent2` | `oklch(0.76 0.1 205)` → `oklch(0.52 0.11 208)` | Secondary cyan/teal. Eyebrows, secondary emphasis |
| `--win` | `oklch(0.78 0.15 142)` → `oklch(0.62 0.15 142)` | Positive / winner / "live" green |
| `--tie` | `oklch(0.68 0.04 258)` → `oklch(0.6 0.04 258)` | Neutral / tie grey |
| `--bad` | `oklch(0.64 0.17 26)` | Negative / error red |
| `--amber` | `oklch(0.78 0.14 78)` | "Soon" / roadmap / caution |

**Default accent swatch options** (the tweak menu): `oklch(0.56 0.13 150)` (chlorophyll, default), `oklch(0.5 0.11 168)`, `oklch(0.58 0.12 135)`, `oklch(0.63 0.17 274)`, `oklch(0.72 0.13 220)`.

### Dark palette (default)
| Token | Value |
|---|---|
| `--bg` | `oklch(0.165 0.018 258)` |
| `--navBg` | `oklch(0.20 0.02 258 / 0.85)` (translucent, backdrop-blurred) |
| `--panel` | `oklch(0.215 0.021 258)` |
| `--panelDeep` | `oklch(0.195 0.02 258)` |
| `--panel2` | `oklch(0.255 0.023 258)` |
| `--border` | `oklch(0.325 0.025 258)` |
| `--text` | `oklch(0.95 0.006 258)` |
| `--muted` | `oklch(0.72 0.02 258)` |
| `--faint` | `oklch(0.58 0.02 258)` |
| `--rowAlt` | `oklch(0.19 0.02 258)` (zebra rows) |
| `--shadowCard` | `0 1px 2px oklch(0 0 0 / 0.24), 0 10px 30px oklch(0 0 0 / 0.26)` |
| `--shadowLift` | `0 2px 6px oklch(0 0 0 / 0.3), 0 20px 50px oklch(0 0 0 / 0.4)` |

Viewer/stage-specific dark tokens: `--stage1 oklch(0.30 0.032 258)`, `--stage2 oklch(0.135 0.018 258)`, `--stageFrame oklch(0.145 0.018 258)`, `--vignette oklch(0.10 0.015 258 / 0.55)`, `--ctlBg oklch(0.16 0.02 258 / 0.78)`.

### Light palette
| Token | Value |
|---|---|
| `--bg` | `oklch(0.963 0.006 250)` |
| `--navBg` | `oklch(0.99 0.004 258 / 0.85)` |
| `--panel` | `oklch(1 0 0)` |
| `--panelDeep` | `oklch(0.987 0.004 258)` |
| `--panel2` | `oklch(0.955 0.008 250)` |
| `--border` | `oklch(0.9 0.008 258)` |
| `--text` | `oklch(0.26 0.02 258)` |
| `--muted` | `oklch(0.48 0.02 258)` |
| `--faint` | `oklch(0.62 0.02 258)` |
| `--rowAlt` | `oklch(0.965 0.006 258)` |
| `--shadowCard` | `0 1px 2px oklch(0.5 0.03 258 / 0.05), 0 8px 24px oklch(0.5 0.04 258 / 0.08)` |
| `--shadowLift` | `0 4px 10px oklch(0.5 0.03 258 / 0.08), 0 20px 48px oklch(0.5 0.05 258 / 0.14)` |

Viewer/stage-specific light tokens: `--stage1 oklch(0.93 0.008 258)`, `--stage2 oklch(0.8 0.012 258)`, `--stageFrame oklch(0.9 0.01 258)`, `--vignette oklch(0.55 0.02 258 / 0.15)`, `--ctlBg oklch(1 0 0 / 0.82)`.

### App background
A layered radial-gradient wash plus a subtle dot grid, fixed attachment:
```css
background:
  radial-gradient(130% 80% at 12% -8%, var(--wash), transparent 52%),
  radial-gradient(120% 70% at 92% 4%, var(--wash2), transparent 46%),
  radial-gradient(var(--gridDot) 1px, transparent 1.4px) 0 0 / 26px 26px,
  var(--bg);
background-attachment: fixed;
```
Washes: `--wash` green tint, `--wash2` cyan tint, `--gridDot` faint teal dot — all defined per theme.

### Typography
Three Google Fonts:
- **Space Grotesk** (600 mostly) — display / headings / logo. Tight tracking (`letter-spacing: -0.02em` to `-0.03em`).
- **IBM Plex Sans** (400/500/600) — body & UI. This is the base `body` font.
- **IBM Plex Mono** (400/500/600) — labels, eyebrows, stats, tabular numbers, code. Uppercase with wide tracking for eyebrows (`letter-spacing: 0.08em–0.24em`).

Representative sizes: hero H1 **52px**/1.02; page H1 **32px**; section titles ~15–16px; body **16.5px** (hero) / ~13–15px elsewhere; eyebrows/labels **10–11.5px** mono uppercase; smallest meta ~10px.

### Radii, spacing, borders
- Border radius: cards/panels **14px**; buttons/inputs/nav items **8–11px**; pills/chips **999px**; sidebar logo tile **8px**.
- Cards: `background: var(--panel)`, `border: 1px solid var(--border)`, `box-shadow: var(--shadowCard)`, padding ~18–22px.
- Content max-width **1180px** (most pages), **820px** (roadmap/stub), centered with ~30px horizontal padding.
- 1px borders everywhere; frequent `color-mix(in oklch, var(--accent) N%, var(--border))` for accent-tinted borders.

---

## Layout / App Shell

```
┌──────────┬─────────────────────────────────────────────┐
│          │  Kingdom scope bar (sticky top, blurred)     │
│ Sidebar  ├─────────────────────────────────────────────┤
│ (fixed,  │                                             │
│ ~248px,  │   Main content (max-width 1180px, centered) │
│ collap-  │   — the active screen renders here          │
│ sible to │                                             │
│ ~72px)   │                                             │
│          ├─────────────────────────────────────────────┤
│  Theme ⏾ │  Footer (border-top, links)                 │
└──────────┴─────────────────────────────────────────────┘
```

### Sidebar (left, fixed)
- Header: logo tile (rounded square, accent-tinted bg) containing the **tree-in-hexagon brand mark** (SVG), the wordmark "**Bio 3D**" (Space Grotesk; "3D" in accent), and a collapse toggle.
- Grouped nav with mono uppercase group headings: **Overview** (Home, Arena) · **Rankings** (Leaderboard, Models, Difficulty) · **Analysis** (Benchmark, Coverage, Significance) · **Data** (Dataset, Tasks, Spotlight) · **About** (Methodology, Submit).
- Each item: 20px icon slot + label. Active item uses `--accent` text + `color-mix(accent 14%)` background; hover uses `color-mix(accent 10%)`. Each nav icon is a small hand-drawn SVG.
- **Collapsed state** (~72px): labels hidden, items centered, icon-only. State persists.
- Bottom: **theme toggle** button (sun/moon glyph + "Theme" label).
- **Mobile (≤760px)**: sidebar slides off-canvas (`translateX(-100%)`), a hamburger button (top-left, 40×40) opens it over a scrim.

### Kingdom scope bar (sticky, top of content)
- Left: mono "KINGDOM" label + a **kingdom selector** (button that expands into a segmented control): **All · 🌿 Plants · 🍄 Fungi · 🐾 Animals (soon)**. Selecting a kingdom **re-filters every screen's data**. On wide screens, kingdom summary stats show on the right (`.b3d-kstats`, hidden ≤1120px).
- Right (wide only): a compact top-nav duplicate of primary links.
- Selecting **Animals** routes to a dedicated "Animals are next on the roadmap" screen.

---

## Screens / Views

All screens are kingdom-scoped (data refilters when the kingdom changes) and theme-aware. Content max-width 1180px unless noted.

### 1. Home / Landing
- **Purpose**: explain the product and route into the Arena.
- **Hero** (open, unboxed — no card): two-column grid `1.02fr / 0.98fr`, ~34px vertical padding, with a faint concentric-ring radial motif behind the right column.
  - Left: mono eyebrow "**Biological 3D reconstruction · benchmark**" (accent2, no icon); H1 **52px** "The *life-sciences* arena for **3D** generation" (the "3D" has a stacked/extruded multi-layer `text-shadow` in the accent color; "life-sciences" in accent2); a 16.5px muted lede; primary + secondary CTAs; a **stats strip** (border-top) with a **live pulse dot** (breathing animation) beside "1,585 votes cast", plus "9 models · 18 tasks · 2 kingdoms live"; and a "Generators from" row listing Microsoft, Tencent, Google, OpenAI, Tripo AI, Deemos, Inria.
  - Right (`.b3d-hero-viz`): a **live 3D specimen viewer** on a scan turntable (see 3D Viewer); on mobile it moves above the text (`order:-1`).
- Below hero: kingdom cards / feature grid.

### 2. Arena (the core loop)
- **Purpose**: blind pairwise vote.
- **Task strip** (`.b3d-strip`): the subject prompt + category/criterion selects.
- **Pair** (`.b3d-pair`): two viewer **stages** side by side (`1fr 1fr`, collapses to one column ≤760px). Each stage: real 3D viewer (GLB via `<model-viewer>`, or molecular via 3Dmol) with reset + fullscreen controls (top-right, appear on hover), a hover hint, loading spinner, and — **after voting** — a reveal pill top-left showing the real model name (e.g. "TRELLIS", "Hunyuan3D v2") and a small "**• 1st / • 2nd**" rank chip top-right. The winning stage gets a soft gold top-border.
- **Vote bar** (`.b3d-votebar`, becomes sticky-bottom on mobile): "A is better" / "Tie" / "B is better" style actions. Voting is disabled once a vote is cast for the pair.
- **Reveal / fanfare** (deliberately subtle): on first vote only, a light confetti burst; otherwise just the name pills, rank chips, and a single centered "**Next pair →**" button that advances to a fresh pair (remounts both viewers). The two viewers' cameras are **synced** (`Bio3DViewer.syncPair`) so rotating one rotates both.
- **State**: `voted` (null | 'A' | 'B' | 'tie'), `sessionVotes` counter, a pair sequence key that increments on Next.

### 3. Leaderboard
- **Purpose**: ranked models by Bradley–Terry rating.
- Tabbed (`lbTab`: e.g. Overall / per-category). A ranked table/list of models with rating, confidence interval, win-rate, vote count, and expandable rows (`lbOpen`). Kingdom-scoped.

### 4. Models (index) + Model Detail
- **Models**: grid/list of all generators (TRELLIS/Microsoft, Hunyuan3D v2/Tencent, etc.) with a distinct **generated model icon** per entry (not monograms), org, and headline stats.
- **Model Detail** (`modeldetail`): drill-in for one model — its ratings across tasks/kingdoms, strengths/weaknesses, sample outputs.

### 5. Difficulty
- Intro copy + a **difficulty chart**: a horizontal bar/lollipop plot of tasks ordered by how hard they are (how often models fail), with per-task top-3 labels. Includes a small label de-overlap pass so end labels don't collide.

### 6. Benchmark
- Aggregate benchmark scores; comparison tables/bars across models and tasks.

### 7. Coverage
- What kingdoms/tasks/tiers are covered vs. planned.

### 8. Significance (flagship viz)
- **Forest plot** of Bradley–Terry estimates with confidence intervals per model; a **head-to-head (H2H) win-rate matrix** (models × models grid of colored cells). Kingdom-scoped — in Fungi, the matrix collapses to only the models with fungi ratings.

### 9. Dataset
- Composition **stacked/segmented bars** by kingdom/tier + an **inventory table** with tier pills (image→3D, procedural, scan).

### 10. Tasks
- Catalog of the 18 tasks with kind, kingdom, and metadata; each row/card describes the subject and the reconstruction goal.

### 11. Methodology
- **Pipeline diagram** (submission → pairwise sampling → voting → Bradley–Terry fit → ranking) and the **BT model formula** rendered as math.

### 12. Submit
- Form/instructions for submitting a new model to the arena.

### 13. Spotlight
- Featured comparison / notable result.

### 14. Animals (roadmap / stub)
- Centered 820px screen: amber eyebrow, H1 "Animals are next on the roadmap", explanatory copy, a 3-up status grid (✓ Plants live · ✓ Fungi live · ◷ Animals in sourcing), and an "exit" button back to the app. Reached by selecting the Animals kingdom.

---

## 3D Viewer (`viewer.js` — reusable)

`viewer.js` registers a framework-agnostic `window.Bio3DViewer` used for every real 3D surface (hero specimen + both arena stages). **This file can be reused nearly as-is**; it has no framework dependency.

- **`Bio3DViewer.mount(slotEl, asset, opts)`** — mounts a renderer into a slot element, dispatching on `asset.format`:
  - **GLB/GLTF** → a `<model-viewer>` element (Google's web component, loaded from CDN `model-viewer@3.5.0`). Configured with `camera-controls`, transparent poster/background, and **eager loading** (`loading="eager"` / reveal on load — the default lazy IntersectionObserver does **not** fire reliably when embedded, so eager mount is required).
  - **Molecular** (PDB/mmCIF etc.) → a **3Dmol.js** viewer.
- **`Bio3DViewer.syncPair(slotA, slotB)`** — locks the two arena cameras together so orbiting one orbits both.
- Chrome rendered around each viewer: reset + fullscreen control buttons (`.viewer-ctl`, 31×31, appear on hover / always visible on touch), a hover hint (`.viewer-hint`), a loading spinner (`.viewer-spinner`, 0.8s spin), and an error state (`.viewer-error`, amber). These use the stage/viewer CSS tokens above.

**In production**: if using React, wrap `<model-viewer>` in a component (register the custom element once), or use `@google/model-viewer` + a 3Dmol React wrapper. Preserve: eager loading, camera-sync across the pair, transparent background, and the hover-reveal controls.

### Sample assets
The prototype references sample GLB models over HTTP (~8MB each) for the arena/hero. In production, wire these to your real asset pipeline/CDN. Model identities used in copy: **TRELLIS** (Microsoft), **Hunyuan3D v2** (Tencent), plus Google, OpenAI, Tripo AI, Deemos, Inria as generator orgs.

---

## Interactions & Behavior

- **Routing**: single `screen` state string selects the active view (`home`, `arena`, `leaderboard`, `models`, `modeldetail`, `difficulty`, `benchmark`, `coverage`, `significance`, `dataset`, `tasks`, `methodology`, `submit`, `spotlight`, plus the `animals` roadmap screen). In production use the router's URL routes.
- **Kingdom scope**: `kingdom` state (`all` | `plants` | `fungi` | `animals`) filters data on every screen; selecting `animals` routes to the roadmap screen.
- **Theme**: `theme` state (`dark` default | `light`), toggled from the sidebar; persisted (localStorage). Drives the entire CSS-var palette. Respect `prefers-color-scheme` for the initial value if no stored preference.
- **Sidebar collapse**: `navCollapsed` boolean, persisted; icon-only rail when collapsed.
- **Mobile nav**: `navMobileOpen` boolean; off-canvas drawer + scrim; hamburger toggle.
- **Voting**: click A/B/Tie → sets `voted`, increments `sessionVotes`, reveals model names + rank chips, shows medals, (first vote) confetti; "Next pair" increments a pair key and remounts fresh viewers with cameras re-synced.
- **Menus**: kingdom selector and any nav dropdowns expand/collapse via `openMenu` / `kingdomOpen` state; close on outside click / re-select.

### Motion (all respect `prefers-reduced-motion`)
Keyframes defined: `b3d-spin` (0.8s viewer spinner), `b3d-rise` / `b3d-rise-sm` (entrance, translateY + fade), `b3d-grow-x` (scaleX bar growth), `b3d-pop` (scale-in), `b3d-draw` (SVG stroke-dashoffset draw-on), `b3d-medal-drop` / `b3d-medal-shine`, `b3d-fade-in`, `b3d-confetti` (translate + rotate to CSS-var targets), `b3d-breathe` (2.4s live-pulse dot). A global `@media (prefers-reduced-motion: reduce)` collapses all animation/transition durations to ~0.

### Accessibility
- Visible focus ring on all interactive elements: `2px solid var(--accent)`, `outline-offset:2px`, `border-radius:8px` (via `:focus-visible`).
- `aria-label`s on icon-only controls (hamburger, theme toggle, collapse, kingdom selector, viewer controls).
- Viewer controls become always-visible under `@media (hover:none)`.

---

## State Management
Single component state object (in the prototype). In production, model as app/router state:
- `screen` — active route.
- `kingdom` — `all | plants | fungi | animals` scope filter.
- `theme` — `dark | light` (persisted).
- `accent` — accent color (tweakable prop; persisted if you expose it).
- `navCollapsed` (persisted), `navMobileOpen`, `openMenu` / `kingdomOpen`.
- `lbTab`, `lbOpen` — leaderboard tab + expanded row.
- `voted` (`null | 'A' | 'B' | 'tie'`), `sessionVotes`, pair sequence key, `stageLoading`.

### Data fetching (production)
The prototype uses static in-memory data. Real endpoints you'll likely need: current arena pair (subject + two model outputs + asset URLs), submit-vote, leaderboard/ratings (with CIs), per-model detail, task/dataset catalogs, difficulty + significance + coverage aggregates. All should accept a kingdom filter.

---

## Assets
- **Fonts**: Google Fonts — Space Grotesk, IBM Plex Sans, IBM Plex Mono. Use the codebase's font-loading approach.
- **Brand mark**: a **tree-in-hexagon** SVG (brown trunk, low green canopy, inside a hexagon outline). Appears as the sidebar logo, favicon (inline data-URI SVG in `<head>` — see top of the HTML for the exact path data), and loading states. Recreate as an SVG component; it is theme-aware via `currentColor`/accent.
- **Model icons**: each generator has its own small generated SVG icon (not text monograms) on the Models pages.
- **Nav icons**: small hand-drawn SVGs, one per nav item (home, arena, leaderboard, etc.), defined in the `icon(name)` helper in the logic class.
- **3D models**: sample GLB assets loaded from CDN/HTTP in the prototype; replace with your asset pipeline. `<model-viewer>` (v3.5.0) and 3Dmol.js are loaded from CDNs.
- **No raster images / no icon fonts / no emoji** except the kingdom glyphs (🌿 Plants, 🍄 Fungi, 🐾 Animals) used in the kingdom selector.

---

## Screenshots
Reference captures of each screen live in `screenshots/` (dark theme). They show intended layout, spacing, and hierarchy — treat them as visual truth alongside the token tables above.

- `01-home.png` — Home / landing (hero + kingdom cards)
- `02-arena.png` — Arena (task strip + A/B vote loop)
- `03-leaderboard.png` — Leaderboard
- `04-models.png` — Models index
- `05-difficulty.png` — Difficulty chart
- `06-benchmark.png` — Benchmark
- `07-coverage.png` — Coverage
- `08-significance.png` — Significance (forest plot + H2H matrix)
- `09-dataset.png` — Dataset (composition bars + inventory table)
- `10-tasks.png` — Tasks catalog
- `11-methodology.png` — Methodology (pipeline + BT formula)
- `12-submit.png` — Submit
- `13-spotlight.png` — Spotlight

*(The arena's live 3D viewer stages sit below the fold and load real GLB assets; the capture shows the top of the screen. See the app for the full stages.)*

## Files
- **`Bio 3D Arena v2.dc.html`** — the complete prototype (all screens, shell, logic). This is the source of truth for layout, copy, and behavior. The template markup is between `<x-dc>…</x-dc>`; the logic is the `class Component extends DCLogic { … }` block near the bottom (~line 960+). Theme tokens live in the `themeVars`/`vars` getter (~line 1495). Nav structure is in the sidebar-groups builder (~line 1008).
- **`viewer.js`** — the reusable 3D viewer registry (`window.Bio3DViewer`). Reuse in production.
- **`support.js`** — the prototype runtime only. **Do not ship.** Included so the HTML opens standalone.
- **`DESIGN_SYSTEM.md`** — supplementary notes on the visual system (if present).

### Reading tips
- Copy is final — lift exact strings from the HTML.
- All measurements/colors are inline in `style="…"` attributes and in the theme-token getter; there is no separate stylesheet (by design of the prototyping tool). Consolidate these into your own tokens/theme file when porting.
- The tweakable props are declared on the `data-props` of the logic `<script>` tag: `theme` (enum dark/light) and `accent` (color, default `oklch(0.56 0.13 150)`, with the swatch options listed above).
