"""AgentGuard API.  Run:  uvicorn backend.main:app --reload   (docs at /docs)"""
import os, time, threading, collections, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, Literal
import numpy as np, pandas as pd
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .models.data import load
from .models.features import txn_features
from .pipeline import AgentGuard
from .services.otp import OTPService
from .services.alerts import Alerts
from .services.drift import DriftMonitor
from .services.simulator import Simulator

DEV = os.getenv("AG_DEV", "1") == "1"            # DEV: OTP is returned in the response so the demo works without SMS
STATUS = {"SUCCESS": "COMPLETED", "VERIFY": "AWAITING_OTP", "PAUSE": "IN_REVIEW", "BLOCK": "BLOCKED"}


class S:                                          # in-memory app state (swap for MongoDB later)
    ag = sim = drift = None
    records, order, queue, feedback, ws = {}, collections.deque(maxlen=5000), [], [], set()
    otp, alerts, lock = OTPService(), Alerts(), threading.Lock()


def clean(o):
    if isinstance(o, dict): return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)): return [clean(v) for v in o]
    if isinstance(o, np.integer): return int(o)
    if isinstance(o, (np.floating, float)): return float(o) if np.isfinite(o) else None
    if isinstance(o, np.bool_): return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)): return o.isoformat()
    return o


@asynccontextmanager
async def lifespan(app):
    df = load(); rows = df.to_dict("records"); cut = int(len(rows) * .8)
    S.ag = AgentGuard()
    for t in rows[:cut]: S.ag.record(t)           # warm-up: realistic history for every account
    now = pd.Timestamp.now("UTC").tz_localize(None)
    S.sim = Simulator(rows[:cut], max(now, rows[cut - 1]["ts"] + pd.Timedelta(hours=1)))
    warm = txn_features(df.iloc[:cut])                 # features are causal; skip the cold-start period (empty history)
    S.drift = DriftMonitor(warm.iloc[int(cut * .4):])
    yield

