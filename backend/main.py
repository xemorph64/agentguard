from collections import Counter, deque
from pathlib import Path
from typing import Optional
import random
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.types import Txn, now, Account, OUTCOME_META, TYPOLOGIES
from app.agents.orchestrator import Orchestrator
from app.db.memory import MemoryStore
from app.db.sqlite import Db
from app.graph.memgraph import Graph
from app.policy.engine import PolicyEngine
from app.chase.engine import ChaseEngine
from app.ledger.chain import Ledger
from app.holds.engine import HoldEngine

STATIC = Path(__file__).parent / "static"


class DummyHub:
    def publish(self, topic, payload): pass


class DummyMetrics:
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


app = FastAPI(title="AgentGuard API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
svcs = Services()
orchestrator = Orchestrator(svcs)
history: deque = deque(maxlen=300)   # recent decisions for the dashboard


def ensure_account(aid: str, **kw) -> Account:
    acc = svcs.store.accounts.get(aid)
    if acc is None:
        acc = svcs.store.add_account(Account(id=aid, name=kw.pop("name", aid), persona=kw.pop("persona", "salaried"),
                                             vpa=f"{aid}@upi", phone="000", **kw))
        svcs.graph.add_node(aid, "Account", acc.name, persona=acc.persona)
    return acc


# Seed with some accounts for testing
ensure_account("acc_1", name="Payer", persona="gig")
ensure_account("acc_2", name="Payee", persona="biz")


class TxnRequest(BaseModel):
    payer: str
    payee: str
    amount: float
    channel: str = "p2p"
    device_fp: str = ""
    geo: str = ""
    on_call: bool = False
    call_minutes: float = 0.0
    screen_share: bool = False
    sim_changed_hrs_ago: Optional[float] = None
    pin_reset_hrs_ago: Optional[float] = None


async def process(req: TxnRequest, scenario: str = "manual") -> dict:
    ensure_account(req.payer)
    ensure_account(req.payee)
    txn = Txn(txn_id=uuid.uuid4().hex, ts=now(), **req.model_dump())
    decision = await orchestrator.score(txn)
    d = decision.to_dict()
    if d["outcome"] != "BLOCK":   # blocked money never moved
        svcs.graph.add_edge(txn.payer, txn.payee, "PAID", txn_id=txn.txn_id, ts=txn.ts, amount=txn.amount)
    if txn.device_fp:
        svcs.graph.add_node(txn.device_fp, "Device", txn.device_fp)
        svcs.graph.add_edge(txn.payer, txn.device_fp, "USED_DEVICE", ts=txn.ts)
        svcs.store.accounts[txn.payer].known_devices.add(txn.device_fp)
    svcs.graph.refresh_warm_properties(svcs.store)
    d["request"] = {**req.model_dump(), "scenario": scenario}
    history.appendleft(d)
    return d


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/analyze")
async def analyze(req: TxnRequest):
    return await process(req)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/meta")
def meta():
    return {"outcomes": OUTCOME_META, "typologies": TYPOLOGIES}


@app.get("/transactions")
def transactions(limit: int = 100):
    return list(history)[:limit]


@app.get("/stats")
def stats():
    h = list(history)
    lat = sorted(x["total_latency_ms"] for x in h)
    return {
        "total": len(h),
        "by_outcome": Counter(x["outcome"] for x in h),
        "by_typology": Counter(x["dominant"] for x in h if x["dominant"]),
        "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else 0,
        "p95_latency_ms": lat[int(len(lat) * 0.95)] if lat else 0,
    }


@app.get("/graph")
def graph():
    return svcs.graph.subgraph_for(list(svcs.graph.nodes), {"PAID", "USED_DEVICE"})


@app.get("/ledger")
def ledger(limit: int = 30):
    return svcs.ledger.recent(limit)


@app.get("/ledger/verify")
def ledger_verify(mode: str = "live"):
    return svcs.ledger.verify(mode=mode)


@app.post("/ledger/tamper")
def ledger_tamper():
    return {"tamper": svcs.ledger.tamper(), "verify": svcs.ledger.verify(mode="sandbox")}


@app.post("/ledger/restore")
def ledger_restore():
    return svcs.ledger.restore()


# ---------- scenario simulator ----------
def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:5]}"


