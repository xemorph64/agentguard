// Boot, routing, polling. Views are mounted once and hidden/shown, so switching tabs or a poll
// never destroys a selection, camera, playback position or filter. Polling asks for a cheap
// version number and only refetches when the server state actually changed.
import { api, post, store, conn, onConn, toast, $, $$ } from "./core.js";
import { createOverview } from "./views/overview.js";
import { createInvestigate } from "./views/investigate.js";
import { createSimulate } from "./views/simulate.js";
import { createNetwork } from "./views/network.js";
import { createAudit } from "./views/audit.js";
import { createDemo } from "./demo.js";

const main = $("#view");
const roots = {};
for (const name of ["overview", "investigate", "simulate", "network", "audit"]) {
  const el = document.createElement("div");
  el.style.height = "100%"; el.hidden = true; el.dataset.view = name;
  main.append(el); roots[name] = el;
}
const views = {};
views.investigate = createInvestigate(roots.investigate);
views.overview = createOverview(roots.overview, { runScenario: n => views.investigate.runScenario(n) });
views.simulate = createSimulate(roots.simulate);
views.network = createNetwork(roots.network);
views.audit = createAudit(roots.audit);

let current = null;
function route() {
  const [, name = "overview", param] = (location.hash || "#/overview").split("/");
  const view = views[name] ? name : "overview";
  if (current && current !== view) { views[current].hide?.(); roots[current].hidden = true; }
  roots[view].hidden = false;
  $$(".nav a").forEach(a => a.setAttribute("aria-current", a.dataset.route === view ? "page" : "false"));
  const changed = current !== view;
  current = view;
  store.ui.route = view;
  views[view].show?.(param ? decodeURIComponent(param) : null);
  if (changed) document.title = `AgentGuard · ${view[0].toUpperCase() + view.slice(1)}`;
}
export function go(name, param) {
  const h = `#/${name}${param ? "/" + encodeURIComponent(param) : ""}`;
  if (location.hash !== h) location.hash = h; else route();
}
addEventListener("hashchange", route);

// ------------------------------------------------------------ server state
let polling = false;
async function poll(force = false) {
  if (polling) return;
  polling = true;
  try {
    const { version } = await api("/version", { timeout: 6000 });
    if (force || version !== store.server.version) {
      const [overview, investigations] = await Promise.all([api("/overview"), api("/investigations")]);
      store.server.version = version;
      store.set("overview", overview);
      store.set("investigations", investigations);
      store.emit("version", version);
    }
  } catch { /* conn state already updated by api() */ }
  finally { polling = false; }
}
store.on("refresh", () => poll(true));

async function boot() {
  try {
    store.set("meta", await api("/meta"));
    await poll(true);
  } catch {
    setTimeout(boot, 2500);   // offline banner is showing; keep trying
    return;
  }
}

// ------------------------------------------------------------ connection UI
onConn(c => {
  const el = $("#conn");
  el.className = "conn " + (c.online ? "ok" : "bad");
  el.querySelector("span").textContent = c.online ? "Engine online" : "Backend offline";
  $("#offline").hidden = !!c.online;
  document.body.classList.toggle("stale", !c.online);
  if (c.online && !store.server.meta) boot();
});
$("#retryBtn").onclick = async () => {
  $("#retryBtn").disabled = true;
  try { await api("/health", { timeout: 5000 }); await (store.server.meta ? poll(true) : boot()); toast("Reconnected to the engine"); }
  catch { toast("Still can't reach the backend. Is ./run.sh running?", true); }
  $("#retryBtn").disabled = false;
};

$("#resetBtn").onclick = async () => {
  if (!confirm("Reset clears every payment, investigation and ledger record. Continue?")) return;
  try { await post("/reset"); await poll(true); toast("Workspace reset"); if (current === "investigate") go("investigate"); }
  catch (e) { toast(`Reset failed: ${e.message}`, true); }
};

const demo = createDemo({ views, go, main });
$("#demoBtn").onclick = () => demo.start();

route();
boot();
setInterval(() => { if (!document.hidden) poll(); }, 3000);
window.agentguard = { views, store };   // handy for debugging from the console
