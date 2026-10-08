// Canvas money-flow graph. The backend graph is the source of truth: this only lays it out,
// draws it, and reports what the user points at. Money is drawn as particles travelling along
// directed edges; what happened to it (settled / held / paused / blocked) decides where they stop.

import { layoutGraph } from "./layout.js";
import { inrShort, inr, esc, reducedMotion, STATE_LABEL, time } from "../core.js";

const L = (l, c, h) => (a = 1) => `oklch(${l} ${c} ${h} / ${a})`;
export const PAL = {
  bg: L(0.135, 0.011, 250), surface: L(0.215, 0.014, 250), line: L(0.42, 0.016, 250),
  text: L(0.945, 0.006, 250), text2: L(0.79, 0.012, 250), text3: L(0.62, 0.014, 250),
  accent: L(0.82, 0.105, 205), settled: L(0.79, 0.1, 172), held: L(0.82, 0.13, 78),
  paused: L(0.74, 0.085, 268), blocked: L(0.67, 0.2, 26), victim: L(0.76, 0.055, 238), neutral: L(0.6, 0.014, 250),
};
const STATE_COLOR = { settled: PAL.settled, held: PAL.held, paused: PAL.paused, blocked: PAL.blocked };
const STATUS_COLOR = { normal: PAL.neutral, victim: PAL.victim, watch: PAL.held, suspicious: PAL.blocked, flagged: PAL.blocked };
const SEV = { settled: 0, held: 1, paused: 2, blocked: 3 };
const STOP_AT = { settled: 1, held: 1, paused: 0.5, blocked: 0.6 };   // where money stops along the edge

export class Graph2D {
  constructor(container, { minimap = false } = {}) {
    this.el = container;
    this.canvas = document.createElement("canvas");
    this.canvas.tabIndex = 0;
    this.canvas.setAttribute("role", "img");
    this.canvas.setAttribute("aria-label", "Money-flow graph. Use the inspector panel for a text view.");
    this.tip = document.createElement("div");
    this.tip.className = "tooltip"; this.tip.hidden = true;
    container.append(this.canvas, this.tip);
    this.ctx = this.canvas.getContext("2d");
    this.cam = { x: 0, y: 0, k: 1 };
    this.pos = new Map();
    this.nodes = []; this.edges = []; this.nodeById = new Map(); this.edgeById = new Map();
    this.reveal = null; this.revealTs = null; this.riskOverride = null;
    this.highlight = null; this.follow = null; this.sel = null; this.hover = null; this.focus = null;
    this.filters = { devices: "evidence", minRisk: 0, states: null };
    this.particles = []; this.pulses = []; this.handlers = {};
    this.view = new Map(); this.visible = new Set();
    this.dpr = 1; this.w = 0; this.h = 0; this.dirty = true; this.camAnim = null;
    this.reduced = reducedMotion();
    this._bind();
    this.ro = new ResizeObserver(() => this._resize());
    this.ro.observe(container);
    this._resize();
    this._raf = requestAnimationFrame(t => this._frame(t));
  }

  on(ev, fn) { this.handlers[ev] = fn; return this; }
  emit(ev, v) { this.handlers[ev]?.(v); }

  // ------------------------------------------------------------ data
  setData(graph, { fit = false, focus } = {}) {
    const nodes = graph?.nodes || [], edges = graph?.edges || [];
    this.pos = layoutGraph(nodes, edges, this.pos);
    this.nodes = nodes; this.edges = edges;
    this.nodeById = new Map(nodes.map(n => [n.id, n]));
    this.edgeById = new Map(edges.map(e => [e.id, e]));
    const vol = n => (n.metadata?.attempted_in || 0) + (n.metadata?.attempted_out || 0);
    this.maxVol = Math.max(1, ...nodes.filter(n => n.type === "account").map(vol));
    this.maxAmt = Math.max(1, ...edges.filter(e => e.type === "payment").map(e => e.amount));
    for (const n of nodes) n._r = n.type === "account" ? 9 + 13 * Math.sqrt(vol(n) / this.maxVol) : 7;
    if (focus !== undefined) this.focus = focus;
    if (this.sel && !(this.sel.type === "node" ? this.nodeById.has(this.sel.id) : this.edgeById.has(this.sel.id))) this.sel = null;
    this.particles = this.particles.filter(p => this.edgeById.has(p.edge));
    this._computeVisible();
    if (fit) this.fit(false);
    this.dirty = true;
  }