def scenario_txns(name: str) -> list[TxnRequest]:
    R = TxnRequest
    if name == "normal":
        ppl = [ensure_account(_id("user"), avg_amt_90d=1500).id for _ in range(4)]
        return [R(payer=random.choice(ppl), payee=random.choice(ppl[1:] + ["acc_2"]),
                  amount=round(random.uniform(150, 2500)), device_fp="dev_home") for _ in range(6)]
    if name == "digital_arrest":   # T1: coerced victim on a call, screen shared, draining savings
        victim = ensure_account(_id("elder"), name="Elderly victim", persona="elderly", balance=400000).id
        fraud = ensure_account(_id("cbi"), name="'CBI officer'", persona="mule", age_days=4).id
        return [R(payer=victim, payee=fraud, amount=180000, on_call=True, call_minutes=55, screen_share=True)]
    if name == "account_takeover":  # T3: SIM swapped, new device, new city
        v = ensure_account(_id("user"), balance=90000).id
        thief = ensure_account(_id("ato"), persona="mule", age_days=2).id
        return [R(payer=v, payee=thief, amount=49999, sim_changed_hrs_ago=6, pin_reset_hrs_ago=2,
                  device_fp=_id("newdev"), geo="PATNA")]
    if name == "mule_fanin":       # T8/T9: many unrelated victims → young mule → forwards out
        mule = ensure_account(_id("mule"), name="Mule collector", persona="mule", age_days=3).id
        sink = ensure_account(_id("sink"), persona="ring", age_days=5).id
        txns = [R(payer=ensure_account(_id("victim"), cluster=_id("c")).id, payee=mule, amount=round(random.uniform(8000, 25000)))
                for _ in range(10)]
        return txns + [R(payer=mule, payee=sink, amount=150000)]
    if name == "structuring":      # T11: many just-under-threshold payments
        tgt = ensure_account(_id("collector"), persona="ring", age_days=20).id
        return [R(payer=ensure_account(_id("smurf"), cluster=_id("c")).id, payee=tgt,
                  amount=random.choice([9900, 9950, 9990, 49900, 49990])) for _ in range(8)]
    if name == "probing":          # T7: tiny payments to new IDs, then a big one
        p = ensure_account(_id("user"), balance=120000).id
        txns = [R(payer=p, payee=ensure_account(_id("probe")).id, amount=1) for _ in range(5)]
        return txns + [R(payer=p, payee=ensure_account(_id("probe"), persona="mule", age_days=1).id, amount=60000)]
    if name == "device_farm":      # T12: one phone driving many accounts
        dev = _id("farm_dev")
        dst = ensure_account(_id("cashout"), persona="ring", age_days=7).id
        return [R(payer=ensure_account(_id("rented"), persona="mule", age_days=10).id, payee=dst,
                  amount=round(random.uniform(5000, 15000)), device_fp=dev) for _ in range(6)]
    raise HTTPException(404, f"unknown scenario {name}")


SCENARIOS = ["normal", "digital_arrest", "account_takeover", "mule_fanin", "structuring", "probing", "device_farm"]


@app.get("/scenarios")
def scenarios():
    return SCENARIOS


@app.post("/simulate/{name}")
async def simulate(name: str):
    out = [await process(r, scenario=name) for r in scenario_txns(name)]
    return {"scenario": name, "count": len(out), "decisions": out}


@app.post("/reset")
def reset():
    global svcs, orchestrator
    svcs = Services()
    orchestrator = Orchestrator(svcs)
    history.clear()
    ensure_account("acc_1", name="Payer", persona="gig")
    ensure_account("acc_2", name="Payee", persona="biz")
    return {"ok": True}
