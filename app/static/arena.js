// Arena client: fetch a comparison, render both models, record a vote, advance.
// Category + criterion selectors scope what gets shown and which axis is judged.
let current = null; // active pairwise comparison (2-up); null while a K-wise ballot is shown
let busy = false;
// Post-vote reveal (Feature C): the `next` payload from a vote/kvote response is stashed here
// instead of rendered immediately whenever a `reveal` is shown, so "Next pair" can advance to it.
let pendingNext = null;

const el = (id) => document.getElementById(id);

// First-visit onboarding banner: shown once, state persisted in localStorage. Fail-quiet.
(function initOnboarding() {
  const banner = document.getElementById("onboard-banner");
  const dismiss = document.getElementById("onboard-dismiss");
  if (!banner || !dismiss) return;
  let seen = true;
  try {
    seen = !!localStorage.getItem("bio3d_onboarded");
  } catch (e) {
    seen = true; // localStorage unavailable → don't show, never break the arena
  }
  if (!seen) banner.hidden = false;
  dismiss.addEventListener("click", () => {
    banner.hidden = true;
    try {
      localStorage.setItem("bio3d_onboarded", "1");
    } catch (e) {
      /* ignore */
    }
  });
})();

// Mobile A/B toggle: mark JS active (gates the "hide inactive model" CSS) + wire the switch.
document.body.classList.add("js-ab");

