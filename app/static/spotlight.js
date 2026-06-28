// Spotlight: click a thumbnail → load that GLB into the sticky side-panel <model-viewer>,
// plus a client-side filter toolbar (class / scored / search) and a per-group "show all" cap
// so a species with dozens of models stays browsable instead of being one long monolith.

const slot = document.getElementById("live-viewer-slot");

function loadCard(card) {
  const thumb = card.querySelector(".thumb");
  if (!thumb || !slot) return;
  const url = thumb.getAttribute("data-asset");
  document
    .querySelectorAll(".spotlight-card.active")
    .forEach((c) => c.classList.remove("active"));
  card.classList.add("active");
  slot.classList.remove("viewer-empty");
  slot.innerHTML = "";
  const mv = document.createElement("model-viewer");
  mv.setAttribute("src", url);
  mv.setAttribute("camera-controls", "");
  mv.setAttribute("auto-rotate", "");
  mv.style.width = "100%";
  mv.style.height = "100%";
  slot.appendChild(mv);
}

document.querySelectorAll(".thumb").forEach((t) => {
  t.addEventListener("click", () => {
    const card = t.closest(".spotlight-card");
    if (card) loadCard(card);
    if (window.matchMedia("(max-width: 900px)").matches) {
      slot.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });
});

// ---- Filter toolbar + per-group cap ----
const toolbar = document.getElementById("spotlight-toolbar");
const CAP = toolbar ? parseInt(toolbar.getAttribute("data-cap") || "8", 10) : 8;
const chips = Array.from(document.querySelectorAll(".chip"));
const scoredOnly = document.getElementById("scored-only");
const search = document.getElementById("spotlight-search");
const resultCount = document.getElementById("result-count");
const allCards = Array.from(document.querySelectorAll(".spotlight-card"));
let activeCls = "all";

function filtersActive() {
  return (
    activeCls !== "all" ||
    (scoredOnly && scoredOnly.checked) ||
    (search && search.value.trim() !== "")
  );
}

function cardMatches(card) {
  if (activeCls !== "all" && card.getAttribute("data-cls") !== activeCls)
    return false;
  if (
    scoredOnly &&
    scoredOnly.checked &&
    card.getAttribute("data-scored") !== "1"
  )
    return false;
  const q = search ? search.value.trim().toLowerCase() : "";
  if (q && !(card.getAttribute("data-label") || "").includes(q)) return false;
  return true;
}

function apply() {
  const active = filtersActive();
  let shown = 0;
  document.querySelectorAll(".group").forEach((group) => {
    const cards = Array.from(group.querySelectorAll(".spotlight-card"));
    const matches = cards.filter(cardMatches);
    // Cap only applies with no active filter and until the user expands the group.
    const uncapped = active || group.classList.contains("uncapped");
    let i = 0;
    cards.forEach((card) => {
      let show = matches.includes(card);
      if (show && !uncapped) {
        if (i >= CAP) show = false;
        i += 1;
      }
      card.classList.toggle("hidden", !show);
      if (show) shown += 1;
    });

    let btn = group.querySelector(".show-all-btn");
    const needBtn = !uncapped && matches.length > CAP;
    if (needBtn && !btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "show-all-btn";
      group.querySelector(".spotlight-grid").after(btn);
      btn.addEventListener("click", () => {
        group.classList.add("uncapped");
        apply();
      });
    }
    if (btn) {
      btn.style.display = needBtn ? "" : "none";
      if (needBtn) btn.textContent = `Show all (${matches.length})`;
    }

    group.style.display = matches.length ? "" : "none";
    if (active && matches.length) group.open = true;
  });
  if (resultCount) {
    resultCount.textContent = active
      ? `showing ${shown} of ${allCards.length}`
      : `${allCards.length} models`;
  }
}

chips.forEach((chip) =>
  chip.addEventListener("click", () => {
    chips.forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    activeCls = chip.getAttribute("data-filter-cls");
    apply();
  }),
);
if (scoredOnly) scoredOnly.addEventListener("change", apply);
if (search) search.addEventListener("input", apply);

apply();

// ---- Auto-load the best (lowest-chamfer) model into the aside on arrival ----
(function autoLoad() {
  let best = null;
  let bestChamfer = Infinity;
  allCards.forEach((card) => {
    const c = parseFloat(card.getAttribute("data-chamfer"));
    if (!Number.isNaN(c) && c < bestChamfer) {
      bestChamfer = c;
      best = card;
    }
  });
  if (!best) best = allCards[0];
  if (best) loadCard(best);
})();
