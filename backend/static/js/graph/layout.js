// Flow layout: money reads left → right. Accounts are layered by their position in the payment
// flow (sources left, sinks right), refined with a small force pass, then connected components
// are shelf-packed. Positions are keyed by id and reused, so data refreshes never reshuffle.

const XS = 250;          // horizontal distance between flow layers
const YS = 74;           // vertical spacing inside a layer

export function layoutGraph(nodes, edges, prev = new Map()) {
  const byId = new Map(nodes.map(n => [n.id, n]));
  const pay = edges.filter(e => e.type === "payment" && byId.has(e.source) && byId.has(e.target));
  const dev = edges.filter(e => e.type === "device" && byId.has(e.source) && byId.has(e.target));

  // connected components over payments + device links
  const parent = new Map(nodes.map(n => [n.id, n.id]));
  const find = x => { while (parent.get(x) !== x) { parent.set(x, parent.get(parent.get(x))); x = parent.get(x); } return x; };
  for (const e of [...pay, ...dev]) { const a = find(e.source), b = find(e.target); if (a !== b) parent.set(a, b); }
  const comps = new Map();
  for (const n of nodes) { const r = find(n.id); if (!comps.has(r)) comps.set(r, []); comps.get(r).push(n.id); }

  const out = new Map();
  const fresh = [];
  let keptBottom = -Infinity, keptLeft = Infinity;
  for (const ids of comps.values()) {
    const known = ids.filter(id => prev.has(id));
    if (known.length === ids.length) {          // untouched component: keep exactly where it was
      for (const id of ids) out.set(id, { ...prev.get(id) });
      for (const id of ids) { keptBottom = Math.max(keptBottom, prev.get(id).y); keptLeft = Math.min(keptLeft, prev.get(id).x); }
      continue;
    }
    const pos = layoutComponent(ids, byId, pay, dev, prev);
    if (known.length) {                          // grew: keep the old part anchored
      for (const id of ids) out.set(id, pos.get(id));
      for (const id of ids) keptBottom = Math.max(keptBottom, pos.get(id).y);
    } else fresh.push(pos);
  }

  // shelf-pack fresh components (largest first) below anything kept
  fresh.sort((a, b) => b.size - a.size);
  const boxes = fresh.map(pos => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of pos.values()) { x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y); }
    return { pos, x0, y0, w: x1 - x0 + 200, h: y1 - y0 + 150 };
  });
  const maxW = Math.max(1500, ...boxes.map(b => b.w));
  let cx = 0, cy = keptBottom > -Infinity ? keptBottom + 220 : 0, rowH = 0;
  const left = keptLeft < Infinity ? keptLeft : 0;
  for (const b of boxes) {
    if (cx > 0 && cx + b.w > maxW) { cx = 0; cy += rowH; rowH = 0; }
    for (const [id, p] of b.pos) out.set(id, { x: left + cx + (p.x - b.x0), y: cy + (p.y - b.y0) });
    cx += b.w; rowH = Math.max(rowH, b.h);
  }
  return out;
}

