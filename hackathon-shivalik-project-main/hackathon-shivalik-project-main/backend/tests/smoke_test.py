"""Replays the chronological tail of the data through history+graph+agents like live traffic.
Run: python -m backend.tests.smoke_test"""
import time, numpy as np
from ..models.data import load
from ..services.history import AccountHistory
from ..services.graph import LiveGraph
from ..agents.mule import MuleAgent
from ..agents.aml import AMLAgent

df = load()
cut = int(len(df) * .80)                      # test period = last 20% (never seen in training)
hist, graph = AccountHistory(), LiveGraph()
mule, aml = MuleAgent(hist, graph), AMLAgent(hist, graph)
import joblib, pathlib
MULES = set(joblib.load(pathlib.Path(__file__).parents[1] / 'models/saved/mule_model.joblib')['mule_accounts'])

def rec(t):
    hist.record(t); graph.add_txn(t["src"], t["dst"], t["amount"])

rows = df.to_dict("records")
for t in rows[:cut]:
    rec(t)

rng = np.random.default_rng(1)
tail = rows[cut:]
mule_touch = [i for i, t in enumerate(tail) if t["src"] in MULES or t["dst"] in MULES]
pick = {i for i, t in enumerate(tail) if t["is_laundering"]} | set(rng.choice(len(tail), 150, replace=False).tolist()) \
       | set(rng.choice(mule_touch, min(250, len(mule_touch)), replace=False).tolist())
res, lat = [], []
for i, t in enumerate(tail):
    if i in pick:
        s = time.perf_counter()
        m, a = mule.analyze(t, {}), aml.analyze(t, {})
        lat.append((time.perf_counter() - s) * 1000)
        res.append((int(t["is_laundering"]), m.score, a.score, m, a, int(t["src"] in MULES or t["dst"] in MULES)))
    rec(t)

y = np.array([r[0] for r in res]); ms = np.array([r[1] for r in res]); as_ = np.array([r[2] for r in res])
for name, sc in (("mule", ms), ("aml", as_), ("max", np.maximum(ms, as_))):
    flag = sc >= 61
    print(f"{name:5s} laundering caught(score>=61): {flag[y==1].mean():.0%} | legit flagged: {flag[y==0].mean():.1%}")
print(f"latency per txn (mule+aml, sequential): mean {np.mean(lat):.0f} ms, p95 {np.percentile(lat, 95):.0f} ms")
ex = next(r for r in res if r[0] == 1 and r[4].score > 60)
print("\nEXAMPLE (laundering txn)\n MULE:", ex[3].score, ex[3].reason, ex[3].flags, "\n AML :", ex[4].score, ex[4].reason, ex[4].flags)

cold = np.array([r[3].details["cold_start"] for r in res])
for lab, mask in (("cold-start accounts", cold), ("accounts with history", ~cold)):
    pos = mask & (y == 1)
    print(f"mule on {lab}: laundering txns={pos.sum()}, caught={(ms[pos] >= 61).mean() if pos.sum() else float('nan'):.0%}")
fp = [r for r in res if r[0] == 0 and r[1] >= 61][:3]
for r in fp: print("MULE FALSE POSITIVE:", r[3].score, r[3].reason)

tm = np.array([r[5] for r in res])
print("\nMULE AGENT vs 'txn touches a mule account':")
for lab, mask in (("with history", ~cold), ("cold start", cold)):
    p_, n_ = mask & (tm == 1), mask & (tm == 0)
    print(f"  {lab}: mule txns={p_.sum()} caught(>=61)={(ms[p_]>=61).mean() if p_.sum() else float('nan'):.0%} | non-mule flagged={(ms[n_]>=61).mean() if n_.sum() else float('nan'):.1%}")
