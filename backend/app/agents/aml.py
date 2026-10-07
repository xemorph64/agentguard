"""AMLAgent — graph/network features: network risk, structuring, distance to fraud."""
from __future__ import annotations

from .base import BaseAgent, Ctx, clip

WEIGHTS = {"network_risk": 0.30, "structuring_aggregation": 0.28, "distance_to_flagged": 0.16,
           "cycle_participation": 0.10, "fan_out_2hop": 0.16}


class AMLAgent(BaseAgent):
    name = "aml"
    timeout_ms = 40

    def __init__(self) -> None:
        # Load the model up front so the first request doesn't blow the agent timeout.
        import joblib
        from pathlib import Path
        a = joblib.load(Path(__file__).parents[1] / "models" / "saved" / "aml_model.joblib")
        self.model, self.cal, self.ml_features = a["model"], a["calibrator"], a["features"]
        if hasattr(self.model, "n_jobs"):
            self.model.n_jobs = 1  # single-row predicts are much faster without the process pool

    def features(self, ctx: Ctx) -> dict:
        g, payee = ctx.graph, ctx.payee
        if ctx.disable_graph:
            return {"network_risk": 0.0, "structuring_aggregation": 0.0,
                    "unlinked_fraction": None, "distance_to_flagged_hops": None,
                    "cycle_member": False, "fan_out_2hop": 0}
        risk = ctx.ov("network_risk", g.network_risk.get(payee.id, 0.0))
        override_struct = ctx.overrides.get("structuring_aggregation")
        if override_struct is None:
            struct, unlinked = self._structuring_into_payee(ctx)
        else:
            struct, unlinked = override_struct, 0.0
        dist = ctx.ov("distance_to_flagged_hops", g.distance_to_flagged(payee.id, max_hops=3))
        fo2 = self._fan_out_2hop(g, payee.id)
        return {
            "network_risk": risk,
            "structuring_aggregation": round(struct, 2),
            "unlinked_fraction": round(unlinked, 2),
            "distance_to_flagged_hops": dist,
            "cycle_member": payee.id in g.cycle_members,
            "fan_out_2hop": fo2,
        }

    @staticmethod
    def _structuring_into_payee(ctx: Ctx) -> tuple[float, float]:
        """Distinct senders making just-below-threshold payments into the payee in 24h,
        weighted by how unlinked those senders are (graph-derived)."""
        g = ctx.graph
        t0 = ctx.now_ts - 86400
        senders: dict[str, bool] = {}   # sender -> just_below?
        for i in g._in.get(ctx.payee.id, ()):
            e = g.edges[i]
            if e["type"] != "PAID" or e["props"].get("ts", 0) < t0:
                continue
            amt = e["props"].get("amount", 0)
            just_below = 9000 < amt < 10000 or 45000 < amt < 50000
            if just_below:
                senders[e["src"]] = True
        if not senders:
            return 0.0, 0.0
        from .mule import MuleAgent
        unlinked = MuleAgent._unlinked_fraction(ctx, sorted(senders))
        count_f = clip(len(senders) / 6.0)
        # linkage knowledge is graph-derived: without the graph this signal decays
        return count_f * (0.35 + 0.65 * unlinked), unlinked

    @staticmethod
    def _fan_out_2hop(g, account: str) -> int:
        sinks1 = set()
        for i in g._out.get(account, ()):
            e = g.edges[i]
            if e["type"] == "PAID":
                sinks1.add(e["dst"])
        sinks2 = set()
        for s in sinks1:
            for i in g._out.get(s, ()):
                e = g.edges[i]
                if e["type"] == "PAID":
                    sinks2.add(e["dst"])
        return len(sinks2)

    def score(self, f: dict, ctx: Ctx) -> tuple[float, list]:
        dist = f["distance_to_flagged_hops"]
        dist_f = 0.0 if dist is None else {1: 1.0, 2: 0.7, 3: 0.4}.get(dist, 0.0)
        c = {
            "network_risk": WEIGHTS["network_risk"] * clip(f["network_risk"] / 100.0),
            "structuring_aggregation": WEIGHTS["structuring_aggregation"] * f["structuring_aggregation"],
            "distance_to_flagged": WEIGHTS["distance_to_flagged"] * dist_f,
            "cycle_participation": WEIGHTS["cycle_participation"] * (1.0 if f["cycle_member"] else 0.0),
            "fan_out_2hop": WEIGHTS["fan_out_2hop"] * clip(f["fan_out_2hop"] / 15.0),
        }
        graph_score = sum(c.values()) * 100

        # Run ML model (using some basic available features, zeroing others)
        try:
            import joblib, pandas as pd, numpy as np
            from pathlib import Path
            if getattr(self, 'model', None) is None:
                mpath = Path(__file__).parents[1] / "models" / "saved" / "aml_model.joblib"
                a = joblib.load(mpath)
                self.model, self.cal, self.ml_features = a["model"], a["calibrator"], a["features"]
            
            # create dummy dataframe for prediction
            row = {feat: 0.0 for feat in self.ml_features}
            row['log_amount'] = np.log1p(ctx.txn.amount)
            row['hour'] = 12
            row['src_amount_vs_avg'] = ctx.txn.amount / max(ctx.payer.avg_amt_90d, 1.0)
            
            import pandas as pd
            x = pd.DataFrame([row])[self.ml_features]
            p = float(self.cal.predict(self.model.predict_proba(x)[:, 1])[0])
            ml_score = p * 100
        except Exception as e:
            ml_score = 0.0
            print("AML model error:", e)

        final_score = 0.5 * graph_score + 0.5 * ml_score
        sigs = self._signals(f, c, top=3)
        sigs.append({"feature": "ml_model_prob", "value": round(ml_score, 1), "contribution": 0.5 * ml_score})
        
        return final_score, sigs
