// Arena client: fetch a comparison, render both models, record a vote, advance.
// Category + criterion selectors scope what gets shown and which axis is judged.
let current = null; // active pairwise comparison (2-up); null while a K-wise ballot is shown
let busy = false;

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
  // Thread ?set=... from the page URL when present (e.g. ?set=calibration scopes a session);
  // otherwise default every fetch to K-wise so 4-up ballots are served where available.
  // _build_kwise_comparison falls back to a transparent pairwise payload when no task has an
  // admitted same-paradigm quad, and render() already branches on the resulting shape, so
  // this default is always safe.
  const urlSet = new URLSearchParams(location.search).get("set") || "kwise";
  p.set("set", urlSet);
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
    label.textContent = "Option " + (i + 1) + " ";
    const fmtChip = document.createElement("span");
    fmtChip.className = "fmt-chip";
    label.appendChild(fmtChip);
    const slot = document.createElement("div");
    slot.className = "viewer-slot";
    const pickBtn = document.createElement("button");
    pickBtn.type = "button";
    pickBtn.className = "vote-btn win kwise-pick-btn";
    pickBtn.textContent = "Pick this one";
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
    if (data.next) {
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
      flash("Vote recorded ✓");
      await loadNext();
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
  if (!confirm("Flag this model as not a plant / failed?")) return;
  btn.disabled = true;
  try {
    const res = await fetch("/api/flag", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_id: outputId, reason: "not_a_plant" }),
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