function layoutComponent(ids, byId, pay, dev, prev) {
  const set = new Set(ids);
  const accounts = ids.filter(id => byId.get(id).type === "account");
  const devices = ids.filter(id => byId.get(id).type !== "account");
  const P = pay.filter(e => set.has(e.source));
  const D = dev.filter(e => set.has(e.source));

  // --- flow rank (Kahn; nodes left in cycles take one more than their ranked predecessors)
  const indeg = new Map(accounts.map(a => [a, 0])), succ = new Map(accounts.map(a => [a, []])), pred = new Map(accounts.map(a => [a, []]));
  for (const e of P) { if (e.source === e.target) continue; indeg.set(e.target, indeg.get(e.target) + 1); succ.get(e.source).push(e.target); pred.get(e.target).push(e.source); }
  const rank = new Map();
  const q = accounts.filter(a => indeg.get(a) === 0);
  q.forEach(a => rank.set(a, 0));
  while (q.length) {
    const u = q.shift();
    for (const v of succ.get(u)) {
      rank.set(v, Math.max(rank.get(v) ?? 0, rank.get(u) + 1));
      indeg.set(v, indeg.get(v) - 1);
      if (indeg.get(v) === 0) q.push(v);
    }
  }
  for (const a of accounts) if (!rank.has(a)) rank.set(a, Math.max(0, ...pred.get(a).map(p => (rank.get(p) ?? -1) + 1)));

  // --- initial placement: layers, ordered by predecessor barycenter
  const layers = new Map();
  for (const a of accounts) { const r = rank.get(a); if (!layers.has(r)) layers.set(r, []); layers.get(r).push(a); }
  const pos = new Map();
  for (const r of [...layers.keys()].sort((a, b) => a - b)) {
    const L = layers.get(r);
    const bary = a => { const ps = pred.get(a).filter(p => pos.has(p)); return ps.length ? ps.reduce((s, p) => s + pos.get(p).y, 0) / ps.length : 0; };
    L.sort((a, b) => bary(a) - bary(b) || a.localeCompare(b));
    L.forEach((a, i) => pos.set(a, prev.get(a) ? { ...prev.get(a) } : { x: r * XS, y: (i - (L.length - 1) / 2) * YS }));
  }
  for (const d of devices) {
    if (prev.get(d)) { pos.set(d, { ...prev.get(d) }); continue; }
    const users = D.filter(e => e.target === d).map(e => pos.get(e.source)).filter(Boolean);
    const mx = users.length ? users.reduce((s, p) => s + p.x, 0) / users.length : 0;
    const my = users.length ? Math.max(...users.map(p => p.y)) : 0;
    pos.set(d, { x: mx - 40, y: my + 80 });
  }
  if (ids.every(id => prev.has(id))) return pos;

  // --- force refinement (small components only need a few hundred cheap iterations)
  const arr = ids.map(id => ({ id, ...pos.get(id), vx: 0, vy: 0, acc: byId.get(id).type === "account", fixed: prev.has(id) }));
  const idx = new Map(arr.map((n, i) => [n.id, i]));
  const springs = [...P.map(e => [idx.get(e.source), idx.get(e.target), XS * 0.9, 0.02]),
                   ...D.map(e => [idx.get(e.source), idx.get(e.target), 70, 0.05])];
  const iters = arr.length > 160 ? 120 : 260;
  for (let it = 0; it < iters; it++) {
    const cool = 1 - it / iters;
    for (let i = 0; i < arr.length; i++) {
      const a = arr[i];
      for (let j = i + 1; j < arr.length; j++) {
        const b = arr[j];
        let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 > 90000) continue;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        const f = 2400 / d2;
        const d = Math.sqrt(d2);
        // repel mostly vertically between same-layer accounts so layers stay readable
        const fx = (a.acc && b.acc ? 0.25 : 1) * f * dx / d, fy = f * dy / d;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
    }
    for (const [i, j, L, k] of springs) {
      const a = arr[i], b = arr[j];
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
      const f = (d - L) * k;
      a.vx += f * dx / d * 0.3; a.vy += f * dy / d; b.vx -= f * dx / d * 0.3; b.vy -= f * dy / d;
    }
    for (const n of arr) {
      if (n.acc) n.vx += (rank.get(n.id) * XS - n.x) * 0.12;   // stay in your flow layer
      n.vy += -n.y * 0.002;
      if (n.fixed) { n.vx = n.vy = 0; continue; }
      const sp = Math.hypot(n.vx, n.vy), cap = 30 * cool + 2;
      if (sp > cap) { n.vx *= cap / sp; n.vy *= cap / sp; }
      n.x += n.vx; n.y += n.vy; n.vx *= 0.55; n.vy *= 0.55;
    }
  }
  for (const n of arr) pos.set(n.id, { x: n.x, y: n.y });
  return pos;
}


