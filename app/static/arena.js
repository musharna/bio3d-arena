// Arena client: fetch a comparison, render both models, record a vote, advance.
let current = null;
let busy = false;

const el = (id) => document.getElementById(id);

async function loadNext() {
  busy = true;
  setStatus("Loading next comparison…");
  try {
    const res = await fetch("/api/next");
    if (res.status === 404) {
      setStatus("No comparisons available yet. Add tasks + outputs in Admin.");
      current = null;
      return;
    }
    const data = await res.json();
    render(data);
  } catch (e) {
    setStatus("Error loading comparison: " + e);
  } finally {
    busy = false;
  }
}

function render(data) {
  current = data;
  el("task-cat").textContent = data.task.category;
  el("task-title").textContent = data.task.title;
  el("task-prompt").textContent = data.task.prompt;
  el("criterion-name").textContent = data.criterion.name;
  el("viewer-a").setAttribute("src", data.a.url);
  el("viewer-b").setAttribute("src", data.b.url);
  setStatus("");
}

async function vote(winner) {
  if (busy || !current) return;
  busy = true;
  setStatus("Recording vote…");
  try {
    const res = await fetch("/api/vote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ comparison_id: current.comparison_id, winner }),
    });
    const data = await res.json();
    if (data.next) {
      render(data.next);
      flash("Vote recorded ✓");
    } else {
      setStatus("Vote recorded. No more comparisons available.");
      current = null;
    }
  } catch (e) {
    setStatus("Error recording vote: " + e);
  } finally {
    busy = false;
  }
}

function setStatus(msg) {
  el("status-line").textContent = msg;
}

function flash(msg) {
  const s = el("status-line");
  s.textContent = msg;
  s.classList.add("flash");
  setTimeout(() => s.classList.remove("flash"), 700);
}

document.querySelectorAll(".vote-btn").forEach((btn) => {
  btn.addEventListener("click", () => vote(btn.dataset.winner));
});

// Keyboard shortcuts: arrow keys for A/B, t for tie, x for bad.
document.addEventListener("keydown", (e) => {
  if (e.key === "ArrowLeft") vote("a");
  else if (e.key === "ArrowRight") vote("b");
  else if (e.key === "t") vote("tie");
  else if (e.key === "x") vote("bad");
});

loadNext();
