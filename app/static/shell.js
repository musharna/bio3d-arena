// app/static/shell.js — sidebar collapse, theme toggle, mobile drawer. All persisted.
(function () {
  var root = document.documentElement;
  function setTheme(t) {
    root.setAttribute("data-theme", t);
    try {
      localStorage.setItem("bio3d_theme", t);
    } catch (e) {}
  }
  var themeBtn = document.getElementById("b3d-theme");
  if (themeBtn)
    themeBtn.addEventListener("click", function () {
      setTheme(root.getAttribute("data-theme") === "light" ? "dark" : "light");
    });
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
    if (e.key === "Escape") closeDrawer();
  });
})();
