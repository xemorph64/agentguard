"""VelocityAgent — time-window counters and burst patterns."""
from __future__ import annotations

from .base import BaseAgent, Ctx, clip

WEIGHTS = {"micro_burst_10m": 0.20, "distinct_payees_1h": 0.20, "sum_1h_vs_baseline": 0.20,
           "sub_threshold_clustering": 0.30, "rapid_fire_1m": 0.10, "probed_route": 0.40}


class VelocityAgent(BaseAgent):
    name = "velocity"
    timeout_ms = 10

    def features(self, ctx: Ctx) -> dict:
        v = ctx.store.velocity(ctx.txn.payer)
        for k in ("micro_burst_10m", "distinct_payees_1h", "count_out_1h", "count_out_1m", "sum_out_1h"):
            v[k] = ctx.ov(k, v[k])   # simulator / counterfactual what-ifs
        baseline_per_txn = max(ctx.payer.avg_amt_90d, 100.0)
        return {
            "micro_burst_10m": v["micro_burst_10m"],
            "distinct_payees_1h": v["distinct_payees_1h"],
            "count_out_1h": v["count_out_1h"],
            "count_out_1m": v["count_out_1m"],
            "sum_out_1h": round(v["sum_out_1h"], 0),
            "sum_1h_vs_baseline": round(v["sum_out_1h"] / (baseline_per_txn * 5), 2),
            "sub_threshold_count_24h": self._sub_threshold_count(ctx),
            # a large payment down a route that micro-payments just tested (same payee / same phone)
            "probed_route": self._probed_route(ctx) if ctx.txn.amount > 1000 else 0,
        }

    @staticmethod
    def _probed_route(ctx: Ctx) -> int:
        from ..fraud.typology import _probe_linked_payee
        if ctx.disable_graph:
            t = ctx.txn
            return sum(1 for m in ctx.store.ledger_moves.get(t.payer, ())
                       if m[1] == "out" and m[2] <= 50 and m[3] == t.payee and m[0] >= t.ts - 3600)
        return _probe_linked_payee(ctx)

    @staticmethod
    def _sub_threshold_count(ctx: Ctx) -> int:
        """Outgoing payments just under the ₹10k / ₹50k reporting thresholds in 24h."""
        moves = ctx.store.ledger_moves.get(ctx.txn.payer, ())
        t0 = ctx.now_ts - 86400
        return sum(1 for m in moves if m[0] >= t0 and m[1] == "out"
                   and (9000 < m[2] < 10000 or 45000 < m[2] < 50000))

    def score(self, f: dict, ctx: Ctx) -> tuple[float, list]:
        c = {
            "micro_burst_10m": WEIGHTS["micro_burst_10m"] * clip(f["micro_burst_10m"] / 8.0),
            "distinct_payees_1h": WEIGHTS["distinct_payees_1h"] * clip(f["distinct_payees_1h"] / 10.0),
            "sum_1h_vs_baseline": WEIGHTS["sum_1h_vs_baseline"] * clip(f["sum_1h_vs_baseline"]),
            "sub_threshold_clustering": WEIGHTS["sub_threshold_clustering"]
                * clip(f["sub_threshold_count_24h"] / 5.0),
            "rapid_fire_1m": WEIGHTS["rapid_fire_1m"] * clip(f["count_out_1m"] / 3.0),
            "probed_route": WEIGHTS["probed_route"] * clip(f["probed_route"] / 3.0),
        }
        return sum(c.values()) * 100, self._signals(f, c)
