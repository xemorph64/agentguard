"""In-memory per-account history (swap for MongoDB/Redis later). Reuses the SAME feature code as training."""
from collections import defaultdict, deque
import pandas as pd
from ..models.features import account_features, txn_features

COLS = ["txn_id", "ts", "src", "dst", "amount", "channel", "src_bank", "dst_bank", "cross_border"]


class AccountHistory:
    def __init__(self, max_per_acct=200):
        self.by_acct = defaultdict(lambda: deque(maxlen=max_per_acct))
        self.devices = defaultdict(set)        # account -> device ids it has used
        self.device_accts = defaultdict(set)   # device id -> accounts that used it (device-farm detection)
        self.last_loc = {}                     # account -> (epoch_s, lat, lon)

    def record(self, t):
        row = {k: t.get(k, 0) for k in COLS}
        row["ts"] = pd.Timestamp(row["ts"])
        if t.get("device_id"):
            self.devices[t["src"]].add(t["device_id"]); self.device_accts[t["device_id"]].add(t["src"])
        if t.get("lat") is not None and t.get("lon") is not None:
            self.last_loc[t["src"]] = (row["ts"].timestamp(), t["lat"], t["lon"])
        self.by_acct[t["src"]].append(row)
        self.by_acct[t["dst"]].append(row)

    def _frame(self, accts):
        rows = {}
        for a in accts:
            for r in self.by_acct.get(a, ()):
                rows[r["txn_id"]] = r
        return pd.DataFrame(list(rows.values()), columns=COLS)

    def account_features(self, acct, as_of):
        d = self._frame([acct])
        if d.empty:
            return None
        return account_features(d, as_of).loc[acct].to_dict()

    def txn_features(self, t):
        new = {k: t.get(k, 0) for k in COLS}
        new["txn_id"], new["ts"] = "__new__", pd.Timestamp(t["ts"])
        d = pd.concat([self._frame([t["src"], t["dst"]]), pd.DataFrame([new])], ignore_index=True)
        d = d.sort_values("ts", kind="stable").reset_index(drop=True)
        i = d.index[d.txn_id == "__new__"][0]
        return txn_features(d).iloc[i].to_dict()

    def sent(self, acct):
        return [r for r in self.by_acct.get(acct, ()) if r["src"] == acct]

    def has_paid(self, src, dst):
        return any(r["src"] == src and r["dst"] == dst for r in self.by_acct.get(src, ()))
