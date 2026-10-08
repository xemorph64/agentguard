// 3D network explorer (lazy-loaded). Same flow layout as 2D on the ground plane; the third
// dimension is risk — suspicious accounts rise out of the network. Money travels as particles
// along arcs. Only used where spatial exploration of a large network helps.
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/OrbitControls.js";
import { layoutGraph } from "./layout.js";
import { esc, inr, reducedMotion, STATE_LABEL } from "../core.js";

const HEX = { settled: 0x5ccbb0, held: 0xe8b45a, paused: 0x9aa6e0, blocked: 0xec5a4f, victim: 0x8fb4d6, neutral: 0x7f8792, accent: 0x63d2e0, surface: 0x1a1f27, bg: 0x0b0f14 };
const STATUS = { normal: HEX.neutral, victim: HEX.victim, watch: HEX.held, suspicious: HEX.blocked, flagged: HEX.blocked };
const SEV = { settled: 0, held: 1, paused: 2, blocked: 3 };
const STOP = { settled: 1, held: 1, paused: 0.5, blocked: 0.6 };
const S = 0.55;            // world scale from 2D layout units
const RISE = 2.2;          // elevation per risk point

export class Graph3D {
  constructor(el, { onSelect } = {}) {
    this.el = el; this.onSelect = onSelect;
    this.filters = { devices: "evidence", minRisk: 0, states: null };
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(2, devicePixelRatio));
    this.renderer.setClearColor(HEX.bg);
    el.append(this.renderer.domElement);
    this.renderer.domElement.setAttribute("aria-label", "3D network explorer");
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(HEX.bg, 900, 2600);
    this.camera = new THREE.PerspectiveCamera(50, 1, 1, 6000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true; this.controls.dampingFactor = 0.08; this.controls.screenSpacePanning = true;
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const key = new THREE.DirectionalLight(0xffffff, 1.1); key.position.set(300, 800, 400); this.scene.add(key);
    this.root = new THREE.Group(); this.scene.add(this.root);
    this.grid = new THREE.GridHelper(4000, 50, 0x232a35, 0x161b23); this.scene.add(this.grid);
    this.ray = new THREE.Raycaster(); this.mouse = new THREE.Vector2();
    this.tip = document.createElement("div"); this.tip.className = "tooltip"; this.tip.hidden = true; el.append(this.tip);
    const ui = document.createElement("div");
    ui.className = "graph-tools";
    ui.innerHTML = `<div class="grp"><button type="button" title="Reset camera" aria-label="Reset camera"><svg class="ic"><use href="#i-fit"/></svg></button></div>`;
    ui.querySelector("button").onclick = () => this.resetCamera();
    el.append(ui);
    const lg = document.createElement("div");
    lg.className = "graph-legend";
    lg.innerHTML = `<span>Height = risk</span><span><i style="background:var(--settled)"></i>Settled</span><span><i style="background:var(--held)"></i>Held</span><span><i style="background:var(--paused)"></i>Paused</span><span><i style="background:var(--danger)"></i>Blocked</span>`;
    el.append(lg);
    this.pos = new Map(); this.meshes = new Map(); this.curves = new Map(); this.particles = [];
    const pg = new THREE.BufferGeometry();
    this.pPos = new Float32Array(3 * 1200); this.pCol = new Float32Array(3 * 1200);
    pg.setAttribute("position", new THREE.BufferAttribute(this.pPos, 3));
    pg.setAttribute("color", new THREE.BufferAttribute(this.pCol, 3));
    this.points = new THREE.Points(pg, new THREE.PointsMaterial({ size: 7, vertexColors: true, transparent: true, opacity: 0.95, sizeAttenuation: true, depthWrite: false }));
    this.scene.add(this.points);
    this._bind();
    this.ro = new ResizeObserver(() => this._resize()); this.ro.observe(el);
    this._resize();
    this.running = true;
    this._loop();
  }

  setFilters(f) { Object.assign(this.filters, f); if (this.data) this._build(false); }
  setData(graph) { this.data = graph; this._build(!this.fitted); this.fitted = true; }

