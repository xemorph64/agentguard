// OVERVIEW: what AgentGuard does, what it has seen, and where to start.
import { store, esc, icon, inr, inrShort, plural, sevLabel, ago, stateBlock, conn } from "../core.js";

const OUT_COLORS = [["ALLOW", "var(--settled)", "Allowed"], ["ALLOW_NUDGE", "oklch(0.79 0.1 172 / 0.55)", "Allowed + nudge"],
  ["STEP_UP", "var(--paused)", "Step-up"], ["COOLING_ROOM", "oklch(0.74 0.085 268 / 0.6)", "Cooling room"],
  ["PAUSE", "oklch(0.74 0.085 268 / 0.85)", "Paused"], ["HOLD_CREDIT", "var(--held)", "Held"], ["BLOCK", "var(--danger)", "Blocked"]];

export function createOverview(root, { runScenario }) {
  root.innerHTML = `<div class="view-scroll"><div class="page">
    <section class="ov-head">
      <div>
        <h1>Stop fraudulent UPI payments before the money moves.</h1>
        <p>Every payment is scored by six independent agents in milliseconds. A policy engine decides whether to allow, challenge, hold or block it — and every decision is written to a tamper-evident ledger.</p>
        <div class="pipeline" aria-label="How a decision is made">
          <span><b>Payment</b> request</span>${icon("arrow")}
          <span><b>6 agents</b> Transaction · Behavior · Velocity · Mule · AML · Counsel</span>${icon("arrow")}
          <span><b>Risk</b> fusion + typology</span>${icon("arrow")}
          <span><b>Decision</b> allow → block</span>${icon("arrow")}
          <span><b>Ledger</b> hash-chained</span>
        </div>
      </div>
      <div><button class="btn primary" type="button" id="ovDemo">${icon("play")}Start the guided demo</button></div>
    </section>
    <section class="kpis" id="ovKpis" aria-label="Key figures"></section>
    <div class="ov-grid">
      <section class="panel"><div class="panel-h"><h2>Run a scenario</h2><span class="right faint" style="font-size:12px">Each one sends real payments through the engine</span></div>
        <ul class="scen-list" id="ovScen"></ul></section>
      <div style="display:grid;gap:20px;align-content:start">
        <section class="panel"><div class="panel-h"><h2>Investigations</h2><span class="right"><a href="#/investigate" style="font-size:12.5px">Open workspace</a></span></div><div id="ovInv"></div></section>
        <section class="panel"><div class="panel-h"><h2>Decisions</h2><span class="right faint mono" style="font-size:12px" id="ovLat"></span></div><div class="panel-b" id="ovOut"></div></section>
      </div>
    </div>
  </div></div>`;
  const $ = s => root.querySelector(s);
  $("#ovDemo").onclick = () => document.getElementById("demoBtn").click();

  function renderScen() {
    const sc = store.server.meta?.scenarios;
    if (!sc) { $("#ovScen").innerHTML = `<li>${stateBlock("loading", "Loading scenarios…")}</li>`; return; }
    $("#ovScen").innerHTML = sc.map(s => `<li><div><h3>${esc(s.title)}</h3><p>${esc(s.summary)}</p></div>
      <button class="btn" type="button" data-s="${s.id}">${icon("play")}Run</button></li>`).join("");
    root.querySelectorAll("[data-s]").forEach(b => b.onclick = async () => {
      b.disabled = true;
      location.hash = "#/investigate";
      try { await runScenario(b.dataset.s); } catch { /* the investigate view shows the error + retry */ }
      b.disabled = false;
    });
  }
  function renderKpis() {
    const o = store.server.overview;
    if (!o) { $("#ovKpis").innerHTML = [1, 2, 3, 4, 5].map(() => `<div class="kpi"><div class="skeleton" style="height:12px;width:60%"></div><div class="skeleton" style="height:24px;width:40%;margin-top:10px"></div></div>`).join(""); return; }
    const k = [
      ["Payments monitored", o.monitored, `${o.allowed} allowed`],
      ["Suspicious", o.suspicious, o.monitored ? `${Math.round(100 * o.suspicious / o.monitored)}% challenged or stopped` : "none yet"],
      ["Money protected", inrShort(o.protected_amount), `${inrShort(o.blocked_amount)} blocked · ${inrShort(o.held_amount)} held`, o.blocked_amount > 0],
      ["Active investigations", o.active_investigations, `${plural(o.investigations, "scenario")} run`],
      ["Highest network risk", Math.round(o.network_risk), o.network_risk_label || "no risky accounts", o.network_risk >= 60],
    ];
    $("#ovKpis").innerHTML = k.map(([l, v, s, d]) => `<div class="kpi ${d ? "danger" : ""}"><div class="label">${esc(l)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`).join("");
    const tot = Object.values(o.by_outcome).reduce((a, b) => a + b, 0);
    $("#ovLat").textContent = tot ? `avg ${o.avg_latency_ms} ms · p95 ${Math.round(o.p95_latency_ms)} ms` : "";
    $("#ovOut").innerHTML = tot ? `<div class="outcome-bar" role="img" aria-label="Decision mix">${OUT_COLORS.map(([k, c]) => o.by_outcome[k] ? `<i style="width:${100 * o.by_outcome[k] / tot}%;background:${c}" title="${k}: ${o.by_outcome[k]}"></i>` : "").join("")}</div>
      <div class="legend-row">${OUT_COLORS.filter(([k]) => o.by_outcome[k]).map(([k, c, l]) => `<span><i style="background:${c}"></i>${l} <span class="mono">${o.by_outcome[k]}</span></span>`).join("")}</div>`
      : `<p class="faint">No payments yet. Run a scenario to see the agents decide.</p>`;
  }
  function renderInv() {
    const inv = store.server.investigations || [];
    $("#ovInv").innerHTML = inv.length ? `<ul class="inv-list">${inv.slice(0, 6).map(r => {
      const [lab, cls] = sevLabel(r.severity);
      return `<li><a href="#/investigate/${r.run_id}"><span class="t">${esc(r.title)}</span><span class="h ${cls}">${esc(r.headline || lab)}</span>
        <span class="meta">${plural(r.count, "payment")} · ${ago(r.created)}</span></a></li>`;
    }).join("")}</ul>` : `<div class="panel-b faint">Nothing investigated yet.</div>`;
  }
  store.on("meta", renderScen); store.on("overview", renderKpis); store.on("investigations", renderInv);
  renderScen(); renderKpis(); renderInv();
  return { show() {}, hide() {} };
}
