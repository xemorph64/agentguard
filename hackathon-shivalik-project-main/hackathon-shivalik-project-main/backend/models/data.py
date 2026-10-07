"""Data loaders. Real: IBM AML (Kaggle 'ealtman2019/ibm-transactions-for-anti-money-laundering-aml').
Fallback: built-in synthetic generator with realistic laundering typologies, so everything runs offline."""
import numpy as np
import pandas as pd
from .features import CHANNELS

IBM_CHANNEL = {"ACH": "NET_BANKING", "Wire": "NET_BANKING", "Cheque": "NET_BANKING", "Cash": "NET_BANKING",
               "Reinvestment": "NET_BANKING", "Bitcoin": "UPI"}     # IBM "Credit Card" rows are dropped


def load_ibm(path, nrows=None):
    df = pd.read_csv(path, nrows=nrows)
    df.columns = ["ts", "src_bank", "src", "dst_bank", "dst", "amt_recv", "cur_recv",
                  "amount", "cur_paid", "fmt", "is_laundering"]
    df = df[df["fmt"] != "Credit Card"].copy()                  # cards are out of scope
    df["ts"] = pd.to_datetime(df["ts"], format="%Y/%m/%d %H:%M")
    df["channel"] = df["fmt"].map(IBM_CHANNEL).fillna("NET_BANKING")
    df["cross_border"] = (df["cur_recv"] != df["cur_paid"]).astype(int)
    return finish(df[["ts", "src", "dst", "amount", "channel", "src_bank", "dst_bank",
                      "cross_border", "is_laundering"]])


def finish(df):
    df = df.sort_values("ts", kind="stable").reset_index(drop=True)
    df["txn_id"] = [f"T{i:08d}" for i in range(len(df))]
    return df


def make_synthetic(n_accounts=3000, n_legit=120_000, n_rings=40, n_cycles=30, n_struct=25, seed=7):
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-01-01")
    days = 90
    accts = np.array([f"A{i:05d}" for i in range(n_accounts)])
    bank = rng.integers(0, 12, n_accounts + 5000)

    def mk(src, dst, amount, ts, ch=None, xb=0, laundering=0):
        n = len(amount)
        return pd.DataFrame({
            "ts": ts, "src": src, "dst": dst, "amount": np.round(amount, 2),
            "channel": ch if ch is not None else rng.choice(CHANNELS, n, p=[.85, .15]),
            "cross_border": xb, "is_laundering": laundering})

    # ---- legitimate traffic: favourite payees, merchants (legit fan-in), payroll (legit fan-out)
    fav = rng.integers(0, n_accounts, (n_accounts, 5))
    s = rng.integers(0, n_accounts, n_legit)
    d = np.where(rng.random(n_legit) < .8, fav[s, rng.integers(0, 5, n_legit)], rng.integers(0, n_accounts, n_legit))
    merchants = rng.choice(n_accounts, 30, replace=False)
    to_m = rng.random(n_legit) < .12
    d = np.where(to_m, rng.choice(merchants, n_legit), d)
    d = np.where(d == s, (d + 1) % n_accounts, d)
    hrs = np.clip(rng.normal(14, 4, n_legit), 0, 23.9)
    ts = start + pd.to_timedelta(rng.integers(0, days, n_legit), "D") + pd.to_timedelta(hrs, "h")
    parts = [mk(accts[s], accts[d], rng.lognormal(7.5, 1.0, n_legit), ts,
                xb=(rng.random(n_legit) < .02).astype(int))]
    for p in rng.choice(n_accounts, 10, replace=False):          # payroll
        emp = rng.choice(n_accounts, 40, replace=False)
        for day in (1, 30, 60):
            t = start + pd.Timedelta(days=day, hours=10) + pd.to_timedelta(rng.integers(0, 30, 40), "min")
            parts.append(mk(np.repeat(accts[p], 40), accts[emp], np.round(rng.integers(20, 90, 40) * 1000, -3), t))

    nxt = [n_accounts]                                           # id pool for fresh mule accounts
    def new_acct():
        a = f"M{nxt[0]:05d}"; nxt[0] += 1; return a

    # ---- typology 1: mule chain  victims -> mule1 -> mule2 -> cash-out
    for _ in range(n_rings):
        k = int(rng.integers(8, 25)); vic = rng.choice(accts, k, replace=False)
        m1, m2, co = new_acct(), new_acct(), new_acct()
        t0 = start + pd.Timedelta(days=int(rng.integers(2, days - 2)), hours=int(rng.integers(0, 18)))
        amts = rng.integers(20, 90, k) * 1000.0
        parts.append(mk(vic, np.repeat(m1, k), amts, t0 + pd.to_timedelta(rng.integers(0, 180, k), "min"), laundering=1))
        tot = amts.sum()
        for a, b, h in ((m1, m2, rng.integers(1, 5)), (m2, co, rng.integers(1, 6))):
            nchunk = int(rng.integers(1, 4)); tot *= rng.uniform(.9, .98)
            t0 = t0 + pd.Timedelta(hours=int(h)) + pd.Timedelta(hours=3 if a == m1 else 0)
            parts.append(mk(np.repeat(a, nchunk), np.repeat(b, nchunk), np.repeat(tot / nchunk, nchunk),
                            t0 + pd.to_timedelta(rng.integers(0, 90, nchunk), "min"), laundering=1))
        for m in (m1, m2):                                       # camouflage
            parts.append(mk(np.repeat(m, 2), rng.choice(accts, 2), rng.lognormal(7, .5, 2),
                            t0 + pd.to_timedelta(rng.integers(-2000, 2000, 2), "min")))

    # ---- typology 2: round-tripping cycles
    for _ in range(n_cycles):
        L = int(rng.integers(3, 6)); ring = rng.choice(accts, L, replace=False); amt = rng.integers(80, 400) * 1000.0
        t0 = start + pd.Timedelta(days=int(rng.integers(2, days - 2)), hours=int(rng.integers(0, 12)))
        for lap in range(int(rng.integers(2, 4))):
            for i in range(L):
                amt *= .98; t0 += pd.Timedelta(minutes=int(rng.integers(20, 180)))
                parts.append(mk(np.array([ring[i]]), np.array([ring[(i + 1) % L]]), np.array([amt]),
                                pd.DatetimeIndex([t0]), xb=int(rng.random() < .3), laundering=1))

    # ---- typology 3: structuring (smurfing just under the limit)
    for _ in range(n_struct):
        k = int(rng.integers(8, 16)); src = rng.choice(accts); dst = rng.choice(accts, k, replace=False)
        t0 = start + pd.Timedelta(days=int(rng.integers(2, days - 3)))
        parts.append(mk(np.repeat(src, k), dst, rng.uniform(45_000, 49_900, k),
                        t0 + pd.to_timedelta(rng.integers(0, 2400, k), "min"), xb=1, laundering=1))

    df = pd.concat(parts, ignore_index=True)
    def bank_of(a):  # stable bank id per account
        return bank[int(a[1:])]
    df["src_bank"] = [bank_of(a) for a in df["src"]]
    df["dst_bank"] = [bank_of(a) for a in df["dst"]]
    return finish(df)


def load(path=None, nrows=None):
    return load_ibm(path, nrows) if path else make_synthetic()
