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
      '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" style="transform-origin:50% 90%; animation:b3d-breathe 1.5s ease-in-out infinite;">' +
      '<path d="M12 2L20.66 7V17L12 22L3.34 17V7Z" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linejoin="round" opacity="0.5"/>' +
      '<path d="M10.6 20C10.85 19.3 11 16.2 11.4 12.7L12.6 12.7C13 16.2 13.15 19.3 13.4 20Z" fill="oklch(0.5 0.062 61)"/>' +
      '<g transform="translate(2.640 4.7) scale(0.78 0.78)">' +
      '<path d="M8.5 13.8C6.2 13.6 5 12 5 10.4C4.2 9.4 4.6 7.6 6 7.1C6.2 5.4 8 4.4 9.6 5.1C10.6 3.9 13.4 3.9 14.4 5.1C16 4.4 17.8 5.4 18 7.1C19.4 7.6 19.8 9.4 19 10.4C19 12 17.8 13.6 15.5 13.8Z" fill="color-mix(in oklch, var(--accent) 82%, #063a1e)"/>' +
      '<path d="M9.2 12.9C7.5 12.7 6.5 11.6 6.6 10.3C6 9.5 6.4 8.3 7.4 8C7.6 6.8 8.9 6.1 10 6.7C10.8 5.9 13.2 5.9 14 6.7C15.1 6.1 16.4 6.8 16.6 8C17.6 8.3 18 9.5 17.4 10.3C17.5 11.6 16.5 12.7 14.8 12.9Z" fill="color-mix(in oklch, var(--accent) 92%, #eaffea)"/>' +
      '</g></svg><span>' + label + "</span>";
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
    slot._resetView = null;
    slot._onResize = null;
    const d = document.createElement("div");
    d.className = "viewer-error";
    d.innerHTML = "⚠️ <span>" + msg + "</span>";
    slot.appendChild(d);
  }

  function addControls(slot, onFlag) {
    const bar = document.createElement("div");
    bar.className = "viewer-controls";
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "viewer-ctl";
    reset.setAttribute("aria-label", "Reset view");
    reset.title = "Reset view";
    reset.textContent = "⟳";
    reset.addEventListener("click", () => {
      if (slot._resetView) slot._resetView();
    });
    const fs = document.createElement("button");
    fs.type = "button";
    fs.className = "viewer-ctl";
    fs.setAttribute("aria-label", "Fullscreen");
    fs.title = "Fullscreen";
    fs.textContent = "⛶";
    fs.addEventListener("click", () => toggleFullscreen(slot));
    bar.appendChild(reset);
    bar.appendChild(fs);
    if (onFlag) {
      const flag = document.createElement("button");
      flag.type = "button";
      flag.className = "viewer-ctl";
      flag.setAttribute("aria-label", "Flag: not a plant / failed");
      flag.title = "Flag: not a plant / failed";
      flag.textContent = "⚑";
      flag.addEventListener("click", () => onFlag(flag));
      bar.appendChild(flag);
    }
    slot.appendChild(bar);
  }

  function toggleFullscreen(slot) {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (slot.requestFullscreen) slot.requestFullscreen();
  }

  // Single module-level fullscreen listener (added once). On enter, the slot is
  // document.fullscreenElement; on exit it is null, so we keep the previously-
  // fullscreen slot to resize it back. Only molecular slots have _onResize (mesh
  // auto-resizes → null). try/catch guards a slot torn down while fullscreen.
  let fsSlot = null;
  document.addEventListener("fullscreenchange", () => {
    const active = document.fullscreenElement;
    const target = active || fsSlot;
    if (target && target._onResize) {
      try {
        target._onResize();
      } catch (e) {
        /* stale viewer — ignore */
      }
    }
    fsSlot = active;
  });

  function mountMesh(slot, asset, onFlag) {
    const myGen = slot._viewerGen;
    const stale = () => slot._viewerGen !== myGen;
    const loading = spinner(slot, "Loading model…");
    const mv = document.createElement("model-viewer");
    mv.setAttribute("camera-controls", "");
    mv.setAttribute("loading", "eager");
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
    slot._resetView = () => {
      mv.cameraOrbit = "0deg 75deg auto";
      mv.fieldOfView = "auto";
      mv.cameraTarget = "auto auto auto";
      mv.jumpCameraToGoal();
    };
    slot._onResize = null; // model-viewer auto-resizes; clear any stale molecular closure
    addControls(slot, onFlag);
  }

  async function mountMolecular(slot, asset, fmt, onFlag) {
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
      slot._molViewer = viewer;
      slot._resetView = () => {
        viewer.zoomTo();
        viewer.render();
      };
      slot._onResize = () => {
        viewer.resize();
        viewer.render();
      };
      addControls(slot, onFlag);
    } catch (e) {
      if (stale()) return;
      failed(slot, "Structure failed to load");
    }
  }

  // Mount the right viewer; returns the resolved format string.
  function mount(slot, asset, onFlag) {
    slot.innerHTML = ""; // tear down any previous viewer
    slot._viewerGen = (slot._viewerGen || 0) + 1; // invalidate any in-flight mount
    const fmt = (asset.format || "glb").toLowerCase();
    if (MOL.has(fmt)) mountMolecular(slot, asset, fmt, onFlag);
    else if (MESH.has(fmt)) mountMesh(slot, asset, onFlag);
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
      // Sync ONLY the orbital angles (theta/phi) — the "same viewing angle" intent.
      // Leave radius + target on each viewer's own "auto" so it frames its OWN
      // bounding box: the two models can differ wildly in scale/center (a whole
      // plant vs an isolated organ), and copying one's absolute radius+target onto
      // the other aims the camera off its model and throws it out of view.
      const o = src.getCameraOrbit();
      dst.cameraOrbit = o.theta + "rad " + o.phi + "rad auto";
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