  setReveal(txnSet, ts = null) { this.reveal = txnSet; this.revealTs = ts; this._computeVisible(); this.dirty = true; }
  setRiskOverride(map) { this.riskOverride = map; this.dirty = true; }
  setHighlight(h) { this.highlight = h && (h.nodes?.length || h.edges?.length) ? { nodes: new Set(h.nodes || []), edges: new Set(h.edges || []) } : null; this.dirty = true; }
  setFollow(f) { this.follow = f ? { nodes: new Set(f.nodes), edges: new Set(f.edges) } : null; this.particles = []; this.dirty = true; }
  setFilters(f) { Object.assign(this.filters, f); this._computeVisible(); this.dirty = true; }
  setFocus(id) { this.focus = id; this.dirty = true; }
  select(sel, { center = false } = {}) {
    this.sel = sel; this.dirty = true;
    if (center && sel) {
      const ids = sel.type === "node" ? [sel.id] : [this.edgeById.get(sel.id)?.source, this.edgeById.get(sel.id)?.target];
      this.fit(true, ids.filter(Boolean), 1.25);
    }
  }

  _computeVisible() {
    const f = this.filters, rev = this.reveal;
    const ve = new Map();   // edgeId -> view {amount, count, state}
    const nodeVisible = new Set();
    for (const e of this.edges) {
      if (e.type !== "payment") continue;
      let txs = e.transactions;
      if (rev) txs = txs.filter(t => rev.has(t.id));
      if (!txs.length) continue;
      if (f.states && !txs.some(t => f.states.has(t.state))) continue;
      const a = this.nodeById.get(e.source), b = this.nodeById.get(e.target);
      if (f.minRisk && Math.max(a?.risk || 0, b?.risk || 0) < f.minRisk) continue;
      const state = txs.reduce((s, t) => SEV[t.state] > SEV[s] ? t.state : s, "settled");
      ve.set(e.id, { amount: txs.reduce((s, t) => s + t.amount, 0), count: txs.length, state, last: txs[txs.length - 1] });
      nodeVisible.add(e.source); nodeVisible.add(e.target);
    }
    if (!rev && !f.minRisk && !f.states) for (const n of this.nodes) if (n.type === "account") nodeVisible.add(n.id);
    for (const e of this.edges) {
      if (e.type !== "device" || !nodeVisible.has(e.source) || f.devices === "none") continue;
      const d = this.nodeById.get(e.target);
      if (!d || (f.devices === "evidence" && !d.metadata?.evidence)) continue;
      if (rev && !e.registered && this.revealTs != null && (e.first_ts ?? 0) > this.revealTs + 1) continue;
      ve.set(e.id, { device: true });
      nodeVisible.add(e.target);
    }
    this.view = ve; this.visible = nodeVisible;
  }

  visibleIds() { return [...this.visible]; }

  // ------------------------------------------------------------ camera
  _resize() {
    const r = this.el.getBoundingClientRect();
    if (!r.width || !r.height) return;
    this.dpr = Math.min(2, devicePixelRatio || 1);
    this.w = r.width; this.h = r.height;
    this.canvas.width = Math.round(r.width * this.dpr); this.canvas.height = Math.round(r.height * this.dpr);
    if (!this._fitted && this.nodes.length) this.fit(false);
    this.dirty = true;
  }
  toScreen(x, y) { return [(x - this.cam.x) * this.cam.k + this.w / 2, (y - this.cam.y) * this.cam.k + this.h / 2]; }
  toWorld(sx, sy) { return [(sx - this.w / 2) / this.cam.k + this.cam.x, (sy - this.h / 2) / this.cam.k + this.cam.y]; }

