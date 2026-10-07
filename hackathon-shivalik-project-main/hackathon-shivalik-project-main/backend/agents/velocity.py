"""Velocity Agent: multi-window counters, micro-payment probing, multi-payee bursts, spend spikes, personal baseline."""
import pandas as pd
from ..orchestrator import BaseAgent, AgentResult
from ..channel_rules import noisy_or


class VelocityAgent(BaseAgent):
    name = "velocity"

    def __init__(self, store):
        self.store = store

    def analyze(self, txn, ctx):
        now, amt = pd.Timestamp(txn["ts"]).timestamp(), float(txn["amount"])
        v = self.store.stats(txn["src"], now, amt, txn["dst"])
        comps, why, flags = [], [], []

        def hit(w, msg, flag=None):
            comps.append(w); why.append(msg)
            if flag: flags.append(flag)

        if v["c1m"] + 1 >= 8:   hit(.80, f"{v['c1m']+1} payments in 1 minute", "VEL_BURST")
        elif v["c1m"] + 1 >= 4: hit(.50, f"{v['c1m']+1} payments in 1 minute", "VEL_BURST")
        if v["c5m"] + 1 >= 8:   hit(.50, f"{v['c5m']+1} payments in 5 minutes", "VEL_BURST_5M")
        if v["c1h"] + 1 >= 15:  hit(.50, f"{v['c1h']+1} payments in 1 hour", "VEL_BURST_1H")
        if amt < 200 and v["small10m"] + 1 >= 4:
            hit(.80, f"micro-payment probing: {v['small10m']+1} tiny payments in 10 minutes", "VEL_MICRO_PROBING")
        elif v["small10m"] >= 3 and amt >= 10_000:
            hit(.85, f"large payment ({amt:,.0f}) after {v['small10m']} micro-payment probes", "VEL_MICRO_PROBING")
        if v["payees_1h"] >= 6:
            hit(.55, f"{v['payees_1h']} different payees in 1 hour", "VEL_MULTI_PAYEE")
        r = v["recent"]
        if len(r) >= 4 and all(r[i] < r[i + 1] for i in range(len(r) - 1)) and r[-1] >= 3 * r[0]:
            hit(.40, "escalating amounts (probing limits)", "VEL_ESCALATION")
        if v["spend_ratio"] and v["spend_ratio"] >= 5 and v["s24h"] + amt >= 50_000:
            hit(.70 if v["spend_ratio"] >= 10 else .50, f"24h spend is {v['spend_ratio']:.1f}x the customer's daily average", "VEL_SPEND_SPIKE")
        if txn.get("failed_attempts_1h", 0) >= 3:
            hit(.50, f"{txn['failed_attempts_1h']} failed attempts before success", "VEL_FAILED_THEN_SUCCESS")

        score = round(100 * noisy_or(comps), 1)
        return AgentResult(self.name, score, "; ".join(why) or "normal transaction rate", flags,
                           {k: v[k] for k in ("c1m", "c5m", "c1h", "c24h", "payees_1h", "spend_ratio", "small10m")})
