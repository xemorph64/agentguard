"""Train the AML transaction model (Random Forest + isotonic calibration, TIME-based split).
Run:  python -m backend.models.train_aml [path/to/HI-Small_Trans.csv]"""
import sys, json, joblib
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from .data import load
from .features import txn_features, TXN_FEATURES
from .common import pick_thresholds, evaluate

SAVED = Path(__file__).parent / "saved"

def main(path=None):
    df = load(path)                         # already sorted by ts
    X, y = txn_features(df), df["is_laundering"].values
    n = len(df); a, b = int(n * .65), int(n * .80)   # train | val | test  (chronological)
    Xtr, ytr, Xva, yva, Xte, yte = X[:a], y[:a], X[a:b], y[a:b], X[b:], y[b:]
    print(f"txns={n} laundering={y.sum()} ({y.mean():.2%})  test_pos={yte.sum()}")

    rf = RandomForestClassifier(n_estimators=250, max_depth=16, min_samples_leaf=3,
                                class_weight="balanced_subsample", n_jobs=-1, random_state=7)
    rf.fit(Xtr, ytr)     
    cal = IsotonicRegression(out_of_bounds="clip").fit(rf.predict_proba(Xva)[:, 1], yva)
    mid, hi = pick_thresholds(yva, cal.predict(rf.predict_proba(Xva)[:, 1]))
    pte = cal.predict(rf.predict_proba(Xte)[:, 1])
    metrics = evaluate(yte, pte, mid, hi)
    print(json.dumps(metrics, indent=2))

    joblib.dump({"model": rf, "calibrator": cal, "features": TXN_FEATURES, "thr_mid": mid, "thr_hi": hi,
                 "importance": dict(zip(TXN_FEATURES, map(float, rf.feature_importances_))),
                 "mean": Xtr.mean().to_dict(), "std": Xtr.std().replace(0, 1).to_dict(),
                 "metrics": metrics}, SAVED / "aml_model.joblib")
    (SAVED / "aml_metrics.json").write_text(json.dumps(metrics, indent=2))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