function setAB(which) {
  document.querySelectorAll(".ab-btn").forEach((b) => {
    const on = b.dataset.ab === which;
    b.classList.toggle("is-active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  const cols = document.querySelectorAll(".pair .model-col");
  if (cols[0]) cols[0].classList.toggle("is-active", which === "a");
  if (cols[1]) cols[1].classList.toggle("is-active", which === "b");
}

document
  .querySelectorAll(".ab-btn")
  .forEach((b) => b.addEventListener("click", () => setAB(b.dataset.ab)));
const qs = () => {
  const cat = el("sel-category").value;
  const crit = el("sel-criterion").value;
  const p = new URLSearchParams();
  if (cat && cat !== "all") p.set("category", cat);
  if (crit) p.set("criterion", crit);
  // Baseline arena is plain 1v1 pairwise (matches the design). K-wise 4-up is opt-in only:
  // thread ?set=... from the page URL when present (e.g. ?set=kwise or ?set=calibration);
  // with no ?set, /api/next serves a pairwise comparison.
  const urlSet = new URLSearchParams(location.search).get("set");
  if (urlSet) p.set("set", urlSet);
  const s = p.toString();
  return s ? "?" + s : "";
};

async function loadMeta() {
  const meta = await (await fetch("/api/meta")).json();
  const catSel = el("sel-category");
  meta.categories.forEach((c) => {
    const o = document.createElement("option");
    o.value = c.slug;
    // Roadmap placeholders (no tasks yet) are shown but not selectable.
    o.textContent = c.coming_soon ? `${c.name} — coming soon` : c.name;
    o.disabled = !!c.coming_soon;
    catSel.appendChild(o);
  });
  const critSel = el("sel-criterion");
  meta.criteria.forEach((c) => {
    const o = document.createElement("option");
    o.value = c.slug;
    o.textContent = c.name;
    critSel.appendChild(o);
  });
}

async function loadNext() {
  busy = true;
  setStatus("Loading next comparison…");
  try {
    const res = await fetch("/api/next" + qs());
    if (res.status === 404) {
      setStatus(
        "No comparisons available for this filter. Try another category.",
      );
      current = null;
      return;
    }
    render(await res.json());
  } catch (e) {
    setStatus("Error loading comparison: " + e);
  } finally {
    busy = false;
  }
}

// True when the page is in a scoped session mode (e.g. ?set=calibration).
const inSessionMode = () => new URLSearchParams(location.search).has("set");

function render(data) {
  // Terminal payload from a scoped mode (e.g. calibration): no card to render.
  if (data && data.done) {
    current = null;
    setKwiseVisible(false);
    const p = data.progress || {};
    const label = data.set
      ? data.set.charAt(0).toUpperCase() + data.set.slice(1)
      : "Set";
    setStatus(`${label} complete — ${p.voted ?? 0}/${p.total ?? 0} voted.`);
    return;
  }
  // /api/next?set=kwise returns {kind:"kwise", ...} for a 4-up ballot, or a plain pairwise
  // payload (no `kind` field at all) when _build_kwise_comparison fell back — that fallback
  // shape is handled by the existing 2-up path below with no special-casing needed.
  if (data && data.kind === "kwise") {
    renderKwise(data);
  } else {
    renderPair(data);
  }
}

function renderPair(data) {
  setKwiseVisible(false);
  current = data;
  el("task-cat").textContent = data.task.category;
  el("task-title").textContent = data.task.title;
  el("task-prompt").textContent = data.task.prompt;
  el("criterion-name").textContent = data.criterion.name;
  // Reference photo (what the organism should look like) — shown so voters can judge fidelity,
  // not just aesthetics. Hidden when a task has no reference on record.
  const refPanel = el("reference-panel");
  const refGallery = el("reference-gallery");
  if (refPanel && refGallery) {
    const refs = (data.task.references || []).filter((r) => r && r.url);
    refGallery.textContent = "";
    for (const r of refs) {
      const img = document.createElement("img");
      img.className = "reference-img";
      img.src = r.url;
      img.loading = "lazy";
      img.alt = "Reference photo of this organism";
      if (r.credit) img.title = r.credit; // CC attribution / "reconstruction input photo"
      // Click to zoom: the thumbnail is cropped (object-fit:cover); the lightbox shows the
      // full uncropped photo so a voter can actually inspect the organism.
      img.addEventListener("click", () =>
        openReferenceLightbox(r.url, r.credit),
      );
      refGallery.appendChild(img);
    }
    refPanel.hidden = refs.length === 0;
  }
  // Shared viewer registry (viewer.js) picks model-viewer vs 3Dmol by format.
  el("fmt-a").textContent = window.Bio3DViewer.mount(
    el("slot-a"),
    data.a,
    (btn) => flagOutput(data.a.output_id, btn),
  ).toUpperCase();
  el("fmt-b").textContent = window.Bio3DViewer.mount(
    el("slot-b"),
    data.b,
    (btn) => flagOutput(data.b.output_id, btn),
  ).toUpperCase();
  window.Bio3DViewer.syncPair(el("slot-a"), el("slot-b"));
  setMachineGeneratedBadge("a", data.a);
  setMachineGeneratedBadge("b", data.b);
  setAB("a"); // each new pair starts on Model A
  setStatus("");
}

// Show the 4-up grid + all-bad button, hide the 2-up pair/vote-bar/ab-toggle (or vice versa).
// .pair/.vote-bar/.ab-toggle each carry their own unconditional `display` rule in style.css
// (.ab-toggle even has a mobile media-query override), so a plain `hidden` attribute would
// lose the cascade to those author rules — inline style always wins, so use it here instead.
function setKwiseVisible(active) {
  const pair = document.querySelector(".pair");
  const voteBar = document.querySelector(".vote-bar");
  const abToggle = document.querySelector(".ab-toggle");
  if (pair) pair.style.display = active ? "none" : "";
  if (voteBar) voteBar.style.display = active ? "none" : "";
  if (abToggle) abToggle.style.display = active ? "none" : "";
  el("kwise-grid").hidden = !active;
  el("kwise-allbad").hidden = !active;
}

function renderKwise(data) {
  current = null; // pairwise vote()/keyboard shortcuts must no-op while a K-wise ballot is shown
  setKwiseVisible(true);
  // K-wise ballots carry no reference gallery — hide the strip thumbnail so it doesn't render as
  // an empty circle (only 2-up pairs populate the subject thumbnail from data.task.references).
  const kRefPanel = el("reference-panel");
  if (kRefPanel) kRefPanel.hidden = true;
  el("task-cat").textContent = "K-wise"; // kwise task payload has no `category` field
  el("task-title").textContent = data.task.title;
  el("task-prompt").textContent = data.task.prompt;
  el("criterion-name").textContent = data.criterion.name;
  const grid = el("kwise-grid");
  grid.innerHTML = "";
  data.outputs.forEach((o, i) => {
    const cell = document.createElement("div");
    cell.className = "model-col kwise-cell";
    const label = document.createElement("div");
    label.className = "model-label";
    label.textContent = "Model " + "ABCD"[i] + " ";
    const fmtChip = document.createElement("span");
    fmtChip.className = "fmt-chip";
    label.appendChild(fmtChip);
    // AUP labeling is display-posture-wide: a commercial-model output must carry the
    // machine-generated badge in the 4-up grid exactly as it does in the 2-up pair view.
    if (o.machine_generated) {
      const badge = document.createElement("span");
      badge.className = "ai-badge";
      badge.textContent = "🤖 AI-generated";
      badge.title = o.attribution
        ? `Machine-generated — ${o.attribution}`
        : "Machine-generated";
      label.appendChild(badge);
    }
    const slot = document.createElement("div");
    slot.className = "viewer-slot";
    const pickBtn = document.createElement("button");
    pickBtn.type = "button";
    pickBtn.className = "vote-btn win kwise-pick-btn";
    pickBtn.textContent = "Pick this one";
    pickBtn.dataset.outputId = o.output_id; // read back by showKwiseReveal to label this card
    pickBtn.addEventListener("click", () =>
      submitKvote(data.ballot_id, o.output_id),
    );
    cell.appendChild(label);
    cell.appendChild(slot);
    cell.appendChild(pickBtn);
    grid.appendChild(cell);
    // Shared viewer registry (viewer.js) picks model-viewer vs 3Dmol by format — same
    // {url, format, output_id} shape _serialize uses for a/b, so the flag callback reuses
    // flagOutput unchanged.
    fmtChip.textContent = window.Bio3DViewer.mount(slot, o, (btn) =>
      flagOutput(o.output_id, btn),
    ).toUpperCase();
  });
  el("kwise-allbad").onclick = () => submitKvote(data.ballot_id, null);
  setStatus("");
}

async function submitKvote(ballotId, bestOutputId) {
  if (busy) return;
  busy = true;
  setStatus("Recording pick…");
  try {
    // qs() threads the current category/criterion filter (as /api/kvote reads them to scope
    // the follow-up ballot) — dropping it here would silently reset the user's filter every pick.
    const res = await fetch("/api/kvote" + qs(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ballot_id: ballotId,
        best_output_id: bestOutputId,
      }),
    });
    if (!res.ok) {
      // Honor res.ok before treating this as success (arena.js vote() lesson: a past bug here
      // showed failed votes as recorded because res.ok was never checked).
      let detail = "pick not recorded";
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {
        /* non-JSON error body */
      }
      setStatus("Could not record pick: " + detail);
      return;
    }
    const data = await res.json();
    if (data.reveal) {
      // Hold the follow-up ballot/pair until "Next pair" is clicked — showKwiseReveal labels
      // each card in place with its real name and marks the pick, same grid stays on screen.
      pendingNext = data.next;
      showKwiseReveal(data.reveal);
      flash("Pick recorded ✓");
    } else if (data.next) {
      render(data.next);
      flash("Pick recorded ✓");
    } else {
      setStatus("Pick recorded. No more comparisons for this filter.");
      setKwiseVisible(false);
    }
  } catch (e) {
    setStatus("Error recording pick: " + e);
  } finally {
    busy = false;
  }
}

// AUP labeling: badge + attribution tooltip on any output produced by a commercial
// generation model (never on our own assets or redistributable-license scans).
function setMachineGeneratedBadge(side, output) {
  const badge = el(`ai-badge-${side}`);
  if (!badge) return;
  badge.hidden = !output.machine_generated;
  badge.title = output.attribution
    ? `Machine-generated — ${output.attribution}`
    : "Machine-generated";
}

async function vote(winner) {
  if (busy || !current) return;
  busy = true;
  setStatus("Recording vote…");
  try {
    const res = await fetch("/api/vote" + qs(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ comparison_id: current.comparison_id, winner }),
    });
    if (!res.ok) {
      // Failed vote (rate-limit 429, already-voted/dup 409, captcha 403, unknown 404):
      // surface the reason and do NOT claim success or advance.
      let detail = "vote not recorded";
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {
        /* non-JSON error body */
      }
      setStatus("Could not record vote: " + detail);
      return;
    }
    const data = await res.json();
    if (inSessionMode()) {
      // In a scoped mode the embedded `data.next` shortcut is built by the
      // regular (unscoped) builder, so ignore it and re-fetch through the
      // mode-aware path (qs threads ?set). loadNext handles the `done` payload.
      // No reveal in session mode — it re-fetches straight through.
      flash("Vote recorded ✓");
      await loadNext();
    } else if (data.reveal) {
      // Non-gold vote: hold `next` until "Next pair" is clicked, and clear `current` so a
      // stray keyboard shortcut (arrow/t/x — these don't check button `disabled`) can't
      // double-vote the pair that's still on screen during the reveal.
      current = null;
      pendingNext = data.next;
      showReveal(data.reveal);
      flash("Vote recorded ✓");
    } else if (data.next) {
      render(data.next);
      flash("Vote recorded ✓");
    } else {
      setStatus("Vote recorded. No more comparisons for this filter.");
      current = null;
    }
  } catch (e) {
    setStatus("Error recording vote: " + e);
  } finally {
    busy = false;
  }
}

