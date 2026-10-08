// Inspector panels. Pure renderers over backend payloads + small event wiring.
import { esc, inr, inrShort, time, mins, icon, outcomePill, riskClass, plural, STATE, STATE_LABEL, reducedMotion, store } from "./core.js";

const AGENT_ORDER = ["transaction", "behavior", "velocity", "mule", "aml", "counsel"];
const riskCol = s => s >= 60 ? "var(--danger)" : s >= 30 ? "var(--held)" : "var(--settled)";

// ------------------------------------------------------------ agents → final risk → decision → why
// opts.onEvidence(feature|null)   opts.animate   opts.evidence (feature -> {nodes, edges})
export function agentPanel(el, tx, opts = {}) {
  const agents = AGENT_ORDER.map(a => tx.agents.find(x => x.agent === a)).filter(Boolean);
  const meta = store.server.meta || {};
  const drivers = tx.drivers || [];
  const ev = opts.evidence || tx.evidence || {};
  el.innerHTML = `
    <div class="agents" role="list">
      ${agents.map(a => {
        const top = a.signals?.[0]?.contribution >= 4 ? a.signals[0] : null;
        const counsel = a.agent === "counsel";
        return `<div class="agent ${counsel ? "counsel" : ""} pending" role="listitem" title="${esc(a.question)}">
          <span class="n">${esc(a.label)}</span>
          <span class="bar"><i style="background:${riskCol(a.score)}" data-w="${Math.max(2, a.score)}"></i></span>
          <span class="s ${counsel ? "" : riskClass(a.score)}">${a.status === "ok" ? Math.round(a.score) : "—"}</span>
          <span class="sig">${counsel ? (tx.counsel_discount ? `Legitimacy ${Math.round(a.score)} → −${tx.counsel_discount} pts` : "No legitimacy evidence") : top ? esc(top.label) : "Nothing unusual"}</span>
        </div>`;
      }).join("")}
    </div>
    <div class="verdict pending agent" style="grid-template-columns:auto 1fr">
      <div><div class="label">Final risk</div><div class="score" style="color:${riskCol(tx.score)}">${Math.round(tx.score)}<small> / 100</small></div></div>
      <div style="justify-self:end;text-align:right"><div class="label" style="margin-bottom:6px">Decision</div>${outcomePill(tx.outcome, true)}</div>
      <div class="why">${tx.hard_rule ? `Hard rule <span class="mono">${esc(tx.hard_rule)}</span> fired. ` : ""}${esc(tx.routing_reason || "")}${tx.typology_name ? ` · <b>${esc(tx.dominant)}</b> ${esc(tx.typology_name)}` : ""}</div>
    </div>
    ${drivers.length ? `<div style="margin-top:16px"><div class="label" style="margin-bottom:8px">Why — strongest signals${Object.keys(ev).length && opts.onEvidence ? " (click to see the evidence)" : ""}</div>
      <div class="why-list">${drivers.slice(0, 4).map(d => `
        <button class="why-btn" type="button" data-f="${esc(d.feature)}" aria-pressed="false" ${ev[d.feature] && opts.onEvidence ? "" : "disabled style='cursor:default'"}>
          <span class="l">${esc(d.label)}</span><span class="c">+${d.contribution.toFixed(0)}</span>
          <span class="a">${esc(meta.agents?.[d.agent] || d.agent)} agent${d.value !== null && d.value !== undefined && typeof d.value !== "boolean" ? ` · value ${esc(fmtVal(d.value))}` : ""}</span>
        </button>`).join("")}</div></div>` : ""}
    ${tx.counterfactual ? `<div class="cf" style="margin-top:12px"><b>What would change it:</b> ${esc(tx.counterfactual.text)} <span class="mono">(${tx.score} → ${tx.counterfactual.score_if})</span></div>` : ""}
  `;
  const rows = [...el.querySelectorAll(".agent")];
  const show = (r) => { r.classList.remove("pending"); const i = r.querySelector(".bar i"); if (i) i.style.setProperty("--w", i.dataset.w / 100); };
  if (opts.animate && !reducedMotion()) rows.forEach((r, i) => setTimeout(() => show(r), 140 + i * 230));
  else rows.forEach(show);
  if (opts.onEvidence) el.querySelectorAll(".why-btn:not([disabled])").forEach(b => b.addEventListener("click", () => {
    const on = b.getAttribute("aria-pressed") !== "true";
    el.querySelectorAll(".why-btn").forEach(x => x.setAttribute("aria-pressed", "false"));
    b.setAttribute("aria-pressed", String(on));
    opts.onEvidence(on ? b.dataset.f : null);
  }));
  return rows.length * 230 + 200;
}
const fmtVal = v => typeof v === "number" ? (Math.abs(v) >= 1000 ? inr(v) : String(Math.round(v * 100) / 100)) : String(v);

