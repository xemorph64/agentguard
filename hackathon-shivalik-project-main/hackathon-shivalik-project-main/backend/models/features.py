"""Shared feature engineering. Used by BOTH training and live serving (no train/serve skew).

Canonical transaction schema:
  ts, src, dst, amount, channel, src_bank, dst_bank, cross_border, is_laundering(train only)
"""
import numpy as np
import pandas as pd

CHANNELS = ["UPI", "NET_BANKING"]            # digital payments only (no credit/debit cards)
STRUCT_LIMIT = 50_000.0   # reporting-style threshold used for structuring checks (configurable)

MULE_FEATURES = [
    "in_count", "out_count", "unique_senders", "unique_receivers", "in_sum", "out_sum",
    "avg_in", "std_in", "max_in", "avg_out", "pass_through_ratio", "retention_ratio",
    "fan_in_out_ratio", "median_hold_hours", "rapid_forward_ratio", "account_age_hours",
    "activity_density", "round_in_ratio", "cross_bank_in_ratio", "night_out_ratio",
]

TXN_FEATURES = [
    "log_amount", "hour", "is_night", "is_round", "near_limit", "cross_border", "cross_bank",
    "chan_UPI", "chan_NET_BANKING",
    "src_cnt_24h", "src_sum_24h", "src_in_cnt_24h", "src_in_sum_24h",
    "dst_in_cnt_24h", "dst_in_sum_24h", "new_payee", "src_amount_vs_avg", "forward_ratio",
]


