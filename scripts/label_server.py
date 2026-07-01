"""Mode-C browser labeler — a tiny local web app for human trait calibration.

Shows one (contact-sheet image + taxon + expected trait) at a time with four verdict
buttons (keyboard 1-4), a note box, skip, and a per-class progress bar. Each click
autosaves to a resumable CSV store that the existing `calibration_labels.py ingest`
consumes unchanged (it reads output_id/trait_key/trait_class/human_verdict; the extra
`note` column is ignored, kept for your audit).

  .venv/bin/python scripts/label_server.py \\
      --sample data/study/calibration_labels_2026-06-30.csv \\
      --store  data/study/calibration_labels_filled.csv \\
      [--seed /mnt/c/Users/a2b32/Desktop/bio3d-calibration/calibration_labels.csv]

Then open http://127.0.0.1:8765 . Reads only the sample + image files; writes only the
store CSV. Never touches the DB — calibration stays behind `ingest --commit`.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.calibration_labels import VOCAB  # noqa: E402

STORE_FIELDS = [
    "output_id",
    "trait_key",
    "trait_class",
    "taxon",
    "expected",
    "contact_sheet",
    "human_verdict",
    "note",
]


def load_sample(sample_csv) -> list[dict]:
    """Read the canonical blind sample. output_id coerced to int; row order preserved."""
    rows = []
    with open(sample_csv, newline="") as f:
        for r in csv.DictReader(f):
            rows.append(
                {
                    "output_id": int(r["output_id"]),
                    "trait_key": r["trait_key"],
                    "trait_class": r["trait_class"],
                    "taxon": r.get("taxon", ""),
                    "expected": r.get("expected", ""),
                    "contact_sheet": r.get("contact_sheet", ""),
                }
            )
    return rows


def load_store(store_csv) -> dict:
    """Read an existing label store → {(output_id, trait_key): {human_verdict, note}}.
    Empty dict if the file does not exist yet."""
    p = Path(store_csv)
    if not p.exists():
        return {}
    store = {}
    with p.open(newline="") as f:
        for r in csv.DictReader(f):
            v = (r.get("human_verdict") or "").strip().lower()
            if not v:
                continue
            store[(int(r["output_id"]), r["trait_key"])] = {
                "human_verdict": v,
                "note": (r.get("note") or "").strip(),
            }
    return store


def merge_seed(store: dict, seed_csv, rows) -> int:
    """Pull non-blank, valid human_verdicts from a prior CSV (e.g. the desktop copy) into
    `store`, only for (output_id, trait_key) pairs present in the sample. Returns count
    added. A non-blank but out-of-vocab verdict is a loud error, not a silent drop."""
    valid_keys = {(r["output_id"], r["trait_key"]) for r in rows}
    added = 0
    with open(seed_csv, newline="") as f:
        for r in csv.DictReader(f):
            v = (r.get("human_verdict") or "").strip().lower()
            if not v:
                continue
            if v not in VOCAB:
                raise ValueError(f"invalid human_verdict {v!r} in seed {seed_csv}")
            key = (int(r["output_id"]), r["trait_key"])
            if key in valid_keys and key not in store:
                store[key] = {"human_verdict": v, "note": (r.get("note") or "").strip()}
                added += 1
    return added


def write_store(store_csv, rows, store) -> None:
    """Rewrite the store CSV from scratch (rows are tiny). Only labeled rows are written,
    in sample order, with full sample columns so the file is directly ingestible."""
    with open(store_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=STORE_FIELDS)
        w.writeheader()
        for r in rows:
            key = (r["output_id"], r["trait_key"])
            lab = store.get(key)
            if not lab:
                continue
            w.writerow({**{k: r.get(k, "") for k in STORE_FIELDS}, **lab})


def next_unlabeled(rows, store, after: int = -1):
    """Index of the first row after `after` whose (output_id, trait_key) is not in store,
    or None if all subsequent rows are labeled."""
    for i in range(after + 1, len(rows)):
        if (rows[i]["output_id"], rows[i]["trait_key"]) not in store:
            return i
    return None


def progress(rows, store) -> dict:
    """Overall + per-trait_class labeled/total counts."""
    per = {}
    labeled = 0
    for r in rows:
        cls = r["trait_class"]
        d = per.setdefault(cls, {"labeled": 0, "total": 0})
        d["total"] += 1
        if (r["output_id"], r["trait_key"]) in store:
            d["labeled"] += 1
            labeled += 1
    return {"labeled": labeled, "total": len(rows), "per_class": per}


# --------------------------------------------------------------------------- #
# FastAPI app                                                                  #
# --------------------------------------------------------------------------- #


def build_app(rows, store, store_csv):
    import hashlib

    from fastapi import FastAPI
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    app = FastAPI(title="Mode-C labeler")

    # Identity is by content, not row position: a stale tab that reordered would
    # otherwise desync image/text/save from the server's row list. out2sheet maps
    # output_id → its contact sheet; key2row maps (output_id, trait_key) → row dict;
    # VERSION fingerprints the row set so a stale client is told to reload.
    out2sheet = {r["output_id"]: r["contact_sheet"] for r in rows}
    key2row = {(r["output_id"], r["trait_key"]): r for r in rows}
    VERSION = hashlib.sha1(
        "|".join(f"{r['output_id']}:{r['trait_key']}" for r in rows).encode()
    ).hexdigest()[:12]

    def _row_payload(i):
        r = rows[i]
        lab = store.get((r["output_id"], r["trait_key"]), {})
        return {
            "i": i,
            "output_id": r["output_id"],
            "trait_key": r["trait_key"],
            "trait_class": r["trait_class"],
            "taxon": r["taxon"],
            "expected": r["expected"],
            "human_verdict": lab.get("human_verdict", ""),
            "note": lab.get("note", ""),
        }

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE_HTML

    @app.get("/api/rows")
    def api_rows():
        first = next_unlabeled(rows, store, after=-1)
        return JSONResponse(
            {
                "rows": [_row_payload(i) for i in range(len(rows))],
                "vocab": sorted(VOCAB),
                "progress": progress(rows, store),
                "start": first if first is not None else 0,
                "version": VERSION,
            }
        )

    @app.get("/img/{output_id}")
    def img(output_id: int):
        # Keyed by output_id (not row index) so the image always matches the row the
        # client is showing, even if the server's row order changed under a stale tab.
        path = out2sheet.get(output_id)
        if not path or not Path(path).exists():
            return JSONResponse({"error": "image missing"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.post("/api/save")
    async def save(payload: dict):
        if payload.get("version") and payload["version"] != VERSION:
            return JSONResponse({"stale": True}, status_code=409)  # client must reload
        verdict = (payload.get("verdict") or "").strip().lower()
        note = (payload.get("note") or "").strip()
        key = (int(payload["output_id"]), payload["trait_key"])
        if key not in key2row:
            return JSONResponse({"error": "unknown row"}, status_code=404)
        if verdict == "":  # clear a label (correction)
            store.pop(key, None)
        else:
            if verdict not in VOCAB:
                return JSONResponse({"error": f"bad verdict {verdict!r}"}, status_code=400)
            store[key] = {"human_verdict": verdict, "note": note}
        write_store(store_csv, rows, store)
        return JSONResponse({"ok": True, "progress": progress(rows, store)})

    return app


PAGE_HTML = """<!doctype html><html><head><meta charset=utf-8>
<title>Mode-C labeler</title>
<style>
 body{font:15px/1.4 system-ui,sans-serif;margin:0;background:#111;color:#eee}
 header{padding:8px 14px;background:#1b1b1b;border-bottom:1px solid #333;position:sticky;top:0;
   display:flex;gap:16px;align-items:center;flex-wrap:wrap}
 #bar{flex:1;height:10px;background:#333;border-radius:5px;overflow:hidden;min-width:160px}
 #bar>div{height:100%;background:#3a8;width:0}
 main{display:flex;gap:18px;padding:18px;align-items:flex-start;flex-wrap:wrap}
 #imgwrap{flex:1;min-width:340px;max-width:760px}
 #sheet{width:100%;border:1px solid #333;border-radius:8px;background:#000}
 #panel{width:360px;max-width:100%}
 .meta{background:#1b1b1b;border:1px solid #333;border-radius:8px;padding:14px;margin-bottom:14px}
 .meta b{color:#9cf}
 .exp{font-size:18px;margin:6px 0;color:#fff}
 button.v{display:block;width:100%;text-align:left;margin:6px 0;padding:12px 14px;font-size:15px;
   border:1px solid #444;border-radius:8px;background:#222;color:#eee;cursor:pointer}
 button.v:hover{background:#2c2c2c}
 button.v .k{display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;
   background:#3a8;color:#021;border-radius:5px;margin-right:10px;font-weight:700}
 .pc .k{background:#3a8}.pw .k{background:#e85}.ab .k{background:#c66}.na .k{background:#89a}
 #note{width:100%;box-sizing:border-box;margin:8px 0;padding:8px;background:#181818;color:#eee;
   border:1px solid #444;border-radius:6px;min-height:46px}
 .nav{display:flex;gap:8px;margin-top:8px}
 .nav button{flex:1;padding:8px;background:#222;color:#bbb;border:1px solid #444;border-radius:6px;cursor:pointer}
 .chip{font-size:12px;padding:3px 8px;background:#222;border:1px solid #444;border-radius:12px;cursor:pointer;margin-right:6px}
 #cur{color:#9cf}.done{color:#3a8}
 select{background:#181818;color:#eee;border:1px solid #444;border-radius:6px;padding:4px}
 small{color:#888}
 button.v{white-space:normal;line-height:1.35}
 .eg{color:#8a8;font-weight:400}
 .tree{background:#161c16;border:1px solid #2c3a2c;border-radius:8px;padding:10px 12px;margin-bottom:12px;font-size:13px;color:#bcd}
 .tree b{color:#9cf}
</style></head><body>
<header>
 <b>Mode-C labeler</b>
 <span id=count></span>
 <div id=bar><div></div></div>
 <span>class: <select id=filter><option value="">all</option></select></span>
 <span id=perclass></span>
</header>
<main>
 <div id=imgwrap><img id=sheet alt="contact sheet"></div>
 <div id=panel>
  <div class=meta>
   <div><b id=taxon></b> &middot; <span id=cls></span> &middot; <small id=loc></small></div>
   <div class=exp>Trait: <span id=exp></span></div>
   <small>Judge THIS trait from the 4 views. Keys 1-4. ←/→ navigate.</small>
  </div>
  <div class=tree>
   <b>Decide in order:</b><br>
   1. Is it a recognizable plant? <b>No</b> (junk / blob / not a plant) → press <b>4</b>.<br>
   2. Is this feature present in the model? <b>No</b> → press <b>3</b>.<br>
   3. Does it match the expected value? <b>Yes</b> → press <b>1</b>. &nbsp; <b>No</b> (wrong color/shape/count) → press <b>2</b>.
  </div>
  <button class="v pc" data-v=present_correct><span class=k>1</span><b>present_correct</b> — feature is there AND matches <span class=eg>(e.g. fruit present & red as expected)</span></button>
  <button class="v pw" data-v=present_wrong><span class=k>2</span><b>present_wrong</b> — feature is there but the value is WRONG <span class=eg>(right organ, wrong color/shape/count — e.g. fruit present but green not red)</span></button>
  <button class="v ab" data-v=absent><span class=k>3</span><b>absent</b> — a real plant, but this feature is genuinely missing <span class=eg>(counts against the model)</span></button>
  <button class="v na" data-v=not_assessable><span class=k>4</span><b>not_assessable</b> — can't judge: not a recognizable plant / junk, or the region isn't visible <span class=eg>(dropped from scoring — NOT counted against the model)</span></button>
  <textarea id=note placeholder="optional note (e.g. 'wrong shade', 'partial plant')"></textarea>
  <div><span class=chip id=notplant>✗ not a plant → 4</span>
       <span class=chip data-note="too low-res to judge">blurry</span></div>
  <div class=nav>
   <button id=prev>← prev</button>
   <button id=skip>skip →</button>
   <button id=nextunl>next unlabeled »</button>
  </div>
  <div class=nav><button id=clear>clear this label</button></div>
 </div>
</main>
<script>
let ROWS=[],VOCAB=[],cur=0,filter="",VERSION="";
async function load(){
 const d=await (await fetch('/api/rows')).json();
 ROWS=d.rows;VOCAB=d.vocab;cur=d.start;VERSION=d.version;
 const sel=document.getElementById('filter');
 [...new Set(ROWS.map(r=>r.trait_class))].sort().forEach(c=>{
   const o=document.createElement('option');o.value=c;o.textContent=c;sel.appendChild(o);});
 sel.onchange=e=>{filter=e.target.value;const n=firstInFilter();if(n!=null)cur=n;render();};
 updateProgress(d.progress);render();
}
function inFilter(i){return !filter||ROWS[i].trait_class===filter;}
function firstInFilter(){for(let i=0;i<ROWS.length;i++)if(inFilter(i))return i;return null;}
function render(){
 const r=ROWS[cur];if(!r)return;
 document.getElementById('sheet').src='/img/'+r.output_id+'?t='+Date.now();
 document.getElementById('taxon').textContent=r.taxon;
 document.getElementById('cls').textContent=r.trait_class;
 document.getElementById('exp').textContent=r.expected||'(no description)';
 document.getElementById('loc').textContent='row '+(cur+1)+'/'+ROWS.length+' · output '+r.output_id;
 document.getElementById('note').value=r.note||'';
 document.querySelectorAll('button.v').forEach(b=>{
   b.style.outline=(b.dataset.v===r.human_verdict)?'2px solid #9cf':'none';});
}
function updateProgress(p){
 document.querySelector('#bar>div').style.width=(100*p.labeled/p.total)+'%';
 document.getElementById('count').textContent=p.labeled+' / '+p.total+' labeled';
 document.getElementById('perclass').innerHTML=Object.entries(p.per_class).sort()
   .map(([c,v])=>`<span class=chip ${v.labeled>=20?'style="border-color:#3a8"':''}>${c} ${v.labeled}/${v.total}</span>`).join(' ');
}
async function save(verdict){
 const note=document.getElementById('note').value;
 const r=ROWS[cur];
 const res=await fetch('/api/save',{method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({output_id:r.output_id,trait_key:r.trait_key,verdict,note,version:VERSION})});
 if(res.status===409){alert('The labeling set changed on the server — reloading to resync.');location.reload();return;}
 const d=await res.json();
 if(d.error){alert(d.error);return;}
 ROWS[cur].human_verdict=verdict;ROWS[cur].note=note;
 updateProgress(d.progress);
 // advance to next unlabeled within the active filter
 let n=cur+1;while(n<ROWS.length&&(!inFilter(n)||ROWS[n].human_verdict))n++;
 if(n<ROWS.length){cur=n;}render();
}
document.querySelectorAll('button.v').forEach(b=>b.onclick=()=>save(b.dataset.v));
document.querySelectorAll('.chip[data-note]').forEach(c=>c.onclick=()=>{
 document.getElementById('note').value=c.dataset.note;document.getElementById('note').focus();});
document.getElementById('notplant').onclick=()=>{
 document.getElementById('note').value='not a plant / junk';save('not_assessable');};
document.getElementById('prev').onclick=()=>{let n=cur-1;while(n>=0&&!inFilter(n))n--;if(n>=0){cur=n;render();}};
document.getElementById('skip').onclick=()=>{let n=cur+1;while(n<ROWS.length&&!inFilter(n))n++;if(n<ROWS.length){cur=n;render();}};
document.getElementById('nextunl').onclick=()=>{let n=cur+1;while(n<ROWS.length&&(!inFilter(n)||ROWS[n].human_verdict))n++;if(n<ROWS.length){cur=n;render();}};
document.getElementById('clear').onclick=()=>save('');
document.addEventListener('keydown',e=>{
 if(e.target.tagName==='TEXTAREA'||e.target.tagName==='SELECT')return;
 if(e.key>='1'&&e.key<='4'){save(VOCAB_ORDER[e.key]);}
 else if(e.key==='ArrowRight'){document.getElementById('skip').click();}
 else if(e.key==='ArrowLeft'){document.getElementById('prev').click();}
});
const VOCAB_ORDER={'1':'present_correct','2':'present_wrong','3':'absent','4':'not_assessable'};
load();
</script></body></html>"""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sample", default="data/study/calibration_labels_2026-06-30.csv")
    p.add_argument("--store", default="data/study/calibration_labels_filled.csv")
    p.add_argument("--seed", default=None, help="prior CSV to import existing verdicts from")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)

    rows = load_sample(args.sample)
    store = load_store(args.store)
    if args.seed and Path(args.seed).exists():
        added = merge_seed(store, args.seed, rows)
        if added:
            write_store(args.store, rows, store)
        print(f"seeded {added} prior verdicts from {args.seed}")
    pr = progress(rows, store)
    print(f"loaded {len(rows)} rows; {pr['labeled']} already labeled")
    print(f"open http://{args.host}:{args.port}  (store → {args.store})")

    import uvicorn

    app = build_app(rows, store, args.store)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
