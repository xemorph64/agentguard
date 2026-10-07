"""CounselAgent — the customer's side of the debate. Bounded legitimacy discount.

Counsel never overrides a hard rule and never adds risk; it can lower the fused
score by at most `counsel.max_discount` (default 15) points.
"""
from __future__ import annotations

from datetime import datetime

from ..core.types import IST
from .base import BaseAgent, Ctx, clip

WEIGHTS = {"prior_relationship": 0.35, "family_cluster": 0.20, "recurring_payee": 0.15,
           "verified_merchant": 0.15, "salary_day": 0.08, "festival_season": 0.07}


class CounselAgent(BaseAgent):
    name = "counsel"
    timeout_ms = 15

    def __init__(self, max_discount: int = 15) -> None:
        self.max_discount = max_discount

    def features(self, ctx: Ctx) -> dict:
        p, payee = ctx.payer, ctx.payee
        seen = p.known_payees.get(ctx.txn.payee, [])
        recurring = len([t for t in seen if t >= ctx.now_ts - 90 * 86400]) >= 3
        day = datetime.fromtimestamp(ctx.now_ts, IST)
        return {
            "prior_txns_to_payee": len(seen),
            "relationship_days": int((ctx.now_ts - seen[0]) // 86400) if seen else 0,
            "family_cluster": bool(p.cluster and p.cluster == payee.cluster),
            "recurring_payee": recurring,
            "verified_merchant": bool(payee.verified_merchant and ctx.txn.channel in ("p2m", "collect")),
            "salary_day": p.salary_day == day.day,
            "festival_season": day.month in (10, 11, 2),
        }

    def score(self, f: dict, ctx: Ctx) -> tuple[float, list]:
        """Returns legitimacy 0-100 (higher = more credible as a legit payment)."""
        prior_f = clip(f["prior_txns_to_payee"] / 5.0) * clip(f["relationship_days"] / 90.0 + 0.2)
        c = {
            "prior_relationship": WEIGHTS["prior_relationship"] * prior_f,
            "family_cluster": WEIGHTS["family_cluster"] * (1.0 if f["family_cluster"] else 0.0),
            "recurring_payee": WEIGHTS["recurring_payee"] * (1.0 if f["recurring_payee"] else 0.0),
            "verified_merchant": WEIGHTS["verified_merchant"] * (1.0 if f["verified_merchant"] else 0.0),
            "salary_day": WEIGHTS["salary_day"] * (1.0 if f["salary_day"] else 0.0),
            "festival_season": WEIGHTS["festival_season"] * (1.0 if f["festival_season"] else 0.0),
        }
        return sum(c.values()) * 100, self._signals(f, c)

    def discount(self, legitimacy: float, hard_rule_fired: bool) -> float:
        if hard_rule_fired:
            return 0.0
        return round(min(legitimacy / 100.0 * self.max_discount, self.max_discount), 1)