async function flagOutput(outputId, btn) {
  if (!outputId || btn.disabled) return;
  if (!confirm("Flag this model as not the organism / failed?")) return;
  btn.disabled = true;
  try {
    const res = await fetch("/api/flag", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_id: outputId, reason: "not_the_organism" }),
    });
    if (!res.ok) {
      btn.disabled = false;
      let detail = "flag not recorded";
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {
        /* non-JSON error body */
      }
      setStatus("Could not flag: " + detail);
      return;
    }
    btn.textContent = "✓";
    flash("Flag recorded ✓");
  } catch (e) {
    btn.disabled = false;
    setStatus("Error flagging: " + e);
  }
}

function setStatus(msg) {
  el("status-line").textContent = msg;
}

function flash(msg) {
  const s = el("status-line");
  s.textContent = msg;
  s.classList.add("flash");
  setTimeout(() => s.classList.remove("flash"), 700);
}

// ------------------------------------------------------------------ post-vote reveal (Feature C)
// Purely additive fanfare on top of the existing vote/kvote flows: reveals the real model
// names + rank + gold winner border, then gates advancing on an explicit "Next pair" click.
// Never touches vote recording — `reveal` only exists in the response after the vote/pick is
// already committed server-side.

function disableVoteBar(disabled) {
  document
    .querySelectorAll(".vote-bar .vote-btn")
    .forEach((b) => (b.disabled = disabled));
}

