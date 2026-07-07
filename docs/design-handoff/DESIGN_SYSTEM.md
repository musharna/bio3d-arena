# Bio 3D Arena — Refreshed Design System (handoff)

Direction chosen: **Focus** (viewer-first). Prototype lives in `Bio 3D Arena.dc.html`
(nav switches screens; theme toggle top-right; accent is tweakable). This doc maps the
system back onto `app/templates/*.html` + `app/static/style.css`. Everything below is
theme-aware via CSS custom properties — keep the existing `--*` variable approach, just
replace the values and add the new ones.

## Design intent
- **Models dominate.** Task context collapses to one slim strip so the eye goes
  reference → the two 3D stages → vote. Stages are tall (430px) and darker than the page
  with an inner vignette so the rendered model pops off the surface.
- **Quiet, precise chrome.** Sober dark UI, one confident indigo accent, data in mono.
  Reads like a scientific instrument, not a dev tool.
- **One table system** for every data-dense page (Leaderboard, Difficulty, Benchmark,
  Coverage): uppercase mono-ish micro headers, hairline row rules, tabular-nums, semantic
  color only where it carries meaning (win/tie/bad, firm/provisional, pass/fail, heatmap).

## Tokens (replace `:root` in style.css)

### Dark (primary)
```
--bg:        oklch(0.165 0.018 258)   /* page */
--nav-bg:    oklch(0.20 0.02 258 / .85) /* sticky nav, backdrop-blur 10px */
--panel:     oklch(0.215 0.021 258)   /* cards, task strip */
--panel2:    oklch(0.255 0.023 258)   /* inputs, chips, inset */
--border:    oklch(0.325 0.025 258)
--text:      oklch(0.95 0.006 258)
--muted:     oklch(0.72 0.02 258)
--faint:     oklch(0.58 0.02 258)     /* captions, hints, micro-labels */
--accent:    oklch(0.63 0.17 274)     /* indigo — brand + active nav + links-ish */
--accent2:   oklch(0.72 0.12 248)     /* GLB chips, secondary links */
--win:       oklch(0.72 0.15 152)     /* A/B better, PASS, firm, heatmap high */
--tie:       oklch(0.68 0.04 258)     /* neutral */
--bad:       oklch(0.64 0.17 26)      /* both-bad, FAIL, heatmap low */
--amber:     oklch(0.78 0.14 78)      /* provisional, heatmap mid */
/* stage: radial-gradient(130% 100% at 50% 30%, --stage1, --stage2 70%) + inset vignette */
--stage1:    oklch(0.30 0.032 258)
--stage2:    oklch(0.135 0.018 258)
```

### Light (ship theme-aware; body.light or [data-theme=light])
```
--bg:     oklch(0.975 0.005 258)   --panel:  oklch(0.995 0.003 258)
--panel2: oklch(0.955 0.007 258)   --border: oklch(0.885 0.01 258)
--text:   oklch(0.26 0.02 258)     --muted:  oklch(0.48 0.02 258)   --faint: oklch(0.62 0.02 258)
--accent2: oklch(0.55 0.15 258)    --stage1: oklch(0.93 0.008 258)  --stage2: oklch(0.8 0.012 258)
/* --accent/--win/--bad/--amber stay the same hue in both themes */
```
Tinted fills use `color-mix(in oklch, var(--x) N%, transparent|var(--panel2))` so they track
the theme automatically — e.g. chip bg `color-mix(in oklch, var(--accent) 16%, transparent)`.

## Type
- Display / headings: **Space Grotesk** 600, `letter-spacing:-0.02em` (h1 28px, h2 16–18px,
  model labels 14px).
- Body / UI: **IBM Plex Sans** (400/500/600).
- Numbers, metrics, IDs, micro-labels, kbd: **IBM Plex Mono** with `font-variant-numeric:
  tabular-nums`.
- Table column headers: 10–10.5px, weight 600, `letter-spacing:.07em`, uppercase, `--faint`.

