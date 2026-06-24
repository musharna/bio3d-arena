// Click a thumbnail → load that GLB into the sticky side-panel <model-viewer>.
const slot = document.getElementById("live-viewer-slot");

document.querySelectorAll(".thumb").forEach((t) => {
  t.addEventListener("click", () => {
    const url = t.getAttribute("data-asset");
    const card = t.closest(".spotlight-card");

    document
      .querySelectorAll(".spotlight-card.active")
      .forEach((c) => c.classList.remove("active"));
    if (card) card.classList.add("active");

    slot.classList.remove("viewer-empty");
    slot.innerHTML = "";
    const mv = document.createElement("model-viewer");
    mv.setAttribute("src", url);
    mv.setAttribute("camera-controls", "");
    mv.setAttribute("auto-rotate", "");
    mv.style.width = "100%";
    mv.style.height = "100%";
    slot.appendChild(mv);

    // On narrow screens the panel stacks below the grid — bring it into view.
    if (window.matchMedia("(max-width: 900px)").matches) {
      slot.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });
});