// ------------------------------------------------------------ transaction (edge) inspector
export function txnInspector(el, edge, nodesById, txDetail, h = {}) {
  const src = nodesById.get(edge.source), dst = nodesById.get(edge.target);
  const txs = edge.transactions;
  const multi = txs.length > 1;
  const cur = txDetail;
  el.innerHTML = `
    <div class="insp-sec">
      <h3>${multi ? `${txs.length} payments on this route` : "Transaction"}<span class="right">${outcomePill(cur ? cur.outcome : edge.outcome)}</span></h3>
      <div class="big-amt">${inr(multi ? edge.amount : txs[0].amount)}</div>
      <div class="route">
        <div class="acct">${dot(src)}<b>${esc(src?.label || edge.source)}</b></div>
        <div class="via">${icon("down")}${multi ? plural(txs.length, "payment") : esc(STATE_LABEL[edge.state])}</div>
        <div class="acct">${dot(dst)}<b>${esc(dst?.label || edge.target)}</b></div>
      </div>
      ${multi ? `<table class="table" style="margin-top:12px"><thead><tr><th>Time</th><th class="r">Amount</th><th>Outcome</th></tr></thead><tbody>
        ${txs.map(t => `<tr data-tx="${esc(t.id)}" aria-selected="${cur?.txn_id === t.id}"><td class="mono">${time(t.ts)}</td><td class="r mono">${inr(t.amount)}</td><td>${outcomePill(t.outcome)}</td></tr>`).join("")}
      </tbody></table>` : ""}
    </div>
    <div class="insp-sec" id="txBody">${cur ? "" : `<div class="state loading"><div class="skeleton" style="width:180px;height:10px"></div></div>`}</div>
    <div class="insp-sec"><div class="actions">
      <button class="btn sm" data-act="focus">${icon("fit")}Focus transaction</button>
      <button class="btn sm" data-act="follow">${icon("flow")}Follow money</button>
      <button class="btn sm" data-act="source">Open source account</button>
    </div></div>`;
  el.querySelectorAll("tr[data-tx]").forEach(r => r.addEventListener("click", () => h.pickTxn?.(r.dataset.tx)));
  el.querySelector('[data-act="focus"]').onclick = () => h.focusEdge?.(edge.id);
  el.querySelector('[data-act="follow"]').onclick = () => h.follow?.(edge.target);
  el.querySelector('[data-act="source"]').onclick = () => h.openAccount?.(edge.source);
  if (cur) {
    const body = el.querySelector("#txBody");
    body.innerHTML = `
      <dl class="dl" style="margin-bottom:14px">
        <dt>Time</dt><dd>${time(cur.ts)}</dd>
        <dt>Transaction</dt><dd>${esc(cur.txn_id)}</dd>
        ${cur.device_fp ? `<dt>Device</dt><dd>${esc(cur.device_fp)}</dd>` : ""}
        ${cur.geo ? `<dt>Location</dt><dd class="txt">${esc(cur.geo)}</dd>` : ""}
        <dt>Typology</dt><dd class="txt">${cur.dominant ? `${esc(cur.dominant)} · ${esc(cur.typology_name)}` : "None"}</dd>
        ${cur.hold_minutes ? `<dt>Held for</dt><dd>${cur.hold_minutes} min</dd>` : ""}
      </dl>
      ${cur.caption ? `<p class="muted" style="font-size:13px;margin-bottom:14px">${esc(cur.caption)}</p>` : ""}
      <div class="label" style="margin-bottom:6px">Agent signals</div><div id="txAgents"></div>`;
    agentPanel(body.querySelector("#txAgents"), cur, { onEvidence: h.onEvidence, animate: false });
  }
}

