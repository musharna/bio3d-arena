// Mount a preview viewer for each pending submission + mirror the admin token
// into every approve/reject form (entered once at the top of the page).
document.querySelectorAll(".mod-viewer").forEach((slot) => {
  window.Bio3DViewer.mount(slot, {
    url: slot.dataset.url,
    format: slot.dataset.format,
  });
});

const tokenInput = document.getElementById("admin-token");
if (tokenInput) {
  const sync = () =>
    document
      .querySelectorAll(".token-mirror")
      .forEach((i) => (i.value = tokenInput.value));
  tokenInput.addEventListener("input", sync);
  document
    .querySelectorAll("form")
    .forEach((f) => f.addEventListener("submit", sync));
}