  fit(animate = true, ids = null, maxK = 1.6) {
    const list = (ids && ids.length ? ids : [...this.visible]).map(id => this.pos.get(id)).filter(Boolean);
    if (!list.length || !this.w) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of list) { x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y); }
    // room for the filter chips above and the legend below
    const padX = 90, padTop = 70, padBottom = 80;
    const k = Math.min(maxK, (this.w - padX * 2) / Math.max(1, x1 - x0), (this.h - padTop - padBottom) / Math.max(1, y1 - y0));
    const kk = Math.max(0.12, k);
    const to = { x: (x0 + x1) / 2, y: (y0 + y1) / 2 + (padBottom - padTop) / 2 / kk, k: kk };
    this._fitted = true;
    this.animateCam(to, animate);
  }
  animateCam(to, animate = true) {
    if (!animate || this.reduced) { this.cam = to; this.camAnim = null; this.dirty = true; return; }
    this.camAnim = { from: { ...this.cam }, to, t0: performance.now(), dur: 650 };
  }
  zoomBy(f, sx = this.w / 2, sy = this.h / 2) {
    const [wx, wy] = this.toWorld(sx, sy);
    const k = Math.min(4, Math.max(0.1, this.cam.k * f));
    this.cam = { k, x: wx - (sx - this.w / 2) / k, y: wy - (sy - this.h / 2) / k };
    this.camAnim = null; this.dirty = true;
  }

  // ------------------------------------------------------------ geometry
  _geom(e) {
    const a = this.pos.get(e.source), b = this.pos.get(e.target);
    if (!a || !b) return null;
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
    const rev = this.edgeById.has(`${e.target}>${e.source}`);
    const bend = e.type === "device" ? 0 : rev ? 0.16 : 0.06;
    const cx = (a.x + b.x) / 2 - dy * bend, cy = (a.y + b.y) / 2 + dx * bend;
    const ra = (this.nodeById.get(e.source)?._r || 8) + 2, rb = (this.nodeById.get(e.target)?._r || 8) + 5;
    return { a, b, cx, cy, len, t0: Math.min(0.4, ra / len), t1: Math.max(0.6, 1 - rb / len) };
  }
  static at(g, t) {
    const u = 1 - t;
    return [u * u * g.a.x + 2 * u * t * g.cx + t * t * g.b.x, u * u * g.a.y + 2 * u * t * g.cy + t * t * g.b.y];
  }
  static tangent(g, t) {
    const x = 2 * (1 - t) * (g.cx - g.a.x) + 2 * t * (g.b.x - g.cx), y = 2 * (1 - t) * (g.cy - g.a.y) + 2 * t * (g.b.y - g.cy);
    const d = Math.hypot(x, y) || 1; return [x / d, y / d];
  }

  // ------------------------------------------------------------ hit testing
  _hit(sx, sy) {
    const [wx, wy] = this.toWorld(sx, sy);
    let best = null, bd = Infinity;
    for (const id of this.visible) {
      const n = this.nodeById.get(id), p = this.pos.get(id);
      if (!n || !p) continue;
      const d = Math.hypot(p.x - wx, p.y - wy), r = n._r + 5 / this.cam.k;
      if (d < r && d < bd) { bd = d; best = { type: "node", id }; }
    }
    if (best) return best;
    const tol = 7 / this.cam.k;
    for (const [id, v] of this.view) {
      if (v.device) continue;
      const g = this._geom(this.edgeById.get(id));
      if (!g) continue;
      let prev = Graph2D.at(g, g.t0);
      for (let i = 1; i <= 16; i++) {
        const cur = Graph2D.at(g, g.t0 + (g.t1 - g.t0) * i / 16);
        const d = segDist(wx, wy, prev, cur);
        if (d < tol && d < bd) { bd = d; best = { type: "edge", id }; }
        prev = cur;
      }
    }
    return best;
  }

  // ------------------------------------------------------------ input
  _bind() {
    const c = this.canvas;
    let down = null;
    c.addEventListener("pointerdown", e => {
      c.setPointerCapture(e.pointerId);
      const hit = this._hit(e.offsetX, e.offsetY);
      down = { x: e.offsetX, y: e.offsetY, cam: { ...this.cam }, hit, moved: false };
      this.camAnim = null;
    });
    c.addEventListener("pointermove", e => {
      if (down) {
        const dx = e.offsetX - down.x, dy = e.offsetY - down.y;
        if (Math.hypot(dx, dy) > 3) down.moved = true;
        if (!down.moved) return;
        if (down.hit?.type === "node") {
          const [wx, wy] = this.toWorld(e.offsetX, e.offsetY);
          this.pos.set(down.hit.id, { x: wx, y: wy });
        } else {
          c.classList.add("dragging");
          this.cam = { ...down.cam, x: down.cam.x - dx / this.cam.k, y: down.cam.y - dy / this.cam.k };
        }
        this.dirty = true; this.tip.hidden = true;
        return;
      }
      const hit = this._hit(e.offsetX, e.offsetY);
      const key = hit ? hit.type + hit.id : null;
      if (key !== (this.hover ? this.hover.type + this.hover.id : null)) { this.hover = hit; this.dirty = true; this.emit("hover", hit); }
      c.classList.toggle("pointing", !!hit);
      this._tooltip(hit, e.offsetX, e.offsetY);
    });
    c.addEventListener("pointerup", e => {
      c.classList.remove("dragging");
      if (down && !down.moved) { this.sel = down.hit; this.dirty = true; this.emit("select", down.hit); }
      down = null;
    });
    c.addEventListener("pointerleave", () => { if (!down) { this.hover = null; this.tip.hidden = true; this.dirty = true; } });
    c.addEventListener("dblclick", e => { const hit = this._hit(e.offsetX, e.offsetY); if (hit?.type === "node") this.emit("focus", hit.id); });
    c.addEventListener("wheel", e => { e.preventDefault(); this.zoomBy(Math.exp(-e.deltaY * 0.0015), e.offsetX, e.offsetY); this.tip.hidden = true; }, { passive: false });
    c.addEventListener("keydown", e => {
      if (e.key === "+" || e.key === "=") this.zoomBy(1.2);
      else if (e.key === "-") this.zoomBy(1 / 1.2);
      else if (e.key === "0") this.fit();
      else if (e.key === "Escape") { this.sel = null; this.dirty = true; this.emit("select", null); }
      else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {   // step through accounts by risk
        const order = [...this.visible].map(id => this.nodeById.get(id)).filter(n => n?.type === "account").sort((a, b) => b.risk - a.risk);
        if (!order.length) return;
        const i = order.findIndex(n => n.id === this.sel?.id);
        const n = order[(i + (e.key === "ArrowRight" ? 1 : -1) + order.length) % order.length];
        this.select({ type: "node", id: n.id }, { center: true }); this.emit("select", this.sel);
      } else return;
      e.preventDefault();
    });
  }

  _tooltip(hit, x, y) {
    if (!hit) { this.tip.hidden = true; return; }
    let html = "";
    if (hit.type === "node") {
      const n = this.nodeById.get(hit.id), m = n.metadata || {};
      if (n.type === "account") {
        html = `<b>${esc(n.label)}</b><div class="row"><span>Risk</span><span>${Math.round(this._risk(n))}</span></div>
          <div class="row"><span>In</span><span>${inr(m.in_amount)}</span></div><div class="row"><span>Out</span><span>${inr(m.attempted_out)}</span></div>
          <div class="row"><span>Status</span><span>${esc(n.status)}</span></div>`;
      } else html = `<b>${esc(n.label)}</b><div class="row"><span>Accounts</span><span>${m.account_count}</span></div>`;
    } else {
      const e = this.edgeById.get(hit.id), v = this.view.get(hit.id);
      if (!e || !v) return;
      html = `<b>${esc(this.nodeById.get(e.source)?.label)} → ${esc(this.nodeById.get(e.target)?.label)}</b>
        <div class="row"><span>${v.count} payment${v.count > 1 ? "s" : ""}</span><span>${inr(v.amount)}</span></div>
        <div class="row"><span>Outcome</span><span>${STATE_LABEL[v.state]}</span></div>
        <div class="row"><span>Last</span><span>${time(v.last.ts)}</span></div>`;
    }
    this.tip.innerHTML = html;
    this.tip.hidden = false;
    const tw = this.tip.offsetWidth, th = this.tip.offsetHeight;
    this.tip.style.left = Math.min(x + 14, this.w - tw - 8) + "px";
    this.tip.style.top = Math.min(y + 14, this.h - th - 8) + "px";
  }

  // ------------------------------------------------------------ particles
  burst(edgeId, state) {
    const v = this.view.get(edgeId);
    if (!v || this.reduced) return;
    const e = this.edgeById.get(edgeId);
    const n = Math.round(4 + 6 * Math.sqrt((e?.amount || 1) / this.maxAmt));
    for (let i = 0; i < n; i++) this.particles.push({ edge: edgeId, t: -i * 0.07, sp: 0.55, state: state || v.state, burst: true, size: 2.6 });
    this.pulses.push({ id: e?.target, t: 0, color: STATE_COLOR[state || v.state] });
  }
  pulseNode(id, color = PAL.blocked) { if (!this.reduced) this.pulses.push({ id, t: 0, color }); }

  _spawnAmbient(dt) {
    if (this.reduced) return;
    const edges = [...this.view.entries()].filter(([, v]) => !v.device);
    const lod = edges.length > 220;
    for (const [id, v] of edges) {
      const inFocus = this.follow ? this.follow.edges.has(id) : this.highlight ? this.highlight.edges.has(id) : !lod;
      if (!inFocus && !(this.sel?.id === id)) continue;
      const rate = (this.follow ? 1.6 : 0.45) * (0.5 + Math.sqrt(v.amount / this.maxAmt));
      if (Math.random() < rate * dt) this.particles.push({ edge: id, t: 0, sp: 0.32 + Math.random() * 0.08, state: v.state, size: 1.6 + 1.6 * Math.sqrt(v.amount / this.maxAmt) });
    }
    if (this.particles.length > 900) this.particles.splice(0, this.particles.length - 900);
  }

  // ------------------------------------------------------------ render
  _risk(n) { return this.riskOverride?.has(n.id) ? this.riskOverride.get(n.id) : n.risk; }

  _frame(t) {
    this._raf = requestAnimationFrame(tt => this._frame(tt));
    const dt = Math.min(0.05, (t - (this._lt || t)) / 1000); this._lt = t;
    // hidden tab or a view that isn't on screen: don't spend frames on it
    if (!this.canvas.isConnected || document.hidden || !this.w || this.canvas.offsetParent === null) return;
    if (this.camAnim) {
      const a = this.camAnim, p = Math.min(1, (t - a.t0) / a.dur), e = 1 - Math.pow(1 - p, 4);
      this.cam = { x: a.from.x + (a.to.x - a.from.x) * e, y: a.from.y + (a.to.y - a.from.y) * e, k: a.from.k * Math.pow(a.to.k / a.from.k, e) };
      if (p >= 1) this.camAnim = null;
      this.dirty = true;
    }
    this._spawnAmbient(dt);
    const animating = this.particles.length || this.pulses.length;
    if (!this.dirty && !animating) return;
    for (const p of this.particles) p.t += p.sp * dt;
    this.particles = this.particles.filter(p => p.t < STOP_AT[p.state] + 0.25);
    for (const p of this.pulses) p.t += dt;
    this.pulses = this.pulses.filter(p => p.t < 1.1);
    this._draw();
    this.dirty = false;
  }

  _alpha(kind, id, e) {
    // dimming: follow > evidence highlight > hover neighbourhood
    if (this.follow) return (kind === "node" ? this.follow.nodes.has(id) : this.follow.edges.has(id)) ? 1 : 0.1;
    if (this.highlight) return (kind === "node" ? this.highlight.nodes.has(id) : this.highlight.edges.has(id)) ? 1 : 0.13;
    const h = this.hover?.type === "node" ? this.hover.id : null;
    if (h) {
      if (kind === "node") return id === h || this.view.has(`${id}>${h}`) || this.view.has(`${h}>${id}`) || this.view.has(`${id}~${h}`) || this.view.has(`${h}~${id}`) ? 1 : 0.25;
      return e.source === h || e.target === h ? 1 : 0.15;
    }
    return 1;
  }

  _draw() {
    const { ctx, cam } = this;
    const k = cam.k, s = Math.max(0.55, Math.min(1.5, k));
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = PAL.bg(); ctx.fillRect(0, 0, this.w, this.h);
    this._grid();
    const smallGraph = this.visible.size <= 40;

    // --- edges
    const labels = [];
    for (const [id, v] of this.view) {
      const e = this.edgeById.get(id), g = e && this._geom(e);
      if (!g) continue;
      const alpha = this._alpha("edge", id, e);
      const hot = this.sel?.id === id || this.hover?.id === id || (this.highlight?.edges.has(id)) || (this.follow?.edges.has(id));
      if (v.device) {
        const [ax, ay] = this.toScreen(...Graph2D.at(g, g.t0)), [bx, by] = this.toScreen(...Graph2D.at(g, g.t1));
        ctx.strokeStyle = PAL.held(0.5 * alpha); ctx.lineWidth = 1.2 * s; ctx.setLineDash([2 * s, 3 * s]);
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke(); ctx.setLineDash([]);
        continue;
      }
      const col = STATE_COLOR[v.state];
      const w = (1.1 + 4.2 * Math.sqrt(v.amount / this.maxAmt)) * s;
      const stop = v.state === "blocked" ? 0.6 : 1;
      const tEnd = v.state === "blocked" ? g.t0 + (g.t1 - g.t0) * stop : g.t1;
      // solid part: where money actually went
      this._curve(g, g.t0, tEnd, col((hot ? 0.95 : 0.55) * alpha), w);
      if (v.state === "blocked") {
        this._curve(g, tEnd + 0.04, g.t1, col(0.14 * alpha), Math.max(1, w * 0.5));
        const [cx, cy] = this.toScreen(...Graph2D.at(g, tEnd + 0.02)), [tx, ty] = Graph2D.tangent(g, tEnd);
        const L = 7 * s;
        ctx.strokeStyle = col(alpha); ctx.lineWidth = 2.2 * s; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(cx - ty * L, cy + tx * L); ctx.lineTo(cx + ty * L, cy - tx * L); ctx.stroke(); ctx.lineCap = "butt";
      } else {
        this._arrow(g, g.t1, col((hot ? 1 : 0.8) * alpha), 5.5 * s + w * 0.6);
      }
      if (v.state === "paused") {
        const [px, py] = this.toScreen(...Graph2D.at(g, 0.5)), [tx, ty] = Graph2D.tangent(g, 0.5);
        ctx.fillStyle = PAL.bg(alpha); ctx.beginPath(); ctx.arc(px, py, 7 * s, 0, 7); ctx.fill();
        ctx.strokeStyle = col(alpha); ctx.lineWidth = 2 * s;
        for (const o of [-2.2, 2.2]) { ctx.beginPath(); ctx.moveTo(px + tx * o * s - ty * 3.5 * s, py + ty * o * s + tx * 3.5 * s); ctx.lineTo(px + tx * o * s + ty * 3.5 * s, py + ty * o * s - tx * 3.5 * s); ctx.stroke(); }
      }
      const showLabel = hot || (alpha === 1 && (this.highlight || this.follow)) || (smallGraph && k >= 0.55) || k >= 1.1;
      if (showLabel && alpha > 0.5) labels.push({ g, v, hot });
    }

    // --- particles (money)
    for (const p of this.particles) {
      const v = this.view.get(p.edge); const e = this.edgeById.get(p.edge);
      if (!v || !e || p.t < 0) continue;
      const g = this._geom(e); if (!g) continue;
      const stop = STOP_AT[p.state];
      const tt = Math.min(p.t, stop);
      const fade = p.t > stop ? 1 - (p.t - stop) / 0.25 : 1;
      const [x, y] = this.toScreen(...Graph2D.at(g, g.t0 + (g.t1 - g.t0) * tt));
      const a = this._alpha("edge", p.edge, e) * fade;
      const col = STATE_COLOR[p.state];
      ctx.fillStyle = col(0.22 * a); ctx.beginPath(); ctx.arc(x, y, p.size * 2.4 * s, 0, 7); ctx.fill();
      ctx.fillStyle = (p.burst ? PAL.text : col)(a); ctx.beginPath(); ctx.arc(x, y, p.size * s, 0, 7); ctx.fill();
    }

    // --- nodes
    const nodeLabels = [];
    const order = [...this.visible].map(id => this.nodeById.get(id)).filter(Boolean).sort((a, b) => this._risk(a) - this._risk(b));
    for (const n of order) {
      const p = this.pos.get(n.id); if (!p) continue;
      const [x, y] = this.toScreen(p.x, p.y);
      if (x < -60 || y < -60 || x > this.w + 60 || y > this.h + 60) continue;
      const a = this._alpha("node", n.id);
      const r = n._r * k;
      const isSel = this.sel?.type === "node" && this.sel.id === n.id;
      if (n.type === "account") {
        const risk = this._risk(n);
        const status = this.riskOverride?.has(n.id) ? (risk >= 60 ? "suspicious" : risk >= 30 ? "watch" : n.status === "victim" ? "victim" : "normal") : n.status;
        const col = STATUS_COLOR[status] || PAL.neutral;
        ctx.fillStyle = PAL.surface(a); ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
        ctx.fillStyle = col(0.16 * a); ctx.fill();
        ctx.strokeStyle = col(a); ctx.lineWidth = (status === "suspicious" || status === "flagged" ? 2.4 : 1.6) * s; ctx.stroke();
        if (status === "flagged") { ctx.lineWidth = 1 * s; ctx.beginPath(); ctx.arc(x, y, r + 4 * s, 0, 7); ctx.stroke(); }
        if (n.metadata?.verified_merchant) { ctx.fillStyle = PAL.text3(a); ctx.font = `600 ${Math.max(9, r * 0.8)}px "Plex Sans"`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("M", x, y + 0.5); }
        if (risk >= 30 && r > 6) {
          ctx.font = `500 ${11 * Math.min(1.2, s)}px "Plex Mono"`; ctx.textAlign = "left"; ctx.textBaseline = "middle";
          const txt = String(Math.round(risk)); const tw = ctx.measureText(txt).width;
          const bx = x + r * 0.72, by = y - r * 0.72;
          ctx.fillStyle = PAL.bg(a); roundRect(ctx, bx - 2, by - 8, tw + 8, 15, 3); ctx.fill();
          ctx.strokeStyle = col(a * 0.8); ctx.lineWidth = 1; ctx.stroke();
          ctx.fillStyle = col(a); ctx.fillText(txt, bx + 2, by);
        }
      } else {
        const shared = n.status === "shared", col = shared ? PAL.held : n.status === "new" ? PAL.paused : PAL.neutral;
        const sz = 7.5 * Math.max(0.6, k);
        ctx.fillStyle = PAL.surface(a); roundRect(ctx, x - sz, y - sz * 1.3, sz * 2, sz * 2.6, 2.5 * s); ctx.fill();
        ctx.strokeStyle = col(a); ctx.lineWidth = 1.6 * s; ctx.stroke();
        ctx.fillStyle = col(a); ctx.fillRect(x - sz * 0.35, y + sz * 0.85, sz * 0.7, 1.4 * s);
      }
      if (n.id === this.focus) this._reticle(x, y, r + 9 * s, a);
      if (isSel || this.hover?.id === n.id) { ctx.strokeStyle = PAL.accent(a); ctx.lineWidth = 2 * s; ctx.beginPath(); ctx.arc(x, y, (n.type === "account" ? r : 10 * Math.max(0.6, k)) + 5 * s, 0, 7); ctx.stroke(); }
      const important = n.id === this.focus || isSel || this.hover?.id === n.id || this._risk(n) >= 60 || this.highlight?.nodes.has(n.id) || this.follow?.nodes.has(n.id);
      if (a > 0.5 && (important || (smallGraph && k >= 0.45) || k >= 0.85)) nodeLabels.push({ n, x, y: y + (n.type === "account" ? r : 12 * Math.max(0.6, k)) + 6, a, important });
    }

    // --- pulses: a ring expanding from a node when money lands or risk jumps
    for (const p of this.pulses) {
      const pos = this.pos.get(p.id), n = this.nodeById.get(p.id); if (!pos || !n) continue;
      const [x, y] = this.toScreen(pos.x, pos.y);
      const e = 1 - Math.pow(1 - p.t / 1.1, 3);
      ctx.strokeStyle = p.color((1 - e) * 0.8); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, n._r * k + 4 + e * 26, 0, 7); ctx.stroke();
    }

    // --- labels last so they sit above everything
    ctx.textBaseline = "top"; ctx.textAlign = "center";
    for (const L of nodeLabels) {
      ctx.font = `${L.important ? 600 : 500} 12px "Plex Sans"`;
      const t = L.n.label; const w = ctx.measureText(t).width;
      ctx.fillStyle = PAL.bg(0.82 * L.a); roundRect(ctx, L.x - w / 2 - 4, L.y - 2, w + 8, 17, 3); ctx.fill();
      ctx.fillStyle = (L.important ? PAL.text : PAL.text2)(L.a); ctx.fillText(t, L.x, L.y);
    }
    ctx.textBaseline = "middle";
    for (const { g, v, hot } of labels) {
      const [x, y] = this.toScreen(...Graph2D.at(g, 0.4));
      const t = `${inrShort(v.amount)}${v.count > 1 ? ` · ${v.count}` : ""}`;
      ctx.font = `500 11.5px "Plex Mono"`;
      const w = ctx.measureText(t).width;
      ctx.fillStyle = PAL.bg(0.9); roundRect(ctx, x - w / 2 - 5, y - 9, w + 10, 18, 3); ctx.fill();
      ctx.strokeStyle = STATE_COLOR[v.state](hot ? 0.9 : 0.4); ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = PAL.text(hot ? 1 : 0.85); ctx.fillText(t, x, y + 0.5);
    }
  }

  _grid() {
    const { ctx, cam } = this;
    const step = 80 * cam.k; if (step < 18) return;
    ctx.fillStyle = PAL.line(0.22);
    const [wx0, wy0] = this.toWorld(0, 0);
    const sx = ((-wx0 % 80) + 80) % 80 * cam.k, sy = ((-wy0 % 80) + 80) % 80 * cam.k;
    for (let x = sx; x < this.w; x += step) for (let y = sy; y < this.h; y += step) ctx.fillRect(x, y, 1.2, 1.2);
  }
  _curve(g, t0, t1, color, w) {
    if (t1 <= t0) return;
    const { ctx } = this;
    ctx.strokeStyle = color; ctx.lineWidth = w; ctx.beginPath();
    for (let i = 0; i <= 20; i++) {
      const [x, y] = this.toScreen(...Graph2D.at(g, t0 + (t1 - t0) * i / 20));
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.stroke();
  }
  _arrow(g, t, color, size) {
    const { ctx } = this;
    const [x, y] = this.toScreen(...Graph2D.at(g, t)), [tx, ty] = Graph2D.tangent(g, t);
    ctx.fillStyle = color; ctx.beginPath();
    ctx.moveTo(x + tx * 2, y + ty * 2);
    ctx.lineTo(x - tx * size - ty * size * 0.55, y - ty * size + tx * size * 0.55);
    ctx.lineTo(x - tx * size + ty * size * 0.55, y - ty * size - tx * size * 0.55);
    ctx.closePath(); ctx.fill();
  }
  _reticle(x, y, R, a) {
    const { ctx } = this;
    ctx.strokeStyle = PAL.accent(0.9 * a); ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.arc(x, y, R, 0, 7); ctx.stroke();
    for (let i = 0; i < 4; i++) {
      const ang = i * Math.PI / 2;
      ctx.beginPath(); ctx.moveTo(x + Math.cos(ang) * (R + 2), y + Math.sin(ang) * (R + 2)); ctx.lineTo(x + Math.cos(ang) * (R + 8), y + Math.sin(ang) * (R + 8)); ctx.stroke();
    }
  }

  destroy() { cancelAnimationFrame(this._raf); this.ro.disconnect(); this.canvas.remove(); this.tip.remove(); }
}

