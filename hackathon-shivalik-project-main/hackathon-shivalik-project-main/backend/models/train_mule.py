"""Train the mule-account model (XGBoost + isotonic calibration) on POINT-IN-TIME snapshots.
Run:  python -m backend.models.train_mule [path/to/HI-Small_Trans.csv]
Label: an account is a MULE if it received laundering money AND forwarded laundering money (intermediary).
Snapshots at several cut-off times teach the model to spot mules EARLY (partial history), exactly as at serving."""
import sys, json, joblib
from pathlib import Path
import pandas as pd, xgboost as xgb
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import train_test_split
from .data import load
from .features import account_features, MULE_FEATURES
from .common import pick_thresholds, evaluate

SAVED = Path(__file__).parent / "saved"
CUT_QUANTILES = (.3, .45, .6, .75, .9, 1.0)


def main(path=None):
    df = load(path)
    laun = df[df.is_laundering == 1]
    mules = set(laun["dst"]) & set(laun["src"])

    frames = []
    for q in CUT_QUANTILES:
        cut = df["ts"].quantile(q)
        Xc = account_features(df[df.ts <= cut], as_of=cut)
        Xc = Xc[(Xc.in_count + Xc.out_count) >= 2].copy()
        Xc["acct"] = Xc.index
        frames.append(Xc)
    X = pd.concat(frames, ignore_index=True)
    y = X["acct"].isin(mules).astype(int).values
    print(f"snapshots={len(X)} accounts={X.acct.nunique()} mule_snapshots={y.sum()} ({y.mean():.2%})")

    accts = pd.Series(X["acct"].unique()); lab = accts.isin(mules).astype(int)   # split by ACCOUNT (no leakage)
    tr, tmp, _, ltmp = train_test_split(accts, lab, test_size=.4, stratify=lab, random_state=7)
    va, te = train_test_split(tmp, test_size=.5, stratify=ltmp, random_state=7)
    sel = lambda s: X["acct"].isin(set(s)).values
    Xtr, ytr, Xva, yva, Xte, yte = X[sel(tr)][MULE_FEATURES], y[sel(tr)], X[sel(va)][MULE_FEATURES], y[sel(va)], X[sel(te)][MULE_FEATURES], y[sel(te)]

    model = xgb.XGBClassifier(
        n_estimators=500, max_depth=5, learning_rate=.05, subsample=.8, colsample_bytree=.8,
        min_child_weight=2, scale_pos_weight=(ytr == 0).sum() / max(ytr.sum(), 1),
        eval_metric="aucpr", early_stopping_rounds=40, random_state=7, n_jobs=-1)
    model.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)

    cal = IsotonicRegression(out_of_bounds="clip").fit(model.predict_proba(Xva)[:, 1], yva)
    mid, hi = pick_thresholds(yva, cal.predict(model.predict_proba(Xva)[:, 1]))
    metrics = evaluate(yte, cal.predict(model.predict_proba(Xte)[:, 1]), mid, hi)
    print(json.dumps(metrics, indent=2))

    joblib.dump({"model": model, "calibrator": cal, "features": MULE_FEATURES, "thr_mid": mid, "thr_hi": hi,
                 "importance": dict(zip(MULE_FEATURES, map(float, model.feature_importances_))),
                 "metrics": metrics, "mule_accounts": sorted(mules)}, SAVED / "mule_model.joblib")
    (SAVED / "mule_metrics.json").write_text(json.dumps(metrics, indent=2))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
