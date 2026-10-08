// Workbench: a graph canvas + inspector with the shared investigation interactions
// (select node/edge, transaction inspector, account panel, follow the money, signal → evidence,
// hop expansion). Investigate and Network both build on it.
import { api, esc, icon, inr, stateBlock, toast } from "./core.js";
import { Graph2D, followPaths } from "./graph/graph2d.js";
import { agentPanel, txnInspector, accountPanel, followSummary } from "./panels.js";

export class Workbench {
  constructor({ canvasWrap, inspector, onScope }) {
    this.wrap = canvasWrap;
    this.insp = inspector;
    this.onScope = onScope;              // (account, hops) → caller loads that scope
    this.graph = new Graph2D(canvasWrap);
    this.data = { nodes: [], edges: [] };
    this.ctx = null;                     // investigation context (focus, key_transaction, explanation, metrics)
    this.txCache = new Map();
    this.stack = [];                     // inspector history for "back"
    this.view = { kind: "case" };
    this.follow = null;                  // {start, dir}
    this.hops = null;
    this.caseRenderer = null;            // set by owner: (el) => void
    this.graph.on("select", sel => {
      if (!sel) return this.open({ kind: "case" }, false);
      if (sel.type === "node") this.openNode(sel.id);
      else this.openEdge(sel.id);
    });
    this.graph.on("focus", id => this.openNode(id));
    this._tools();
  }

  _tools() {
    const t = document.createElement("div");
    t.className = "graph-tools";
    t.innerHTML = `<div class="grp">
        <button type="button" data-z="in" title="Zoom in (+)" aria-label="Zoom in">${icon("plus")}</button>
        <button type="button" data-z="out" title="Zoom out (−)" aria-label="Zoom out">${icon("minus")}</button>
        <button type="button" data-z="fit" title="Fit to view (0)" aria-label="Fit to view">${icon("fit")}</button></div>`;
    t.querySelector('[data-z="in"]').onclick = () => this.graph.zoomBy(1.25);
    t.querySelector('[data-z="out"]').onclick = () => this.graph.zoomBy(0.8);
    t.querySelector('[data-z="fit"]').onclick = () => this.graph.fit();
    this.wrap.append(t);
    this.bar = document.createElement("div");
    this.bar.className = "graph-bar";
    this.wrap.append(this.bar);
    const lg = document.createElement("div");
    lg.className = "graph-legend";
    lg.innerHTML = `<span><i style="background:var(--settled)"></i>Settled</span><span><i style="background:var(--held)"></i>Held</span>
      <span><i style="background:var(--paused)"></i>Paused</span><span><i style="background:var(--danger)"></i>Blocked</span>
      <span><i class="node" style="border-color:var(--danger)"></i>Suspicious</span><span><i class="node" style="border-color:var(--victim)"></i>Victim</span>
      <span><i class="dev" style="border-color:var(--held)"></i>Shared phone</span>`;
    this.wrap.append(lg);
  }

  // ------------------------------------------------------------ data
  setGraph(graph, ctx, { fit = false } = {}) {
    this.data = graph;
    this.ctx = ctx;
    this.graph.setData(graph, { fit, focus: ctx?.focus ?? null });
    if (this.follow && !graph.nodes.some(n => n.id === this.follow.start)) this.clearFollow();
    this.renderBar();
  }
  nodesById() { return new Map(this.data.nodes.map(n => [n.id, n])); }

  renderBar() {
    const chips = [];
    if (this.scopeLabel) chips.push(`<span class="chip">${esc(this.scopeLabel)}<button type="button" data-x="scope" aria-label="Back to the full investigation">${icon("close")}</button></span>`);
    if (this.follow) {
      const n = this.nodesById().get(this.follow.start);
      chips.push(`<span class="chip follow">${icon("flow")}Following ${this.follow.dir === "both" ? "" : this.follow.dir + "bound "}money · ${esc(n?.label || "")}<button type="button" data-x="follow" aria-label="Stop following">${icon("close")}</button></span>`);
    }
    if (this.evidenceLabel) chips.push(`<span class="chip">Evidence: ${esc(this.evidenceLabel)}<button type="button" data-x="ev" aria-label="Clear evidence">${icon("close")}</button></span>`);
    this.bar.innerHTML = chips.join("") + (this.extraBar || "");
    this.bar.querySelector('[data-x="scope"]')?.addEventListener("click", () => this.onScope?.(null));
    this.bar.querySelector('[data-x="follow"]')?.addEventListener("click", () => this.clearFollow());
    this.bar.querySelector('[data-x="ev"]')?.addEventListener("click", () => this.showEvidence(null));
    this.onBar?.(this.bar);
  }