app = FastAPI(title="AgentGuard", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://localhost:3000"],
                   allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ models
class TxnIn(BaseModel):
    src: str; dst: str; amount: float = Field(gt=0)
    channel: Literal["UPI", "NET_BANKING"] = "UPI"
    ts: Optional[datetime] = None; txn_id: Optional[str] = None
    src_bank: int = 0; dst_bank: int = 0; cross_border: bool = False
    device_id: Optional[str] = None; lat: Optional[float] = None; lon: Optional[float] = None
    ip_country: Optional[str] = None; home_country: Optional[str] = None
    request_type: Optional[str] = None; beneficiary_age_hours: Optional[float] = None
    failed_attempts_1h: Optional[int] = None; session_age_s: Optional[float] = None
    recent_credential_change: Optional[bool] = None

class ReviewIn(BaseModel):
    action: Literal["approve", "reject"]; analyst: str = "analyst"; note: str = ""; blacklist_payee: bool = False
class OtpSendIn(BaseModel): txn_id: str
class OtpVerifyIn(BaseModel): txn_id: str; otp: str
class ValueIn(BaseModel): value: str
class PairIn(BaseModel): src: str; dst: str
class TamperIn(BaseModel): index: int = 0


# ------------------------------------------------------------------ core
def summary(rec):
    r, t = rec["result"], rec["txn"]
    return {"txn_id": rec["txn_id"], "ts": t["ts"], "src": t["src"], "dst": t["dst"], "amount": t["amount"],
            "channel": t["channel"], "decision": r["decision"], "score": r["score"], "flags": r["flags"],
            "status": rec["status"], "latency_ms": r["latency_ms"], "label": rec["label"],
            "agent_scores": {a["agent"]: a["score"] for a in r["agents"]},
            "top_reason": (r["explanation"]["audit_reasons"] or [""])[0]}


async def broadcast(kind, rec):
    msg = {"type": kind, "data": summary(rec)}
    for ws in list(S.ws):
        try: await ws.send_json(msg)
        except Exception: S.ws.discard(ws)


def _process(txn, label):
    txn["txn_id"] = txn.get("txn_id") or uuid.uuid4().hex[:12]
    with S.lock:
        out = S.ag.process(txn)
    tf = out.pop("features", None)
    if tf: S.drift.update(tf)
    out = clean(out); d = out["decision"]
    rec = {"txn_id": out["txn_id"], "txn": clean(txn), "result": out, "status": STATUS[d], "label": label, "otp": None}
    S.records[rec["txn_id"]] = rec; S.order.append(rec["txn_id"])
    if len(S.records) > 5000:
        for k in [k for k in S.records if k not in set(S.order)]: del S.records[k]
    msg = out["explanation"]["customer_message"]
    if d == "PAUSE": S.queue.append(rec["txn_id"])
    elif d == "VERIFY":
        code = S.otp.send(rec["txn_id"]); S.alerts.add("SMS", txn["src"], f"OTP {code}. {msg}", rec["txn_id"])
        if DEV: rec["otp"] = code
    elif d == "BLOCK":
        S.alerts.add("SMS", txn["src"], msg, rec["txn_id"])
        S.alerts.add("EMAIL", "fraud-ops@bank.example", f"BLOCKED {rec['txn_id']}: {out['explanation']['analyst_summary']}", rec["txn_id"])
    return rec


async def handle(txn, label=None):
    rec = await run_in_threadpool(_process, txn, label)
    await broadcast("txn", rec)
    return rec


def to_dict(t: TxnIn):
    d = t.model_dump(exclude_none=True) if hasattr(t, "model_dump") else t.dict(exclude_none=True)
    ts = d.pop("ts", None)
    if ts is not None and ts.tzinfo: ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    d["ts"] = pd.Timestamp(ts) if ts is not None else S.sim.advance(1)
    d["cross_border"] = int(d["cross_border"])
    return d


# ------------------------------------------------------------------ endpoints
@app.get("/health")
def health(): return {"status": "ok", "transactions": len(S.records), "ledger": S.ag.ledger.verify()}


@app.post("/analyze")
async def analyze(t: TxnIn):
    rec = await handle(to_dict(t))
    out = {**summary(rec), "explanation": rec["result"]["explanation"], "agents": rec["result"]["agents"],
           "debate": rec["result"]["debate"], "ledger_hash": rec["result"].get("ledger_hash")}
    if rec["otp"]: out["dev_otp"] = rec["otp"]
    return out


@app.get("/transactions")
def transactions(limit: int = Query(50, le=500), decision: Optional[str] = None):
    ids = [i for i in reversed(S.order) if i in S.records]
    recs = [S.records[i] for i in ids if not decision or S.records[i]["result"]["decision"] == decision.upper()]
    return [summary(r) for r in recs[:limit]]


@app.get("/transactions/{txn_id}")
def transaction(txn_id: str):
    r = S.records.get(txn_id)
    if not r: raise HTTPException(404, "unknown transaction")
    return r


@app.websocket("/ws/feed")
async def feed(ws: WebSocket):
    await ws.accept(); S.ws.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect:
        S.ws.discard(ws)


# ---- analyst review queue
@app.get("/review-queue")
def review_queue(): return [summary(S.records[i]) for i in S.queue if i in S.records]


@app.post("/review/{txn_id}")
async def review(txn_id: str, body: ReviewIn):
    if txn_id not in S.queue: raise HTTPException(409, "transaction is not awaiting review")
    rec = S.records[txn_id]; dst = rec["txn"]["dst"]
    S.queue.remove(txn_id)
    approve = body.action == "approve"
    rec["status"], rec["label"] = ("COMPLETED_AFTER_REVIEW", 0) if approve else ("REJECTED", 1)
    S.feedback.append({"txn_id": txn_id, "label": rec["label"], "analyst": body.analyst, "note": body.note, "ts": time.time()})
    if not approve and body.blacklist_payee:
        S.ag.lists.blacklist(dst); S.ag.graph.flag(dst)
    await broadcast("update", rec)
    return summary(rec)


@app.get("/feedback")
def feedback(): return {"count": len(S.feedback), "items": S.feedback[-100:]}      # input for the retrain button


# ---- OTP step-up
@app.post("/otp/send")
def otp_send(b: OtpSendIn):
    rec = S.records.get(b.txn_id)
    if not rec or rec["status"] != "AWAITING_OTP": raise HTTPException(409, "no OTP pending for this transaction")
    code = S.otp.send(b.txn_id); S.alerts.add("SMS", rec["txn"]["src"], f"OTP {code}", b.txn_id)
    return {"sent": True, **({"dev_otp": code} if DEV else {})}


@app.post("/otp/verify")
async def otp_verify(b: OtpVerifyIn):
    rec = S.records.get(b.txn_id)
    if not rec or rec["status"] != "AWAITING_OTP": raise HTTPException(409, "no OTP pending for this transaction")
    res = S.otp.verify(b.txn_id, b.otp)
    if res["ok"]: rec["status"] = "COMPLETED_AFTER_OTP"
    elif res["tries_left"] == 0:                  # failed or expired -> escalate to a human
        rec["status"] = "IN_REVIEW"; S.queue.append(b.txn_id)
    await broadcast("update", rec)
    return {**res, "status": rec["status"]}


# ---- ledger
@app.get("/ledger")
def ledger(limit: int = Query(50, le=500)): return S.ag.ledger.chain[-limit:]
@app.get("/ledger/verify")
def ledger_verify(): return S.ag.ledger.verify()
@app.post("/ledger/tamper")
def ledger_tamper(b: TamperIn):
    if not 0 <= b.index < len(S.ag.ledger.chain): raise HTTPException(404, "no such entry")
    S.ag.ledger.tamper(b.index); return S.ag.ledger.verify()
@app.post("/ledger/demo-repair")
def ledger_repair(): S.ag.ledger.repair(); return S.ag.ledger.verify()


# ---- blacklist / whitelist
@app.get("/lists")
def get_lists(): return {"blacklist": sorted(S.ag.lists.black), "whitelist": sorted(map(list, S.ag.lists.white))}
@app.post("/lists/blacklist")
def bl_add(b: ValueIn): S.ag.lists.blacklist(b.value); return get_lists()
@app.delete("/lists/blacklist")
def bl_del(value: str): S.ag.lists.unblacklist(value); return get_lists()
@app.post("/lists/whitelist")
def wl_add(b: PairIn): S.ag.lists.whitelist(b.src, b.dst); return get_lists()
@app.delete("/lists/whitelist")
def wl_del(src: str, dst: str): S.ag.lists.unwhitelist(src, dst); return get_lists()


# ---- simulator
@app.post("/simulate/{scenario}")
async def simulate(scenario: str, count: int = Query(1, ge=1, le=20)):
    if scenario not in Simulator.SCENARIOS: raise HTTPException(404, f"scenario must be one of {Simulator.SCENARIOS}")
    out = []
    for _ in range(count):
        for txn, label in getattr(S.sim, scenario)():
            out.append(summary(await handle(txn, label)))
    return out


# ---- metrics / drift / graph / customer
def _conf(recs, positive):
    tp = sum(1 for r in recs if r["label"] == 1 and positive(r)); fn = sum(1 for r in recs if r["label"] == 1 and not positive(r))
    fp = sum(1 for r in recs if r["label"] == 0 and positive(r)); tn = sum(1 for r in recs if r["label"] == 0 and not positive(r))
    return {"precision": round(tp / max(tp + fp, 1), 3), "recall": round(tp / max(tp + fn, 1), 3),
            "false_positive_rate": round(fp / max(fp + tn, 1), 3), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


@app.get("/metrics")
def metrics():
    recs = [S.records[i] for i in S.order if i in S.records]
    dec = collections.Counter(r["result"]["decision"] for r in recs)
    lat = [r["result"]["latency_ms"] for r in recs] or [0]
    lab = [r for r in recs if r["label"] is not None]
    d = lambda r: r["result"]["decision"]
    return {"total": len(recs), "decisions": dict(dec), "review_queue": len(S.queue), "feedback": len(S.feedback),
            "latency": {"mean_ms": round(float(np.mean(lat)), 1), "p95_ms": round(float(np.percentile(lat, 95)), 1), "target_ms": 200,
                        "within_target_pct": round(100 * float(np.mean(np.array(lat) < 200)), 1)},
            "labeled": {"n": len(lab), "fraud": sum(r["label"] == 1 for r in lab),
                        "challenged_any_non_success": _conf(lab, lambda r: d(r) != "SUCCESS"),
                        "intervened_pause_or_block": _conf(lab, lambda r: d(r) in ("PAUSE", "BLOCK"))}}


@app.get("/drift")
def drift(): return S.drift.report()


@app.get("/graph")
def graph():
    recent = [r for r in (S.records[i] for i in list(S.order)[-300:] if i in S.records) if r["result"]["decision"] != "SUCCESS"]
    seeds, bad = [], []
    for r in recent:
        pair = [r["txn"]["src"], r["txn"]["dst"]]; seeds += pair
        if r["result"]["decision"] in ("PAUSE", "BLOCK") or any(f.startswith(("MULE_", "AML_")) for f in r["result"]["flags"]):
            bad += pair
    return S.ag.graph.export_subgraph(list(dict.fromkeys(seeds)), bad)


@app.post("/drift/reset")
def drift_reset(): S.drift.live.clear(); return {"reset": True}


@app.get("/customers/{acct}")
def customer(acct: str):
    sent = S.ag.history.sent(acct)
    mine = [r for r in (S.records[i] for i in S.order if i in S.records) if acct in (r["txn"]["src"], r["txn"]["dst"])]
    recent = mine[-10:]
    avg = float(np.mean([r["result"]["score"] for r in recent])) if recent else 0.0
    blocks = sum(r["result"]["decision"] == "BLOCK" for r in recent); pauses = sum(r["result"]["decision"] == "PAUSE" for r in recent)
    trust = max(0, min(100, round(100 - 0.6 * avg - 12 * blocks - 5 * pauses)))
    hrs = collections.Counter(r["ts"].hour for r in sent).most_common(3)
    return clean({"account": acct, "trust_score": trust,
                  "baseline": {"txns_seen": len(sent), "median_amount": float(np.median([r["amount"] for r in sent])) if sent else None,
                               "usual_hours": [h for h, _ in hrs],
                               "top_payees": collections.Counter(r["dst"] for r in sent).most_common(3),
                               "devices": sorted(S.ag.history.devices.get(acct, []))},
                  "recent_decisions": [summary(r) for r in reversed(recent)],
                  "graph": S.ag.graph.graph_features(acct)})


# ---- scenario comparison: agents vs a simple rule-based system on the SAME transactions
@app.post("/compare")
async def compare(n_normal: int = Query(60, ge=10, le=300)):
    batch, rows = S.sim.make_batch(n_normal), []
    for txn, label in batch:
        h = txn["ts"].hour
        rule = txn["amount"] > 50_000 or (not S.ag.history.has_paid(txn["src"], txn["dst"]) and txn["amount"] > 20_000) \
               or (h < 5 and txn["amount"] > 10_000)
        rec = await handle(txn, label)
        rows.append({"label": label, "rule": rule, "dec": rec["result"]["decision"]})
    def stats(pos):
        tp = sum(r["label"] == 1 and pos(r) for r in rows); fp = sum(r["label"] == 0 and pos(r) for r in rows)
        f = sum(r["label"] == 1 for r in rows)
        return {"fraud_caught": tp, "fraud_missed": f - tp, "false_alarms": fp,
                "recall": round(tp / max(f, 1), 3), "false_alarm_rate": round(fp / max(len(rows) - f, 1), 3)}
    return {"transactions": len(rows), "fraud": sum(r["label"] == 1 for r in rows),
            "rule_based": stats(lambda r: r["rule"]),
            "agents_challenge_or_stop": stats(lambda r: r["dec"] != "SUCCESS"),
            "agents_pause_or_block": stats(lambda r: r["dec"] in ("PAUSE", "BLOCK"))}
