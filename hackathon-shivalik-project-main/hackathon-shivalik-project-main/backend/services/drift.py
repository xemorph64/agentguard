"""Drift monitor: Population Stability Index (PSI) of live features vs the training distribution.
PSI < 0.1 stable, 0.1-0.2 moderate shift, > 0.2 drift (retrain)."""
from collections import deque
import numpy as np

DRIFT_FEATURES = ["log_amount", "hour", "src_cnt_24h", "src_amount_vs_avg", "forward_ratio", "new_payee"]


class DriftMonitor:
    def __init__(self, train_features, window=1000):
        self.cuts, self.expected = {}, {}
        for f in DRIFT_FEATURES:
            x = train_features[f].values
            cuts = np.array([.5]) if len(np.unique(x)) <= 2 else np.unique(np.quantile(x, np.linspace(.1, .9, 9)))
            self.cuts[f] = cuts
            self.expected[f] = np.bincount(np.digitize(x, cuts), minlength=len(cuts) + 1) / len(x)
        self.live = deque(maxlen=window)

    def update(self, tf):
        self.live.append({f: tf[f] for f in DRIFT_FEATURES})

    def report(self, min_n=50):
        n = len(self.live)
        if n < min_n:
            return {"status": "insufficient_data", "n": n, "need": min_n}
        out, worst = {}, 0.0
        for f in DRIFT_FEATURES:
            x = np.array([r[f] for r in self.live])
            a = np.bincount(np.digitize(x, self.cuts[f]), minlength=len(self.cuts[f]) + 1) / n
            e = np.clip(self.expected[f], 1e-4, None); a = np.clip(a, 1e-4, None)
            psi = float(np.sum((a - e) * np.log(a / e))); worst = max(worst, psi)
            out[f] = {"psi": round(psi, 3), "expected": e.round(3).tolist(), "actual": a.round(3).tolist()}
        status = "stable" if worst < .1 else "moderate" if worst < .2 else "drift"
        return {"status": status, "max_psi": round(worst, 3), "n": n, "features": out}
