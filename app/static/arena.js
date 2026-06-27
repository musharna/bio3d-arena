// Arena client: fetch a comparison, render both models, record a vote, advance.
// Category + criterion selectors scope what gets shown and which axis is judged.
let current = null;
let busy = false;

const el = (id) => document.getElementById(id);
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

function render(data) {
  current = data;
  el("task-cat").textContent = data.task.category;
  el("task-title").textContent = data.task.title;
  el("task-prompt").textContent = data.task.prompt;
  el("criterion-name").textContent = data.criterion.name;
  // Shared viewer registry (viewer.js) picks model-viewer vs 3Dmol by format.
  el("fmt-a").textContent = window.Bio3DViewer.mount(
    el("slot-a"),
    data.a,
  ).toUpperCase();
  el("fmt-b").textContent = window.Bio3DViewer.mount(
    el("slot-b"),
    data.b,
  ).toUpperCase();
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
    const data = await res.json();
    if (data.next) {
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
