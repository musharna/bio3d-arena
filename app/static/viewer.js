// Shared 3D viewer registry — mount a renderer into a slot element by asset format.
// model-viewer for GLB/GLTF meshes; 3Dmol.js for PDB/mmCIF molecular structures.
// Adds loading spinner, drag-to-rotate hint, and an asset-failure fallback so the
// core product never silently shows an empty box.
//
// Each mount stamps the slot with an incrementing generation id. Async paths (the
// molecular fetch; the mesh load/error events) re-check that id before touching the
// DOM, so a fast re-mount (e.g. rapid voting) can't have an in-flight load append a
// stale viewer into the slot it already tore down.
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
    const myGen = slot._viewerGen;
    const stale = () => slot._viewerGen !== myGen;
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
      if (stale()) return;
      loading.remove();
      hint(slot, "drag to rotate · scroll to zoom");
    });
    mv.addEventListener("error", () => {
      if (stale()) return;
      failed(slot, "Model failed to load");
    });
    slot.appendChild(mv);
  }

  async function mountMolecular(slot, asset, fmt) {
    const myGen = slot._viewerGen;
    const stale = () => slot._viewerGen !== myGen;
    const loading = spinner(slot, "Loading structure…");
    try {
      const res = await fetch(asset.url);
      if (stale()) return;
      if (!res.ok) throw new Error("HTTP " + res.status);
      const text = await res.text();
      if (stale()) return;
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
      if (stale()) return;
      failed(slot, "Structure failed to load");
    }
  }

  // Mount the right viewer; returns the resolved format string.
  function mount(slot, asset) {
    slot.innerHTML = ""; // tear down any previous viewer
    slot._viewerGen = (slot._viewerGen || 0) + 1; // invalidate any in-flight mount
    const fmt = (asset.format || "glb").toLowerCase();
    if (MOL.has(fmt)) mountMolecular(slot, asset, fmt);
    else if (MESH.has(fmt)) mountMesh(slot, asset);
    else failed(slot, "Unsupported format: " + fmt);
    return fmt;
  }

  // Lock two mesh viewers' cameras together (side-by-side comparison at the same angle).
  // No-op unless BOTH slots hold a <model-viewer> (molecular/mixed/failed pairs rotate freely).
  // Only user-initiated camera-change events propagate — programmatic writes fire source
  // "none" and are ignored, so applying A→B never bounces back (no mutex needed).
  function syncPair(slotA, slotB) {
    const a = slotA && slotA.querySelector("model-viewer");
    const b = slotB && slotB.querySelector("model-viewer");
    if (!a || !b) return;
    function copyCam(src, dst) {
      dst.cameraOrbit = src.getCameraOrbit().toString();
      dst.cameraTarget = src.getCameraTarget().toString();
      dst.fieldOfView = src.getFieldOfView() + "deg";
      dst.jumpCameraToGoal();
    }
    a.addEventListener("camera-change", (e) => {
      if (e.detail && e.detail.source === "user-interaction") copyCam(a, b);
    });
    b.addEventListener("camera-change", (e) => {
      if (e.detail && e.detail.source === "user-interaction") copyCam(b, a);
    });
  }

  window.Bio3DViewer = {
    mount,
    syncPair,
    MESH_FORMATS: MESH,
    MOLECULAR_FORMATS: MOL,
  };
})();
