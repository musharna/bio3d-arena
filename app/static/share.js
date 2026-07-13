// Share affordance on /models/{slug} (#75). Copy-link with a visible confirmation, and a
// graceful fallback when the Clipboard API is unavailable (insecure origin, denied permission,
// older browser) — the link is never a dead end: worst case we reveal it, selected, to copy by
// hand. Keyboard-operable throughout (it is a real <button>).
(function () {
  var root = document.querySelector(".b3d-share");
  if (!root) return;

  var btn = root.querySelector("[data-share-copy]");
  var label = root.querySelector("[data-share-copy-text]");
  var status = root.querySelector(".b3d-share-status");
  var fallback = root.querySelector(".b3d-share-fallback");
  var url = root.getAttribute("data-share-url") || window.location.href;
  var resetTimer = null;

  function confirmCopied() {
    if (label) label.textContent = "Copied";
    root.classList.add("is-copied");
    if (status) status.textContent = "Link copied to clipboard";
    clearTimeout(resetTimer);
    resetTimer = setTimeout(function () {
      if (label) label.textContent = "Copy link";
      root.classList.remove("is-copied");
      if (status) status.textContent = "";
    }, 2400);
  }

  function revealFallback() {
    if (!fallback) return;
    fallback.hidden = false;
    fallback.focus();
    fallback.select();
    if (status) status.textContent = "Press Ctrl+C (⌘C) to copy the link";
  }

  // execCommand is deprecated but is the only copy path on a non-secure origin; treat it as the
  // middle rung, not the primary one.
  function legacyCopy() {
    if (!fallback) return false;
    var wasHidden = fallback.hidden;
    fallback.hidden = false;
    fallback.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (e) {
      ok = false;
    }
    if (ok) {
      fallback.hidden = wasHidden;
      return true;
    }
    return false;
  }

  btn.addEventListener("click", function () {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(confirmCopied, function () {
        if (legacyCopy()) confirmCopied();
        else revealFallback();
      });
      return;
    }
    if (legacyCopy()) confirmCopied();
    else revealFallback();
  });
})();
