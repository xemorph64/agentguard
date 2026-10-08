from collections import Counter, deque, defaultdict
from pathlib import Path
from typing import Optional
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.core.types import Txn, now, Account, OUTCOME_META, TYPOLOGIES
from app.agents.orchestrator import Orchestrator
from app.db.memory import MemoryStore
from app.db.sqlite import Db
from app.graph.memgraph import Graph
from app.policy.engine import PolicyEngine
from app.chase.engine import ChaseEngine
from app.ledger.chain import Ledger
from app.holds.engine import HoldEngine

import scenarios as scenario_lib
from investigation import (Investigator, FEATURE_LABELS, AGENT_LABELS, AGENT_ROLE, SEVERITY, STATE,
                           SETTLED_OUTCOMES, FRICTION)

STATIC = Path(__file__).parent / "static"


class DummyHub:
    def publish(self, topic, payload): pass


class DummyMetrics:
    def __init__(self):
        self.totals = defaultdict(float)

    def record_decision(self, d, txn): pass


class DummyCases:
    def create(self, txn, d, status):
        class CaseMock:
            case_id = "C-" + txn.txn_id[:8]
            def to_dict(self): return {"case_id": self.case_id}
        return CaseMock()


class Services:
    def __init__(self):
        self.db = Db(":memory:")
        self.store = MemoryStore()
        self.graph = Graph()
        self.policy = PolicyEngine(self.db)
        self.policy.load_default()
        self.chase = ChaseEngine(self)
        self.ledger = Ledger(self.db)
        self.holds = HoldEngine(self)
        self.cases = DummyCases()
        self.hub = DummyHub()
        self.metrics = DummyMetrics()


class Workspace:
    """All server state. `reset()` swaps it wholesale; `version` lets clients poll cheaply."""

    def __init__(self):
        self.version = 0
        self.reset()

    def reset(self):
        self.svcs = Services()
        self.orchestrator = Orchestrator(self.svcs)
        self.history: deque = deque(maxlen=3000)   # recent decisions, newest first
        self.decisions: dict[str, dict] = {}        # txn_id -> decision (authoritative)
        self.runs: dict[str, dict] = {}             # run_id -> scenario run record
        self.run_counter = 0
        self.version += 1
        ensure_account("acc_1", name="Payer", persona="gig", ws=self)
        ensure_account("acc_2", name="Payee", persona="biz", ws=self)

    @property
    def investigator(self) -> Investigator:
        return Investigator(self)


