"""Train the Isolation Forest behaviour model (unsupervised: catches NEW fraud patterns with no labels).
Run: python -m backend.models.train_behavior [path/to/HI-Small_Trans.csv]"""
import sys, joblib
from pathlib import Path
import numpy as np
from sklearn.ensemble import IsolationForest
from .data import load
from .features import txn_features

SAVED = Path(__file__).parent / "saved"
BEHAV_FEATURES = ["log_amount", "hour", "new_payee", "src_amount_vs_avg", "src_cnt_24h",
                  "src_sum_24h", "cross_border", "forward_ratio"]

def main(path=None):
    df = load(path)
    X = txn_features(df)[BEHAV_FEATURES]
    X = X[: int(len(X) * .65)]                                   # train period only
    iso = IsolationForest(n_estimators=250, max_samples=4096, contamination="auto", random_state=7, n_jobs=-1).fit(X)
    ecdf = np.sort(-iso.score_samples(X.sample(min(len(X), 20000), random_state=7)))   # anomaly-score distribution
    joblib.dump({"model": iso, "features": BEHAV_FEATURES, "ecdf": ecdf}, SAVED / "behavior_iforest.joblib")
    print("saved IsolationForest; train anomaly p50/p90/p99 =", np.percentile(ecdf, [50, 90, 99]).round(3))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