// ------------------------------------------------------------ account panel
export function accountPanel(el, a, h = {}) {
  const m = a.metadata || {};
  el.innerHTML = `
    <div class="insp-sec">
      <h3>Account<span class="right"><span class="tag">${esc(a.status)}</span></span></h3>
      <div style="display:flex;align-items:baseline;gap:12px;justify-content:space-between">
        <h2 style="font-size:19px">${esc(a.label)}</h2>
        <div style="text-align:right"><div class="label">Risk</div><div class="big-amt ${riskClass(a.risk)}" style="font-size:26px">${Math.round(a.risk)}</div></div>
      </div>
      <div class="mono faint" style="font-size:12px;margin-top:2px">${esc(a.id)} · ${esc(a.persona)} · ${a.age_days < 60 ? `<span class="r-watch">${a.age_days} days old</span>` : `${Math.round(a.age_days / 30)} months old`}</div>
    </div>
    <div class="insp-sec">
      <div class="stats">
        <div><div class="k">Money in</div><div class="v">${inr(a.money_in)}</div></div>
        <div><div class="k">Money out</div><div class="v">${inr(a.money_out)}</div></div>
        <div><div class="k">Net flow</div><div class="v">${inr(a.net_flow)}</div></div>
        <div><div class="k">Attempted out</div><div class="v ${a.attempted_out > a.money_out ? "danger" : ""}">${inr(a.attempted_out)}</div></div>
        <div><div class="k">Transactions</div><div class="v">${a.transaction_count}</div></div>
        <div><div class="k">Funds held for</div><div class="v">${mins(a.holding_minutes)}</div></div>
        <div><div class="k">Network risk</div><div class="v ${riskClass(m.network_risk)}">${Math.round(m.network_risk || 0)}</div></div>
        <div><div class="k">Shares a phone with</div><div class="v ${a.shared_device_accounts ? "danger" : ""}">${plural(a.shared_device_accounts, "account")}</div></div>
      </div>
    </div>
    ${a.risk_signals.length ? `<div class="insp-sec"><h3>Risk signals</h3><ul class="evlist">${a.risk_signals.map(s => `<li><span>${esc(s.label)} <span class="mono faint">+${Math.round(s.contribution)}</span></span></li>`).join("")}</ul></div>` : ""}
    <div class="insp-sec"><h3>Investigate</h3>
      <div class="label" style="margin-bottom:6px">Expand network</div>
      <div class="seg" role="group" aria-label="Expand network">
        ${[[1, "1 hop"], [2, "2 hops"], [0, "Full"]].map(([n, l]) => `<button type="button" data-hops="${n}" aria-pressed="${h.hops === n}">${l}</button>`).join("")}
      </div>
      <div class="label" style="margin:12px 0 6px">Follow the money</div>
      <div class="seg" role="group" aria-label="Follow the money">
        ${[["in", "Inbound"], ["out", "Outbound"], ["both", "Both"]].map(([d, l]) => `<button type="button" data-follow="${d}" aria-pressed="${h.followDir === d}">${l}</button>`).join("")}
      </div>
    </div>
    ${a.devices.length ? `<div class="insp-sec"><h3>Device relationships</h3><ul class="conn-list">${a.devices.map(d => `
      <li><button type="button" data-node="${esc(d.id)}">${icon("phone")}<span class="nm">${esc(d.id)}${d.registered ? ` <span class="faint">registered</span>` : ""}</span>
      <span class="amt ${d.accounts.length ? "r-watch" : ""}">${d.accounts.length ? `+${plural(d.accounts.length, "account")}` : "only this account"}</span></button></li>`).join("")}</ul></div>` : ""}
    <div class="insp-sec"><h3>Connected accounts</h3>${a.connected.length ? `<ul class="conn-list">${a.connected.map(c => `
      <li><button type="button" data-acct="${esc(c.id)}"><span class="dot" style="border-color:${riskCol(c.risk)}"></span><span class="nm">${esc(c.label)}</span>
      <span class="amt">${c.in ? `← ${inrShort(c.in)}` : ""}${c.in && c.out ? " · " : ""}${c.out ? `→ ${inrShort(c.out)}` : ""}</span></button></li>`).join("")}</ul>` : `<p class="muted">No payments yet.</p>`}</div>`;
  el.querySelectorAll("[data-hops]").forEach(b => b.onclick = () => h.expand?.(Number(b.dataset.hops)));
  el.querySelectorAll("[data-follow]").forEach(b => b.onclick = () => h.follow?.(b.dataset.follow));
  el.querySelectorAll("[data-acct]").forEach(b => b.onclick = () => h.openAccount?.(b.dataset.acct));
  el.querySelectorAll("[data-node]").forEach(b => b.onclick = () => h.selectNode?.(b.dataset.node));
}

