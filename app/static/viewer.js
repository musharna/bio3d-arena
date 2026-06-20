// Shared 3D viewer registry — mount a renderer into a slot element by asset format.
// model-viewer for GLB/GLTF meshes; 3Dmol.js for PDB/mmCIF molecular structures.
// Adds loading spinner, drag-to-rotate hint, and an asset-failure fallback so the
// core product never silently shows an empty box.
(function () {
  const MESH = new Set(["glb", "gltf"]);
  const MOL = new Set(["pdb", "cif", "mmcif", "ent", "sdf", "mol"]);

  function spinner(slot, label) {
    const d = document.createElement("div");
    d.className = "viewer-loading";
    d.innerHTML =
      '<div class="viewer-spinner"></div><span>' + label + "</span>";
    slot.appendChild(d);
    return d;
  }

  function hint(slot, text) {
    const h = document.createElement("div");
    h.className = "viewer-hint";
    h.textContent = text;
    slot.appendChild(h);
  }

  function failed(slot, msg) {
    slot.innerHTML = "";
    const d = document.createElement("div");
    d.className = "viewer-error";
    d.innerHTML = "⚠️ <span>" + msg + "</span>";
    slot.appendChild(d);
  }

  function mountMesh(slot, asset) {
    const loading = spinner(slot, "Loading model…");
    const mv = document.createElement("model-viewer");
    mv.setAttribute("camera-controls", "");
    mv.setAttribute("touch-action", "pan-y");
    mv.setAttribute("shadow-intensity", "1");
    mv.setAttribute("exposure", "1.0");
    mv.setAttribute("src", asset.url);
    mv.style.width = "100%";
    mv.style.height = "100%";
    mv.addEventListener("load", () => {
      loading.remove();
      hint(slot, "drag to rotate · scroll to zoom");
    });
    mv.addEventListener("error", () => failed(slot, "Model failed to load"));
    slot.appendChild(mv);
  }

  async function mountMolecular(slot, asset, fmt) {
    const loading = spinner(slot, "Loading structure…");
    try {
      const res = await fetch(asset.url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      const text = await res.text();
      const viewer = window.$3Dmol.createViewer(slot, {
        backgroundColor: "0x131a24",
      });
      let modelType = "pdb";
      if (fmt === "cif" || fmt === "mmcif") modelType = "cif";
      else if (fmt === "sdf" || fmt === "mol") modelType = "sdf";
      viewer.addModel(text, modelType);
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
      loading.remove();
      hint(slot, "drag to rotate · scroll to zoom");
    } catch (e) {
      failed(slot, "Structure failed to load");
    }
  }

  // Mount the right viewer; returns the resolved format string.
  function mount(slot, asset) {
    slot.innerHTML = ""; // tear down any previous viewer
    const fmt = (asset.format || "glb").toLowerCase();
    if (MOL.has(fmt)) mountMolecular(slot, asset, fmt);
    else if (MESH.has(fmt)) mountMesh(slot, asset);
    else failed(slot, "Unsupported format: " + fmt);
    return fmt;
  }

  window.Bio3DViewer = { mount, MESH_FORMATS: MESH, MOLECULAR_FORMATS: MOL };
})();
