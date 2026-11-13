// SPDX-License-Identifier: Apache-2.0
// The overlap check of tests/test_viewer_layout.py, run in the page with the
// sibling groups (area -> selectors). Returns one line per problem found.
(groups) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity) > 0;
  };
  const name = (el) => (el.getAttribute('aria-label') || el.className || el.tagName).toString().slice(0, 40)
    + ' "' + (el.textContent || '').trim().slice(0, 30) + '"';
  const out = [];
  for (const [area, selectors] of Object.entries(groups)) {
    const els = [];
    for (const sel of selectors) for (const el of document.querySelectorAll(sel)) if (visible(el) && !els.includes(el)) els.push(el);
    for (let i = 0; i < els.length; i++) for (let j = i + 1; j < els.length; j++) {
      const a = els[i], b = els[j];
      if (a.contains(b) || b.contains(a)) continue;
      const p = a.getBoundingClientRect(), q = b.getBoundingClientRect();
      const w = Math.min(p.right, q.right) - Math.max(p.left, q.left);
      const h = Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top);
      if (w > 0.5 && h > 0.5) out.push(`${area}: ${name(a)} overlaps ${name(b)} by ${w.toFixed(1)}x${h.toFixed(1)}`);
    }
  }
  // Nothing sticks out of the top bar, the stage chips stay inside the 3D view.
  const bar = document.querySelector('.topbar').getBoundingClientRect();
  for (const el of document.querySelectorAll('.topbar .crumb > *, .topbar .top-actions > *, .topbar .brand, .topbar details.settings')) {
    const r = el.getBoundingClientRect();
    if (visible(el) && (r.left < bar.left - 0.5 || r.right > bar.right + 0.5)) out.push(`top bar: ${name(el)} sticks out (${r.left.toFixed(0)}..${r.right.toFixed(0)} of ${bar.right.toFixed(0)})`);
  }
  const view = document.querySelector('.viewport');
  if (view) {
    const v = view.getBoundingClientRect();
    for (const el of view.querySelectorAll('.stage-chips > *, .stage-chips .chip-end > *, .legend')) {
      const r = el.getBoundingClientRect();
      if (visible(el) && (r.left < v.left - 0.5 || r.right > v.right + 0.5)) out.push(`stage: ${name(el)} leaves the 3D view`);
    }
  }
  // Left panel: no sideways overflow, no text starting left of the panel.
  const panel = document.querySelector('.workspace > .panel');
  if (panel && visible(panel)) {
    if (panel.scrollWidth > panel.clientWidth + 0.5) out.push(`left panel: scrollWidth ${panel.scrollWidth} > clientWidth ${panel.clientWidth}`);
    const edge = panel.getBoundingClientRect().left;
    const walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.textContent.trim() || !visible(node.parentElement)) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (r.width > 0 && r.left < edge - 0.5) { out.push(`left panel: "${node.textContent.trim().slice(0, 30)}" starts ${(edge - r.left).toFixed(1)}px left of the panel`); break; }
      }
    }
    // Rows inside the panel: their own children never collide.
    for (const row of panel.querySelectorAll('.eye, .swatch, .seg-field, .slider-field, summary, .pred-empty-row')) {
      const kids = [...row.children].filter(visible);
      for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
        const p = kids[i].getBoundingClientRect(), q = kids[j].getBoundingClientRect();
        const w = Math.min(p.right, q.right) - Math.max(p.left, q.left);
        const h = Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top);
        if (w > 0.5 && h > 0.5) out.push(`left panel row: ${name(kids[i])} overlaps ${name(kids[j])}`);
      }
      const rr = row.getBoundingClientRect(), pr = panel.getBoundingClientRect();
      if (rr.right > pr.right + 0.5) out.push(`left panel row: ${name(row)} is wider than the panel`);
    }
    for (const btn of panel.querySelectorAll('.seg button')) {
      if (!visible(btn)) continue;
      const range = document.createRange();
      range.selectNodeContents(btn);
      const rects = [...range.getClientRects()].filter((r) => r.width > 1 && r.height > 1);
      if (rects.length > 1) {
        out.push(`left panel seg button: "${(btn.textContent || '').trim()}" wrapped onto ${rects.length} lines`);
      }
    }
  }
  return out;
}
