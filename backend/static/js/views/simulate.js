// SIMULATE: "what changes the decision?" Every control change calls the real engine
// (POST /analyze/preview — scored by the same agents, never recorded). Nothing here is computed locally
// except the difference between two backend answers.
import { post, esc, icon, inr, outcomePill, riskClass, stateBlock, STATE, OUTCOME_SHORT } from "../core.js";
import { agentPanel } from "../panels.js";

const LADDER = [["ALLOW", "Allow", "settled", "Allow / Allow + nudge"], ["VERIFY", "Verify", "paused", "Step-up / Cooling room"],
  ["PAUSE", "Pause / Hold", "held", "Analyst review or credit lien"], ["BLOCK", "Block", "blocked", "Payment stopped"]];
const rung = o => o === "BLOCK" ? "BLOCK" : o === "PAUSE" || o === "HOLD_CREDIT" ? "PAUSE" : o === "STEP_UP" || o === "COOLING_ROOM" ? "VERIFY" : "ALLOW";
const CUSTOM = { ALLOW: "settled", VERIFY: "paused", PAUSE: "held", BLOCK: "blocked" };
const PRESETS = {
  "Everyday payment": { amount: 2500 },
  "Account takeover": { amount: 49999, new_beneficiary: true, new_device: true, sim_changed: true, pin_reset: true, location: "PATNA" },
  "Digital arrest": { amount: 180000, new_beneficiary: true, on_call: true, screen_share: true, call_minutes: 55 },
  "Payment spree": { amount: 9500, new_beneficiary: true, recent_payments_1h: 9 },
};
const DEFAULTS = { amount: 2500, new_beneficiary: false, new_device: false, sim_changed: false, pin_reset: false, screen_share: false, on_call: false, call_minutes: 30, location: "PUNE-W", recent_payments_1h: 0 };
const TOGGLES = [["new_beneficiary", "New beneficiary", "never paid before"], ["new_device", "New device", "unrecognised phone"],
  ["sim_changed", "SIM changed", "4 hours ago"], ["pin_reset", "UPI PIN reset", "1 hour ago"],
  ["screen_share", "Screen sharing", "remote access app"], ["on_call", "On a call", "while paying"]];
// amount slider is logarithmic: ₹100 … ₹5,00,000
const toAmt = v => Math.round(Math.pow(10, 2 + v / 100 * 3.7) / 100) * 100 || 100;
const toSlider = a => Math.round((Math.log10(Math.max(100, a)) - 2) / 3.7 * 100);