// ------------------------------------------------------------ follow-the-money summary
export function followSummary(el, graph, start, path, dir, acct) {
  const byId = new Map(graph.nodes.map(n => [n.id, n]));
  const edges = graph.edges.filter(e => path.edges.includes(e.id) && e.type === "payment");
  const inE = edges.filter(e => e.target === start), outE = edges.filter(e => e.source === start);
  const sum = (es, f = e => e.amount) => es.reduce((s, e) => s + f(e), 0);
  const settledIn = sum(inE, e => e.settled_amount);
  const n = byId.get(start);
  const further = edges.filter(e => e.source !== start && e.target !== start);
  const line = (e, other, arrow) => `<div class="fc-edge">${icon(arrow)}${inrShort(e.amount)}${e.count > 1 ? ` · ${e.count}` : ""} <span class="pill ${e.state}" style="height:18px;font-size:11px">${STATE_LABEL[e.state]}</span></div>`;
  el.innerHTML = `
    <div class="insp-sec">
      <h3>Flow summary<span class="right"><span class="tag">${dir === "both" ? "In + out" : dir === "in" ? "Inbound" : "Outbound"}</span></span></h3>
      <div class="stats">
        <div><div class="k">Received</div><div class="v">${inr(settledIn)}</div></div>
        <div><div class="k">Forwarded (attempted)</div><div class="v ${sum(outE) ? "danger" : ""}">${inr(sum(outE))}</div></div>
        <div><div class="k">Sources</div><div class="v">${inE.length}</div></div>
        <div><div class="k">Destinations</div><div class="v">${outE.length}</div></div>
        <div><div class="k">Average holding</div><div class="v">${mins(acct?.holding_minutes)}</div></div>
        <div><div class="k">Further hops</div><div class="v">${further.length}</div></div>
      </div>
    </div>
    <div class="insp-sec"><h3>Path</h3><div class="follow-chain">
      ${dir !== "out" ? inE.sort((a, b) => b.amount - a.amount).slice(0, 4).map(e => `<div class="fc-node">${dot(byId.get(e.source))}${esc(byId.get(e.source)?.label)}</div>${line(e, e.source, "down")}`).join("") +
        (inE.length > 4 ? `<div class="fc-node"><span class="more">+ ${inE.length - 4} more senders · ${inrShort(sum(inE.slice(4)))}</span></div><div class="fc-edge">${icon("down")}</div>` : "") : ""}
      <div class="fc-node">${dot(n)}<b>${esc(n?.label)}</b></div>
      ${dir !== "in" ? outE.map(e => `${line(e, e.target, "down")}<div class="fc-node">${dot(byId.get(e.target))}${esc(byId.get(e.target)?.label)}</div>`).join("") : ""}
      ${dir !== "in" && further.filter(e => outE.some(o => o.target === e.source)).map(e => `${line(e, e.target, "down")}<div class="fc-node">${dot(byId.get(e.target))}${esc(byId.get(e.target)?.label)}</div>`).join("")}
    </div></div>`;
}

export function dot(n) {
  if (!n) return `<span class="dot"></span>`;
  const c = n.type === "device" ? "var(--held)" : n.status === "suspicious" || n.status === "flagged" ? "var(--danger)" : n.status === "watch" ? "var(--held)" : n.status === "victim" ? "var(--victim)" : "var(--text-3)";
  return `<span class="dot" style="border-color:${c}${n.type === "device" ? ";border-radius:2px" : ""}"></span>`;
}

export const STATE_OF = o => STATE[o] || "settled";
