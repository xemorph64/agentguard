import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score, precision_recall_curve


def pick_thresholds(y, p, hi_prec=0.90, mid_prec=0.70):
    """thr_mid -> 'suspected' tier (>=70% precision, recall as high as possible);
    thr_hi -> hard-block tier (>=90% precision, never below 0.6 calibrated probability)."""
    prec, rec, thr = precision_recall_curve(y, p)
    prec = prec[:-1]
    def first(target, default):
        ok = np.where(prec >= target)[0]
        return float(thr[ok[0]]) if len(ok) else default
    hi = max(first(hi_prec, 0.8), 0.6)
    mid = min(first(mid_prec, 0.4), hi)
    return mid, hi


def evaluate(y, p, mid, hi):
    out = {"pr_auc": float(average_precision_score(y, p)), "roc_auc": float(roc_auc_score(y, p)),
           "positives": int(np.sum(y)), "n": int(len(y))}
    for name, t in (("mid", mid), ("hi", hi)):
        pred = p >= t
        tp = int(np.sum(pred & (y == 1))); fp = int(np.sum(pred & (y == 0))); fn = int(np.sum(~pred & (y == 1)))
        out[name] = {"threshold": t, "precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1),
                     "false_positive_rate": fp / max(int(np.sum(y == 0)), 1)}
    return out