export function createSimulate(root) {
  root.innerHTML = `
  <div class="sim">
    <form class="sim-controls" id="simForm" onsubmit="return false" aria-label="Payment to simulate">
      <div><h1>What changes the decision?</h1><p>Change the payment. The real agents re-score it instantly — nothing is recorded.</p></div>
      <div class="field"><div class="label">Start from</div><div class="presets">${Object.keys(PRESETS).map(p => `<button class="btn sm" type="button" data-p="${esc(p)}">${esc(p)}</button>`).join("")}</div></div>
      <div class="field"><div class="top"><label class="label" for="amt">Amount</label><output id="amtOut"></output></div><input type="range" id="amt" min="0" max="100" step="1"></div>
      <div class="field"><div class="label">Signals</div><div class="toggles">
        ${TOGGLES.map(([k, l, h]) => `<label class="toggle"><input type="checkbox" name="${k}"><span class="sw"></span>${l}<span class="hint">${h}</span></label>`).join("")}
      </div></div>
      <div class="field" id="callField"><div class="top"><label class="label" for="callMin">Call length</label><output id="callOut"></output></div><input type="range" id="callMin" min="1" max="90" step="1"></div>
      <div class="field"><label class="label" for="geo">Location</label><select class="sel" id="geo">
        <option value="PUNE-W">Pune (home)</option><option value="MUMBAI">Mumbai · 126 km away</option><option value="DELHI">Delhi · 1,780 km away</option><option value="PATNA">Patna · 1,500 km away</option></select>
        <span class="faint" style="font-size:12px">Last payment was 3 hours ago from Pune.</span></div>
      <div class="field"><div class="top"><label class="label" for="vel">Payments to new payees in the last hour</label><output id="velOut"></output></div><input type="range" id="vel" min="0" max="15" step="1"></div>
      <button class="btn ghost" type="button" id="simReset">${icon("replay")}Reset to an everyday payment</button>
    </form>
    <section class="sim-out" id="simOut" aria-live="polite"></section>
  </div>`;
  const $ = s => root.querySelector(s);
  let inputs = { ...DEFAULTS }, last = null, prev = null, trail = [], ctl = null, timer = null, error = null;

  function syncForm() {
    $("#amt").value = toSlider(inputs.amount); $("#amtOut").textContent = inr(inputs.amount);
    TOGGLES.forEach(([k]) => { root.querySelector(`[name=${k}]`).checked = !!inputs[k]; });
    $("#callMin").value = inputs.call_minutes; $("#callOut").textContent = `${inputs.call_minutes} min`;
    $("#callField").hidden = !inputs.on_call;
    $("#geo").value = inputs.location; $("#vel").value = inputs.recent_payments_1h; $("#velOut").textContent = inputs.recent_payments_1h;
  }
  function changed() { syncForm(); clearTimeout(timer); timer = setTimeout(run, 180); }
  $("#amt").addEventListener("input", e => { inputs.amount = toAmt(+e.target.value); changed(); });
  $("#callMin").addEventListener("input", e => { inputs.call_minutes = +e.target.value; changed(); });
  $("#vel").addEventListener("input", e => { inputs.recent_payments_1h = +e.target.value; changed(); });
  $("#geo").addEventListener("change", e => { inputs.location = e.target.value; changed(); });
  TOGGLES.forEach(([k]) => root.querySelector(`[name=${k}]`).addEventListener("change", e => { inputs[k] = e.target.checked; changed(); }));
  root.querySelectorAll("[data-p]").forEach(b => b.onclick = () => { inputs = { ...DEFAULTS, ...PRESETS[b.dataset.p] }; changed(); });
  $("#simReset").onclick = () => { inputs = { ...DEFAULTS }; trail = []; changed(); };

  async function run() {
    ctl?.abort();
    ctl = new AbortController();
    $("#simOut").classList.add("busy");
    try {
      const res = await post("/analyze/preview", inputs, { signal: ctl.signal });
      prev = last; last = res; error = null;
      if (!trail.length || trail[trail.length - 1].score !== res.score || trail[trail.length - 1].outcome !== res.outcome) trail.push({ score: res.score, outcome: res.outcome });
      if (trail.length > 7) trail = trail.slice(-7);
    } catch (e) {
      if (e.name === "AbortError") return;
      error = e;
    }
    $("#simOut").classList.remove("busy");
    render();
  }

  function render() {
    const out = $("#simOut");
    if (error && !last) { out.innerHTML = stateBlock("error", "The engine didn't answer", error.message, run); return; }
    if (!last) { out.innerHTML = stateBlock("loading", "Scoring with the real agents…"); return; }
    const r = last;
    const step = rung(r.outcome);
    out.innerHTML = `
      ${error ? `<div class="chain-banner bad">${icon("alert")}<b>Showing the last good result.</b><span>${esc(error.message)}</span><button class="btn sm" type="button" id="simRetry">Retry</button></div>` : ""}
      <div class="sim-top">
        <div><div class="label">Risk</div><div class="gauge ${riskClass(r.score)}">${Math.round(r.score)}<small> / 100</small></div></div>
        <div>
          <div class="label" style="margin-bottom:8px">Decision · ${outcomePill(r.outcome)} <span class="faint" style="font-weight:400">${esc(r.routing_reason || "")}</span></div>
          <div class="ladder" role="list">${LADDER.map(([k, l, c, s]) => `<div role="listitem" class="${k === step ? "on" : ""}" style="--c:var(--${c === "blocked" ? "danger" : c})">${l}<small>${s}</small></div>`).join("")}</div>
          <div class="trail" aria-label="Risk history">${trail.map((t, i) => `${i ? icon("arrow") : ""}<span class="${i === trail.length - 1 ? "cur" : ""}">${Math.round(t.score)}</span>`).join("")}</div>
        </div>
      </div>
      ${r.typology_name ? `<p class="muted" style="font-size:13.5px;margin-top:-6px">Pattern recognised: <b style="color:var(--text)">${esc(r.dominant)} · ${esc(r.typology_name)}</b></p>` : ""}
      <div class="sim-cols">
        <section class="panel"><div class="panel-h"><h2>Agent reasoning</h2></div><div class="panel-b" id="simAgents"></div></section>
        <div style="display:grid;gap:20px;align-content:start">
          <section class="panel"><div class="panel-h"><h2>Why did it change?</h2></div><div class="panel-b" id="simDelta"></div></section>
          <section class="panel"><div class="panel-h"><h2>What if…</h2><span class="right faint" style="font-size:12px">each signal removed, re-scored by the engine</span></div><div class="panel-b" id="simCf"></div></section>
        </div>
      </div>`;
    out.querySelector("#simRetry")?.addEventListener("click", run);
    agentPanel(out.querySelector("#simAgents"), r, { animate: false });
    renderDelta(out.querySelector("#simDelta"));
    const cf = r.counterfactuals || [];
    out.querySelector("#simCf").innerHTML = cf.length ? `<div style="display:grid;gap:6px">${cf.map(c => `
      <div class="cf-row"><span>Without <b>${esc(c.label.toLowerCase())}</b></span><span class="mono">${Math.round(c.score_if)} ${outcomePill(c.outcome_if)}</span><span class="d">${c.delta > 0 ? "−" : "+"}${Math.abs(c.delta).toFixed(0)}</span></div>`).join("")}</div>
      ${cf[0]?.delta > 0 ? `<p class="muted" style="font-size:13px;margin-top:10px">Removing the <b style="color:var(--text)">${esc(cf[0].label.toLowerCase())}</b> signal alone would reduce the risk by <b style="color:var(--text)">${cf[0].delta.toFixed(0)} points</b>${cf[0].outcome_if !== r.outcome ? ` and change the decision to <b style="color:var(--text)">${esc(OUTCOME_SHORT[cf[0].outcome_if])}</b>` : ""}.</p>` : ""}`
      : `<p class="faint">Turn on a risk signal to see how much each one contributes.</p>`;
  }

  function renderDelta(el) {
    if (!prev) { el.innerHTML = `<p class="faint">Change a control — the strongest changes in the agents' signals will appear here.</p>`; return; }
    const sig = r => { const m = new Map(); r.agents.forEach(a => a.agent !== "counsel" && a.signals.forEach(s => m.set(a.agent + ":" + s.feature, { label: s.label, agent: a.label, c: s.contribution }))); return m; };
    const a = sig(prev), b = sig(last);
    const keys = new Set([...a.keys(), ...b.keys()]);
    const diffs = [...keys].map(k => ({ ...(b.get(k) || a.get(k)), d: (b.get(k)?.c || 0) - (a.get(k)?.c || 0) })).filter(x => Math.abs(x.d) >= 1).sort((x, y) => Math.abs(y.d) - Math.abs(x.d)).slice(0, 5);
    const ds = last.score - prev.score;
    el.innerHTML = `<p style="font-size:13.5px;margin-bottom:10px">Risk <span class="mono">${Math.round(prev.score)} → ${Math.round(last.score)}</span>
      <b class="${ds > 0 ? "r-high" : ds < 0 ? "" : "faint"}" style="${ds < 0 ? "color:var(--settled)" : ""}">${ds > 0 ? "+" : ""}${Math.round(ds)}</b>
      ${prev.outcome !== last.outcome ? ` · decision ${esc(OUTCOME_SHORT[prev.outcome])} → <b>${esc(OUTCOME_SHORT[last.outcome])}</b>` : ""}</p>
      ${diffs.length ? `<ul class="delta-list">${diffs.map(d => `<li><span>${esc(d.label)} <span class="faint">· ${esc(d.agent)}</span></span><span class="d ${d.d > 0 ? "up" : "down"}">${d.d > 0 ? "+" : "−"}${Math.abs(d.d).toFixed(0)}</span></li>`).join("")}</ul>`
        : `<p class="faint">No agent signal moved by more than a point.</p>`}`;
  }

  syncForm();
  return { show() { if (!last) run(); }, hide() {}, setInputs(i) { inputs = { ...DEFAULTS, ...i }; changed(); } };
}
