"""MuleAgent — beneficiary-side account behaviour, store + graph features."""
from __future__ import annotations

import copy

from .base import BaseAgent, Ctx, clip

WEIGHTS = {
    "distinct_sources_24h": 0.25, "unlinked_sources": 0.12, "pass_through_24h": 0.25,
    "median_hold_min": 0.08, "account_age_days": 0.10, "shared_device_accounts": 0.10,
    "dormant_awakening": 0.04, "phone_fri": 0.06,
}


class MuleAgent(BaseAgent):
    name = "mule"
    timeout_ms = 35

    def run(self, ctx: Ctx):
        """A mule can sit on either side of a payment: score payee and payer, keep the riskier."""
        payee_res = super().run(ctx)
        sender_ctx = copy.copy(ctx)
        sender_ctx.payee = ctx.payer
        payer_res = super().run(sender_ctx)
        best = payer_res if payer_res.status == "ok" and payer_res.score > payee_res.score else payee_res
        if best.status == "ok":
            best.features["side"] = "payer" if best is payer_res else "payee"
        return best

    def features(self, ctx: Ctx) -> dict:
        payee = ctx.payee
        io = ctx.store.inflow_outflow(payee.id, since=ctx.now_ts - 86400)
        hold_min = ctx.store.median_hold_minutes(payee.id)
        sources = ctx.graph.fan_in(payee.id, 86400)["sources"]
        if ctx.disable_graph:
            # ablation: the graph is down → linkage + device features unavailable
            unlinked, shared = 0.0, 0
        else:
            unlinked = ctx.ov("unlinked_sources", self._unlinked_fraction(ctx, sources))
            shared = ctx.graph.shared_device_count(payee.id)
        return {
            "distinct_sources_24h": ctx.ov("distinct_sources_24h", io["distinct_sources"]),
            "unlinked_sources": round(unlinked, 2),
            "pass_through_24h": ctx.ov("pass_through_24h",
                                       round(io["outflow"] / io["inflow"], 2) if io["inflow"] > 0 else 0.0),
            "median_hold_min": round(hold_min, 1) if hold_min is not None else None,
            "account_age_days": ctx.ov("account_age_days", payee.age_days),
            "shared_device_accounts": shared,
            "dormant_awakening": bool(payee.dormant_since) and io["in_count"] >= 3,
            "phone_fri": payee.fri_level,
        }

    @staticmethod
    def _unlinked_fraction(ctx: Ctx, sources: list[str]) -> float:
        """Fraction of inbound sources that share no cluster with each other or the payee.

        Families legitimately pay each other (linked clusters); mule fan-in comes
        from strangers. This is the graph-derived signal the per-account view misses.
        """
        if len(sources) < 2:
            return 0.0
        payee_cluster = ctx.payee.cluster
        pairs = total = 0
        for i in range(len(sources)):
            for j in range(i + 1, len(sources)):
                a, b = ctx.store.accounts.get(sources[i]), ctx.store.accounts.get(sources[j])
                if not a or not b:
                    continue
                total += 1
                if a.cluster != b.cluster and a.cluster != payee_cluster:
                    pairs += 1
        return pairs / total if total else 0.0

    def score(self, f: dict, ctx: Ctx) -> tuple[float, list]:
        age = f["account_age_days"]
        hold = f["median_hold_min"]
        hold_f = 0.3 if hold is None else 1.0 if hold < 10 else 0.7 if hold < 30 else 0.4 if hold < 60 else 0.1
        passthrough_f = clip((f["pass_through_24h"] - 0.5) / 0.5) if f["pass_through_24h"] > 0.5 else 0.0
        c = {
            "distinct_sources_24h": WEIGHTS["distinct_sources_24h"] * clip(f["distinct_sources_24h"] / 20.0),
            "unlinked_sources": WEIGHTS["unlinked_sources"] * f["unlinked_sources"],
            "pass_through_24h": WEIGHTS["pass_through_24h"] * passthrough_f,
            "median_hold_min": WEIGHTS["median_hold_min"] * hold_f,
            "account_age_days": WEIGHTS["account_age_days"]
                * (1.0 if age < 7 else 0.7 if age < 30 else 0.3 if age < 90 else 0.0),
            "shared_device_accounts": WEIGHTS["shared_device_accounts"] * clip(f["shared_device_accounts"] / 10.0),
            "dormant_awakening": WEIGHTS["dormant_awakening"] * (1.0 if f["dormant_awakening"] else 0.0),
            "phone_fri": WEIGHTS["phone_fri"] * {"VERY_HIGH": 1.0, "HIGH": 0.7, "MEDIUM": 0.3, "LOW": 0.0}.get(f["phone_fri"], 0.0),
        }
        return sum(c.values()) * 100, self._signals(f, c, top=5)

    def confidence(self, f: dict, score: float) -> float:
        if f["distinct_sources_24h"] >= 10 and f["pass_through_24h"] >= 0.9:
            return 0.95
        return 0.75
