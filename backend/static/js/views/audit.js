// AUDIT: every decision is a record in a SHA-256 hash chain. Verify it, try to tamper with it,
// and open any record's evidence.
import { api, post, store, esc, icon, inr, time, outcomePill, stateBlock, toast } from "../core.js";
import { agentPanel } from "../panels.js";

export function createAudit(root) {
  root.innerHTML = `
  <div class="audit">
    <section class="audit-main">
      <div style="display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap">
        <div><h1 style="font-size:20px">Audit ledger</h1><p class="muted" style="margin-top:4px;max-width:70ch">Each decision is hashed together with the hash of the record before it. Change any past record and every later link breaks — so the history can be proven, not just trusted.</p></div>
        <div class="actions" style="margin-left:auto">
          <button class="btn primary" type="button" id="auVerify">${icon("shield")}Verify chain</button>
          <button class="btn danger" type="button" id="auTamper" title="Edits one record in a sandbox copy — the live chain is never modified">Tamper demo</button>
          <button class="btn" type="button" id="auRestore">Restore</button>
        </div>
      </div>
      <div id="auBanner"></div>
      <div class="seg" role="group" aria-label="Filter" id="auFilter" style="margin-bottom:10px">
        ${[["all", "All"], ["BLOCK", "Blocked"], ["held", "Held / paused"], ["allowed", "Allowed"]].map(([k, l], i) => `<button type="button" data-f="${k}" aria-pressed="${i === 0}">${l}</button>`).join("")}
      </div>
      <div class="panel table-wrap"><div id="auTable"></div></div>
    </section>
    <aside class="inspector" id="auInsp" aria-label="Record evidence"></aside>
  </div>`;
  const $ = s => root.querySelector(s);
  const S = { rows: null, verify: null, sandbox: null, filter: "all", sel: null, error: null };

  async function load() {
    try {
      const [rows, verify] = await Promise.all([api("/ledger?limit=500"), api("/ledger/verify")]);
      S.rows = rows; S.verify = verify; S.error = null;
    } catch (e) { S.error = e; }
    render();
  }
  function banner() {
    const v = S.sandbox || S.verify;
    if (!v) return "";
    if (S.sandbox && !S.sandbox.valid) {
      const b = S.sandbox.broken;
      return `<div class="chain-banner bad">${icon("alert")}<b>Tampering detected at record #${b.seq}.</b><span>${esc(b.reason)} — the record's content no longer matches its hash, so every record after it is unverifiable. (Sandbox copy; the live ledger is untouched.)</span></div>`;
    }
    return `<div class="chain-banner ok">${icon("check")}<b>Chain intact.</b><span>${v.records} records verified · Merkle root <span class="hash">${esc((v.anchor?.root || "").slice(0, 16))}…</span></span></div>`;
  }
  function render() {
    $("#auBanner").innerHTML = S.error && !S.rows ? "" : banner();
    if (S.error && !S.rows) { $("#auTable").innerHTML = stateBlock("error", "Couldn't load the ledger", S.error.message, load); return; }
    if (!S.rows) { $("#auTable").innerHTML = stateBlock("loading", "Loading ledger…"); return; }
    if (!S.rows.length) { $("#auTable").innerHTML = stateBlock("empty", "No decisions recorded yet", "Run a scenario — every payment decision is written here."); renderInsp(); return; }
    const f = S.filter;
    const rows = S.rows.filter(r => f === "all" || (f === "BLOCK" && r.decision === "BLOCK") || (f === "allowed" && r.decision.startsWith("ALLOW")) || (f === "held" && !r.decision.startsWith("ALLOW") && r.decision !== "BLOCK"));
    const broken = S.sandbox && !S.sandbox.valid ? S.sandbox.broken.seq : null;
    $("#auTable").innerHTML = `<table class="table"><thead><tr><th>#</th><th>Time</th><th>Transaction</th><th>Decision</th><th class="r">Risk</th><th class="r">Amount</th><th>Typology</th><th>Hash</th><th>Links to</th></tr></thead><tbody>
      ${rows.map(r => `<tr data-tx="${esc(r.txn_id)}" aria-selected="${S.sel === r.txn_id}" class="${broken && r.seq === broken ? "broken" : ""}" style="${broken && r.seq > broken ? "opacity:.5" : ""}">
        <td class="mono faint">${r.seq}</td><td class="mono">${time(r.ts)}</td><td class="mono">${esc(r.txn_id)}</td><td>${outcomePill(r.decision)}</td>
        <td class="r mono">${Math.round(r.score)}</td><td class="r mono">${inr(r.amount)}</td><td>${esc((r.typology || []).join(", ") || "—")}</td>
        <td class="hash">${esc(r.hash.slice(0, 10))}…</td><td class="hash">${esc(r.prev_hash.slice(0, 10))}…</td></tr>`).join("")}
    </tbody></table>`;
    root.querySelectorAll("tr[data-tx]").forEach(tr => tr.onclick = () => openRecord(tr.dataset.tx));
    renderInsp();
  }
  async function openRecord(txn) {
    S.sel = txn;
    history.replaceState(null, "", `#/audit/${txn}`);
    root.querySelectorAll("tr[data-tx]").forEach(tr => tr.setAttribute("aria-selected", String(tr.dataset.tx === txn)));
    await renderInsp();
  }
  async function renderInsp() {
    const el = $("#auInsp");
    const rec = S.rows?.find(r => r.txn_id === S.sel);
    if (!S.sel || !rec) {
      el.innerHTML = `<div class="insp-h"><span class="crumb">Record evidence</span></div><div class="insp-sec"><p class="muted">Select a record to see exactly what was decided, by which agents, and how it is chained.</p></div>`;
      return;
    }
    el.innerHTML = `<div class="insp-h"><span class="crumb">Record #${rec.seq}</span></div>
      <div class="insp-sec"><h3>Decision record<span class="right">${outcomePill(rec.decision)}</span></h3>
        <div class="big-amt">${inr(rec.amount)}</div>
        <dl class="dl" style="margin-top:12px">
          <dt>Receipt</dt><dd>${esc(rec.receipt_id)}</dd><dt>Risk</dt><dd>${Math.round(rec.score)}</dd>
          <dt>Policy</dt><dd>${esc(rec.policy_version)}</dd><dt>Models</dt><dd>${esc(Object.values(rec.model_versions || {}).join(" · "))}</dd>
          <dt>Features hash</dt><dd title="${esc(rec.features_hash)}">${esc(rec.features_hash.slice(0, 22))}…</dd>
        </dl></div>
      <div class="insp-sec"><h3>Chain link</h3>
        <div class="hash" style="word-break:break-all"><span class="faint">prev</span> ${esc(rec.prev_hash)}</div>
        <div style="padding:6px 0 6px 4px;color:var(--text-3)">${icon("down")}</div>
        <div class="hash" style="word-break:break-all;color:var(--text)"><span class="faint">hash</span> ${esc(rec.hash)}</div>
        <p class="faint" style="font-size:12px;margin-top:8px">hash = SHA-256(prev_hash ‖ canonical JSON of this record)</p></div>
      <div class="insp-sec" id="auTx">${stateBlock("loading", "Loading decision evidence…")}</div>`;
    try {
      const tx = await api(`/transactions/${encodeURIComponent(rec.txn_id)}`);
      if (S.sel !== rec.txn_id) return;
      const box = el.querySelector("#auTx");
      box.innerHTML = `<h3>Evidence</h3><p style="font-size:13.5px;margin-bottom:12px">${esc(tx.payer_label)} → ${esc(tx.payee_label)}${tx.caption ? `<br><span class="muted">${esc(tx.caption)}</span>` : ""}</p><div id="auAgents"></div>
        ${tx.run_id ? `<a class="btn sm" style="margin-top:14px" href="#/investigate/${esc(tx.run_id)}">Open the investigation</a>` : ""}`;
      agentPanel(box.querySelector("#auAgents"), tx, { animate: false });
    } catch (e) {
      el.querySelector("#auTx").innerHTML = stateBlock("error", "Couldn't load the decision", e.message, renderInsp);
    }
  }

  $("#auVerify").onclick = async () => {
    try { S.verify = await api("/ledger/verify"); S.sandbox = null; render(); toast(S.verify.valid ? `Chain verified · ${S.verify.records} records` : "Chain verification failed", !S.verify.valid); }
    catch (e) { toast(`Verify failed: ${e.message}`, true); }
  };
  $("#auTamper").onclick = async () => {
    try {
      const r = await post("/ledger/tamper");
      if (!r.tamper.ok) return toast("Nothing to tamper with yet — run a scenario first.");
      S.sandbox = r.verify; render();
      toast(`Record #${r.tamper.seq}: amount ${inr(r.tamper.was)} → ${inr(r.tamper.now)} (sandbox)`);
      root.querySelector("tr.broken")?.scrollIntoView({ block: "center", behavior: "smooth" });
    } catch (e) { toast(`Tamper demo failed: ${e.message}`, true); }
  };
  $("#auRestore").onclick = async () => { try { await post("/ledger/restore"); S.sandbox = null; await load(); toast("Sandbox restored · chain intact"); } catch (e) { toast(e.message, true); } };
  root.querySelectorAll("[data-f]").forEach(b => b.onclick = () => { S.filter = b.dataset.f; root.querySelectorAll("[data-f]").forEach(x => x.setAttribute("aria-pressed", String(x === b))); render(); });
  store.on("version", () => { if (!root.hidden) load(); });

  return {
    async show(param) { await load(); if (param) openRecord(param); },
    hide() {},
    async verify() { $("#auVerify").click(); },
    openRecord, load,
  };
}
