"""End-to-end: 6 agents + orchestrator on scripted scenarios, then replay of unseen traffic.
Run: python -m backend.tests.e2e_test"""
import time, collections, numpy as np
from ..models.data import load
from ..pipeline import AgentGuard

df = load(); rows = df.to_dict("records"); cut = int(len(rows) * .80)
ag = AgentGuard()
for t in rows[:cut]: ag.record(t)

# ---- replay unseen traffic: all laundering txns + 200 random legit; everything is recorded afterwards
rng = np.random.default_rng(3); tail = rows[cut:]
pick = {i for i, t in enumerate(tail) if t["is_laundering"]} | set(rng.choice(len(tail), 200, replace=False).tolist())
dec, lat, fpflags = {0: collections.Counter(), 1: collections.Counter()}, [], collections.Counter()
for i, t in enumerate(tail):
    if i in pick:
        o = ag.process(t); dec[int(t["is_laundering"])][o["decision"]] += 1; lat.append(o["latency_ms"])
        if not t["is_laundering"] and o["decision"] == "BLOCK": fpflags.update(o["flags"][:2])
    else:
        ag.record(ag._norm(t))
print("== replay of unseen traffic (synthetic) ==")
for k, name in ((1, "laundering"), (0, "legit")):
    n = sum(dec[k].values()); print(f"{name:10s} n={n:4d} " + "  ".join(f"{d}:{dec[k][d]/n:.0%}" for d in ("SUCCESS", "VERIFY", "PAUSE", "BLOCK")))
print("flags on legit BLOCKs:", dict(fpflags))
print(f"latency (all 5 agents + explainer + ledger): mean {np.mean(lat):.0f} ms, p95 {np.percentile(lat,95):.0f} ms")

# ---- scripted scenarios
base = tail[-1]["ts"] + __import__("pandas").Timedelta(hours=2)
cnt = collections.Counter(r["src"] for r in rows[:cut]); me = [a for a, c in cnt.most_common(200) if c >= 25][60]
fav = collections.Counter(r["dst"] for r in rows[:cut] if r["src"] == me).most_common(1)[0][0]
mk = lambda **k: {"src": me, "dst": fav, "amount": 1800, "channel": "UPI", "ts": base, **k}
def show(title, o):
    print(f"\n[{title}]  -> {o['decision']}  (score {o['score']}, {o['latency_ms']} ms)  flags={o['flags'][:4]}")
    print("   customer:", o["explanation"]["customer_message"]); print("   analyst :", o["explanation"]["top_factors"][:2] and o["explanation"]["audit_reasons"][0])

print("\n== scripted scenarios ==")
show("normal payment, usual payee", ag.process(mk(device_id="D-HOME", lat=18.52, lon=73.85, ip_country="IN", home_country="IN")))
show("normal again (same device)", ag.process(mk(ts=base + __import__("pandas").Timedelta(hours=3), device_id="D-HOME", lat=18.52, lon=73.85, ip_country="IN", home_country="IN")))
show("UPI collect scam at 2:30am, new device, foreign IP", ag.process(mk(dst="A02999", amount=48_000, ts=base.normalize() + __import__("pandas").Timedelta(days=1, hours=2, minutes=30),
     request_type="COLLECT", device_id="D-X9", ip_country="RU", home_country="IN", session_age_s=3)))
t2 = base + __import__("pandas").Timedelta(days=2)
show("net-banking takeover: new device, failed logins, new beneficiary", ag.process(mk(channel="NET_BANKING", dst="A02998", amount=120_000, ts=t2, device_id="D-Y1",
     ip_country="RO", home_country="IN", failed_attempts_1h=4, recent_credential_change=True, beneficiary_age_hours=1)))
for k in range(6): ag.process(mk(dst=f"A0290{k}", amount=20, ts=t2 + __import__("pandas").Timedelta(hours=1, seconds=15 * k)))
show("micro-payment probing then big transfer", ag.process(mk(dst="A02990", amount=40_000, ts=t2 + __import__("pandas").Timedelta(hours=1, seconds=100))))
ag.process(mk(ts=t2 + __import__("pandas").Timedelta(hours=5), device_id="D-HOME", lat=18.52, lon=73.85))
show("impossible travel (Pune -> Delhi in 10 min)", ag.process(mk(amount=9000, ts=t2 + __import__("pandas").Timedelta(hours=5, minutes=10), device_id="D-HOME", lat=28.61, lon=77.20)))
ag.lists.blacklist("A02777")
show("blacklisted payee (hard rule)", ag.process(mk(dst="A02777", amount=500, ts=t2 + __import__("pandas").Timedelta(hours=6))))
print("\nledger:", ag.ledger.verify()); ag.ledger.tamper(2); print("after tampering entry 2:", ag.ledger.verify())
