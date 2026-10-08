// INVESTIGATE: scenario → money moves → network emerges → suspicious entity lights up →
// agents analyze → risk rises → evidence → decision → audit trail. Driven by the run's real timeline.
import { api, post, store, esc, icon, inr, inrShort, time, mins, plural, outcomePill, riskClass, stateBlock, toast, countTo, sevLabel, STATE, reducedMotion } from "../core.js";
import { Workbench, keyTxnSection } from "../workbench.js";
import { PAL } from "../graph/graph2d.js";

const SPEEDS = [0.5, 1, 2, 4];

export function createInvestigate(root) {
  root.innerHTML = `
  <div class="ws">
    <aside class="rail" aria-label="Scenarios and cases">
      <section class="rail-sec"><h2>Run a scenario</h2><div class="scen-grid" id="invScen"></div></section>
      <section class="rail-sec" style="flex:1"><h2>Investigations</h2><div id="invCases"></div></section>
    </aside>
    <section class="stage" aria-label="Money flow">
      <header class="story" id="story"></header>
      <div class="canvas-wrap" id="invCanvas"><div class="empty-stage" id="invEmpty"></div></div>
      <div class="playback" id="pb" hidden></div>
    </section>
    <aside class="inspector" id="invInsp" aria-label="Inspector"></aside>
  </div>`;
  const $ = s => root.querySelector(s);
  const S = { payload: null, runId: null, loading: false, error: null, scope: null,
              cursor: -1, playing: false, speed: 1, timer: null, liveItem: null };

  const wb = new Workbench({
    canvasWrap: $("#invCanvas"), inspector: $("#invInsp"),
    onScope: (account, hops) => setScope(account, hops),
  });
  wb.caseTitle = "Case summary";
  wb.caseRenderer = el => renderCase(el);

  // ------------------------------------------------------------ rail
  function renderScenarios() {
    const sc = store.server.meta?.scenarios || [];
    $("#invScen").innerHTML = sc.map(s => `<button class="btn" type="button" data-s="${s.id}" title="${esc(s.summary)}" ${S.loading ? "disabled" : ""}>${esc(s.title)}</button>`).join("");
    root.querySelectorAll("#invScen [data-s]").forEach(b => b.onclick = () => runScenario(b.dataset.s));
  }
  function renderCases() {
    const inv = store.server.investigations || [];
    $("#invCases").innerHTML = inv.length ? inv.map(r => {
      const [lab, cls] = sevLabel(r.severity);
      return `<button class="case-btn" type="button" data-run="${r.run_id}" aria-current="${r.run_id === S.runId}">
        <span class="t">${esc(r.title)}</span><span class="sev ${cls}">${lab}</span>
        <span class="s">${esc(r.headline || "")} · ${plural(r.count, "payment")}</span></button>`;
    }).join("") : `<p class="faint" style="font-size:13px">No investigations yet. Run a scenario above — every payment in it goes through the six agents.</p>`;
    root.querySelectorAll("[data-run]").forEach(b => b.onclick = () => { location.hash = `#/investigate/${b.dataset.run}`; });
  }
  store.on("meta", renderScenarios);
  store.on("investigations", inv => {
    renderCases();
    if (S.runId && !inv.some(r => r.run_id === S.runId) && !S.loading) {   // workspace was reset underneath us
      clear("This investigation no longer exists — the workspace was reset.");
    }
  });

  // ------------------------------------------------------------ loading
  async function runScenario(name, { autoplay = true } = {}) {
    S.loading = true; S.error = null; renderScenarios(); stop();
    const title = store.server.meta?.scenarios.find(s => s.id === name)?.title || name;
    setEmpty("loading", `Running “${title}” through the agents…`);
    try {
      const p = await post(`/simulate/${name}`);
      S.loading = false;
      history.replaceState(null, "", `#/investigate/${p.run_id}`);
      load(p, { autoplay });
      store.emit("refresh");
      return p;
    } catch (e) {
      S.loading = false; renderScenarios();
      setEmpty("error", "Scenario failed", e.message, () => runScenario(name, { autoplay }));
      throw e;
    }
  }
  async function openRun(runId) {
    if ((S.runId === runId && S.payload) || S.loading) return;
    stop();
    setEmpty("loading", "Loading investigation…");
    try { load(await api(`/investigations/${runId}`), { autoplay: false }); }
    catch (e) { setEmpty("error", e.status === 404 ? "Investigation not found" : "Couldn't load the investigation", e.status === 404 ? "It may have been cleared by a reset." : e.message, e.status === 404 ? null : () => openRun(runId)); }
  }
  function load(p, { autoplay }) {
    S.payload = p; S.runId = p.run_id; S.scope = null;
    wb.scopeLabel = null; wb.hops = null; wb.follow = null; wb.graph.setFollow(null); wb.graph.setHighlight(null); wb.evidenceLabel = null;
    wb.stack = []; wb.view = { kind: "case" };
    wb.setGraph(p.graph, p, { fit: true });
    $("#invEmpty").hidden = true;
    renderCases(); renderScenarios();
    S.cursor = autoplay ? -1 : p.timeline.length - 1;
    renderPlayback();
    applyCursor(false);
    wb.renderInspector();
    if (autoplay) setTimeout(() => play(), reducedMotion() ? 0 : 500);
  }
  function clear(msg) {
    stop(); S.payload = null; S.runId = null;
    wb.setGraph({ nodes: [], edges: [] }, null);
    $("#pb").hidden = true;
    setEmpty("empty", msg);
    renderStory(); wb.renderInspector();
  }
  function setEmpty(kind, title, detail = "", retry = null) {
    const el = $("#invEmpty");
    el.hidden = false;
    if (kind === "empty") {
      el.innerHTML = `<div><h2>${esc(title || "Pick a scenario to investigate")}</h2>
        <p>Each scenario sends real payments through the six agents. Watch the money move, see which account lights up, and why.</p>
        <button class="btn primary" type="button" id="invStart">${icon("play")}Run the mule fan-in scenario</button></div>`;
      el.querySelector("#invStart").onclick = () => runScenario("mule_fanin");
    } else el.innerHTML = `<div>${stateBlock(kind, title, detail, retry)}</div>`;
  }

  // ------------------------------------------------------------ scope (hop expansion)
  async function setScope(account, hops) {
    if (!account) {
      S.scope = null; wb.scopeLabel = null; wb.hops = null;
      wb.setGraph(S.payload.graph, S.payload, { fit: true });
      $("#pb").hidden = false; applyCursor(false); return;
    }
    try {
      const res = await api(`/graph/investigation?account=${encodeURIComponent(account)}&hops=${hops}`);
      stop(); S.cursor = S.payload ? S.payload.timeline.length - 1 : -1;
      S.scope = { account, hops };
      const n = res.graph.nodes.find(x => x.id === account);
      wb.scopeLabel = `${n?.label || account} · ${hops === 0 ? "full network" : plural(hops, "hop")}`;
      wb.hops = { account, n: hops };
      wb.graph.setReveal(null); wb.graph.setRiskOverride(null);
      wb.setGraph(res.graph, { ...(S.payload || {}), focus: account }, { fit: true });
      $("#pb").hidden = true;
      wb.renderInspector();
    } catch (e) { toast(`Couldn't expand the network: ${e.message}`, true); }
  }

  // ------------------------------------------------------------ playback
  const items = () => S.payload?.timeline || [];
  function renderPlayback() {
    const pb = $("#pb");
    const it = items();
    pb.hidden = !it.length || !!S.scope;
    if (pb.hidden) return;
    const t0 = it[0].ts;
    pb.innerHTML = `
      <div class="pb-ctrls">
        <button class="btn icon" type="button" data-pb="play" aria-label="${S.playing ? "Pause" : "Play investigation"}" title="${S.playing ? "Pause" : "Play investigation"} (space)">${icon(S.playing ? "pause" : "play")}</button>
        <button class="btn icon" type="button" data-pb="step" aria-label="Step" title="Step forward">${icon("step")}</button>
        <button class="btn icon" type="button" data-pb="replay" aria-label="Replay" title="Replay from the start">${icon("replay")}</button>
      </div>
      <div class="pb-caption" aria-live="polite"><span class="when" id="pbWhen"></span><span class="what" id="pbWhat"></span></div>
      <div class="pb-speed"><span class="faint" style="font-size:12px">Speed</span><div class="seg">${SPEEDS.map(s => `<button type="button" data-sp="${s}" aria-pressed="${S.speed === s}">${s}×</button>`).join("")}</div></div>
      <div class="track" id="track"><div class="rail-line"></div><div class="prog" id="prog"></div>
        ${it.map((x, i) => {
          const span = Math.max(1, it[it.length - 1].ts - t0);
          // position by real time, but keep a minimum gap so bursts stay clickable
          const lin = (x.ts - t0) / span, ord = i / Math.max(1, it.length - 1);
          const left = 1.5 + 97 * (0.45 * lin + 0.55 * ord);
          const c = x.kind === "event" ? "var(--text-2)" : `var(--${({ settled: "settled", held: "held", paused: "paused", blocked: "danger" })[x.state]})`;
          return `<button type="button" class="${x.kind === "event" ? "ev" : ""}" style="left:${left}%;--c:${c}" data-i="${i}" aria-label="${esc(x.caption || x.text || "")}" title="${esc(x.caption || x.text || "")}"><i></i></button>`;
        }).join("")}
      </div>`;
    pb.querySelector('[data-pb="play"]').onclick = () => S.playing ? stop() : play();
    pb.querySelector('[data-pb="step"]').onclick = () => { stop(); step(); };
    pb.querySelector('[data-pb="replay"]').onclick = () => { stop(); S.cursor = -1; applyCursor(false); play(); };
    pb.querySelectorAll("[data-sp]").forEach(b => b.onclick = () => { S.speed = Number(b.dataset.sp); renderPlayback(); updateTrack(); });
    pb.querySelectorAll("[data-i]").forEach(b => b.onclick = () => { stop(); S.cursor = Number(b.dataset.i); applyCursor(true); });
    updateTrack();
  }
  function updateTrack() {
    const it = items(), pb = $("#pb");
    if (pb.hidden) return;
    pb.querySelectorAll("[data-i]").forEach(b => { const i = +b.dataset.i; b.classList.toggle("done", i <= S.cursor); b.classList.toggle("cur", i === S.cursor); });
    const cur = pb.querySelector(`[data-i="${S.cursor}"]`);
    $("#prog").style.setProperty("--p", cur ? parseFloat(cur.style.left) / 100 : 0);
    const x = it[S.cursor];
    $("#pbWhen").textContent = x ? time(x.ts) : "";
    $("#pbWhat").innerHTML = x ? (x.kind === "event" ? `<span class="faint">Event · </span>${esc(x.text)}`
      : `${esc(x.caption)} — ${outcomePill(x.outcome)}`) : `<span class="faint">${it.length} steps · press play to watch the money move</span>`;
  }
  function play() {
    if (!items().length) return;
    if (S.cursor >= items().length - 1) S.cursor = -1;
    S.playing = true; renderPlayback(); tick();
  }
  function stop() { S.playing = false; clearTimeout(S.timer); if (S.payload) renderPlayback(); }
  function tick() {
    if (!S.playing) return;
    if (S.cursor >= items().length - 1) { S.playing = false; renderPlayback(); finish(); return; }
    step();
    const x = items()[S.cursor];
    const base = x.kind === "event" ? 1700 : x.state === "blocked" || x.state === "paused" ? 1900 : 1100;
    S.timer = setTimeout(tick, base / S.speed);
  }
  function step() {
    if (S.cursor >= items().length - 1) return finish();
    S.cursor++;
    applyCursor(true);
  }
  function finish() { wb.open({ kind: "case" }, false); }

  function applyCursor(animate) {
    const it = items();
    if (!S.payload || S.scope) return;
    const done = S.cursor >= it.length - 1;
    const upTo = it.slice(0, S.cursor + 1);
    const txns = new Set(upTo.filter(x => x.kind === "txn").map(x => x.txn_id));
    const lastTs = upTo.length ? upTo[upTo.length - 1].ts : (it[0]?.ts ?? 0) - 1;
    wb.graph.setReveal(done ? null : txns, lastTs);
    // risk as the agents saw it at this moment (backend decision scores, in order)
    if (done) wb.graph.setRiskOverride(null);
    else {
      const risk = new Map(S.payload.graph.nodes.filter(n => n.type === "account").map(n => [n.id, 0]));
      for (const x of upTo) if (x.kind === "txn" && (x.typology || x.outcome !== "ALLOW")) risk.set(x.risk_account, Math.max(risk.get(x.risk_account) || 0, x.score));
      const prev = wb.graph.riskOverride;
      const focus = S.payload.focus;
      if (animate && focus && prev && (risk.get(focus) || 0) - (prev.get(focus) || 0) >= 15) wb.graph.pulseNode(focus, (risk.get(focus) >= 60 ? PAL.blocked : PAL.held));
      wb.graph.setRiskOverride(risk);
    }
    const x = it[S.cursor];
    if (animate && x?.kind === "txn") wb.graph.burst(`${x.payer}>${x.payee}`, x.state);
    S.liveItem = done ? null : x;
    renderStory(animate);
    updateTrack();
    if (!done && x?.kind === "txn" && wb.view.kind === "case") renderLive();
    else if (done && wb.view.kind === "case") wb.renderInspector();
  }

  // ------------------------------------------------------------ story bar (facts update live during playback)
  function liveFacts() {
    const p = S.payload, focus = p.focus;
    const done = S.cursor >= items().length - 1;
    if (done) return { ...p.metrics, protected: p.metrics.blocked_amount + p.metrics.held_amount + p.metrics.paused_amount };
    const upTo = items().slice(0, S.cursor + 1).filter(x => x.kind === "txn");
    const settled = x => x.state === "settled" || x.state === "held";
    const ins = upTo.filter(x => x.payee === focus), outs = upTo.filter(x => x.payer === focus);
    return {
      inbound: ins.filter(settled).reduce((s, x) => s + x.amount, 0),
      inbound_attempted: ins.reduce((s, x) => s + x.amount, 0),
      protected: upTo.filter(x => !settled(x) || x.state === "held").reduce((s, x) => s + x.amount, 0),
      outbound_attempted: outs.reduce((s, x) => s + x.amount, 0),
      inbound_sources: new Set(ins.map(x => x.payer)).size,
      outbound_sinks: new Set(outs.map(x => x.payee)).size,
      focus_risk: Math.max(0, ...upTo.filter(x => x.risk_account === focus && (x.typology || x.outcome !== "ALLOW")).map(x => x.score)),
      blocked_amount: upTo.filter(x => x.state === "blocked").reduce((s, x) => s + x.amount, 0),
      transaction_count: upTo.length,
      suspicious_count: upTo.filter(x => x.outcome !== "ALLOW" && x.outcome !== "ALLOW_NUDGE").length,
      partial: true,
    };
  }
  function renderStory(animate = false) {
    const el = $("#story");
    const p = S.payload;
    if (!p) { el.innerHTML = `<div><h1>Investigation</h1><p class="sum">Run a scenario or open a case from the left.</p></div>`; return; }
    const ex = p.explanation || {};
    const done = S.cursor >= items().length - 1;
    const m = liveFacts();
    const danger = !!ex.alert;
    const alertTxt = done ? (ex.alert || "NO SUSPICIOUS ACTIVITY") : S.cursor < 0 ? "READY" : "ANALYZING PAYMENTS";
    const cls = done ? (danger ? "danger" : "ok") : "warn";
    const focusNode = p.graph.nodes.find(n => n.id === p.focus);
    const anyOut = (p.metrics.outbound_attempted || 0) > 0;
    const facts = p.focus ? [
      ["Sent to " + (focusNode?.label || "focus"), inrShort(m.inbound_attempted || 0), "in"],
      ...(anyOut ? [["Tried to move on", inrShort(m.outbound_attempted || 0), "out", (m.outbound_attempted || 0) > 0]] : []),
      ["Stopped or held", inrShort(m.protected || 0), "prot"],
      ...(p.metrics.inbound_sources >= 3 ? [["Sources", m.inbound_sources ?? 0, "src"]] : []),
      ["Risk", Math.round(m.focus_risk || 0), "risk", (m.focus_risk || 0) >= 60],
    ] : [
      ["Payments", m.transaction_count, "n"],
      ["Flow", inrShort(p.metrics.total_flow), "flow"],
      ["Challenged", m.suspicious_count, "s"],
    ];
    const prevVals = Object.fromEntries([...el.querySelectorAll(".fact .v")].map(v => [v.dataset.k, v.textContent]));
    el.innerHTML = `
      <div style="min-width:0">
        <div class="alert ${cls}">${icon(done && danger ? "alert" : done ? "check" : "flow")}${esc(alertTxt)}</div>
        <h1>${esc(p.title)}${done && ex.typology ? ` <span class="faint" style="font-weight:500;font-size:14px">${esc(ex.typology)} · ${esc(ex.typology_name)}</span>` : ""}</h1>
        <p class="sum">${done ? esc(ex.summary) : S.liveItem ? esc(S.liveItem.caption || S.liveItem.text || "") : esc(p.summary)}</p>
      </div>
      <div class="facts">${facts.map(([k, v, key, hot]) => `<div class="fact"><div class="k">${esc(k)}</div><div class="v ${hot ? "danger" : ""} ${animate && prevVals[key] !== undefined && prevVals[key] !== String(v) ? "tick" : ""}" data-k="${key}">${esc(v)}</div></div>`).join("")}</div>`;
  }

  // ------------------------------------------------------------ inspector: live step / case summary
  function renderLive() {
    const x = S.liveItem;
    const el = wb.insp;
    const meta = store.server.meta || {};
    const order = ["transaction", "behavior", "velocity", "mule", "aml", "counsel"];
    el.innerHTML = wb.header("Live analysis") + `
      <div class="insp-sec"><h3>Payment ${S.cursor + 1} of ${items().length}<span class="right">${outcomePill(x.outcome)}</span></h3>
        <div class="big-amt">${inr(x.amount)}</div>
        <p class="muted" style="font-size:13px;margin-top:6px">${esc(x.caption)}</p></div>
      <div class="insp-sec"><h3>Agents scoring this payment</h3><div class="agents">
        ${order.filter(a => a in x.agents).map(a => `<div class="agent ${a === "counsel" ? "counsel" : ""}"><span class="n">${esc(meta.agents?.[a] || a)}</span>
          <span class="bar"><i style="--w:${Math.max(2, x.agents[a]) / 100};background:${x.agents[a] >= 60 ? "var(--danger)" : x.agents[a] >= 30 ? "var(--held)" : "var(--settled)"}"></i></span>
          <span class="s ${a === "counsel" ? "" : riskClass(x.agents[a])}">${Math.round(x.agents[a])}</span></div>`).join("")}
      </div>
      <div class="verdict" style="grid-template-columns:auto 1fr"><div><div class="label">Final risk</div><div class="score ${riskClass(x.score)}">${Math.round(x.score)}<small> / 100</small></div></div>
        <div style="justify-self:end;text-align:right"><div class="label" style="margin-bottom:6px">Decision</div>${outcomePill(x.outcome, true)}</div>
        <div class="why">${x.hard_rule ? `<b style="color:var(--text)">Rule, not score:</b> ${esc(x.hard_rule === "flagged_beneficiary" ? "the beneficiary was already flagged, so new credits to it are held" : x.hard_rule === "flagged_account_outbound" ? "this account was already flagged, so it cannot move money out" : x.hard_rule)}.` : esc(x.routing_reason || "")}</div></div></div>
      <div class="insp-sec"><button class="btn sm" type="button" id="liveOpen">Inspect this transaction</button></div>`;
    wb.wireHeader();
    el.querySelector("#liveOpen").onclick = () => { stop(); wb.openEdge(`${x.payer}>${x.payee}`, x.txn_id); };
  }

  function renderCase(el) {
    const p = S.payload;
    if (!p) { el.innerHTML = `<div class="insp-sec"><p class="muted">Select a scenario to see the agents' reasoning, the evidence and the decision.</p></div>`; return; }
    if (S.cursor < items().length - 1 && !S.scope && S.liveItem) return renderLive();
    const ex = p.explanation || {}, m = p.metrics, kt = p.key_transaction;
    const fn = p.graph.nodes.find(n => n.id === p.focus);
    const danger = !!ex.alert;
    // money-network cases (mule, farm, structuring) get flow metrics; single-victim cases get the attempt
    const flowCase = m.inbound_sources >= 3 || m.outbound_attempted > 0;
    el.innerHTML = `
      <div class="insp-sec">
        <div class="final-card ${danger ? "" : "ok"}">
          <div class="alert">${icon(danger ? "alert" : "check")}${esc(ex.headline || "")}</div>
          <p style="margin-top:8px;font-size:13.5px">${esc(ex.summary || "")}</p>
          <div class="grid">
            ${p.focus && !flowCase ? `
              <div><div class="k">Sent to ${esc(fn?.label || "focus")}</div><div class="v">${inr(m.inbound_attempted)}</div></div>
              <div><div class="k">Actually reached it</div><div class="v">${inr(m.inbound)}</div></div>
              <div><div class="k">Payment attempts</div><div class="v">${p.decisions.filter(d => d.request.payee === p.focus).length}</div></div>
              <div><div class="k">Highest risk</div><div class="v ${riskClass(m.max_score)}">${Math.round(m.max_score)}</div></div>`
            : p.focus ? `
              <div><div class="k">${esc(fn?.label || "Focus")} received</div><div class="v">${inr(m.inbound)}</div></div>
              <div><div class="k">Tried to forward</div><div class="v ${m.outbound_attempted ? "r-high" : ""}">${inr(m.outbound_attempted)}</div></div>
              <div><div class="k">Inbound sources</div><div class="v">${m.inbound_sources}</div></div>
              <div><div class="k">Outbound sinks</div><div class="v">${m.outbound_sinks}</div></div>
              <div><div class="k">Holding period</div><div class="v">${mins(m.holding_minutes)}</div></div>
              <div><div class="k">Network risk</div><div class="v ${riskClass(m.network_risk)}">${Math.round(m.network_risk)}</div></div>`
            : `<div><div class="k">Payments</div><div class="v">${m.transaction_count}</div></div><div><div class="k">Flow</div><div class="v">${inr(m.total_flow)}</div></div>`}
            <div><div class="k">Stopped (blocked)</div><div class="v ${m.blocked_amount ? "r-high" : ""}">${inr(m.blocked_amount)}</div></div>
            <div><div class="k">Held / paused</div><div class="v">${inr(m.held_amount + m.paused_amount)}</div></div>
          </div>
          ${kt ? `<div style="display:flex;justify-content:space-between;align-items:center;margin-top:14px"><span class="label">Decision on key payment</span>${outcomePill(kt.outcome, true)}</div>` : ""}
        </div>
        ${p.focus ? `<div class="actions" style="margin-top:12px">
          <button class="btn sm primary" type="button" data-a="follow">${icon("flow")}Follow the money</button>
          <button class="btn sm" type="button" data-a="acct">Open ${esc(fn?.label || "account")}</button>
          ${kt ? `<a class="btn sm" href="#/audit/${esc(kt.txn_id)}">${icon("shield")}Audit record</a>` : ""}</div>` : ""}
      </div>
      ${ex.evidence?.length ? `<div class="insp-sec"><h3>Evidence at the moment it was caught</h3><ul class="evlist">${ex.evidence.map(e => `<li>${esc(e)}</li>`).join("")}</ul></div>` : ""}
      ${kt ? `<div class="insp-sec" id="ktSec"></div>` : ""}
      <div class="insp-sec"><h3>Transactions in this case</h3><table class="table"><tbody>
        ${p.decisions.map(d => `<tr data-e="${esc(d.request.payer)}>${esc(d.request.payee)}" data-t="${esc(d.txn_id)}">
          <td class="mono faint">${time(d.ts)}</td><td style="white-space:normal">${esc(nodeLabel(d.request.payer))} → ${esc(nodeLabel(d.request.payee))}</td>
          <td class="r mono">${inrShort(d.request.amount)}</td><td>${outcomePill(d.outcome)}</td></tr>`).join("")}</tbody></table></div>`;
    el.querySelector('[data-a="follow"]')?.addEventListener("click", () => wb.startFollow(p.focus, "both"));
    el.querySelector('[data-a="acct"]')?.addEventListener("click", () => wb.openNode(p.focus));
    el.querySelectorAll("tr[data-e]").forEach(r => r.onclick = () => wb.openEdge(r.dataset.e, r.dataset.t));
    if (kt) keyTxnSection(el.querySelector("#ktSec"), p, wb, { animate: S.revealAgents });
    S.revealAgents = false;
  }
  const nodeLabel = id => S.payload.graph.nodes.find(n => n.id === id)?.label || id;

  // keyboard: space = play/pause, → = step (when the stage has focus or nothing else does)
  root.addEventListener("keydown", e => {
    if (e.target.closest("input,select,textarea")) return;
    if (e.key === " " && S.payload && !S.scope) { e.preventDefault(); S.playing ? stop() : play(); }
  });

  renderStory(); setEmpty("empty"); wb.renderInspector(); renderScenarios(); renderCases();

  return {
    wb, state: S,
    show(param) { if (param && param !== S.runId) openRun(param); else if (!param && !S.payload && !S.loading) { const last = store.server.investigations?.[0]; if (last) location.hash = `#/investigate/${last.run_id}`; } wb.graph.dirty = true; },
    hide() { stop(); },
    runScenario, play, stop, items,
    isPlaying: () => S.playing,
    revealKeyAgents() { S.revealAgents = true; wb.stack = []; wb.graph.select(null); wb.open({ kind: "case" }, false); return document.querySelector("#ktSec"); },
  };
}
