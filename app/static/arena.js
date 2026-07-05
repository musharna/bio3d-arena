// Arena client: fetch a comparison, render both models, record a vote, advance.
// Category + criterion selectors scope what gets shown and which axis is judged.
let current = null;
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
  // Thread ?set=calibration (or any other set) from the page URL into every fetch.
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
    const p = data.progress || {};
    const label = data.set
      ? data.set.charAt(0).toUpperCase() + data.set.slice(1)
      : "Set";
    setStatus(`${label} complete — ${p.voted ?? 0}/${p.total ?? 0} voted.`);
    return;
  }
  current = data;
  el("task-cat").textContent = data.task.category;
  el("task-title").textContent = data.task.title;
  el("task-prompt").textContent = data.task.prompt;
  el("criterion-name").textContent = data.criterion.name;
  // Reference photo (what the organism should look like) — shown so voters can judge fidelity,
  // not just aesthetics. Hidden when a task has no reference on record.
  const refPanel = el("reference-panel");
  const refImg = el("reference-img");
  if (refPanel && refImg) {
    if (data.task.reference) {
      refImg.src = data.task.reference;
      refPanel.hidden = false;
    } else {
      refImg.removeAttribute("src");
      refPanel.hidden = true;
    }
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
  setAB("a"); // each new pair starts on Model A
  setStatus("");
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

document.querySelectorAll(".vote-btn").forEach((btn) => {
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
