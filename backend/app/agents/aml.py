"""AMLAgent — graph/network features: network risk, structuring, distance to fraud."""
from __future__ import annotations

from functools import lru_cache

from .base import BaseAgent, Ctx, clip

WEIGHTS = {"network_risk": 0.30, "structuring_aggregation": 0.28, "distance_to_flagged": 0.16,
           "cycle_participation": 0.10, "fan_out_2hop": 0.16}


@lru_cache(maxsize=1)
def _load_model():
    import warnings
    import joblib
    from pathlib import Path
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")   # pickled with a neighbouring sklearn minor version
        a = joblib.load(Path(__file__).parents[1] / "models" / "saved" / "aml_model.joblib")
    if hasattr(a["model"], "n_jobs"):
        a["model"].n_jobs = 1  # single-row predicts are much faster without the process pool
    return a


class AMLAgent(BaseAgent):
    name = "aml"
    timeout_ms = 40

    def __init__(self) -> None:
        # Load the model up front so the first request doesn't blow the agent timeout.
        a = _load_model()
        self.model, self.cal, self.ml_features = a["model"], a["calibrator"], a["features"]

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
            if not g.is_money(e) or e["props"].get("ts", 0) < t0:
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
            if g.is_money(e):
                sinks1.add(e["dst"])
        sinks2 = set()
        for s in sinks1:
            for i in g._out.get(s, ()):
                e = g.edges[i]
                if g.is_money(e):
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
        ml_score = self._ml_score(ctx)
        final_score = 0.5 * graph_score + 0.5 * ml_score
        sigs = self._signals(f, {k: v * 0.5 for k, v in c.items()}, top=3)
        if ml_score >= 1:
            sigs.append({"feature": "ml_laundering_prob", "value": round(ml_score, 1),
                         "contribution": round(0.5 * ml_score, 1)})
            sigs.sort(key=lambda x: -x["contribution"])
        f["ml_laundering_prob"] = round(ml_score, 1)
        return final_score, sigs

    def ml_row(self, ctx: Ctx) -> dict:
        """The trained model's transaction features (models/features.py::txn_features),
        computed causally from settled history in the store — same definitions as training."""
        import math
        t, store = ctx.txn, ctx.store
        t0 = t.ts - 86400
        src_moves = [m for m in store.ledger_moves.get(t.payer, ()) if t0 <= m[0] < t.ts]
        dst_moves = [m for m in store.ledger_moves.get(t.payee, ()) if t0 <= m[0] < t.ts]
        src_out = [m[2] for m in src_moves if m[1] == "out"]
        src_in = [m[2] for m in src_moves if m[1] == "in"]
        dst_in = [m[2] for m in dst_moves if m[1] == "in"]
        prior_out = [m[2] for m in store.ledger_moves.get(t.payer, ()) if m[1] == "out" and m[0] < t.ts]
        avg = sum(prior_out) / len(prior_out) if prior_out else ctx.payer.avg_amt_90d
        hour = int((t.ts % 86400 // 3600 + 5.5) % 24)
        src_in_sum = sum(src_in)
        return {
            "log_amount": math.log1p(t.amount), "hour": hour, "is_night": int(hour < 6),
            "is_round": int(t.amount % 1000 == 0), "near_limit": int(45000 <= t.amount < 50000),
            "cross_border": 0, "cross_bank": 0,
            "chan_UPI": int(t.channel != "netbanking"), "chan_NET_BANKING": int(t.channel == "netbanking"),
            "src_cnt_24h": len(src_out), "src_sum_24h": sum(src_out),
            "src_in_cnt_24h": len(src_in), "src_in_sum_24h": src_in_sum,
            "dst_in_cnt_24h": len(dst_in), "dst_in_sum_24h": sum(dst_in),
            "new_payee": int(not ctx.payer.known_payees.get(t.payee)),
            "src_amount_vs_avg": min(max(t.amount / max(avg, 1.0), 0.0), 50.0),
            "forward_ratio": min(t.amount / (src_in_sum + 1), 10.0),
        }

    def _ml_score(self, ctx: Ctx) -> float:
        try:
            import pandas as pd
            x = pd.DataFrame([self.ml_row(ctx)])[self.ml_features].astype(float)
            return float(self.cal.predict(self.model.predict_proba(x)[:, 1])[0]) * 100
        except Exception:  # noqa: BLE001 — model unavailable: graph half still scores
            return 0.0
