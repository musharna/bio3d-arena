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
  const s = p.toString();
  return s ? "?" + s : "";
};

async function loadMeta() {
  const meta = await (await fetch("/api/meta")).json();
  const catSel = el("sel-category");
  meta.categories.forEach((c) => {
    const o = document.createElement("option");
    o.value = c.slug;
    o.textContent = c.name;
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

// ---- Viewer registry: pick a renderer by asset format -----------------------
const MESH_FORMATS = new Set(["glb", "gltf"]);
const MOLECULAR_FORMATS = new Set(["pdb", "cif", "mmcif", "ent"]);

function mountMesh(slot, asset) {
  const mv = document.createElement("model-viewer");
  mv.setAttribute("camera-controls", "");
  mv.setAttribute("touch-action", "pan-y");
  mv.setAttribute("shadow-intensity", "1");
  mv.setAttribute("exposure", "1.0");
  mv.setAttribute("src", asset.url);
  mv.style.width = "100%";
  mv.style.height = "100%";
  slot.appendChild(mv);
}

async function mountMolecular(slot, asset, fmt) {
  // 3Dmol renders into the slot div; fetch the structure text and style it.
  const viewer = window.$3Dmol.createViewer(slot, {
    backgroundColor: "0x131a24",
  });
  const text = await (await fetch(asset.url)).text();
  const modelType = fmt === "cif" || fmt === "mmcif" ? "cif" : "pdb";
  viewer.addModel(text, modelType);
  // Cartoon shows for proteins (with backbone); stick+sphere covers small molecules.
  viewer.setStyle(
    {},
    {
      stick: { radius: 0.15 },
      sphere: { scale: 0.28 },
      cartoon: { color: "spectrum" },
    },
  );
  viewer.zoomTo();
  viewer.render();
}

function mountViewer(slot, asset) {
  slot.innerHTML = ""; // tear down the previous viewer
  const fmt = (asset.format || "glb").toLowerCase();
  if (MOLECULAR_FORMATS.has(fmt)) {
    mountMolecular(slot, asset, fmt);
  } else if (MESH_FORMATS.has(fmt)) {
    mountMesh(slot, asset);
  } else {
    slot.textContent = "Unsupported format: " + fmt;
  }
  return fmt;
}

function render(data) {
  current = data;
  el("task-cat").textContent = data.task.category;
  el("task-title").textContent = data.task.title;
  el("task-prompt").textContent = data.task.prompt;
  el("criterion-name").textContent = data.criterion.name;
  el("fmt-a").textContent = mountViewer(el("slot-a"), data.a).toUpperCase();
  el("fmt-b").textContent = mountViewer(el("slot-b"), data.b).toUpperCase();
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

(async () => {
  await loadMeta();
  await loadNext();
})();
