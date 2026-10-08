// Guided demo. Every stage drives the real backend (reset, simulate, transactions, ledger);
// the script only decides what to show and for how long. Presenter can pause, skip, replay, exit.
import { api, post, esc, icon, inr, mins, outcomePill, store, toast, time } from "./core.js";

class Skip extends Error {}

export function createDemo({ views, go, main }) {
  const bar = document.getElementById("demoBar");
  const D = { active: false, paused: false, skip: false, idx: 0, gate: null, ctx: {}, token: 0 };
  const inv = () => views.investigate;

  const STEPS = [
    { title: "A legitimate payment", text: "Eight everyday payments between people who know each other. All six agents agree: allow.",
      run: async () => {
        go("investigate"); setSpeed(4);
        await inv().runScenario("normal");
        await playback();
        await wait(2500);
      } },
    { title: "Account takeover", text: "SIM swap, PIN reset, then ₹49,999 from an unknown phone in Patna. Watch the agents challenge it.",
      run: async () => {
        setSpeed(2);
        await inv().runScenario("account_takeover");
        await playback();
        inv().revealKeyAgents()?.scrollIntoView({ block: "start", behavior: "smooth" });
        await wait(5500);
      } },
    { title: "Mule network", text: "Ten unrelated victims pay one 3-day-old account. Watch the money converge — and what happens when it tries to leave.",
      run: async () => {
        setSpeed(1);
        D.ctx.mule = await inv().runScenario("mule_fanin");
        await playback();
        await wait(3000);
      } },
    { title: "Follow the money", text: "From the suspected mule: every rupee in, every rupee it tried to move on.",
      run: async () => {
        const p = D.ctx.mule;
        inv().wb.startFollow(p.focus, "both");
        await wait(6500);
        inv().wb.clearFollow();
      } },
    { title: "Agent detection", text: "Each agent scores the cash-out independently. Click a signal to see the graph evidence behind it.",
      run: async () => {
        inv().revealKeyAgents()?.scrollIntoView({ block: "start", behavior: "smooth" });
        await wait(2400);
        // demonstrate a signal with a real graph footprint (pass-through → in + out edges)
        const prefer = ["pass_through_24h", "distinct_sources_24h", "shared_device_accounts", "unlinked_sources"];
        const btns = [...document.querySelectorAll("#ktSec .why-btn:not([disabled])")];
        const btn = btns.find(b => prefer.includes(b.dataset.f)) || btns[0];
        btn?.click();
        await wait(5000);
        if (btn?.getAttribute("aria-pressed") === "true") btn.click();
      } },
    { title: "Block decision", text: "The cash-out is blocked before it settles. The mule is flagged; every later credit to it is held.",
      run: async () => {
        const kt = D.ctx.mule.key_transaction;
        inv().wb.openEdge(`${kt.payer}>${kt.payee}`, kt.txn_id);
        inv().wb.graph.select({ type: "edge", id: `${kt.payer}>${kt.payee}` }, { center: true });
        await wait(6000);
      } },
    { title: "Audit evidence", text: "The decision is a record in a SHA-256 hash chain. Verify it — then try to tamper with it.",
      run: async () => {
        const kt = D.ctx.mule.key_transaction;
        go("audit", kt.txn_id);
        await wait(1800);
        await views.audit.verify();
        D.ctx.verify = await api("/ledger/verify");
        D.ctx.ledger = await api(`/ledger/record/${kt.txn_id}`);
        await wait(4500);
      } },
    { title: "Why AgentGuard stopped this", text: "Everything on this card comes from the engine's own records.",
      run: async () => { await showWhy(); await wait(600000, true); } },
  ];

  function setSpeed(s) { inv().state.speed = s; }

  // ------------------------------------------------------------ pausable / skippable waiting
  function wait(ms, untilSkip = false) {
    return new Promise((resolve, reject) => {
      const tok = D.token;
      let left = ms, last = performance.now();
      const iv = setInterval(() => {
        if (!D.active || tok !== D.token) { clearInterval(iv); return reject(new Skip()); }
        if (D.skip) { clearInterval(iv); D.skip = false; return untilSkip ? resolve() : reject(new Skip()); }
        const now = performance.now();
        if (!D.paused) left -= now - last;
        last = now;
        if (left <= 0) { clearInterval(iv); resolve(); }
      }, 100);
    });
  }
  async function playback() {
    // the investigate view auto-plays a freshly run scenario; wait for its real timeline to finish
    const tok = D.token;
    await wait(700);
    while (D.active && tok === D.token) {
      if (D.skip) { D.skip = false; inv().stop(); finishNow(); throw new Skip(); }
      if (!D.paused && !inv().isPlaying()) {
        if (inv().state.cursor >= inv().items().length - 1) return;
        inv().play();
      }
      await new Promise(r => setTimeout(r, 150));
    }
    throw new Skip();
  }
  function finishNow() {
    const s = inv().state;
    s.cursor = inv().items().length - 2;
    inv().play();   // one tick lands on the final state
    setTimeout(() => inv().stop(), 50);
  }

  // ------------------------------------------------------------ the final card
  async function showWhy() {
    const p = D.ctx.mule;
    if (!p) return;
    const kt = p.key_transaction, m = p.metrics, ex = p.explanation;
    const rec = D.ctx.ledger || await api(`/ledger/record/${kt.txn_id}`).catch(() => null);
    const v = D.ctx.verify || await api("/ledger/verify").catch(() => null);
    const agents = kt.agents.filter(a => a.agent !== "counsel");
    const alarmed = agents.filter(a => a.score >= 40);
    go("investigate", p.run_id);
    const sheet = document.createElement("div");
    sheet.className = "why-sheet"; sheet.id = "whySheet";
    sheet.innerHTML = `<div class="why-card" role="dialog" aria-label="Why AgentGuard stopped this">
      <div class="alert" style="color:var(--danger);font-size:12px;font-weight:600;letter-spacing:.06em;display:flex;gap:7px;align-items:center">${icon("alert")}${esc(ex.headline)}</div>
      <h1 style="margin-top:6px">Why AgentGuard stopped this</h1>
      <p class="lead">${esc(ex.summary)}</p>
      <div class="why-grid">
        <div><h3>Risk</h3><div class="v r-high">${Math.round(kt.score)}<span class="faint" style="font-size:14px"> / 100</span></div><p class="muted" style="font-size:12.5px;margin-top:4px">${esc(kt.dominant)} · ${esc(kt.typology_name)}</p></div>
        <div><h3>Money flow</h3><div class="v">${inr(m.inbound)} in</div><p class="muted" style="font-size:12.5px;margin-top:4px">${m.inbound_sources} unrelated senders → ${inr(m.outbound_attempted)} out attempted · held ${mins(m.holding_minutes)}</p></div>
        <div><h3>Decision</h3><div style="margin-top:4px">${outcomePill(kt.outcome, true)}</div><p class="muted" style="font-size:12.5px;margin-top:8px">${esc(kt.routing_reason)} · ${inr(m.blocked_amount)} stopped</p></div>
        <div><h3>Agent consensus</h3><div class="v">${alarmed.length} of ${agents.length}</div><p class="muted" style="font-size:12.5px;margin-top:4px">${agents.map(a => `${esc(a.label)} ${Math.round(a.score)}`).join(" · ")}</p></div>
        <div><h3>Evidence when it was caught</h3><ul class="evlist" style="margin-top:2px">${ex.evidence.slice(0, 4).map(e => `<li>${esc(e)}</li>`).join("")}</ul></div>
        <div><h3>Audit record</h3><div class="mono" style="font-size:14px">${esc(rec?.receipt_id || kt.receipt_id)}</div><p class="hash" style="margin-top:4px;word-break:break-all">#${rec?.seq ?? "—"} · ${esc((rec?.hash || "").slice(0, 24))}…</p><p style="font-size:12.5px;margin-top:4px;color:${v?.valid ? "var(--settled)" : "var(--danger)"}">${v?.valid ? `Chain intact · ${v.records} records verified` : "Chain not verified"}</p></div>
      </div>
      <div class="actions" style="margin-top:20px;justify-content:flex-end">
        <button class="btn" type="button" data-w="replay">${icon("replay")}Replay demo</button>
        <button class="btn primary" type="button" data-w="close">Explore it yourself</button></div></div>`;
    main.append(sheet);
    sheet.querySelector('[data-w="close"]').onclick = () => stop();
    sheet.querySelector('[data-w="replay"]').onclick = () => start();
    sheet.querySelector('[data-w="close"]').focus();
  }

  // ------------------------------------------------------------ control bar
  function renderBar() {
    if (!D.active) { bar.hidden = true; return; }
    const s = STEPS[D.idx];
    bar.hidden = false;
    bar.innerHTML = `<span class="step">STEP ${D.idx + 1} / ${STEPS.length}</span>
      <div class="txt"><b>${esc(s.title)}</b><span>${esc(s.text)}</span></div>
      <div class="dots" aria-hidden="true">${STEPS.map((_, i) => `<i class="${i <= D.idx ? "on" : ""}"></i>`).join("")}</div>
      <div class="ctl">
        <button class="btn icon" type="button" data-d="pause" aria-label="${D.paused ? "Resume" : "Pause"}" title="${D.paused ? "Resume" : "Pause"}">${icon(D.paused ? "play" : "pause")}</button>
        <button class="btn icon" type="button" data-d="skip" aria-label="Skip to next step" title="Skip">${icon("skip")}</button>
        <button class="btn icon" type="button" data-d="replay" aria-label="Replay demo" title="Replay from the start">${icon("replay")}</button>
        <button class="btn icon ghost" type="button" data-d="exit" aria-label="Exit demo" title="Exit demo">${icon("close")}</button>
      </div>`;
    bar.querySelector('[data-d="pause"]').onclick = () => { D.paused = !D.paused; if (D.paused) inv().stop(); renderBar(); };
    bar.querySelector('[data-d="skip"]').onclick = () => { D.skip = true; if (D.paused) { D.paused = false; renderBar(); } };
    bar.querySelector('[data-d="replay"]').onclick = () => start();
    bar.querySelector('[data-d="exit"]').onclick = () => stop();
  }

  async function start() {
    document.getElementById("whySheet")?.remove();
    D.token++; const tok = D.token;
    Object.assign(D, { active: true, paused: false, skip: false, idx: 0, ctx: {} });
    renderBar();
    try { await post("/reset"); store.emit("refresh"); }
    catch (e) { toast(`Demo couldn't start: ${e.message}`, true); return stop(); }
    for (D.idx = 0; D.idx < STEPS.length; D.idx++) {
      if (!D.active || tok !== D.token) return;
      renderBar();
      try { await STEPS[D.idx].run(); }
      catch (e) {
        if (!(e instanceof Skip)) { toast(`Demo step failed: ${e.message}`, true); console.error(e); }
        if (!D.active || tok !== D.token) return;
      }
    }
    if (tok === D.token) stop(false);
  }
  function stop(removeSheet = true) {
    D.active = false; D.token++;
    if (removeSheet) document.getElementById("whySheet")?.remove();
    renderBar();
  }
  document.addEventListener("keydown", e => {
    if (!D.active || e.target.closest("input,select,textarea")) return;
    if (e.key === "Escape") { stop(); }
  });
  return { start, stop, get active() { return D.active; } };
}
