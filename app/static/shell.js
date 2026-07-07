// app/static/shell.js — sidebar collapse, theme cycle (auto/light/dark), kingdom
// dropdown, mobile drawer. All persisted.
(function () {
  var root = document.documentElement;
  var THEME_KEY = "bio3d_theme";

  function osTheme() {
    return matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  function storedThemeMode() {
    // "auto" whenever nothing (or something other than an explicit
    // light/dark override) is stored — this is what makes the OS-follow
    // live-listener below a no-op once the user picks an explicit mode.
    var t;
    try {
      t = localStorage.getItem(THEME_KEY);
    } catch (e) {
      t = null;
    }
    return t === "light" || t === "dark" ? t : "auto";
  }
  var THEME_GLYPH = { auto: "⏾", light: "☀", dark: "☾" };
  var THEME_LABEL = { auto: "Auto", light: "Light", dark: "Dark" };
  var themeBtn = document.getElementById("b3d-theme");
  function paintThemeBtn(mode) {
    if (!themeBtn) return;
    var icon = themeBtn.querySelector(".b3d-nav-icon");
    var label = themeBtn.querySelector(".b3d-nav-label");
    if (icon) icon.textContent = THEME_GLYPH[mode];
    if (label) label.textContent = THEME_LABEL[mode];
    themeBtn.setAttribute(
      "aria-label",
      "Theme: " + THEME_LABEL[mode] + " (click to cycle Auto / Light / Dark)",
    );
  }
  function applyEffectiveTheme(mode) {
    root.setAttribute(
      "data-theme",
      mode === "light" || mode === "dark" ? mode : osTheme(),
    );
  }
  function setThemeMode(mode) {
    try {
      localStorage.setItem(THEME_KEY, mode);
    } catch (e) {}
    applyEffectiveTheme(mode);
    paintThemeBtn(mode);
  }
  // Reflect whatever the pre-paint <head> script already resolved (it runs
  // before this deferred script and sets data-theme from the same storage
  // key), so the button label matches on first render without a flash.
  paintThemeBtn(storedThemeMode());
  if (themeBtn)
    themeBtn.addEventListener("click", function () {
      var order = ["auto", "light", "dark"];
      var next = order[(order.indexOf(storedThemeMode()) + 1) % order.length];
      setThemeMode(next);
    });
  // Live OS-follow: only takes effect while the user hasn't pinned an
  // explicit light/dark override (i.e. stored mode is "auto").
  var media = matchMedia("(prefers-color-scheme: dark)");
  var mediaListener = function (e) {
    if (storedThemeMode() === "auto") {
      root.setAttribute("data-theme", e.matches ? "dark" : "light");
    }
  };
  if (media.addEventListener) media.addEventListener("change", mediaListener);
  else if (media.addListener) media.addListener(mediaListener); // older Safari

  // Kingdom scope dropdown (kbar)
  var kdrop = document.querySelector(".b3d-kdrop");
  var kdropBtn = document.getElementById("b3d-kdrop-btn");
  function closeKdrop() {
    if (kdrop) kdrop.classList.remove("is-open");
    if (kdropBtn) kdropBtn.setAttribute("aria-expanded", "false");
  }
  if (kdropBtn && kdrop) {
    kdropBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = kdrop.classList.toggle("is-open");
      kdropBtn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("click", function (e) {
      if (kdrop.classList.contains("is-open") && !kdrop.contains(e.target)) {
        closeKdrop();
      }
    });
  }

  var COLLAPSE_KEY = "bio3d_nav_collapsed";
  var app = document.querySelector(".b3d-app");
  try {
    if (localStorage.getItem(COLLAPSE_KEY) === "1" && app)
      app.classList.add("is-collapsed");
  } catch (e) {}
  var collapseBtn = document.getElementById("b3d-collapse");
  if (collapseBtn && app)
    collapseBtn.addEventListener("click", function () {
      var c = app.classList.toggle("is-collapsed");
      try {
        localStorage.setItem(COLLAPSE_KEY, c ? "1" : "0");
      } catch (e) {}
    });
  var burger = document.getElementById("b3d-burger");
  var sidebar = document.querySelector(".b3d-sidebar");
  var scrim = document.querySelector(".b3d-scrim");
  function closeDrawer() {
    if (sidebar) sidebar.classList.remove("is-open");
    if (scrim) scrim.classList.remove("is-open");
  }
  if (burger && sidebar)
    burger.addEventListener("click", function () {
      sidebar.classList.toggle("is-open");
      if (scrim) scrim.classList.toggle("is-open");
    });
  if (scrim) scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closeDrawer();
      closeKdrop();
    }
  });
})();
