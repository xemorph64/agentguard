"""AML Agent: calibrated Random Forest + typology rules engine + graph round-tripping check."""
import joblib
import numpy as np, pandas as pd
from pathlib import Path
from ..orchestrator import BaseAgent, AgentResult
from ..models.features import STRUCT_LIMIT

MODEL = Path(__file__).parents[1] / "models" / "saved" / "aml_model.joblib"
HIGH_RISK_COUNTRIES = {"KP", "IR", "MM"}      # example FATF call-for-action list; VERIFY against the current list


def typology_rules(f, txn, graph):
    """Returns (score 0-100, [hit names]) from classic AML typologies."""
    hits, s = [], 0
    amt = txn["amount"]
    if f["near_limit"] and f["src_cnt_24h"] >= 3:
        hits.append("STRUCTURING"); s += 45 + (10 if f["new_payee"] else 0)
    if f["src_in_cnt_24h"] >= 3 and f["forward_ratio"] >= 0.8:
        hits.append("RAPID_PASS_THROUGH"); s += 40
    if f["dst_in_cnt_24h"] >= 10 and f["dst_in_sum_24h"] >= 2 * STRUCT_LIMIT:
        hits.append("FAN_IN_AGGREGATION"); s += 25
    if graph.graph_features(txn["src"])["in_cycle"]:
        hits.append("ROUND_TRIPPING"); s += 40
    if f["cross_border"] and amt >= STRUCT_LIMIT:
        hits.append("HIGH_VALUE_CROSS_BORDER"); s += 25
    if f["src_cnt_24h"] >= 10:
        hits.append("VELOCITY_SPIKE"); s += 20
    if f["new_payee"] and amt >= STRUCT_LIMIT:
        hits.append("LARGE_NEW_PAYEE"); s += 15
    return min(s, 100), hits


class AMLAgent(BaseAgent):
    name = "aml"

    def __init__(self, history, graph, model_path=MODEL):
        a = joblib.load(model_path)
        self.model, self.cal, self.features = a["model"], a["calibrator"], a["features"]
        self.imp, self.mean, self.std = a["importance"], a["mean"], a["std"]
        self.thr_mid, self.thr_hi = a["thr_mid"], a["thr_hi"]
        self.history, self.graph = history, graph

    def _drivers(self, f, k=3):
        """Fast explanation: importance x standardised deviation (full SHAP: explain_shap)."""
        sc = {n: self.imp[n] * abs((f[n] - self.mean[n]) / self.std[n]) for n in self.features}
        return [{"feature": n, "value": round(float(f[n]), 2), "impact": round(float(v), 3)}
                for n, v in sorted(sc.items(), key=lambda r: -r[1])[:k]]

    def analyze(self, txn, ctx):
        f = ctx.get("tf") or self.history.txn_features(txn)
        x = pd.DataFrame([f])[self.features]
        p = float(self.cal.predict(self.model.predict_proba(x)[:, 1])[0])
        ml, (rule, hits) = 100 * p, typology_rules(f, txn, self.graph)

        score = 0.65 * ml + 0.35 * rule + (10 if ml > 60 and rule > 60 else 0)
        flags = [f"AML_{h}" for h in hits]
        if txn.get("dst_country") in HIGH_RISK_COUNTRIES or txn.get("src_country") in HIGH_RISK_COUNTRIES:
            flags.append("SANCTIONED"); score = 100
        score = round(min(score, 100), 1)

        drivers = self._drivers(f)
        reason = (f"AML model {p:.0%}; typologies: {', '.join(hits) if hits else 'none'}; "
                  f"drivers: {', '.join(d['feature'] + '=' + str(d['value']) for d in drivers)}")
        return AgentResult(self.name, score, reason, flags,
                           {"ml_prob": round(p, 3), "rule_score": rule, "rules": hits, "drivers": drivers})

    def explain_shap(self, txn):
        """On-demand exact SHAP for the transaction detail card (needs `pip install shap`)."""
        import shap
        x = pd.DataFrame([self.history.txn_features(txn)])[self.features]
        sv = shap.TreeExplainer(self.model).shap_values(x)
        sv = sv[1] if isinstance(sv, list) else sv[..., 1]
        return sorted(zip(self.features, sv[0].tolist()), key=lambda r: -abs(r[1]))[:5]
