// Core: API client (with connection state), formatting, tiny pub/sub store, DOM helpers.

// ---------------------------------------------------------------- API
const listeners = new Set();
export const conn = { online: null, lastError: null };
function setOnline(v, err) {
  if (conn.online === v && !err) return;
  conn.online = v; conn.lastError = err || null;
  listeners.forEach(fn => fn(conn));
}
export const onConn = fn => { listeners.add(fn); return () => listeners.delete(fn); };

export class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}

export async function api(path, { method = "GET", body, signal, timeout = 15000 } = {}) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(new DOMException("timeout", "TimeoutError")), timeout);
  signal?.addEventListener("abort", () => ctl.abort(signal.reason), { once: true });
  let res;
  try {
    res = await fetch(path, {
      method, signal: ctl.signal,
      headers: body ? { "content-type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    clearTimeout(t);
    if (signal?.aborted) throw e;               // caller cancelled: not a connectivity problem
    setOnline(false, e);
    throw new ApiError(e.name === "TimeoutError" || e.name === "AbortError"
      ? "The backend took too long to answer." : "Can't reach the backend.", 0);
  }
  clearTimeout(t);
  setOnline(true);
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* keep statusText */ }
    throw new ApiError(detail || `HTTP ${res.status}`, res.status);
  }
  return res.json();
}
export const post = (path, body, opts = {}) => api(path, { ...opts, method: "POST", body });

// ---------------------------------------------------------------- store
// server: data from the backend · ui: what the user is looking at. Polling only ever writes `server`.
export const store = {
  server: { meta: null, overview: null, investigations: [], version: -1 },
  ui: { route: "overview", runId: null },
  subs: new Map(),
  on(key, fn) { if (!this.subs.has(key)) this.subs.set(key, new Set()); this.subs.get(key).add(fn); return () => this.subs.get(key).delete(fn); },
  emit(key, v) { this.subs.get(key)?.forEach(fn => fn(v)); },
  set(key, v) { this.server[key] = v; this.emit(key, v); },
};

// ---------------------------------------------------------------- formatting
const inrFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
export const inr = n => "₹" + inrFmt.format(Math.round(n || 0));
export function inrShort(n) {
  n = Math.round(n || 0);
  if (n >= 1e7) return "₹" + (n / 1e7).toFixed(n >= 1e8 ? 0 : 1).replace(/\.0$/, "") + "Cr";
  if (n >= 1e5) return "₹" + (n / 1e5).toFixed(n >= 1e6 ? 1 : 2).replace(/\.?0+$/, "") + "L";
  if (n >= 1e3) return "₹" + (n / 1e3).toFixed(n >= 1e4 ? 1 : 1).replace(/\.0$/, "") + "K";
  return "₹" + n;
}
export const time = ts => new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
export const ago = ts => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
};
export const mins = m => m == null ? "—" : m < 1 ? `${Math.round(m * 60)}s` : m < 90 ? `${Math.round(m)}m` : `${(m / 60).toFixed(1)}h`;
export const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// outcome → money state (mirrors backend investigation.STATE)
export const STATE = { ALLOW: "settled", ALLOW_NUDGE: "settled", HOLD_CREDIT: "held", STEP_UP: "paused", COOLING_ROOM: "paused", PAUSE: "paused", BLOCK: "blocked" };
export const STATE_LABEL = { settled: "Settled", held: "Held", paused: "Paused", blocked: "Blocked" };
export const OUTCOME_SHORT = { ALLOW: "Allow", ALLOW_NUDGE: "Allow + nudge", STEP_UP: "Step-up", COOLING_ROOM: "Cooling room", HOLD_CREDIT: "Hold credit", PAUSE: "Pause", BLOCK: "Block" };
export const outcomePill = (o, big = false) => `<span class="pill ${STATE[o] || "settled"}${big ? " big" : ""}">${esc(OUTCOME_SHORT[o] || o)}</span>`;
export const riskClass = r => r >= 60 ? "r-high" : r >= 30 ? "r-watch" : "r-low";
export const sevLabel = s => s >= 4 ? ["Blocked", "r-high"] : s >= 2 ? ["Intervened", "r-watch"] : ["Clean", "r-low"];

// ---------------------------------------------------------------- DOM
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const icon = (id, cls = "ic") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

let toastTimer;
export function toast(msg, err = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", err);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

export function stateBlock(kind, title, detail = "", retry = null) {
  const id = "r" + Math.random().toString(36).slice(2, 8);
  const html = `<div class="state ${kind}" role="${kind === "error" ? "alert" : "status"}">
    ${kind === "loading" ? `<div class="skeleton" style="width:160px;height:10px"></div>` : ""}
    <strong>${esc(title)}</strong>${detail ? `<span>${esc(detail)}</span>` : ""}
    ${retry ? `<button class="btn sm" id="${id}" type="button">${icon("replay")}Retry</button>` : ""}</div>`;
  if (retry) queueMicrotask(() => document.getElementById(id)?.addEventListener("click", retry));
  return html;
}

// animate a number in an element from its current value (respects reduced motion)
export function countTo(el, to, fmt = v => Math.round(v), dur = 500) {
  const from = Number(el.dataset.v ?? 0);
  el.dataset.v = to;
  if (reducedMotion() || from === to) { el.textContent = fmt(to); return; }
  const t0 = performance.now();
  const step = now => {
    const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
    el.textContent = fmt(from + (to - from) * e);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
