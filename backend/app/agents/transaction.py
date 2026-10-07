"""TransactionAgent — amount semantics vs the payer's 90-day baseline."""
from __future__ import annotations

from .base import BaseAgent, Ctx, clip

WEIGHTS = {
    "amount_z": 0.28, "drain": 0.18, "payee_novel": 0.16, "payee_vpa_age_days": 0.14,
    "just_below_threshold": 0.10, "round_number": 0.08, "hour_deviation": 0.06,
}


class TransactionAgent(BaseAgent):
    name = "transaction"
    timeout_ms = 15

    def features(self, ctx: Ctx) -> dict:
        t, p = ctx.txn, ctx.payer
        z = (t.amount - p.avg_amt_90d) / max(p.std_amt_90d, 1.0)
        hour = ctx.now_ts % 86400 // 3600  # UTC hour; usual_hours stored in IST terms
        hour_ist = int((hour + 5.5) % 24)
        novel = ctx.ov("payee_novel", t.payee not in p.known_payees
                       or len(p.known_payees.get(t.payee, [])) == 0)
        return {
            "amount_z": round(ctx.ov("amount_z", z), 2),
            "drain": round(t.amount / max(p.balance, 1.0), 3),
            "payee_novel": bool(novel),
            "payee_vpa_age_days": ctx.ov("payee_vpa_age_days", ctx.payee.age_days),
            "just_below_threshold": bool(
                9000 < t.amount < 10000 or 45000 < t.amount < 50000 or 900 < t.amount < 1000),
            "round_number": t.amount >= 5000 and t.amount % 1000 == 0,
            "hour_deviation": not (p.usual_hours[0] <= hour_ist <= p.usual_hours[1]),
        }

    def score(self, f: dict, ctx: Ctx) -> tuple[float, list]:
        z_f = clip(abs(f["amount_z"]) / 6.0)
        drain_f = clip(f["drain"] / 0.8)
        novel_f = 1.0 if f["payee_novel"] else 0.0
        age = f["payee_vpa_age_days"]
        age_f = 1.0 if age < 7 else 0.6 if age < 30 else 0.2 if age < 90 else 0.0
        jb_f = 0.7 if f["just_below_threshold"] else 0.0
        round_f = 0.5 if f["round_number"] else 0.0
        hour_f = 0.4 if f["hour_deviation"] else 0.0
        c = {
            "amount_z": WEIGHTS["amount_z"] * z_f,
            "drain": WEIGHTS["drain"] * drain_f,
            "payee_novel": WEIGHTS["payee_novel"] * novel_f,
            "payee_vpa_age_days": WEIGHTS["payee_vpa_age_days"] * age_f,
            "just_below_threshold": WEIGHTS["just_below_threshold"] * jb_f,
            "round_number": WEIGHTS["round_number"] * round_f,
            "hour_deviation": WEIGHTS["hour_deviation"] * hour_f,
        }
        return sum(c.values()) * 100, self._signals(f, c)

    def confidence(self, f: dict, score: float) -> float:
        if score > 45:
            return 0.85
        return 0.6
