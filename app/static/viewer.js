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
      flag.setAttribute("aria-label", "Hide this output from the arena");
      flag.title = "Hide this output from the arena";
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

  // 3Dmol is only needed for the rare PDB/mmCIF molecular case, so it is not shipped
  // in the page <head>. Inject it once, on the first molecular mount, and cache the
  // load promise so concurrent mounts share a single fetch.
  let _threeDmolPromise = null;
  function ensure3Dmol() {
    if (window.$3Dmol) return Promise.resolve();
    if (_threeDmolPromise) return _threeDmolPromise;
    _threeDmolPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/3dmol@2.4.2/build/3Dmol-min.js";
      s.onload = () => resolve();
      s.onerror = () => {
        _threeDmolPromise = null; // allow a later mount to retry
        reject(new Error("3Dmol failed to load"));
      };
      document.head.appendChild(s);
    });
    return _threeDmolPromise;
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
    mv.setAttribute("touch-action", "pan-y");
    mv.setAttribute("shadow-intensity", "1");
    mv.setAttribute("exposure", "1.0");
    mv.setAttribute("loading", "eager");
    // Do not show ANY geometry until the whole mesh has arrived. model-viewer renders a GLB
    // progressively, so a partly-streamed mesh appears as a handful of loose triangles — which
    // is indistinguishable from an output that genuinely IS a degenerate few-triangle blob (the
    // corpus contains some, and the gold decoy looks like that on purpose). A voter who sees
    // that cannot tell "still loading" from "this model produced garbage", and votes on it.
    // `reveal="manual"` holds the frame blank until we dismiss it on `load`.
    mv.setAttribute("reveal", "manual");
    mv.setAttribute(
      "aria-label",
      "Interactive 3D model — drag to rotate, scroll or pinch to zoom",
    );
    // Open on the low-detail mesh when the release produced one: it is a fraction of the bytes,
    // and the ballot cannot be judged at all until every slot has arrived. `armDetailUpgrade`
    // below swaps in `asset.url` the moment anyone looks closely. Absent lod_url — an older
    // bundle, a mesh too small to be worth a second file, or one the LOD gate refused — this is
    // exactly the behaviour it has always had.
    const lodUrl = asset.lod_url || null;
    mv.setAttribute("src", lodUrl || asset.url);
    mv.style.width = "100%";
    mv.style.height = "100%";
    mv.addEventListener("load", () => {
      if (stale()) return;
      try {
        mv.dismissPoster();
      } catch (_) {
        /* older model-viewer without manual reveal — it was already visible */
      }
      loading.remove();
      hint(slot, "drag to rotate · scroll or pinch to zoom");
      slot.dispatchEvent(
        new CustomEvent("bio3d:viewer-settled", {
          bubbles: true,
          detail: { ok: true },
        }),
      );
    });
    mv.addEventListener("error", () => {
      if (stale()) return;
      failed(slot, "Model failed to load");
      // A failure is SETTLED too: the voter can see it did not load and "both bad" is a
      // legitimate call. Not emitting this would wedge the vote controls forever.
      slot.dispatchEvent(
        new CustomEvent("bio3d:viewer-settled", {
          bubbles: true,
          detail: { ok: false },
        }),
      );
    });
    slot.appendChild(mv);
    bindResetView(slot, mv);
    slot._onResize = null; // model-viewer auto-resizes; clear any stale molecular closure
    addControls(slot, onFlag);
    // Served the low-detail mesh? Then the full one has to be one interaction away.
    if (lodUrl) armDetailUpgrade(slot, mv, asset.url, stale);
  }

  function bindResetView(slot, mv) {
    slot._resetView = () => {
      mv.cameraOrbit = "0deg 75deg auto";
      mv.fieldOfView = "auto";
      mv.cameraTarget = "auto auto auto";
      mv.jumpCameraToGoal();
    };
  }

  // Attributes that decide how a mesh is LIT and FRAMED. The upgraded viewer must carry every
  // one of them or the full mesh would render differently from the LOD it replaces — and on a
  // fidelity benchmark a lighting change mid-inspection reads as a property of the model.
  // How far a voter must dolly IN before the full mesh is fetched. 0.9 is deliberately close to
  // 1: the cost of upgrading early is bytes, the cost of upgrading late is a voter scoring our
  // decimation as the generator's geometry — and only one of those corrupts the benchmark.
  const ZOOM_IN_FRACTION = 0.9;

  const MESH_VIEW_ATTRS = [
    "camera-controls",
    "touch-action",
    "shadow-intensity",
    "exposure",
    "loading",
    "reveal",
    "aria-label",
  ];

  /**
   * Swap the low-detail mesh for the real one the moment a voter looks closely.
   *
   * The LOD exists only to make the ballot's FIRST frame arrive sooner. It is a decimated mesh:
   * at a 158x168 grid cell it is indistinguishable, but zoomed in it is not, and a voter who
   * sees our faceting attributes it to the generator. So the full mesh must arrive before any
   * close inspection can happen, and this is what guarantees it.
   *
   * The upgrade loads into a SECOND, hidden model-viewer and only removes the LOD once the
   * replacement has actually rendered. Reassigning `src` in place would have been far less code,
   * but `reveal="manual"` holds the frame blank until the new mesh decodes — so the model would
   * visibly VANISH for a second or two at the exact moment the voter leaned in. Camera state is
   * copied across first, so the swap lands on the view they had already framed.
   */
  function armDetailUpgrade(slot, lodViewer, fullUrl, stale) {
    let started = false;
    const start = () => {
      if (started || stale()) return;
      started = true;
      const full = document.createElement("model-viewer");
      for (const a of MESH_VIEW_ATTRS) {
        const v = lodViewer.getAttribute(a);
        if (v !== null) full.setAttribute(a, v);
      }
      full.setAttribute("src", fullUrl);
      // Stacked exactly over the LOD, invisible until it has something to show. The slot is the
      // positioning context; it may be `static` in the grid layout, which would anchor the
      // overlay to the page instead of the cell.
      if (getComputedStyle(slot).position === "static")
        slot.style.position = "relative";
      full.style.cssText =
        "position:absolute;inset:0;width:100%;height:100%;opacity:0";
      full.addEventListener("load", () => {
        // Ballot moved on while the full mesh was in flight — drop it, do not touch the slot.
        if (stale()) {
          full.remove();
          return;
        }
        try {
          full.cameraOrbit = lodViewer.cameraOrbit;
          full.fieldOfView = lodViewer.fieldOfView;
          full.cameraTarget = lodViewer.cameraTarget;
          full.jumpCameraToGoal();
        } catch (_) {
          /* camera not readable yet — the default framing is still correct */
        }
        try {
          full.dismissPoster();
        } catch (_) {
          /* older model-viewer without manual reveal */
        }
        full.style.opacity = "1";
        lodViewer.remove();
        // Reset-view and fullscreen resize must now drive the viewer that is actually on screen.
        bindResetView(slot, full);
      });
      // If the full mesh fails, the LOD stays exactly where it is. A voter keeps a working
      // ballot; deliberately NOT emitting viewer-settled here, because the slot already settled
      // on the LOD and re-emitting would double-count against the vote gate.
      full.addEventListener("error", () => full.remove());
      slot.appendChild(full);
    };

    // Watch the CAMERA, not the input device.
    //
    // This listened for `wheel` and `touchstart` on the host element until 2026-08-02, and it
    // fired for about 1 voter in 10 in production. Measured, not guessed: a real wheel demonstrably
    // reached the element (it zoomed the camera and fired both capture- and bubble-phase probes),
    // so delivery was never the problem. The trigger was.
    //
    // Two mechanisms, and the fix removes both rather than patching either:
    //
    //   * `{once: true}` removes a listener the first time it FIRES, but `start()` returns early
    //     without setting `started` when the slot is stale. Any wheel arriving during a remount
    //     therefore consumed the one and only listener and disabled the upgrade permanently, with
    //     nothing logged and no way to notice.
    //   * more fundamentally, raw input events couple this safeguard to how a third-party web
    //     component handles its own gestures. model-viewer owns zoom; we were guessing at the
    //     input that produces it, so pinch, keyboard zoom and programmatic framing never
    //     triggered an upgrade at all — even on the runs where the wheel path did work.
    //
    // `camera-change` is the signal that actually means "someone is looking closer", and
    // model-viewer emits it however the zoom was produced. Watching state instead of input also
    // makes the failure visible: if the event stopped firing the upgrade would stop entirely,
    // rather than degrading to one voter in ten.
    let baseRadius = null;
    const readRadius = () => {
      try {
        const o = lodViewer.getCameraOrbit && lodViewer.getCameraOrbit();
        return o && typeof o.radius === "number" ? o.radius : null;
      } catch (_) {
        return null;
      }
    };
    // The framing model-viewer settles on after `load` is the baseline; anything read before that
    // is the pre-framing default and would make the first real frame look like a zoom.
    lodViewer.addEventListener("load", () => {
      baseRadius = readRadius();
    });

    const onCamera = (e) => {
      // 'none' is our own jumpCameraToGoal and the initial framing; only a person counts.
      if (!e || !e.detail || e.detail.source !== "user-interaction") return;
      const r = readRadius();
      if (baseRadius === null || r === null) return;
      // Dollying IN is what reveals faceting. Rotating at a 158x168 grid cell does not, and
      // upgrading on rotation would spend the full mesh's bytes on most ballots and give back
      // the saving the LOD exists for.
      if (r <= baseRadius * ZOOM_IN_FRACTION) {
        lodViewer.removeEventListener("camera-change", onCamera);
        start();
      }
    };
    lodViewer.addEventListener("camera-change", onCamera);

    // Fullscreen is an explicit request for a closer look before the camera has moved at all.
    document.addEventListener("fullscreenchange", () => {
      if (
        document.fullscreenElement &&
        slot.contains(document.fullscreenElement)
      )
        start();
      else if (document.fullscreenElement === slot) start();
    });
  }

  async function mountMolecular(slot, asset, fmt, onFlag) {
    const myGen = slot._viewerGen;
    const stale = () => slot._viewerGen !== myGen;
    const loading = spinner(slot, "Loading structure…");
    try {
      await ensure3Dmol(); // lazy-loaded on first molecular mount (not in page <head>)
      if (stale()) return;
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
      hint(slot, "drag to rotate · scroll or pinch to zoom");
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