## Spacing / shape
- Page gutter 30px, content max-width 1180px.
- Radii: cards/panels 13–14px, controls/chips 8–9px, pills/badges 999px, stages 14px.
- Panel treatment: `1px solid var(--border)` + a 2px accent top-border on "hero" panels
  (viewer stages, per-tier cards). No heavy shadows except the floating vote bar
  (`0 14px 40px` at ~0.6 alpha) and card lift on hover (border → accent 55%).

## Component notes (→ templates)
- **Nav** (`base.html`): sticky, blur. **Grouped IA** — only two anchors stay flat (**Arena**,
  **Leaderboard**); the rest collapse into dropdown menus: **Results** (Difficulty · Benchmark ·
  Significance · Spotlight), **Data** (Coverage · Dataset · Tasks), **About** (Methodology ·
  Fidelity · Procedural), plus a **Submit ↗** outline CTA. Unbuilt pages carry a `soon` tag in
  the menu. Active menu = accent-tinted when the current screen is one of its children.
- **Brand / "Bio 3D" identity**: wordmark sets **3D** in the accent; the mark is an **isometric
  cube** (hexagon clip + 3-shade conic-gradient). Model stages read as real **3D viewports** —
  a receding perspective **ground-grid** + an **XYZ axis gizmo** (R/G/B) bottom-left, with the
  subject emoji as the "specimen" on the floor. Together these signal biology (kingdom motifs)
  + 3D (viewport furniture) without illustration.
- **Arena** (`arena.html`): task strip (round ref thumb + chip + title + Judge-on selects) →
  two stages → **floating pill vote bar** (A/B better carry a `--win` dot; Both-bad muted).
  Keep the keyboard hints as mono `<kbd>`.
- **Leaderboard** (`leaderboard.html`): grid table `54px 1fr 96px 224px 72px`. Rank medals
  for top 3 (gold/silver/bronze), `model` kind chip, mono score (rank 1 = accent), CI whisker
  (track + `--accent 45%` range + accent dot).
- **Difficulty** (`difficulty.html`): heatmap grid `1fr 132px×3`. Cell bg =
  `color-mix(win|amber|bad, 24–58%, panel2)` scaled by value; legend + per-tier top-3 cards.
- **Benchmark** (`benchmark.html`): agreement grid (Spearman colored ±: win/bad), dual
  inspect viewer (recon vs GT, GT panel uses `--win` top-border), metric grid with mono
  chamfer (green) + PASS/FAIL pills.
- **Coverage** (`coverage.html`): paradigm stat cards (mono accent numeral), coverage grid
  with firm(green)/provisional(amber) confidence pills + in-arena ✓ / `excluded`.
- **Spotlight** (`spotlight_index.html`): taxon cards, stage-gradient header w/ `featured`
  badge, common name (accent2) + italic latin + description.

## Interaction & viewer states (polish)
- **Hover/active**: buttons brighten on hover (`filter:brightness(1.09)`) and press down on
  active (`brightness(0.94); translateY(1px)`); nav links + kingdom tabs + paradigm tabs get a
  faint accent-tint / text-color lift; **data-table rows** highlight on hover
  (`color-mix(accent 7%, panel2)`) for scannability; viewer control buttons (⟳ ⛶) shift to
  `panel2` with an accent border. All with `transition ~0.12s`.
- **Viewer loading state**: centered spinner (`--accent2` top border, `viewer-spin` keyframe)
  + mono caption "Loading model…", over the stage gradient.
- **Viewer error state**: amber (`--amber`) ⚠ glyph + "Model failed to load" centered — keep
  the existing copy, just restyle to the token palette (border-none, panel background).
- **Empty/no-preview** (Spotlight thumbs etc.): dashed `--border`, muted "click to load"
  caption — distinct from the error state so a missing render never reads as a failure.

## Information architecture — kingdom axis (v2)
The site is scoped by **biological kingdom** as its top-level axis, with finer filters nested
beneath it. This is the primary organizing principle — implement it before per-page filters.

