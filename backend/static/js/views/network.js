// NETWORK: the whole money network across every investigation. 2D by default; 3D explorer on demand.
import { api, store, esc, icon, plural, stateBlock, toast } from "../core.js";
import { Workbench } from "../workbench.js";

export function createNetwork(root) {
  root.innerHTML = `
  <div class="ws">
    <aside class="rail" aria-label="Network filters">
      <section class="rail-sec"><h2>Find an account</h2>
        <input class="sel" style="width:100%" id="netSearch" type="search" placeholder="Name or ID" list="netList" autocomplete="off"><datalist id="netList"></datalist></section>
      <section class="rail-sec"><h2>View</h2>
        <div class="seg" role="group" aria-label="Dimension"><button type="button" data-dim="2d" aria-pressed="true">2D investigation</button><button type="button" data-dim="3d" aria-pressed="false">${icon("cube", "ic")} 3D explorer</button></div>
        <p class="faint" style="font-size:12px;margin-top:8px" id="dimHint">2D reads best. Switch to 3D to explore large networks spatially.</p></section>
      <section class="rail-sec"><h2>Minimum risk <output class="mono" id="riskOut" style="float:right;color:var(--text)">0</output></h2>
        <input type="range" id="netRisk" min="0" max="90" step="10" value="0" aria-label="Minimum account risk"></section>
      <section class="rail-sec"><h2>Payments</h2><div class="toggles" id="netStates">
        ${[["settled", "Settled"], ["held", "Held"], ["paused", "Paused"], ["blocked", "Blocked"]].map(([k, l]) => `<label class="toggle"><input type="checkbox" value="${k}" checked><span class="sw"></span>${l}</label>`).join("")}
      </div></section>
      <section class="rail-sec"><h2>Phones</h2><div class="seg" role="group" aria-label="Devices">
        <button type="button" data-dev="evidence" aria-pressed="true">Evidence</button><button type="button" data-dev="all" aria-pressed="false">All</button><button type="button" data-dev="none" aria-pressed="false">None</button></div></section>
      <section class="rail-sec" id="netStats"></section>
    </aside>
    <section class="stage" aria-label="Network">
      <div class="canvas-wrap" id="netCanvas"><div id="net3d" style="position:absolute;inset:0;z-index:4" hidden></div><div class="empty-stage" id="netEmpty" hidden></div></div>
    </section>
    <aside class="inspector" id="netInsp" aria-label="Inspector"></aside>
  </div>`;
  const $ = s => root.querySelector(s);
  const S = { data: null, version: -1, dim: "2d", g3: null, loading: false, error: null };
  const wb = new Workbench({ canvasWrap: $("#netCanvas"), inspector: $("#netInsp"), onScope: (acc, hops) => acc ? scope(acc, hops) : unscope() });
  wb.caseTitle = "Network";
  wb.caseRenderer = el => {
    const d = S.data;
    if (!d) { el.innerHTML = `<div class="insp-sec"><p class="muted">Loading…</p></div>`; return; }
    const top = d.graph.nodes.filter(n => n.type === "account" && n.risk >= 30).sort((a, b) => b.risk - a.risk).slice(0, 10);
    el.innerHTML = `<div class="insp-sec"><p class="muted" style="font-size:13px">Every account and payment from every investigation. Click an account to investigate it, or pick one below.</p></div>
      <div class="insp-sec"><h3>Highest-risk accounts</h3>${top.length ? `<ul class="conn-list">${top.map(n => `<li><button type="button" data-n="${esc(n.id)}"><span class="dot" style="border-color:${n.risk >= 60 ? "var(--danger)" : "var(--held)"}"></span><span class="nm">${esc(n.label)}</span><span class="amt ${n.risk >= 60 ? "r-high" : "r-watch"}">${Math.round(n.risk)}</span></button></li>`).join("")}</ul>` : `<p class="faint">No account is above risk 30.</p>`}</div>`;
    el.querySelectorAll("[data-n]").forEach(b => b.onclick = () => { wb.openNode(b.dataset.n); wb.graph.select({ type: "node", id: b.dataset.n }, { center: true }); S.g3?.focusNode(b.dataset.n); });
  };

  async function load({ fit = false } = {}) {
    if (S.loading || wb.scopeLabel) return;
    S.loading = true;
    try {
      const d = await api("/graph/investigation");
      S.data = d; S.error = null;
      const empty = !d.graph.nodes.length;
      $("#netEmpty").hidden = !empty;
      if (empty) $("#netEmpty").innerHTML = `<div>${stateBlock("empty", "The network is empty", "Run a scenario to put payments on the map.")}</div>`;
      wb.setGraph(d.graph, { ...d, focus: null }, { fit: fit || !S.fitted });
      S.fitted = S.fitted || !empty;
      S.g3?.setData(d.graph);
      renderStats(); fillSearch();
      if (wb.view.kind === "case") wb.renderInspector();
    } catch (e) {
      S.error = e;
      if (!S.data) { $("#netEmpty").hidden = false; $("#netEmpty").innerHTML = `<div>${stateBlock("error", "Couldn't load the network", e.message, () => load({ fit: true }))}</div>`; }
    } finally { S.loading = false; }
  }
  async function scope(acc, hops) {
    try {
      const d = await api(`/graph/investigation?account=${encodeURIComponent(acc)}&hops=${hops}`);
      const n = d.graph.nodes.find(x => x.id === acc);
      wb.scopeLabel = `${n?.label || acc} · ${hops === 0 ? "full network" : plural(hops, "hop")}`; wb.hops = { account: acc, n: hops };
      wb.setGraph(d.graph, { ...d, focus: acc }, { fit: true });
      S.g3?.setData(d.graph);
      wb.renderInspector();
    } catch (e) { toast(`Couldn't expand: ${e.message}`, true); }
  }
  function unscope() { wb.scopeLabel = null; wb.hops = null; load({ fit: true }); }

  function renderStats() {
    const g = S.data.graph;
    const acc = g.nodes.filter(n => n.type === "account"), dev = g.nodes.filter(n => n.type === "device");
    const pay = g.edges.filter(e => e.type === "payment");
    $("#netStats").innerHTML = `<h2>In the network</h2><dl class="dl">
      <dt>Accounts</dt><dd>${acc.length}</dd><dt>Phones</dt><dd>${dev.length}</dd>
      <dt>Payment routes</dt><dd>${pay.length}</dd><dt>Payments</dt><dd>${pay.reduce((s, e) => s + e.count, 0)}</dd>
      <dt>Suspicious accounts</dt><dd class="r-high">${acc.filter(n => n.risk >= 60).length}</dd></dl>`;
  }
  function fillSearch() {
    $("#netList").innerHTML = S.data.graph.nodes.filter(n => n.type === "account").map(n => `<option value="${esc(n.label)}">${esc(n.id)}</option>`).join("");
  }
  $("#netSearch").addEventListener("change", e => {
    const q = e.target.value.trim().toLowerCase();
    const n = S.data?.graph.nodes.find(x => x.label.toLowerCase() === q || x.id.toLowerCase() === q) || S.data?.graph.nodes.find(x => x.label.toLowerCase().includes(q) || x.id.toLowerCase().includes(q));
    if (!n) return toast("No account matches that.");
    wb.openNode(n.id); wb.graph.select({ type: "node", id: n.id }, { center: true }); S.g3?.focusNode(n.id);
  });
  $("#netRisk").addEventListener("input", e => { $("#riskOut").textContent = e.target.value; wb.graph.setFilters({ minRisk: Number(e.target.value) }); S.g3?.setFilters({ minRisk: Number(e.target.value) }); });
  $("#netStates").addEventListener("change", () => {
    const on = new Set([...root.querySelectorAll("#netStates input:checked")].map(i => i.value));
    const states = on.size === 4 ? null : on;
    wb.graph.setFilters({ states }); S.g3?.setFilters({ states });
  });
  root.querySelectorAll("[data-dev]").forEach(b => b.onclick = () => {
    root.querySelectorAll("[data-dev]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
    wb.graph.setFilters({ devices: b.dataset.dev }); S.g3?.setFilters({ devices: b.dataset.dev });
  });
  root.querySelectorAll("[data-dim]").forEach(b => b.onclick = () => setDim(b.dataset.dim));

  async function setDim(dim) {
    S.dim = dim;
    root.querySelectorAll("[data-dim]").forEach(x => x.setAttribute("aria-pressed", String(x.dataset.dim === dim)));
    const box = $("#net3d");
    if (dim === "3d") {
      box.hidden = false;
      if (!S.g3) {
        box.innerHTML = `<div class="empty-stage"><div>${stateBlock("loading", "Loading 3D explorer…")}</div></div>`;
        try {
          const { Graph3D } = await import("../graph/graph3d.js");
          box.innerHTML = "";
          S.g3 = new Graph3D(box, { onSelect: id => id ? wb.openNode(id) : null });
          S.g3.setFilters({ ...wb.graph.filters });
        } catch (e) {
          box.innerHTML = `<div class="empty-stage"><div>${stateBlock("error", "3D isn't available here", e.message)}</div></div>`;
          return;
        }
      }
      S.g3.setData(wb.data); S.g3.resume();
      $("#dimHint").textContent = "Drag to orbit · right-drag to pan · scroll to zoom · click a node to investigate.";
    } else {
      box.hidden = true; S.g3?.pause();
      $("#dimHint").textContent = "2D reads best. Switch to 3D to explore large networks spatially.";
    }
  }

  store.on("version", () => { if (root.isConnected && !root.hidden) load(); else S.stale = true; });
  return {
    wb,
    show() { load({ fit: !S.fitted }); wb.graph.dirty = true; if (S.dim === "3d") S.g3?.resume(); },
    hide() { S.g3?.pause(); },
  };
}