  _build(fit) {
    const g = this.data;
    this.pos = layoutGraph(g.nodes, g.edges, this.pos);
    for (const o of [...this.root.children]) { this.root.remove(o); o.geometry?.dispose(); o.material?.dispose?.(); }
    this.meshes.clear(); this.curves.clear(); this.particles = [];
    const f = this.filters;
    const byId = new Map(g.nodes.map(n => [n.id, n]));
    const maxAmt = Math.max(1, ...g.edges.filter(e => e.type === "payment").map(e => e.amount));
    const vol = n => (n.metadata?.attempted_in || 0) + (n.metadata?.attempted_out || 0);
    const maxVol = Math.max(1, ...g.nodes.map(vol));
    const visible = new Set();
    const edgeViews = [];
    for (const e of g.edges) {
      if (e.type !== "payment") continue;
      const a = byId.get(e.source), b = byId.get(e.target);
      if (f.minRisk && Math.max(a.risk, b.risk) < f.minRisk) continue;
      if (f.states && !e.transactions.some(t => f.states.has(t.state))) continue;
      edgeViews.push(e); visible.add(e.source); visible.add(e.target);
    }
    if (!f.minRisk && !f.states) g.nodes.forEach(n => n.type === "account" && visible.add(n.id));
    for (const e of g.edges) if (e.type === "device" && visible.has(e.source) && f.devices !== "none") {
      const d = byId.get(e.target);
      if (f.devices === "all" || d.metadata?.evidence) { visible.add(e.target); edgeViews.push(e); }
    }
    const p3 = id => { const p = this.pos.get(id), n = byId.get(id); return new THREE.Vector3(p.x * S, n.type === "device" ? 6 : 10 + n.risk * RISE, p.y * S); };
    const sphere = new THREE.SphereGeometry(1, 20, 14), box = new THREE.BoxGeometry(1, 1.6, 0.4);
    for (const id of visible) {
      const n = byId.get(id);
      const isAcc = n.type === "account";
      const col = isAcc ? STATUS[n.status] || HEX.neutral : n.status === "shared" ? HEX.held : HEX.paused;
      const mat = new THREE.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: isAcc && n.risk >= 60 ? 0.55 : 0.18, roughness: 0.5, metalness: 0.1 });
      const m = new THREE.Mesh(isAcc ? sphere : box, mat);
      const r = isAcc ? 6 + 9 * Math.sqrt(vol(n) / maxVol) : 7;
      m.scale.setScalar(r); m.position.copy(p3(id)); m.userData = { id, r };
      this.root.add(m); this.meshes.set(id, m);
      if (isAcc && n.risk >= 30) {   // stalk from the ground plane makes elevation readable
        const s = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(m.position.x, 0, m.position.z), m.position.clone()]),
          new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.35 }));
        this.root.add(s);
      }
      if (isAcc && (n.risk >= 60 || n.id === this.focusId)) this.root.add(this._label(n.label, m.position, r));
    }
    for (const e of edgeViews) {
      if (!visible.has(e.source) || !visible.has(e.target)) continue;
      const a = p3(e.source), b = p3(e.target);
      const mid = a.clone().lerp(b, 0.5); mid.y += 30 + a.distanceTo(b) * 0.18;
      const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
      const state = e.type === "device" ? null : e.state;
      const col = state ? HEX[state] : HEX.held;
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(28)),
        new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: e.type === "device" ? 0.35 : 0.3 + 0.5 * Math.sqrt(e.amount / maxAmt) }));
      this.root.add(line);
      if (state) this.curves.set(e.id, { curve, state, amount: e.amount, rate: 0.6 + 2 * Math.sqrt(e.amount / maxAmt) });
    }
    if (fit) this.resetCamera();
  }

  _label(text, pos, r) {
    const c = document.createElement("canvas"); const ctx = c.getContext("2d");
    ctx.font = "600 28px Plex Sans, sans-serif";
    const w = Math.ceil(ctx.measureText(text).width) + 20; c.width = w; c.height = 40;
    ctx.font = "600 28px Plex Sans, sans-serif"; ctx.fillStyle = "rgba(11,15,20,0.8)"; ctx.fillRect(0, 0, w, 40);
    ctx.fillStyle = "#eef1f5"; ctx.textBaseline = "middle"; ctx.fillText(text, 10, 21);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthWrite: false, transparent: true }));
    sp.scale.set(w / 2.6, 40 / 2.6, 1); sp.position.copy(pos); sp.position.y += r + 14;
    return sp;
  }

  resetCamera() {
    const box = new THREE.Box3();
    this.meshes.forEach(m => box.expandByPoint(m.position));
    if (box.isEmpty()) { this.camera.position.set(0, 500, 700); this.controls.target.set(0, 0, 0); return; }
    const c = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3()).length();
    this.controls.target.copy(c);
    this.camera.position.set(c.x - size * 0.15, c.y + size * 0.55, c.z + size * 0.85);
    this.grid.position.set(c.x, 0, c.z);
  }
  focusNode(id) {
    const m = this.meshes.get(id); if (!m) return;
    this.focusId = id;
    const off = this.camera.position.clone().sub(this.controls.target).setLength(260);
    this.controls.target.copy(m.position); this.camera.position.copy(m.position.clone().add(off));
  }

  _bind() {
    const c = this.renderer.domElement;
    let down = null;
    c.addEventListener("pointerdown", e => { down = { x: e.clientX, y: e.clientY }; });
    c.addEventListener("pointerup", e => {
      if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 4) {
        const hit = this._pick(e);
        if (hit) { this.focusNode(hit); this.onSelect?.(hit); }
      }
      down = null;
    });
    c.addEventListener("pointermove", e => {
      if (down) return;
      const hit = this._pick(e);
      if (hit !== this.hover) {
        if (this.hover) this.meshes.get(this.hover)?.scale.setScalar(this.meshes.get(this.hover).userData.r);
        this.hover = hit;
        if (hit) this.meshes.get(hit).scale.setScalar(this.meshes.get(hit).userData.r * 1.35);
        c.style.cursor = hit ? "pointer" : "grab";
      }
      if (!hit) { this.tip.hidden = true; return; }
      const n = this.data.nodes.find(x => x.id === hit);
      this.tip.innerHTML = n.type === "account"
        ? `<b>${esc(n.label)}</b><div class="row"><span>Risk</span><span>${Math.round(n.risk)}</span></div><div class="row"><span>In</span><span>${inr(n.metadata.in_amount)}</span></div><div class="row"><span>Out (attempted)</span><span>${inr(n.metadata.attempted_out)}</span></div>`
        : `<b>${esc(n.label)}</b><div class="row"><span>Accounts</span><span>${n.metadata.account_count}</span></div>`;
      const r = this.el.getBoundingClientRect();
      this.tip.hidden = false;
      this.tip.style.left = Math.min(e.clientX - r.left + 14, r.width - this.tip.offsetWidth - 8) + "px";
      this.tip.style.top = Math.min(e.clientY - r.top + 14, r.height - this.tip.offsetHeight - 8) + "px";
    });
    c.addEventListener("pointerleave", () => { this.tip.hidden = true; });
  }
  _pick(e) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(this.mouse, this.camera);
    const hit = this.ray.intersectObjects([...this.meshes.values()], false)[0];
    return hit?.object.userData.id || null;
  }
  _resize() {
    const r = this.el.getBoundingClientRect(); if (!r.width) return;
    this.renderer.setSize(r.width, r.height); this.camera.aspect = r.width / r.height; this.camera.updateProjectionMatrix();
  }

  _loop() {
    if (!this.running) return;
    this._raf = requestAnimationFrame(() => this._loop());
    const now = performance.now(), dt = Math.min(0.05, (now - (this._t || now)) / 1000); this._t = now;
    this.controls.update();
    if (!reducedMotion()) {
      for (const [id, c] of this.curves) if (Math.random() < c.rate * dt && this.particles.length < 1200) this.particles.push({ id, t: 0 });
      this.particles = this.particles.filter(p => { p.t += dt * 0.35; return p.t < STOP[this.curves.get(p.id)?.state] + 0.15; });
    }
    const v = new THREE.Vector3(), col = new THREE.Color();
    let i = 0;
    for (const p of this.particles) {
      const c = this.curves.get(p.id); if (!c) continue;
      c.curve.getPoint(Math.min(p.t, STOP[c.state]), v);
      col.setHex(HEX[c.state]);
      this.pPos.set([v.x, v.y, v.z], i * 3); this.pCol.set([col.r, col.g, col.b], i * 3); i++;
    }
    this.points.geometry.setDrawRange(0, i);
    this.points.geometry.attributes.position.needsUpdate = true; this.points.geometry.attributes.color.needsUpdate = true;
    this.renderer.render(this.scene, this.camera);
  }
  pause() { this.running = false; cancelAnimationFrame(this._raf); }
  resume() { if (!this.running) { this.running = true; this._t = null; this._loop(); } this._resize(); }
}