- **Persistent scope bar** (`base.html`, directly under the main nav, `position:sticky;
  top:<navHeight>`): a segmented control `All · Plants · Fungi · Animals`. **All** is the
  default landing and acts as the cross-kingdom overview (aggregated stats + a combined
  leaderboard that merges paradigms across kingdoms). Selecting any single kingdom scopes the
  WHOLE app — every leaderboard, arena pair, task, spotlight, difficulty grid, benchmark, and
  coverage table refilters to that kingdom. Store as e.g. `?kingdom=` / session; persists
  across page nav. Each data-page `<h1>` carries a small scope pill (ALL KINGDOMS / PLANTS /
  FUNGI) so the active scope is always legible.
- **Right side of the bar**: live stats strip in mono — `N tasks · M votes · updated X`
  (field convention; reads like a real benchmark site). Values are kingdom-scoped.
- **Nested filters**: within a kingdom, the existing category / difficulty-tier / paradigm
  selectors stay as local filters. So kingdom = global switcher, category/paradigm = local.
- **Not-yet-ready kingdoms**: Plants + Fungi are live. **Animals** shows a `soon` badge in the
  bar and routes to a **coming-soon / roadmap** view (status card per kingdom: ✓ live,
  ◷ in-sourcing) instead of the normal screens. Keep the schema identical across kingdoms so
  adding Animals is data-only.
- **Taxonomy breadcrumb**: Spotlight cards show the full lineage `Kingdom › Order › Family ›
  Genus` (mono, faint) above the common + *italic latin* names. Same lineage field can feed
  Arena/Benchmark subject headers later.
- **Kingdom motif (emoji)**: each kingdom carries an emoji (🧬 All · 🌿 Plants · 🍄 Fungi ·
  🐾 Animals) shown in the scope bar; the active subject's emoji appears in the Arena reference
  thumb + as a faint watermark on the model stages, and per-subject emoji sit on Spotlight
  cards (🍅 🌽 🌹 🍄 …). Emoji are already part of this app's vocabulary (vote buttons), so
  this stays on-brand; swap for a custom icon set later if desired.

### Roadmap (not built yet)
- **Taxonomic drill-down below kingdom** — a nested `Order → Family → Genus/Species` filter so
  a leaderboard can be scoped to e.g. *Poaceae (grasses)* or *Fabales (legumes)*. Deferred
  until each kingdom has enough taxa to warrant it; the lineage data is already modeled on
  subjects, so it slots into the same nested-filter pattern as category/paradigm.
- **Per-kingdom real reference imagery + models** in the Arena (prototype uses emoji/placeholder
  stages; production drops real reference photos and GLB/scan models per task).

## Field conventions applied (LMArena × Papers With Code blend)
- **Leaderboard paradigm tabs**: segmented control above the board — `Overall · Image → 3D ·
  Procedural · Scan / capture`. Each swaps the ranked dataset; "Overall" merges + re-sorts.
- **Sortable column headers**: BT score shows the active sort (`↓`), other numeric columns
  show an inactive `↕`. Wire to real sort on implement.
- **Kind chips are typed & colored**: `model` (accent2) · `agent` (accent) · `scan` (win) ·
  `baseline` (muted) — so paradigm is legible at a glance in any table.
- **Provenance links per model**: small mono chips `paper · code · data` after the model name
  (Papers-With-Code convention); scans get `data`, agents get `code`, etc.
- **Footer resource rail**: `Paper ↗ · GitHub ↗ · Dataset ↗ · Submit a model` bordered mono
  chips + Terms/Privacy/Licenses — the academic/OSS-benchmark footer pattern.
- Overall tone stays battle-first and minimal (LMArena): the Arena page is still home and the
  models still dominate; the density lives on the data pages where researchers expect it.

- Focus ring: `2px solid var(--accent)` offset 2px on all interactive elements.
- Vote/tier semantics never rely on color alone (icons + labels present).
- `@media (hover:none)` keeps viewer toolbars visible; `prefers-reduced-motion` kills
  transitions. Mobile: pair → 1 col + the existing A/B toggle; vote bar sticky bottom.
