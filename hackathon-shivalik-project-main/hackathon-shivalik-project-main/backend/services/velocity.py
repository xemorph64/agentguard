"""Sliding-window velocity store (per sender). O(window) per txn, no pandas -> fast."""
from collections import defaultdict, deque
import pandas as pd


class VelocityStore:
    def __init__(self, maxlen=2000):
        self.ev = defaultdict(lambda: deque(maxlen=maxlen))   # acct -> (t, amount, dst)

    def record(self, t):
        self.ev[t["src"]].append((pd.Timestamp(t["ts"]).timestamp(), float(t["amount"]), t["dst"]))

    def stats(self, acct, now, amount, dst):
        evs = self.ev.get(acct, ())
        c = {"c1m": 0, "c5m": 0, "c1h": 0, "c24h": 0, "s1h": 0.0, "s24h": 0.0, "small10m": 0}
        payees, recent = set(), []
        for t, a, d in reversed(evs):
            age = now - t
            if age < 0: continue
            if age > 86400: break
            c["c24h"] += 1; c["s24h"] += a
            if age <= 3600:
                c["c1h"] += 1; c["s1h"] += a; payees.add(d)
                if len(recent) < 4: recent.append(a)
            if age <= 300: c["c5m"] += 1
            if age <= 60: c["c1m"] += 1
            if age <= 600 and a < 200: c["small10m"] += 1
        c["payees_1h"] = len(payees | {dst})
        c["recent"] = recent[::-1] + [amount]
        base = None                                            # personal baseline: average daily spend
        if len(evs) >= 10:
            span = (now - evs[0][0]) / 86400
            if span >= 3: base = sum(a for _, a, _ in evs) / span
        c["spend_ratio"] = ((c["s24h"] + amount) / base) if base else None
        return c
