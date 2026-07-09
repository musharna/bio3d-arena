/* Lazy-load the collapsed VLM-judge board on the leaderboard page.
 *
 * The judge Bradley-Terry fit is expensive (~11s for the plants kingdom on a cold cache), so the
 * main /leaderboard render no longer computes it. This fetches the pre-rendered board fragment
 * from data-judge-url the first time the <details> is expanded, and injects it once. */
(function () {
  "use strict";
  var det = document.querySelector("details.lb-judge-section[data-judge-url]");
  if (!det) return;
  var body = det.querySelector(".lb-judge-body");
  var url = det.getAttribute("data-judge-url");
  if (!body || !url) return;

  function load() {
    if (body.getAttribute("data-loaded") !== "0") return; // already loaded / in flight
    body.setAttribute("data-loaded", "1");
    fetch(url, { headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(function (frag) {
        body.innerHTML = frag;
      })
      .catch(function () {
        body.setAttribute("data-loaded", "0"); // allow a retry on the next expand
        body.innerHTML =
          '<p class="subtle" style="padding:0.4rem 0.1rem">Could not load automated rankings — expand again to retry.</p>';
      });
  }

  // <details> fires "toggle" on open and close; only load when opening.
  det.addEventListener("toggle", function () {
    if (det.open) load();
  });
  // Respect a page that loads with the section already open (e.g. deep link / restored state).
  if (det.open) load();
})();
