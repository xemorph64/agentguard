"""Labelled scenario generator for the demo (label 1 = fraud/laundering, 0 = legitimate)."""
from collections import Counter, defaultdict
import numpy as np, pandas as pd


class Simulator:
    def __init__(self, rows, start, seed=11):
        self.rng, self.clock, self.n = np.random.default_rng(seed), pd.Timestamp(start), 0
        sent = Counter(r["src"] for r in rows)
        self.active = [a for a, c in sent.most_common() if c >= 20 and a.startswith("A")]
        by = defaultdict(list)
        for r in rows:
            if r["src"] in set(self.active[:400]): by[r["src"]].append(r)
        self.fav = {a: Counter(r["dst"] for r in v).most_common(1)[0][0] for a, v in by.items()}
        self.med = {a: float(np.median([r["amount"] for r in v])) for a, v in by.items()}
        self.active = [a for a in self.active if a in self.fav]

    # -- helpers
    def advance(self, seconds=1):
        self.clock += pd.Timedelta(seconds=seconds); return self.clock

    def fresh(self, prefix="N"):
        self.n += 1; return f"{prefix}{self.n:05d}"

    def _a(self): return str(self.rng.choice(self.active))
    def _t(self, **k):
        return {"ts": self.advance(1), "channel": "UPI", "src_bank": int(self.rng.integers(0, 12)),
                "dst_bank": int(self.rng.integers(0, 12)), "cross_border": 0, **k}

    # -- scenarios: each returns [(txn, label), ...]
    def _daytime(self):                              # legit traffic is business-hours heavy, like the training data
        self.advance(float(self.rng.integers(120, 1200)))
        if self.clock.hour < 8 or self.clock.hour >= 22:
            nxt = self.clock.normalize() + pd.Timedelta(hours=8)
            self.clock = nxt if nxt > self.clock else nxt + pd.Timedelta(days=1)

    def normal(self, shift=1.0):
        self._daytime(); a = self._a()
        return [(self._t(src=a, dst=self.fav[a], amount=round(self.med[a] * float(self.rng.lognormal(0, .3)) * shift, 2),
                         device_id=f"D-{a}", lat=18.52, lon=73.85, ip_country="IN", home_country="IN"), 0)]

    def normal_shifted(self):                       # legit traffic whose distribution has moved (drift demo)
        return self.normal(shift=8.0)

    def mule(self):
        m, m2, co = self.fresh("MX"), self.fresh("MX"), self.fresh("MX"); out, tot = [], 0
        for _ in range(int(self.rng.integers(7, 11))):
            amt = float(self.rng.integers(20, 80) * 1000); tot += amt
            out.append((self._t(src=self._a(), dst=m, amount=amt, channel="UPI"), 1)); self.advance(40)
        self.advance(7200); out.append((self._t(src=m, dst=m2, amount=round(tot * .93, 2), channel="NET_BANKING"), 1))
        self.advance(7200); out.append((self._t(src=m2, dst=co, amount=round(tot * .9, 2), channel="NET_BANKING"), 1))
        return out

    def structuring(self):
        a = self._a(); out = []
        for _ in range(8):
            out.append((self._t(src=a, dst=self.fresh(), amount=round(float(self.rng.uniform(45000, 49900)), 2), cross_border=1), 1))
            self.advance(1200)
        return out

    def cycle(self):
        ring = [self._a() for _ in range(4)]; amt, out = 150_000.0, []
        for _ in range(3):
            for i in range(4):
                amt *= .98; out.append((self._t(src=ring[i], dst=ring[(i + 1) % 4], amount=round(amt, 2), channel="NET_BANKING"), 1))
                self.advance(1800)
        return out

    def upi_scam(self):
        a = self._a()
        return [(self._t(src=a, dst=self.fresh(), amount=float(self.rng.integers(25, 49) * 1000), request_type="COLLECT",
                         device_id=f"D-NEW{self.n}", ip_country="RU", home_country="IN", session_age_s=3,
                         beneficiary_age_hours=2), 1)]

    def micro_probing(self):                         # tiny UPI payments to many new IDs, then one big payment
        a, out = self._a(), []
        for _ in range(5):
            out.append((self._t(src=a, dst=self.fresh(), amount=float(self.rng.integers(1, 50))), 1)); self.advance(10)
        out.append((self._t(src=a, dst=self.fresh(), amount=float(self.rng.integers(40, 90) * 1000)), 1))
        return out

    def account_takeover(self):                      # failed logins, credential change, new device, large transfer to new beneficiary
        a = self._a()
        return [(self._t(src=a, dst=self.fresh(), amount=float(self.rng.integers(60, 150) * 1000), channel="NET_BANKING",
                         device_id=f"D-NEW{self.n}", ip_country="RO", home_country="IN", failed_attempts_1h=4,
                         recent_credential_change=True, beneficiary_age_hours=1, session_age_s=4), 1)]

    def device_farm(self):                           # many accounts, ONE device, small payments to one collector (rules can't see it)
        dev, col, out = f"D-FARM{self.n}", self.fresh("COL"), []
        for a in self.rng.choice(self.active, 8, replace=False):
            out.append((self._t(src=str(a), dst=col, amount=float(self.rng.integers(8, 15) * 1000), device_id=dev,
                                ip_country="IN", home_country="IN"), 1)); self.advance(90)
        return out

    def slow_drip(self):                             # many fresh payees, each just under the 20k rule threshold
        a, out = self._a(), []
        for _ in range(8):
            out.append((self._t(src=a, dst=self.fresh(), amount=float(self.rng.integers(12, 19) * 1000)), 1)); self.advance(150)
        return out

    SCENARIOS = ("normal", "normal_shifted", "mule", "structuring", "cycle", "upi_scam", "account_takeover", "micro_probing", "device_farm", "slow_drip")

    def make_batch(self, n_normal=60):
        units = [self.normal() for _ in range(n_normal)]
        for name, k in (("mule", 2), ("structuring", 2), ("cycle", 2), ("upi_scam", 4), ("account_takeover", 2), ("micro_probing", 2), ("device_farm", 1), ("slow_drip", 2)):
            units += [getattr(self, name)() for _ in range(k)]
        self.rng.shuffle(units)
        return [x for u in units for x in u]
