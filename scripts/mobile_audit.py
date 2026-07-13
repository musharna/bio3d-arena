"""Mobile arena audit harness (task #77).

Drives /arena at phone widths in a real browser and reports the things a
substring assertion cannot see: horizontal overflow, tap-target sizes, whether
the vote bar is actually on screen, and what the K-wise grid reflows to.

Usage: python scripts/mobile_audit.py <out_dir_tag>   (e.g. "before" / "after")
"""

import sys
import pathlib
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
TAG = sys.argv[1] if len(sys.argv) > 1 else "before"
OUT = pathlib.Path("/tmp/mobile-audit") / TAG
OUT.mkdir(parents=True, exist_ok=True)

VIEWPORTS = [("390x844", 390, 844), ("430x932", 430, 932)]

# Measure in-page: overflow, control geometry, what's actually visible.
PROBE = """() => {
  const se = document.scrollingElement;
  const vh = window.innerHeight, vw = window.innerWidth;
  const r = (el) => { if (!el) return null; const b = el.getBoundingClientRect();
    return {w: Math.round(b.width), h: Math.round(b.height), top: Math.round(b.top),
            bottom: Math.round(b.bottom), left: Math.round(b.left), right: Math.round(b.right),
            visible: b.top < vh && b.bottom > 0 && b.width > 0}; };
  // Any element whose right edge exceeds the viewport => horizontal overflow culprit.
  const wide = [];
  document.querySelectorAll('body *').forEach((el) => {
    const b = el.getBoundingClientRect();
    if (b.right > vw + 1 && b.width > 0 && getComputedStyle(el).position !== 'fixed') {
      wide.push({sel: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
                      (el.className && typeof el.className === 'string'
                        ? '.' + el.className.trim().split(/\\s+/).join('.') : ''),
                 right: Math.round(b.right), w: Math.round(b.width)});
    }
  });
  const voteBtns = [...document.querySelectorAll('.vote-bar .vote-btn')].map((b) => {
    const q = b.getBoundingClientRect();
    return {txt: b.textContent.trim().slice(0, 14), w: Math.round(q.width), h: Math.round(q.height),
            bottom: Math.round(q.bottom), onScreen: q.top < vh && q.bottom > 0};
  });
  const cells = [...document.querySelectorAll('.kwise-cell')].map((c) => {
    const q = c.getBoundingClientRect();
    return {w: Math.round(q.width), h: Math.round(q.height), top: Math.round(q.top)};
  });
  const pickBtns = [...document.querySelectorAll('.kwise-pick-btn')].map((b) => {
    const q = b.getBoundingClientRect();
    return {w: Math.round(q.width), h: Math.round(q.height)};
  });
  const slots = [...document.querySelectorAll('.viewer-slot')].map((s) => {
    const q = s.getBoundingClientRect();
    const mv = s.querySelector('model-viewer');
    return {w: Math.round(q.width), h: Math.round(q.height),
            touchAction: mv ? getComputedStyle(mv).touchAction : null,
            slotTouchAction: getComputedStyle(s).touchAction};
  });
  const vb = document.querySelector('.vote-bar');
  return {
    viewport: {vw, vh},
    scrollWidth: se.scrollWidth, clientWidth: se.clientWidth,
    hOverflow: se.scrollWidth > se.clientWidth,
    bodyOverflowX: getComputedStyle(document.body).overflowX,
    docHeight: se.scrollHeight,
    wideElements: wide.slice(0, 12),
    abToggle: r(document.querySelector('.ab-toggle')),
    abToggleDisplay: document.querySelector('.ab-toggle')
      ? getComputedStyle(document.querySelector('.ab-toggle')).display : null,
    taskStrip: r(document.querySelector('.task-strip')),
    refPanel: r(document.querySelector('#reference-panel')),
    hero: r(document.querySelector('.arena-hero')),
    voteBar: r(vb),
    voteBarPosition: vb ? getComputedStyle(vb).position : null,
    voteBtns, slots, cells, pickBtns,
    nextPairBtn: r(document.querySelector('#next-pair-btn')),
    nextPairPosition: document.querySelector('#next-pair-btn')
      ? getComputedStyle(document.querySelector('#next-pair-btn')).position : null,
    pairDisplay: document.querySelector('.pair')
      ? getComputedStyle(document.querySelector('.pair')).gridTemplateColumns : null,
    kwiseCols: document.querySelector('#kwise-grid')
      ? getComputedStyle(document.querySelector('#kwise-grid')).gridTemplateColumns : null,
  };
}"""


def show(label, d):
    print(f"\n===== {label} =====")
    print(f"  viewport {d['viewport']['vw']}x{d['viewport']['vh']}  docHeight={d['docHeight']}")
    print(
        f"  H-OVERFLOW: {d['hOverflow']}  (scrollWidth={d['scrollWidth']} clientWidth={d['clientWidth']})"
    )
    if d["wideElements"]:
        print("  elements past right edge:")
        for w in d["wideElements"]:
            print(f"    - {w['sel'][:70]}  right={w['right']} w={w['w']}")
    print(f"  pair grid-cols   : {d['pairDisplay']}")
    print(f"  kwise grid-cols  : {d['kwiseCols']}")
    print(f"  ab-toggle display: {d['abToggleDisplay']}  rect={d['abToggle']}")
    print(f"  hero             : {d['hero']}")
    print(f"  task strip       : {d['taskStrip']}")
    print(f"  reference panel  : {d['refPanel']}")
    print(f"  vote bar         : pos={d['voteBarPosition']} {d['voteBar']}")
    for b in d["voteBtns"]:
        flag = "  <-- UNDER 44px" if b["h"] < 44 else ""
        print(f"    btn {b['txt']!r:18} {b['w']}x{b['h']} onScreen={b['onScreen']}{flag}")
    for s in d["slots"]:
        print(
            f"    slot {s['w']}x{s['h']} mv.touch-action={s['touchAction']} slot.touch-action={s['slotTouchAction']}"
        )
    if d["cells"]:
        print(f"  kwise cells: {d['cells']}")
        print(f"  pick btns  : {d['pickBtns']}")
    print(f"  next-pair btn: pos={d['nextPairPosition']} {d['nextPairBtn']}")


with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, w, h in VIEWPORTS:
        for mode, url in [("pairwise", f"{BASE}/arena"), ("kwise", f"{BASE}/arena?set=kwise")]:
            # Fresh context per page: no cookie/localStorage bleed between runs.
            ctx = browser.new_context(
                viewport={"width": w, "height": h},
                device_scale_factor=2,
                is_mobile=True,
                has_touch=True,
            )
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(2500)
            d = page.evaluate(PROBE)
            show(f"{mode} @ {name}", d)
            page.screenshot(path=str(OUT / f"{mode}-{name}.png"), full_page=False)
            page.screenshot(path=str(OUT / f"{mode}-{name}-full.png"), full_page=True)

            # Post-vote reveal: vote, then check the Next-pair button reachability.
            if mode == "pairwise":
                try:
                    page.click('.vote-btn[data-winner="a"]', timeout=4000)
                    page.wait_for_timeout(1800)
                    d2 = page.evaluate(PROBE)
                    print("  --- after vote ---")
                    print(f"    next-pair btn: pos={d2['nextPairPosition']} {d2['nextPairBtn']}")
                    print(f"    docHeight={d2['docHeight']} viewportH={d2['viewport']['vh']}")
                    page.screenshot(path=str(OUT / f"postvote-{name}.png"), full_page=False)
                except Exception as e:
                    print(f"    (vote click failed: {e})")
            ctx.close()
    browser.close()
print(f"\nscreenshots -> {OUT}")
