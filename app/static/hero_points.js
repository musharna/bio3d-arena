/* Home-hero point-cloud renderer.
 *
 * Renders the baked per-kingdom point clouds (real GT scan for plants, surface-sampled
 * reconstructions for fungi/animals — see scripts/bake_hero_points.py) as a rotating,
 * depth-sorted cloud on a <canvas>, cycling plants -> fungi -> animals with a clean
 * dissolve. Draws its own orientation gizmo, supports drag-to-rotate, and honors
 * prefers-reduced-motion (static, no cycle). No external dependencies.
 *
 * The canvas carries data-plants / data-fungi / data-animals attrs with the JSON URLs.
 */
(function () {
  "use strict";
  var canvas = document.getElementById("hero-canvas");
  if (!canvas || !canvas.getContext) return;

  var ORDER = ["plants", "fungi", "animals"];
  var URLS = {
    plants: canvas.getAttribute("data-plants"),
    fungi: canvas.getAttribute("data-fungi"),
    animals: canvas.getAttribute("data-animals"),
  };
  var clouds = { plants: null, fungi: null, animals: null };

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var g = canvas.getContext("2d");

  function isDark() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t === "dark") return true;
    if (t === "light") return false;
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function accentRGB() {
    return isDark() ? [111, 213, 152] : [47, 158, 96]; // arena green, per theme
  }

  function rotY(v, s, c) {
    return [c * v[0] + s * v[2], v[1], -s * v[0] + c * v[2]];
  }
  function rotX(v, s, c) {
    return [v[0], c * v[1] - s * v[2], s * v[1] + c * v[2]];
  }

  function drawCloud(pts, theta, phi, alpha) {
    var w = canvas.clientWidth,
      h = canvas.clientHeight;
    var S = Math.min(w, h) * 0.34,
      cx = w / 2,
      cy = h / 2;
    var sy = Math.sin(theta),
      cyc = Math.cos(theta),
      sx = Math.sin(phi),
      cxc = Math.cos(phi);
    var c = accentRGB(),
      ar = c[0],
      ag = c[1],
      ab = c[2];

    var R = new Array(pts.length);
    for (var i = 0; i < pts.length; i++)
      R[i] = rotX(rotY(pts[i], sy, cyc), sx, cxc);
    var idx = R.map(function (_, i) {
      return i;
    }).sort(function (a, b) {
      return R[a][2] - R[b][2];
    });
    for (var j = 0; j < idx.length; j++) {
      var r = R[idx[j]];
      var t = Math.max(0.12, Math.min(1, (r[2] + 1.2) / 2.4));
      g.beginPath();
      g.arc(cx + r[0] * S, cy - r[1] * S, 0.5 + 1.4 * t, 0, Math.PI * 2);
      g.fillStyle =
        "rgba(" +
        ar +
        "," +
        ag +
        "," +
        ab +
        "," +
        ((0.22 + 0.62 * t) * alpha).toFixed(3) +
        ")";
      g.fill();
    }
  }

  function drawGizmo(theta, phi) {
    var h = canvas.clientHeight,
      ox = 24,
      oy = h - 22,
      L = 15;
    var sy = Math.sin(theta),
      cyc = Math.cos(theta),
      sx = Math.sin(phi),
      cxc = Math.cos(phi);
    var axes = [
      [[1, 0, 0], "#e5484d"],
      [[0, 1, 0], "#30a46c"],
      [[0, 0, 1], "#0091ff"],
    ];
    for (var k = 0; k < axes.length; k++) {
      var rv = rotX(rotY(axes[k][0], sy, cyc), sx, cxc);
      g.lineWidth = 2;
      g.lineCap = "round";
      g.strokeStyle = axes[k][1];
      g.beginPath();
      g.moveTo(ox, oy);
      g.lineTo(ox + rv[0] * L, oy - rv[1] * L);
      g.stroke();
      g.beginPath();
      g.arc(ox + rv[0] * L, oy - rv[1] * L, 2.4, 0, Math.PI * 2);
      g.fillStyle = axes[k][1];
      g.fill();
    }
  }

  // ---- animation state ----
  var theta = 0,
    dragTheta = 0;
  var idx = 0; // active kingdom index
  var phaseStart = null;
  var DWELL = 5000,
    FADE = 800; // ms visible, ms dissolve
  var dragging = false,
    lastX = 0,
    pausedFor = 0,
    pauseStart = 0;

  // ---- kingdom pills that light up with the model currently on the turntable ----
  var pills = {};
  Array.prototype.forEach.call(
    document.querySelectorAll(".b3d-hero-pill[data-kingdom]"),
    function (el) {
      pills[el.getAttribute("data-kingdom")] = el;
    },
  );
  var activeKingdom = null;
  function setActivePill(k) {
    if (k === activeKingdom) return;
    activeKingdom = k;
    for (var key in pills) pills[key].classList.toggle("is-active", key === k);
  }
  setActivePill("plants"); // initial highlight, before the clouds finish loading

  function sizeCanvas() {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = canvas.clientWidth,
      h = canvas.clientHeight;
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function ready(k) {
    return clouds[k] && clouds[k].length;
  }

  function frame(ts) {
    sizeCanvas();
    g.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    var phi = 0.42 + (reduce ? 0 : 0.16 * Math.sin(theta * 0.7));

    // pick which cloud(s) to draw and at what alpha
    var cur = ORDER[idx];
    if (reduce) {
      if (ready("plants")) drawCloud(clouds.plants, 0.7 + dragTheta, 0.42, 1);
      drawGizmo(0.7 + dragTheta, 0.42);
      setActivePill("plants");
      return; // no rAF loop under reduced motion
    }

    if (!dragging) theta += 0.011;
    var rot = theta + dragTheta;

    if (phaseStart === null) phaseStart = ts;
    var e = ts - phaseStart - pausedFor;
    if (dragging) {
      drawIfReady(cur, rot, phi, 1);
      drawGizmo(rot, phi);
      setActivePill(cur);
      requestAnimationFrame(frame);
      return;
    }

    if (e < DWELL) {
      drawIfReady(cur, rot, phi, 1);
      setActivePill(cur);
    } else if (e < DWELL + FADE) {
      var f = (e - DWELL) / FADE;
      if (f < 0.5) {
        drawIfReady(cur, rot, phi, 1 - f / 0.5); // dissolve out
        setActivePill(cur);
      } else {
        drawIfReady(ORDER[(idx + 1) % 3], rot, phi, (f - 0.5) / 0.5); // dissolve in
        setActivePill(ORDER[(idx + 1) % 3]); // pill switches at the crossover
      }
    } else {
      idx = (idx + 1) % 3;
      phaseStart = ts;
      pausedFor = 0;
      drawIfReady(ORDER[idx], rot, phi, 1);
      setActivePill(ORDER[idx]);
    }
    drawGizmo(rot, phi);
    requestAnimationFrame(frame);
  }

  function drawIfReady(k, rot, phi, alpha) {
    // skip a kingdom whose data failed to load — fall through to the next visible one
    if (ready(k)) drawCloud(clouds[k], rot, phi, alpha);
  }

  // ---- drag-to-rotate (pauses the cycle clock while held) ----
  canvas.addEventListener("pointerdown", function (ev) {
    dragging = true;
    lastX = ev.clientX;
    pauseStart = ev.timeStamp || 0;
    canvas.setPointerCapture && canvas.setPointerCapture(ev.pointerId);
    canvas.style.cursor = "grabbing";
  });
  canvas.addEventListener("pointermove", function (ev) {
    if (!dragging) return;
    dragTheta += (ev.clientX - lastX) * 0.01;
    lastX = ev.clientX;
  });
  function endDrag(ev) {
    if (!dragging) return;
    dragging = false;
    if (pauseStart) pausedFor += (ev.timeStamp || pauseStart) - pauseStart;
    canvas.style.cursor = "grab";
  }
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.style.cursor = "grab";

  // ---- load the three clouds, then start ----
  ORDER.forEach(function (k) {
    if (!URLS[k]) return;
    fetch(URLS[k])
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        clouds[k] = d;
        // reduced-motion draws a single static frame; do it once plants is available
        // (the initial rAF may have fired before the fetch resolved).
        if (reduce && k === "plants") requestAnimationFrame(frame);
      })
      .catch(function () {
        clouds[k] = null;
      });
  });
  if (!reduce) requestAnimationFrame(frame);
})();