# ---------------------------------------------------------------- account level (mule)
def account_features(df: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """Behavioural profile per account from its in/out transactions."""
    d = df.sort_values("ts", kind="stable").copy()
    as_of = pd.Timestamp(as_of) if as_of is not None else d["ts"].max()
    d["_round"] = (d["amount"] % 100 == 0).astype(float)
    d["_xbank"] = (d["src_bank"] != d["dst_bank"]).astype(float)
    d["_night"] = (d["ts"].dt.hour < 6).astype(float)

    gi = d.groupby("dst")
    inc = pd.DataFrame({
        "in_count": gi.size(), "unique_senders": gi["src"].nunique(),
        "in_sum": gi["amount"].sum(), "avg_in": gi["amount"].mean(),
        "std_in": gi["amount"].std().fillna(0), "max_in": gi["amount"].max(),
        "round_in_ratio": gi["_round"].mean(), "cross_bank_in_ratio": gi["_xbank"].mean(),
        "first_in": gi["ts"].min(),
    })
    go = d.groupby("src")
    out = pd.DataFrame({
        "out_count": go.size(), "unique_receivers": go["dst"].nunique(),
        "out_sum": go["amount"].sum(), "avg_out": go["amount"].mean(),
        "night_out_ratio": go["_night"].mean(), "first_out": go["ts"].min(),
    })
    f = inc.join(out, how="outer")
    f.index.name = "acct"

    # hold time: gap between an outgoing txn and the latest inflow before it
    ins = d[["dst", "ts"]].rename(columns={"dst": "acct", "ts": "in_ts"}).sort_values("in_ts")
    outs = d[["src", "ts"]].rename(columns={"src": "acct"}).sort_values("ts")
    m = pd.merge_asof(outs, ins, left_on="ts", right_on="in_ts", by="acct", direction="backward")
    m["hold_h"] = (m["ts"] - m["in_ts"]).dt.total_seconds() / 3600
    m["rapid"] = (m["hold_h"] <= 6).astype(float)
    hold = m.groupby("acct").agg(median_hold_hours=("hold_h", "median"), rapid_forward_ratio=("rapid", "mean"))
    f = f.join(hold)

    first = f[["first_in", "first_out"]].min(axis=1)
    f["account_age_hours"] = (as_of - first).dt.total_seconds() / 3600
    num = [c for c in f.columns if c not in ("first_in", "first_out", "median_hold_hours")]
    f[num] = f[num].fillna(0)
    f["median_hold_hours"] = f["median_hold_hours"].fillna(720).clip(upper=720)
    f["pass_through_ratio"] = (f["out_sum"] / (f["in_sum"] + 1)).clip(0, 5)
    f["retention_ratio"] = ((f["in_sum"] - f["out_sum"]) / (f["in_sum"] + 1)).clip(-5, 1)
    f["fan_in_out_ratio"] = (f["unique_senders"] + 1) / (f["unique_receivers"] + 1)
    f["activity_density"] = (f["in_count"] + f["out_count"]) / np.maximum(f["account_age_hours"] / 24, 1)
    return f[MULE_FEATURES].astype(float)


# ---------------------------------------------------------------- transaction level (AML)
def _prior_outflow(d, hours):
    """Count/sum of the same src's earlier txns inside the window (excludes current)."""
    t = d["ts"].values.astype("datetime64[s]").astype("int64")
    a = d["amount"].values.astype(float)
    cnt, tot = np.zeros(len(d)), np.zeros(len(d))
    for _, idx in d.groupby("src").indices.items():
        tt, cs = t[idx], np.concatenate([[0], np.cumsum(a[idx])])
        lo = np.searchsorted(tt, tt - hours * 3600, "left")
        pos = np.arange(len(idx))
        cnt[idx], tot[idx] = pos - lo, cs[pos] - cs[lo]
    return cnt, tot


def _inflow(d, acct_col, hours):
    """Count/sum of money RECEIVED by d[acct_col] in the window before each txn."""
    t = d["ts"].values.astype("datetime64[s]").astype("int64")
    a = d["amount"].values.astype(float)
    inc = d.groupby("dst").indices
    cnt, tot = np.zeros(len(d)), np.zeros(len(d))
    for acct, idx in d.groupby(acct_col).indices.items():
        j = inc.get(acct)
        if j is None:
            continue
        it, cs = t[j], np.concatenate([[0], np.cumsum(a[j])])
        lo = np.searchsorted(it, t[idx] - hours * 3600, "left")
        hi = np.searchsorted(it, t[idx], "left")
        cnt[idx], tot[idx] = hi - lo, cs[hi] - cs[lo]
    return cnt, tot


def txn_features(df: pd.DataFrame) -> pd.DataFrame:
    """Causal per-transaction features. df must be sorted by ts."""
    d = df.reset_index(drop=True)
    amt = d["amount"].astype(float)
    hr = d["ts"].dt.hour
    o = pd.DataFrame(index=d.index)
    o["log_amount"], o["hour"] = np.log1p(amt), hr
    o["is_night"] = (hr < 6).astype(int)
    o["is_round"] = ((amt % 1000) == 0).astype(int)
    o["near_limit"] = ((amt >= 0.9 * STRUCT_LIMIT) & (amt < STRUCT_LIMIT)).astype(int)
    o["cross_border"] = d["cross_border"].astype(int)
    o["cross_bank"] = (d["src_bank"] != d["dst_bank"]).astype(int)
    for c in CHANNELS:
        o[f"chan_{c}"] = (d["channel"] == c).astype(int)
    o["src_cnt_24h"], o["src_sum_24h"] = _prior_outflow(d, 24)
    o["src_in_cnt_24h"], o["src_in_sum_24h"] = _inflow(d, "src", 24)
    o["dst_in_cnt_24h"], o["dst_in_sum_24h"] = _inflow(d, "dst", 24)
    o["new_payee"] = (d.groupby(["src", "dst"]).cumcount() == 0).astype(int)
    prior_sum = d.groupby("src")["amount"].cumsum() - amt
    n = d.groupby("src").cumcount().replace(0, np.nan)
    o["src_amount_vs_avg"] = (amt / (prior_sum / n)).replace([np.inf, -np.inf], np.nan).fillna(1).clip(0, 50)
    o["forward_ratio"] = (amt / (o["src_in_sum_24h"] + 1)).clip(0, 10)
    return o[TXN_FEATURES].astype(float)
