"""AgentGuard orchestrator: runs agents in parallel, applies hard rules, returns a decision."""
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict


@dataclass
class AgentResult:
    agent: str
    score: float          # 0-100 risk
    reason: str
    flags: list = field(default_factory=list)  # e.g. ["KNOWN_MULE"] for hard rules
    details: dict = field(default_factory=dict)  # SHAP drivers, graph stats, rule hits


class BaseAgent:
    name = "base"

    def analyze(self, txn: dict, ctx: dict) -> AgentResult:
        raise NotImplementedError


# Weights must sum to 1.0. Explainer is not scored (it runs after the decision).
WEIGHTS = {
    "transaction": 0.20,
    "behavior": 0.20,
    "velocity": 0.15,
    "mule": 0.25,
    "aml": 0.20,
}

# Flags that force a decision regardless of score
HARD_BLOCK_FLAGS = {"BLACKLISTED", "KNOWN_MULE", "MULE_CORROBORATED", "SANCTIONED"}
HARD_ALLOW_FLAGS = {"WHITELISTED"}


def decide(score: float) -> str:
    if score <= 30:
        return "SUCCESS"
    if score <= 60:
        return "VERIFY"
    if score <= 80:
        return "PAUSE"
    return "BLOCK"


class Orchestrator:
    def __init__(self, agents: list, explainer=None, ledger=None, lists=None, ctx_builder=None):
        self.ctx_builder = ctx_builder  # computes shared features once per txn
        self.agents = agents          # the 5 scoring agents
        self.explainer = explainer    # 6th agent: writes explanations
        self.ledger = ledger          # hash-chained audit log
        self.lists = lists            # blacklist/whitelist store

    def analyze(self, txn: dict) -> dict:
        start = time.perf_counter()
        debate = []                   # agent debate log

        # 1. Blacklist / whitelist hard rules run before any agent
        ctx = dict(self.ctx_builder(txn)) if self.ctx_builder else {}
        if self.lists:
            if self.lists.is_blacklisted(txn):
                return self._finalize(txn, [], 100.0, "BLOCK",
                                      ["BLACKLISTED"], debate, start,
                                      "Hard rule: payee/account is blacklisted")
            ctx["whitelisted"] = self.lists.is_whitelisted(txn)

        # 2. Run agents in parallel
        with ThreadPoolExecutor(max_workers=len(self.agents)) as pool:
            results = list(pool.map(lambda a: a.analyze(txn, ctx), self.agents))

        for r in results:
            debate.append(f"{r.agent} agent: score {r.score:.0f} - {r.reason}")

        # 3. Weighted score
        weighted = sum(WEIGHTS.get(r.agent, 0) * r.score for r in results)
        peak = max((r.score for r in results), default=0.0)
        # one strong specialist (>=50) pulls the score halfway toward its own; weak noise never does
        score = weighted + 0.5 * max(0.0, peak - weighted) if peak >= 50 else weighted
        flags = [f for r in results for f in r.flags]
        decision = decide(score)
        rule_note = None

        # 4. Hard rules override the weighted score
        if HARD_BLOCK_FLAGS & set(flags):
            decision, score = "BLOCK", max(score, 90.0)
            rule_note = f"Hard rule {sorted(HARD_BLOCK_FLAGS & set(flags))} overrides"
        elif ctx.get("whitelisted") and decision in ("VERIFY", "PAUSE"):
            decision = "SUCCESS"
            rule_note = "Whitelisted payee downgrades decision"

        # 5. Record disagreement between agents
        top, low = max(results, key=lambda r: r.score), min(results, key=lambda r: r.score)
        if top.score - low.score > 40:
            debate.append(f"Disagreement: {top.agent} ({top.score:.0f}) vs "
                          f"{low.agent} ({low.score:.0f}); "
                          f"{rule_note or 'resolved by weighted average'}")

        return self._finalize(txn, results, score, decision, flags, debate, start, rule_note)

    def _finalize(self, txn, results, score, decision, flags, debate, start, rule_note):
        out = {
            "txn_id": txn.get("txn_id"),
            "channel": txn.get("channel"),        # UPI / NET_BANKING
            "score": round(score, 1),
            "decision": decision,
            "flags": flags,
            "agents": [asdict(r) for r in results],
            "debate": debate,
            "rule_note": rule_note,
        }
        # 6th agent: plain-language explanation for audit + customer
        if self.explainer:
            out["explanation"] = self.explainer.explain(txn, out)
        out["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        if self.ledger:
            out["ledger_hash"] = self.ledger.append(out)
        return out
