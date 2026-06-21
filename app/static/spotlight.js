// Click a thumbnail → load that GLB into a single live <model-viewer>.
document.querySelectorAll(".thumb").forEach((t) => {
  t.addEventListener("click", () => {
    const url = t.getAttribute("data-asset");
    const slot = document.getElementById("live-viewer-slot");
    slot.innerHTML = "";
    const mv = document.createElement("model-viewer");
    mv.setAttribute("src", url);
    mv.setAttribute("camera-controls", "");
    mv.setAttribute("auto-rotate", "");
    mv.style.width = "100%";
    mv.style.height = "480px";
    slot.appendChild(mv);
    slot.scrollIntoView({ behavior: "smooth" });
  });
});