function segDist(px, py, [ax, ay], [bx, by]) {
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy || 1;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l2));
  return Math.hypot(px - ax - t * dx, py - ay - t * dy);
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

// ------------------------------------------------------------ follow the money (pure traversal of backend edges)
export function followPaths(graph, start, dir = "both", maxHops = 6) {
  const pay = graph.edges.filter(e => e.type === "payment");
  const nodes = new Set([start]), edges = new Set();
  const walk = (d) => {
    let frontier = [start]; const seen = new Set([start]);
    for (let h = 0; h < maxHops && frontier.length; h++) {
      const next = [];
      for (const n of frontier) for (const e of pay) {
        const from = d === "out" ? e.source : e.target, to = d === "out" ? e.target : e.source;
        if (from !== n) continue;
        edges.add(e.id); nodes.add(to);
        if (!seen.has(to)) { seen.add(to); next.push(to); }
      }
      frontier = next;
    }
  };
  if (dir !== "in") walk("out");
  if (dir !== "out") walk("in");
  for (const e of graph.edges) if (e.type === "device" && e.source === start) {
    const d = graph.nodes.find(n => n.id === e.target);
    if (d?.metadata?.evidence) { edges.add(e.id); nodes.add(e.target); }
  }
  return { nodes: [...nodes], edges: [...edges] };
}