function rankLabelFor(side, winner) {
  if (winner === "tie") return "tie";
  if (winner === "bad") return "weak"; // "both bad" vote — no real winner to crown
  return winner === side ? "1st" : "2nd";
}

// 2-up pairwise reveal: name pills + rank chips on the two stages, gold border on the winner.
function showReveal(reveal) {
  const cols = document.querySelectorAll(".pair .model-col");
  const sides = [
    ["a", cols[0]],
    ["b", cols[1]],
  ];
  for (const [side, col] of sides) {
    const nameEl = el(`reveal-name-${side}`);
    const chipEl = el(`rank-chip-${side}`);
    const info = reveal[side];
    if (nameEl && info) {
      nameEl.textContent = info.name;
      nameEl.hidden = false;
    }
    if (chipEl) {
      const label = rankLabelFor(side, reveal.winner);
      chipEl.textContent = label;
      chipEl.classList.toggle("is-first", label === "1st");
      chipEl.hidden = false;
    }
    if (col && reveal.winner === side) col.classList.add("is-winner");
  }
  disableVoteBar(true);
  const btn = el("next-pair-btn");
  if (btn) btn.hidden = false;
  maybeFireConfetti();
}

// 4-up K-wise reveal: label each card in place (inline, next to its "Model X" label) + mark
// the picked card with the same gold winner border as the 2-up view.
function showKwiseReveal(reveal) {
  const grid = el("kwise-grid");
  if (!grid) return;
  const cells = grid.querySelectorAll(".kwise-cell");
  const byId = new Map(reveal.outputs.map((o) => [o.output_id, o]));
  cells.forEach((cell) => {
    const pickBtn = cell.querySelector(".kwise-pick-btn");
    const outputId = pickBtn && pickBtn.dataset.outputId;
    const info = outputId ? byId.get(Number(outputId)) : null;
    if (pickBtn) pickBtn.disabled = true;
    if (!info) return;
    let pill = cell.querySelector(".reveal-name");
    if (!pill) {
      pill = document.createElement("span");
      pill.className = "reveal-name";
      const label = cell.querySelector(".model-label");
      if (label) label.appendChild(pill);
      else cell.appendChild(pill);
    }
    pill.textContent = info.name;
    pill.hidden = false;
    if (
      reveal.best_output_id != null &&
      info.output_id === reveal.best_output_id
    ) {
      cell.classList.add("is-winner");
    }
  });
  const allBad = el("kwise-allbad");
  if (allBad) allBad.disabled = true;
  const btn = el("next-pair-btn");
  if (btn) btn.hidden = false;
  maybeFireConfetti();
}

// Clears every reveal artifact (both 2-up and 4-up) and re-enables voting — called right
// before advancing to the pending next pair/ballot.
function clearReveal() {
  ["a", "b"].forEach((side) => {
    const nameEl = el(`reveal-name-${side}`);
    const chipEl = el(`rank-chip-${side}`);
    if (nameEl) {
      nameEl.hidden = true;
      nameEl.textContent = "";
    }
    if (chipEl) {
      chipEl.hidden = true;
      chipEl.textContent = "";
      chipEl.classList.remove("is-first");
    }
  });
  document
    .querySelectorAll(".model-col.is-winner")
    .forEach((c) => c.classList.remove("is-winner"));
  disableVoteBar(false);
  document
    .querySelectorAll(".kwise-pick-btn")
    .forEach((b) => (b.disabled = false));
  const allBad = el("kwise-allbad");
  if (allBad) allBad.disabled = false;
  const btn = el("next-pair-btn");
  if (btn) btn.hidden = true;
  const layer = el("confetti-layer");
  if (layer) layer.textContent = "";
}