  // ------------------------------------------------------------ inspector navigation
  open(view, push = true) {
    if (push && this.view && JSON.stringify(this.view) !== JSON.stringify(view)) this.stack.push(this.view);
    this.view = view;
    if (view.kind !== "case") this.showEvidence(null, false);
    this.renderInspector();
  }
  back() { this.view = this.stack.pop() || { kind: "case" }; this.renderInspector(); if (this.view.kind === "case") this.graph.select(null); }

  header(crumb) {
    return `<div class="insp-h"><span class="crumb">${esc(crumb)}</span><div class="right">
      ${this.stack.length || this.view.kind !== "case" ? `<button class="btn sm ghost" type="button" data-nav="back">Back</button>` : ""}
      ${this.view.kind !== "case" ? `<button class="btn sm ghost icon" type="button" data-nav="close" aria-label="Close">${icon("close")}</button>` : ""}</div></div>`;
  }
  wireHeader() {
    this.insp.querySelector('[data-nav="back"]')?.addEventListener("click", () => this.back());
    this.insp.querySelector('[data-nav="close"]')?.addEventListener("click", () => { this.stack = []; this.graph.select(null); this.open({ kind: "case" }, false); });
  }

  renderInspector() {
    const v = this.view;
    if (v.kind === "case") {
      this.insp.innerHTML = this.header(this.caseTitle || "Case") + `<div id="caseBody"></div>`;
      this.wireHeader();
      this.caseRenderer?.(this.insp.querySelector("#caseBody"));
    } else if (v.kind === "edge") this._renderEdge(v);
    else if (v.kind === "account") this._renderAccount(v);
    else if (v.kind === "follow") this._renderFollow(v);
    else if (v.kind === "device") this._renderDevice(v);
    this.insp.scrollTop = 0;
  }

  openNode(id) {
    const n = this.nodesById().get(id);
    if (!n) return;
    this.graph.select({ type: "node", id });
    this.open(n.type === "device" ? { kind: "device", id } : { kind: "account", id });
  }
  openEdge(id, txnId) {
    const e = this.data.edges.find(x => x.id === id);
    if (!e) return;
    if (e.type === "device") return this.openNode(e.target);
    this.graph.select({ type: "edge", id });
    this.open({ kind: "edge", id, txn: txnId || e.transactions[e.transactions.length - 1].id });
  }

  async txn(id) {
    if (!this.txCache.has(id)) this.txCache.set(id, await api(`/transactions/${encodeURIComponent(id)}`));
    return this.txCache.get(id);
  }

  async _renderEdge(v) {
    const e = this.data.edges.find(x => x.id === v.id);
    if (!e) return this.open({ kind: "case" }, false);
    const nodes = this.nodesById();
    const h = {
      pickTxn: t => this.open({ ...v, txn: t }, false),
      focusEdge: id => this.graph.select({ type: "edge", id }, { center: true }),
      follow: acct => this.startFollow(acct, "both"),
      openAccount: id => this.openNode(id),
      onEvidence: f => this.showEvidenceFrom(this.txCache.get(v.txn), f),
    };
    this.insp.innerHTML = this.header("Transaction inspector") + `<div id="ib"></div>`;
    this.wireHeader();
    const body = this.insp.querySelector("#ib");
    txnInspector(body, e, nodes, null, h);
    try {
      const tx = await this.txn(v.txn);
      if (this.view !== v) return;
      txnInspector(body, e, nodes, tx, h);
    } catch (err) {
      body.querySelector("#txBody").innerHTML = stateBlock("error", "Couldn't load this transaction", err.message, () => this._renderEdge(v));
    }
  }

  async _renderAccount(v) {
    this.insp.innerHTML = this.header("Account investigation") + `<div id="ib">${stateBlock("loading", "Loading account…")}</div>`;
    this.wireHeader();
    const body = this.insp.querySelector("#ib");
    try {
      const a = await api(`/accounts/${encodeURIComponent(v.id)}`);
      if (this.view !== v) return;
      accountPanel(body, a, {
        hops: this.hops?.account === v.id ? this.hops.n : null,
        followDir: this.follow?.start === v.id ? this.follow.dir : null,
        expand: n => this.onScope?.(v.id, n),
        follow: dir => this.startFollow(v.id, dir),
        openAccount: id => this.data.nodes.some(n => n.id === id) ? this.openNode(id) : this.onScope?.(id, 1),
        selectNode: id => this.openNode(id),
      });
    } catch (err) {
      body.innerHTML = stateBlock("error", "Couldn't load this account", err.message, () => this._renderAccount(v));
    }
  }

