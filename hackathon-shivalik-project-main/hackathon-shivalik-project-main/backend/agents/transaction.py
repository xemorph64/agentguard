"""Transaction Agent: personal-baseline amount anomaly + payee novelty + time-of-day + channel rules."""
import numpy as np
from ..orchestrator import BaseAgent, AgentResult
from ..channel_rules import evaluate as channel_eval, noisy_or, LIMITS

POP_MEDIAN_LOG, POP_SCALE = np.log(1800.0), 1.0     # population prior for customers with thin history


class TransactionAgent(BaseAgent):
    name = "transaction"

    def __init__(self, history):
        self.history = history

    def analyze(self, txn, ctx):
        amt = float(txn["amount"])
        sent = self.history.sent(txn["src"])
        amts = np.log1p([r["amount"] for r in sent]) if sent else np.array([])
        comps, why = [], []

        # 1) robust amount z-score (median/MAD on log amounts) vs the customer's own history
        if len(amts) >= 5:
            med = np.median(amts); mad = 1.4826 * np.median(np.abs(amts - med)) + 0.3
            basis = "own history"
        else:
            med, mad, basis = POP_MEDIAN_LOG, POP_SCALE + 0.5, "population prior"
        z = (np.log1p(amt) - med) / mad
        if z > 1.5:
            w = min(1.0, (z - 1.5) / 3.5) * .65
            comps.append(w); why.append(f"amount {amt:,.0f} is {np.expm1(np.log1p(amt) - med):.1f}x the usual ({basis})")

        # 2) payee novelty (+ stronger when large)
        new_payee = ctx.get("new_payee", not self.history.has_paid(txn["src"], txn["dst"]))
        if new_payee:
            w = .15 + (.25 if amt >= 20_000 else 0); comps.append(w); why.append("first payment to this payee")

        # 3) unusual hour for this customer
        hrs = [r["ts"].hour for r in sent]
        h = txn["ts"].hour
        if len(hrs) >= 10 and sum(abs(h - x) <= 2 or abs(h - x) >= 22 for x in hrs) / len(hrs) < .05:
            comps.append(.25); why.append(f"unusual hour ({h}:00) for this customer")

        # 4) amount hugging the channel limit
        lim = LIMITS.get(txn.get("channel"), 100_000)
        if .9 * lim <= amt <= lim:
            comps.append(.30); why.append("amount just under channel limit")

        # 5) channel-specific rules (UPI collect scam, new beneficiary, takeover ...)
        new_dev = ctx.get("new_device", False)
        risk, hits = channel_eval(txn, new_payee, new_dev)
        if hits:
            comps.append(risk); why.append("channel rules: " + ", ".join(n for n, _ in hits))

        score = round(100 * noisy_or(comps), 1)
        flags = [f"TXN_{n}" for n, _ in hits]
        return AgentResult(self.name, score, "; ".join(why) or "consistent with customer's normal payments", flags,
                           {"amount_z": round(float(z), 2), "baseline": basis, "new_payee": bool(new_payee),
                            "channel_hits": [n for n, _ in hits]})
