"""Mule Agent: XGBoost (calibrated) on account behaviour + graph structure + SHAP reasons."""
import joblib
import numpy as np, pandas as pd, xgboost as xgb
from pathlib import Path
from ..orchestrator import BaseAgent, AgentResult

MODEL = Path(__file__).parents[1] / "models" / "saved" / "mule_model.joblib"
COLD_START_P = 0.20           # unknown account: mildly suspicious, never decisive


class MuleAgent(BaseAgent):
    name = "mule"

    def __init__(self, history, graph, model_path=MODEL):
        a = joblib.load(model_path)
        self.model, self.cal, self.features = a["model"], a["calibrator"], a["features"]
        self.thr_mid, self.thr_hi = a["thr_mid"], a["thr_hi"]
        self.history, self.graph = history, graph

    def _account(self, acct, as_of):
        f = self.history.account_features(acct, as_of)
        if f is None:
            return COLD_START_P, [], True, None
        x = pd.DataFrame([f])[self.features]
        p = float(self.cal.predict(self.model.predict_proba(x)[:, 1])[0])
        contrib = self.model.get_booster().predict(xgb.DMatrix(x), pred_contribs=True)[0][:-1]   # true SHAP
        top = sorted(zip(self.features, contrib, x.iloc[0].values), key=lambda r: -r[1])[:3]
        return p, [{"feature": n, "value": round(float(v), 2), "shap": round(float(c), 3)} for n, c, v in top], False, f

    def analyze(self, txn, ctx):
        best = None
        for role, acct, w in (("payee", txn["dst"], 1.0), ("sender", txn["src"], 0.85)):
            p, top, cold, feats = self._account(acct, txn["ts"])
            g = self.graph.graph_features(acct)
            combined = w * (0.7 * p + 0.3 * self.graph.graph_score(acct))
            if best is None or combined > best["combined"]:
                best = dict(role=role, acct=acct, p=p, top=top, cold=cold, g=g, combined=combined, f=feats)

        b, g = best, best["g"]
        flags = []
        f = b["f"] or {}
        pass_through = g["fan_in"] >= 5 and f.get("pass_through_ratio", 0) >= 0.8 and f.get("median_hold_hours", 720) <= 12
        corroborated = g["in_cycle"] or g["flagged_neighbors"] > 0 or pass_through     # merchants (fan-in only) don't qualify
        if not b["cold"] and b["p"] >= self.thr_hi and corroborated:
            flags.append("MULE_CORROBORATED")            # model AND graph agree -> orchestrator hard-blocks
        elif not b["cold"] and b["p"] >= self.thr_mid:
            flags.append("MULE_SUSPECTED")

        why = ", ".join(f"{t['feature']}={t['value']}" for t in b["top"]) or "no history (new account)"
        extra = []
        if g["in_cycle"]: extra.append("part of a circular money loop")
        if g["flagged_neighbors"]: extra.append(f"{g['flagged_neighbors']} flagged accounts nearby")
        if g["cluster_suspicious"]: extra.append(f"suspicious cluster of {g['cluster_size']} accounts")
        reason = (f"{b['role']} {b['acct']}: mule probability {b['p']:.0%}; drivers: {why}"
                  + (f"; graph: {', '.join(extra)}" if extra else ""))
        score = 100 * min(b["combined"], 1.0)
        if "MULE_CORROBORATED" in flags: score = max(score, 90.0)      # keep score consistent with the tier
        elif "MULE_SUSPECTED" in flags: score = max(score, 61.0)
        return AgentResult(self.name, round(score, 1), reason, flags,
                           {"account": b["acct"], "role": b["role"], "cold_start": b["cold"], "ml_prob": round(b["p"], 3),
                            "graph": g, "top_features": b["top"]})