  _renderDevice(v) {
    const d = this.nodesById().get(v.id);
    if (!d) return this.open({ kind: "case" }, false);
    const users = d.metadata.accounts.map(id => this.nodesById().get(id)).filter(Boolean);
    this.insp.innerHTML = this.header("Device") + `
      <div class="insp-sec"><h3>Phone<span class="right"><span class="tag">${esc(d.status)}</span></span></h3>
        <h2 style="font-size:18px">${esc(d.label)}</h2><div class="mono faint" style="font-size:12px">${esc(d.id)}</div>
        <p class="muted" style="margin-top:10px;font-size:13px">${d.metadata.account_count >= 2
          ? `${d.metadata.account_count} accounts were operated from this one phone. Legitimate customers rarely share a handset with strangers; mule farms do.`
          : d.status === "new" ? "Never seen on this customer before — first used for a payment the agents challenged." : "Used by a single account."}</p></div>
      <div class="insp-sec"><h3>Accounts on this phone</h3><ul class="conn-list">${users.map(u => `<li><button type="button" data-acct="${esc(u.id)}">
        <span class="dot" style="border-color:${u.risk >= 60 ? "var(--danger)" : u.risk >= 30 ? "var(--held)" : "var(--text-3)"}"></span><span class="nm">${esc(u.label)}</span><span class="amt">${Math.round(u.risk)}</span></button></li>`).join("")}</ul></div>`;
    this.wireHeader();
    this.insp.querySelectorAll("[data-acct]").forEach(b => b.onclick = () => this.openNode(b.dataset.acct));
    const ids = [d.id, ...users.map(u => u.id)];
    this.graph.setHighlight({ nodes: ids, edges: users.map(u => `${u.id}~${d.id}`) });
    this.evidenceLabel = "accounts sharing this phone"; this.renderBar();
  }

  // ------------------------------------------------------------ follow the money
  startFollow(start, dir = "both") {
    this.follow = { start, dir };
    const path = followPaths(this.data, start, dir);
    this.graph.setFollow(path);
    this.graph.setHighlight(null); this.evidenceLabel = null;
    this.graph.fit(true, path.nodes);
    this.renderBar();
    this.open({ kind: "follow", start, dir });
  }
  clearFollow() {
    this.follow = null; this.graph.setFollow(null); this.renderBar();
    if (this.view.kind === "follow") this.back();
  }
  async _renderFollow(v) {
    const path = followPaths(this.data, v.start, v.dir);
    this.insp.innerHTML = this.header("Follow the money") + `
      <div class="insp-sec"><div class="seg" role="group" aria-label="Direction">
        ${[["in", "Follow inbound"], ["out", "Follow outbound"], ["both", "Both"]].map(([d, l]) => `<button type="button" data-d="${d}" aria-pressed="${v.dir === d}">${l}</button>`).join("")}
      </div></div><div id="fs"></div>`;
    this.wireHeader();
    this.insp.querySelectorAll("[data-d]").forEach(b => b.onclick = () => { this.stack.pop(); this.startFollow(v.start, b.dataset.d); });
    let acct = null;
    try { acct = await api(`/accounts/${encodeURIComponent(v.start)}`); } catch { /* holding time is optional */ }
    if (this.view !== v) return;
    followSummary(this.insp.querySelector("#fs"), this.data, v.start, path, v.dir, acct);
  }

  // ------------------------------------------------------------ signal → evidence
  showEvidenceFrom(tx, feature) {
    if (!feature || !tx?.evidence?.[feature]) return this.showEvidence(null);
    this.showEvidence(tx.evidence[feature]);
  }
  showEvidence(ev, render = true) {
    if (!ev) { this.graph.setHighlight(null); this.evidenceLabel = null; if (render) this.renderBar(); return; }
    if (this.follow) { this.follow = null; this.graph.setFollow(null); }
    const present = new Set(this.data.edges.map(e => e.id));
    const edges = ev.edges.filter(id => present.has(id));
    const nodes = new Set(ev.nodes);
    for (const id of edges) { const e = this.data.edges.find(x => x.id === id); nodes.add(e.source); nodes.add(e.target); }
    this.graph.setHighlight({ nodes: [...nodes], edges });
    this.evidenceLabel = ev.label;
    this.renderBar();
    this.graph.fit(true, [...nodes], 1.3);
    if (!edges.length && !nodes.size) toast("This signal has no graph footprint in the current view.");
  }
}

export function keyTxnSection(el, ctx, wb, { animate = false } = {}) {
  const kt = ctx.key_transaction;
  if (!kt) return;
  el.innerHTML = `<h3>Key transaction<span class="right"><button class="btn sm ghost" type="button" data-open>${inr(kt.amount)} · inspect</button></span></h3>
    <p class="muted" style="font-size:13px;margin:-2px 0 12px">${esc(kt.payer_label)} → ${esc(kt.payee_label)}</p><div data-agents></div>`;
  el.querySelector("[data-open]").onclick = () => wb.openEdge(`${kt.payer}>${kt.payee}`, kt.txn_id);
  wb.txCache.set(kt.txn_id, kt);
  return agentPanel(el.querySelector("[data-agents]"), kt, { animate, onEvidence: f => wb.showEvidenceFrom(kt, f) });
}