// First-vote-only confetti, gated on localStorage + prefers-reduced-motion. Fail-quiet like
// the onboarding banner above — a blocked localStorage must never break the reveal itself.
function maybeFireConfetti() {
  let shown = true;
  try {
    shown = !!localStorage.getItem("bio3d_confetti_shown");
  } catch (e) {
    shown = true; // localStorage unavailable → skip, never break the reveal
  }
  if (shown) return;
  try {
    localStorage.setItem("bio3d_confetti_shown", "1");
  } catch (e) {
    /* ignore */
  }
  if (
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  ) {
    return; // respect reduced-motion: skip the burst entirely, not just speed it up
  }
  const layer = el("confetti-layer");
  if (!layer) return;
  const colors = ["var(--accent)", "var(--accent2)", "var(--win)"];
  for (let i = 0; i < 16; i++) {
    const p = document.createElement("span");
    p.className = "confetti-piece";
    const angle = Math.random() * Math.PI * 2;
    const dist = 70 + Math.random() * 90;
    p.style.setProperty("--cx", Math.cos(angle) * dist + "px");
    p.style.setProperty("--cy", Math.sin(angle) * dist + "px");
    p.style.setProperty("--cr", Math.floor(Math.random() * 360) + "deg");
    p.style.left = 45 + Math.random() * 10 + "%";
    p.style.top = "35%";
    p.style.background = colors[i % colors.length];
    p.style.animationDelay = Math.random() * 0.15 + "s";
    p.addEventListener("animationend", () => p.remove());
    layer.appendChild(p);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = el("next-pair-btn");
  if (!btn) return;
  btn.addEventListener("click", () => {
    clearReveal();
    const n = pendingNext;
    pendingNext = null;
    if (n) render(n);
    else loadNext();
  });
});

// Reference-image lightbox: open on thumbnail click, close on overlay click or Escape.
function openReferenceLightbox(url, credit) {
  const box = el("reference-lightbox");
  const img = el("reference-lightbox-img");
  const cred = el("reference-lightbox-credit");
  if (!box || !img) return;
  img.src = url;
  if (cred) {
    cred.textContent = credit || "";
    cred.hidden = !credit;
  }
  box.hidden = false;
}

function closeReferenceLightbox() {
  const box = el("reference-lightbox");
  const img = el("reference-lightbox-img");
  if (!box) return;
  box.hidden = true;
  if (img) img.removeAttribute("src"); // stop holding the full-size image in memory
}

(function initReferenceLightbox() {
  const box = el("reference-lightbox");
  if (!box) return;
  // Click anywhere on the overlay (including the image) closes it.
  box.addEventListener("click", closeReferenceLightbox);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !box.hidden) closeReferenceLightbox();
  });
})();

// Scoped to .vote-bar (not the bare ".vote-btn" class) so the kwise-pick/all-bad buttons —
// which reuse ".vote-btn" only for visual styling and get their own explicit listeners in
// renderKwise() — don't also pick up this vote() binding (btn.dataset.winner would be
// undefined for them, and vote() would be a silent no-op while current===null, but binding
// it at all is an unnecessary implicit coupling).
document.querySelectorAll(".vote-bar .vote-btn").forEach((btn) => {
  btn.addEventListener("click", () => vote(btn.dataset.winner));
});

// Re-fetch when the filters change.
el("sel-category").addEventListener("change", loadNext);
el("sel-criterion").addEventListener("change", loadNext);

// Keyboard shortcuts: arrow keys for A/B, t for tie, x for bad.
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "SELECT") return;
  if (e.key === "ArrowLeft") vote("a");
  else if (e.key === "ArrowRight") vote("b");
  else if (e.key === "t") vote("tie");
  else if (e.key === "x") vote("bad");
});

// Preselect category/criterion from the URL (?category=plants&criterion=overall) so a
// "Vote on these →" link from /benchmark scopes the arena to that benchmark's pairs.
function preselectFromUrl() {
  const p = new URLSearchParams(location.search);
  for (const [key, id] of [
    ["category", "sel-category"],
    ["criterion", "sel-criterion"],
  ]) {
    const val = p.get(key);
    const sel = el(id);
    if (val && [...sel.options].some((o) => o.value === val)) sel.value = val;
  }
}

(async () => {
  await loadMeta();
  preselectFromUrl();
  await loadNext();
})();