app = FastAPI(title="AgentGuard API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def revalidate_ui(request, call_next):
    """No build step means no hashed filenames: make browsers revalidate (cheap, ETag-based) so a
    stale cached stylesheet can never be paired with fresh JS after an update."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def ensure_account(aid: str, ws: "Workspace" = None, **kw) -> Account:
    ws = ws or WS
    acc = ws.svcs.store.accounts.get(aid)
    if acc is None:
        acc = ws.svcs.store.add_account(Account(id=aid, name=kw.pop("name", aid), persona=kw.pop("persona", "salaried"),
                                                vpa=f"{aid.lower()}@upi", phone="000", **kw))
        ws.svcs.graph.add_node(aid, "Account", acc.name, persona=acc.persona)
    return acc


WS = Workspace()


class TxnRequest(BaseModel):
    payer: str
    payee: str
    amount: float = Field(gt=0)
    channel: str = "p2p"
    device_fp: str = ""
    geo: str = ""
    on_call: bool = False
    call_minutes: float = 0.0
    screen_share: bool = False
    sim_changed_hrs_ago: Optional[float] = None
    pin_reset_hrs_ago: Optional[float] = None


async def process(req: dict, *, ts: Optional[float] = None, scenario: str = "manual", run_id: str = "",
                  caption: str = "") -> dict:
    """Score one payment through the real orchestrator and record its effects."""
    svcs = WS.svcs
    if req["payer"] == req["payee"]:
        raise HTTPException(422, "payer and payee must differ")
    ensure_account(req["payer"])
    ensure_account(req["payee"])
    txn = Txn(txn_id="TX" + uuid.uuid4().hex[:12].upper(), ts=ts or now(), source="scenario" if run_id else "api",
              **req)
    decision = await WS.orchestrator.score(txn, run_id=run_id)
    d = decision.to_dict()
    settled = d["outcome"] in SETTLED_OUTCOMES
    # every attempt is an edge (evidence); only settled ones count as money flow in analytics
    svcs.graph.add_edge(txn.payer, txn.payee, "PAID", txn_id=txn.txn_id, ts=txn.ts, amount=txn.amount,
                        outcome=d["outcome"], score=d["score"], settled=settled, dominant=d["dominant"])
    if txn.device_fp:
        svcs.graph.add_node(txn.device_fp, "Device", txn.device_fp)
        svcs.graph.add_edge(txn.payer, txn.device_fp, "USED_DEVICE", ts=txn.ts)
    svcs.graph.refresh_warm_properties(svcs.store)
    d["request"] = {**req, "scenario": scenario}
    d["run_id"] = run_id
    d["caption"] = caption
    WS.history.appendleft(d)
    WS.decisions[d["txn_id"]] = d
    WS.version += 1
    return d


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/analyze")
async def analyze(req: TxnRequest):
    return await process(req.model_dump())


@app.get("/health")
def health():
    return {"status": "ok", "version": WS.version}


@app.get("/version")
def version():
    return {"version": WS.version, "decisions": len(WS.decisions), "runs": len(WS.runs)}


@app.get("/meta")
def meta():
    return {"outcomes": OUTCOME_META, "typologies": TYPOLOGIES, "features": FEATURE_LABELS,
            "agents": AGENT_LABELS, "agent_roles": AGENT_ROLE, "states": STATE,
            "scenarios": [{"id": k, "title": v[0], "summary": v[1]} for k, v in scenario_lib.SCENARIOS.items()],
            "thresholds": WS.svcs.policy.th, "policy_version": WS.svcs.policy.active_version}


@app.get("/transactions")
def transactions(limit: int = 100):
    return list(WS.history)[:limit]


@app.get("/transactions/{txn_id}")
def transaction(txn_id: str):
    d = WS.decisions.get(txn_id)
    if d is None:
        raise HTTPException(404, f"unknown transaction {txn_id}")
    return WS.investigator.txn_detail(d)


@app.get("/stats")
def stats():
    h = list(WS.history)
    lat = sorted(x["total_latency_ms"] for x in h)
    return {
        "total": len(h),
        "by_outcome": Counter(x["outcome"] for x in h),
        "by_typology": Counter(x["dominant"] for x in h if x["dominant"]),
        "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else 0,
        "p95_latency_ms": lat[int(len(lat) * 0.95)] if lat else 0,
    }


@app.get("/overview")
def overview():
    h = list(WS.decisions.values())
    amt = lambda pred: round(sum(float(d["request"]["amount"]) for d in h if pred(d)), 2)  # noqa: E731
    lat = sorted(x["total_latency_ms"] for x in h)
    inv = WS.investigator
    risk = inv.account_risk()
    active = [r for r in WS.runs.values() if r.get("severity", 0) >= 2]
    top = max(((a, r) for a, r in risk.items() if a in WS.svcs.graph.nodes), key=lambda kv: kv[1], default=(None, 0))
    return {
        "version": WS.version,
        "monitored": len(h),
        "suspicious": sum(1 for d in h if d["outcome"] in FRICTION),
        "allowed": sum(1 for d in h if d["outcome"] in ("ALLOW", "ALLOW_NUDGE")),
        "blocked_amount": amt(lambda d: d["outcome"] == "BLOCK"),
        "held_amount": amt(lambda d: d["outcome"] == "HOLD_CREDIT"),
        "paused_amount": amt(lambda d: d["outcome"] in ("PAUSE", "COOLING_ROOM", "STEP_UP")),
        "protected_amount": amt(lambda d: d["outcome"] in FRICTION),
        "active_investigations": len(active),
        "investigations": len(WS.runs),
        "network_risk": round(top[1], 1),
        "network_risk_account": top[0] if top[1] > 0 else None,
        "network_risk_label": WS.svcs.store.accounts[top[0]].name if top[0] and top[1] > 0 else None,
        "by_outcome": Counter(d["outcome"] for d in h),
        "by_typology": Counter(d["dominant"] for d in h if d["dominant"]),
        "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else 0,
        "p95_latency_ms": lat[int(len(lat) * 0.95)] if lat else 0,
        "ledger_records": WS.svcs.db.ledger_count(),
    }


@app.get("/graph")
def graph():
    g = WS.svcs.graph
    return g.subgraph_for(list(g.nodes), {"PAID", "USED_DEVICE"})


@app.get("/graph/investigation")
def graph_investigation(run_id: Optional[str] = None, account: Optional[str] = None, hops: int = 1):
    """Normalized investigation graph.

    - run_id: one scenario run (its accounts, all their payments)
    - account + hops: an account's neighbourhood (hops=0 → its whole connected network)
    - neither: the full network
    """
    inv = WS.investigator
    if run_id:
        run = WS.runs.get(run_id)
        if run is None:
            raise HTTPException(404, f"unknown run {run_id}")
        return {**_run_payload(run), "scope": {"kind": "run", "run_id": run_id}}
    if account:
        if account not in WS.svcs.store.accounts:
            raise HTTPException(404, f"unknown account {account}")
        scope = inv.neighbourhood(account, hops)
        accounts = {a for a in scope if a in WS.svcs.store.accounts}
        txn_ids = [d["txn_id"] for d in sorted(WS.decisions.values(), key=lambda d: d["ts"])
                   if d["request"]["payer"] in accounts and d["request"]["payee"] in accounts]
        res = inv.investigate(txn_ids, accounts=accounts)
        res["focus"] = account
        res["investigation"]["focus"] = account
        return {**res, "scope": {"kind": "account", "account": account, "hops": hops}}
    txn_ids = [d["txn_id"] for d in sorted(WS.decisions.values(), key=lambda d: d["ts"])]
    accounts = {a for d in WS.decisions.values() for a in (d["request"]["payer"], d["request"]["payee"])}
    res = inv.investigate(txn_ids, accounts=accounts)
    res["timeline"] = []   # the whole-network view is not a story
    return {**res, "scope": {"kind": "network"}}


@app.get("/accounts/{account_id}")
def account_detail(account_id: str):
    res = WS.investigator.account(account_id)
    if res is None:
        raise HTTPException(404, f"unknown account {account_id}")
    return res


# ---------- legacy single-account money map (kept for API compatibility) ----------
@app.get("/v1/accounts")
def v1_accounts():
    return [{"id": aid, "display_name": a.name, "role": a.persona or "user"}
            for aid, a in WS.svcs.store.accounts.items() if not aid.startswith("SIM-")]


@app.get("/v1/graph/account/{account_id}")
def v1_account_money_map(account_id: str, hops: int = 1):
    if account_id not in WS.svcs.store.accounts:
        ensure_account(account_id)
    a = WS.investigator.account(account_id)
    lvl = lambda r: "High risk" if r >= 60 else "Watch" if r >= 30 else "Safe"  # noqa: E731
    senders = [c for c in a["connected"] if c["in"] > 0]
    payees = [c for c in a["connected"] if c["out"] > 0]
    shared = [d for d in a["devices"] if d["accounts"]]
    reasons = [s["label"] for s in a["risk_signals"][:3]] or (
        ["All payments match the customer's normal behaviour."] if a["risk"] < 30 else [])
    if shared:
        reasons.append(f"Shares a phone with {len(shared[0]['accounts'])} other account(s).")
    if len(reasons) < 2:
        reasons.append(f"Network risk {a['metadata']['network_risk']:.0f}/100.")
    return {
        "account": {"id": account_id, "display_name": a["label"], "role": a["persona"],
                    "risk_score": a["risk"], "risk_level": lvl(a["risk"]), "verdict_reasons": reasons},
        "received": {"total_amount": round(sum(s["in"] for s in senders), 2), "count": len(senders),
                     "senders": [{"id": s["id"], "display_name": s["label"], "amount": s["in"],
                                  "risk_level": lvl(s["risk"])} for s in senders]},
        "sent": {"total_amount": round(sum(p["out"] for p in payees), 2), "count": len(payees),
                 "payees": [{"id": p["id"], "display_name": p["label"], "amount": p["out"],
                             "risk_level": lvl(p["risk"])} for p in payees]},
        "shared_devices": [{"device_id": d["id"], "other_accounts": len(d["accounts"]),
                            "other_account_ids": d["accounts"]} for d in a["devices"]],
        "held_payments": [h.to_dict() for h in WS.svcs.store.holds.values()
                          if h.status == "active" and account_id in (h.payer, h.beneficiary)],
        "summary_sentence": f"Received ₹{a['money_in']:,.0f} and sent ₹{a['money_out']:,.0f}.",
    }


# ---------- ledger ----------
@app.get("/ledger")
def ledger(limit: int = 30):
    return WS.svcs.ledger.recent(limit)


@app.get("/ledger/verify")
def ledger_verify(mode: str = "live"):
    return WS.svcs.ledger.verify(mode=mode)


@app.get("/ledger/record/{txn_id}")
def ledger_record(txn_id: str):
    rec = WS.svcs.ledger.find(txn_id=txn_id)
    if rec is None:
        raise HTTPException(404, f"no ledger record for {txn_id}")
    return rec


@app.post("/ledger/tamper")
def ledger_tamper():
    WS.version += 1
    return {"tamper": WS.svcs.ledger.tamper(), "verify": WS.svcs.ledger.verify(mode="sandbox")}


@app.post("/ledger/restore")
def ledger_restore():
    WS.version += 1
    return WS.svcs.ledger.restore()


# ---------- scenario simulator ----------
def _seed_accounts(script: scenario_lib.Script, start: float) -> None:
    """Create the script's accounts with their pre-existing profile (registered devices, known payees)."""
    svcs = WS.svcs
    for spec in script.accounts:
        acc = ensure_account(spec.id, name=spec.name, persona=spec.persona, balance=spec.balance,
                             age_days=spec.age_days, avg_amt_90d=spec.avg_amt_90d, std_amt_90d=spec.std_amt_90d,
                             cluster=spec.cluster, verified_merchant=spec.verified_merchant, city=spec.city,
                             devices=list(spec.devices))
        for dev in spec.devices:
            acc.known_devices.add(dev)
            svcs.graph.add_node(dev, "Device", dev)
            svcs.graph.add_edge(spec.id, dev, "USED_DEVICE", ts=start - spec.age_days * 86400, registered=True)
    for spec in script.accounts:
        acc = svcs.store.accounts[spec.id]
        for payee, n in spec.known_payees.items():
            # n past payments spread over the last ~60 days: the 90-day relationship profile
            acc.known_payees[payee] = [start - 86400 * (60 - i * 55 / max(n, 1)) for i in range(n)]


async def run_scenario(name: str) -> dict:
    WS.run_counter += 1
    run_no = WS.run_counter
    try:
        script = scenario_lib.build(name, run_no)
    except KeyError:
        raise HTTPException(404, f"unknown scenario {name}")
    run_id = f"RUN{run_no}"
    start = now() - script.span - 30          # the story ends ~30s ago, so 1h/24h windows hold it
    _seed_accounts(script, start)
    txn_ids = []
    for step in script.steps:
        req = {k: getattr(step, k) for k in ("payer", "payee", "amount", "channel", "device_fp", "geo", "on_call",
                                             "call_minutes", "screen_share", "sim_changed_hrs_ago",
                                             "pin_reset_hrs_ago", "cooling_override", "stepup_failed")}
        d = await process(req, ts=start + step.at, scenario=name, run_id=run_id, caption=step.caption)
        txn_ids.append(d["txn_id"])
    events = [{"ts": start + e.at, "text": e.text, "account": e.account, "event": e.kind} for e in script.events]
    run = {"run_id": run_id, "scenario": name, "title": script.title, "summary": script.summary,
           "created": now(), "txn_ids": txn_ids, "events": events, "roles": script.roles,
           "accounts": [a.id for a in script.accounts]}
    WS.runs[run_id] = run
    payload = _run_payload(run)
    run["severity"] = SEVERITY.get((payload["key_transaction"] or {}).get("outcome"), 0)
    run["focus"] = payload["focus"]
    run["headline"] = (payload["explanation"] or {}).get("headline")
    WS.version += 1
    return payload


def _run_payload(run: dict) -> dict:
    inv = WS.investigator
    res = inv.investigate(run["txn_ids"], run["events"], run["roles"], accounts=None)
    decisions = [WS.decisions[t] for t in run["txn_ids"] if t in WS.decisions]
    return {
        "scenario": run["scenario"], "run_id": run["run_id"], "title": run["title"], "summary": run["summary"],
        "transactions": [{"txn_id": d["txn_id"], "ts": d["ts"], **d["request"], "caption": d["caption"]}
                         for d in decisions],
        "decisions": decisions,
        **res,
    }


@app.get("/scenarios")
def scenarios():
    return list(scenario_lib.SCENARIOS)


@app.post("/simulate/{name}")
async def simulate(name: str):
    payload = await run_scenario(name)
    return {**payload, "count": len(payload["decisions"])}


@app.get("/investigations")
def investigations():
    return [{k: r.get(k) for k in ("run_id", "scenario", "title", "summary", "created", "severity", "focus",
                                   "headline")} | {"count": len(r["txn_ids"])}
            for r in sorted(WS.runs.values(), key=lambda r: -r["created"])]


@app.get("/investigations/{run_id}")
def investigation(run_id: str):
    run = WS.runs.get(run_id)
    if run is None:
        raise HTTPException(404, f"unknown run {run_id}")
    return _run_payload(run)


# ---------- decision simulator: dry-run scoring, never recorded ----------
GEO_HOME = "PUNE-W"


class PreviewRequest(BaseModel):
    amount: float = Field(2500, gt=0)
    new_beneficiary: bool = False
    new_device: bool = False
    sim_changed: bool = False
    pin_reset: bool = False
    screen_share: bool = False
    on_call: bool = False
    call_minutes: float = 25
    location: str = GEO_HOME
    recent_payments_1h: int = Field(0, ge=0, le=50)
    counterfactuals: bool = True


PREVIEW_FACTORS = [
    ("sim_changed", "SIM change", {"sim_changed": False}),
    ("pin_reset", "PIN reset", {"pin_reset": False}),
    ("new_device", "New device", {"new_device": False}),
    ("screen_share", "Screen sharing", {"screen_share": False}),
    ("on_call", "On a call", {"on_call": False}),
    ("new_beneficiary", "New beneficiary", {"new_beneficiary": False}),
    ("location", "Unusual location", {"location": GEO_HOME}),
    ("recent_payments_1h", "Payment velocity", {"recent_payments_1h": 0}),
    ("amount", "Large amount", {"amount": 2500}),
]


def _sim_accounts():
    """A fixed customer with a real baseline: known phone, a known payee, last paid 3h ago from home."""
    store = WS.svcs.store
    payer = store.accounts.get("SIM-PAYER")
    if payer is None:
        payer = store.add_account(Account(id="SIM-PAYER", name="Simulated customer", persona="salaried",
                                          vpa="sim-payer@upi", phone="000", balance=150000, avg_amt_90d=2500,
                                          std_amt_90d=1500, age_days=1400, cluster="sim"))
        store.add_account(Account(id="SIM-KNOWN", name="Known beneficiary", persona="salaried", vpa="sim-known@upi",
                                  phone="000", age_days=900, cluster="sim"))
        store.add_account(Account(id="SIM-NEW", name="New beneficiary", persona="salaried", vpa="sim-new@upi",
                                  phone="000", age_days=900, cluster="sim2"))
        payer.known_devices.add("SIM-PHONE")
    t = now()
    payer.known_payees = {"SIM-KNOWN": [t - 86400 * k for k in (50, 35, 21, 9, 2)]}
    store.ledger_moves.pop("SIM-PAYER", None)
    base = Txn(txn_id="SIM-BASELINE", ts=t - 3 * 3600, payer="SIM-PAYER", payee="SIM-KNOWN", amount=1800,
               device_fp="SIM-PHONE", geo=GEO_HOME)
    store.txns[base.txn_id] = base
    store.ledger_moves["SIM-PAYER"].append((base.ts, "out", base.amount, base.payee, base.txn_id))
    return payer


async def _preview_once(p: dict) -> dict:
    _sim_accounts()
    txn = Txn(txn_id="SIM-" + uuid.uuid4().hex[:8], ts=now(), payer="SIM-PAYER",
              payee="SIM-NEW" if p["new_beneficiary"] else "SIM-KNOWN", amount=p["amount"],
              device_fp="SIM-NEWPHONE" if p["new_device"] else "SIM-PHONE", geo=p["location"] or GEO_HOME,
              on_call=p["on_call"], call_minutes=p["call_minutes"] if p["on_call"] else 0.0,
              screen_share=p["screen_share"],
              sim_changed_hrs_ago=4.0 if p["sim_changed"] else None,
              pin_reset_hrs_ago=1.0 if p["pin_reset"] else None, source="simulator")
    n = p["recent_payments_1h"]
    ov = {"count_out_1h": n, "distinct_payees_1h": n, "sum_out_1h": n * 2500.0} if n else {}
    decision = await WS.orchestrator.score(txn, record=False, overrides=ov)
    d = decision.to_dict()
    d["request"] = {"payer": txn.payer, "payee": txn.payee, "amount": txn.amount, "device_fp": txn.device_fp,
                    "geo": txn.geo, "on_call": txn.on_call, "call_minutes": txn.call_minutes,
                    "screen_share": txn.screen_share, "scenario": "simulator"}
    return d


@app.post("/analyze/preview")
async def analyze_preview(req: PreviewRequest):
    """Score a hypothetical payment with the real agents — nothing is recorded, ledgered or graphed."""
    p = req.model_dump()
    d = await _preview_once(p)
    inv = WS.investigator
    detail = inv.txn_detail(d)
    detail.pop("evidence", None)
    detail.pop("ledger", None)
    cfs = []
    if req.counterfactuals:
        for key, label, flip in PREVIEW_FACTORS:
            if p[key] == flip[key] or (key == "amount" and p["amount"] <= 2500):
                continue
            alt = await _preview_once({**p, **flip})
            cfs.append({"factor": key, "label": label, "score_if": alt["score"], "outcome_if": alt["outcome"],
                        "outcome_label": OUTCOME_META[alt["outcome"]]["label"],
                        "delta": round(d["score"] - alt["score"], 1)})
        cfs.sort(key=lambda c: -c["delta"])
    return {**detail, "counterfactuals": cfs, "inputs": p}


@app.post("/reset")
def reset():
    WS.reset()
    return {"ok": True, "version": WS.version}
