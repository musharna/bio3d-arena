// Shared 3D viewer registry — mount a renderer into a slot element by asset format.
// model-viewer for GLB/GLTF meshes; 3Dmol.js for PDB/mmCIF molecular structures.
// Used by both the arena (arena.js) and the moderation previews (moderation.js).
(function () {
  const MESH = new Set(["glb", "gltf"]);
  const MOL = new Set(["pdb", "cif", "mmcif", "ent"]);

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
    const viewer = window.$3Dmol.createViewer(slot, {
      backgroundColor: "0x131a24",
    });
    const text = await (await fetch(asset.url)).text();
    const modelType = fmt === "cif" || fmt === "mmcif" ? "cif" : "pdb";
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
  }

  // Mount the right viewer; returns the resolved format string.
  function mount(slot, asset) {
    slot.innerHTML = ""; // tear down any previous viewer
    const fmt = (asset.format || "glb").toLowerCase();
    if (MOL.has(fmt)) mountMolecular(slot, asset, fmt);
    else if (MESH.has(fmt)) mountMesh(slot, asset);
    else slot.textContent = "Unsupported format: " + fmt;
    return fmt;
  }

  window.Bio3DViewer = { mount, MESH_FORMATS: MESH, MOLECULAR_FORMATS: MOL };
})();
