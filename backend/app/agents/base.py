"""Agent base: every agent computes deterministic features and a 0-100 score.

The PRD's trained models (XGBoost / LightGBM / IsolationForest / GraphSAGE)
run behind the same interface; in demo mode each agent is a transparent
feature-weighted scoring engine (master prompt §3 allows exactly this), so
every score the judges see is reproducible from the displayed features.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..core.types import Account, AgentResult, Txn
from ..db.memory import MemoryStore
from ..graph.memgraph import Graph


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class Ctx:
    txn: Txn
    payer: Account
    payee: Account
    store: MemoryStore
    graph: Graph
    policy: dict
    now_ts: float
    disable_graph: bool = False     # ablation mode: graph-derived features unavailable
    overrides: dict = field(default_factory=dict)   # counterfactual flips
    stepup_failed: bool = False

    def ov(self, key: str, value):
        """Feature override (counterfactuals): returns the flipped value if set."""
        return self.overrides.get(key, value)


class BaseAgent:
    name = "agent"
    timeout_ms = 15

    def run(self, ctx: Ctx) -> AgentResult:
        t0 = time.perf_counter()
        try:
            feats = self.features(ctx)
            score, signals = self.score(feats, ctx)
            conf = self.confidence(feats, score)
            status = "ok"
        except Exception as e:  # noqa: BLE001 — fail-safe: unknown, never "safe"
            return AgentResult(agent=self.name, score=-1, confidence=0.0,
                               signals=[{"feature": "error", "value": str(e)[:120]}],
                               latency_ms=(time.perf_counter() - t0) * 1000,
                               status="unknown")
        return AgentResult(
            agent=self.name, score=round(clip(score, 0, 100), 1), confidence=round(conf, 3),
            signals=signals, features=feats,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2), status=status,
        )

    # subclasses implement
    def features(self, ctx: Ctx) -> dict:
        raise NotImplementedError

    def score(self, feats: dict, ctx: Ctx) -> tuple[float, list]:
        raise NotImplementedError

    def confidence(self, feats: dict, score: float) -> float:
        return 0.6

    # helpers
    @staticmethod
    def _signals(feats: dict, contributions: dict, top: int = 4) -> list:
        """Build signal list from {feature: weighted_contribution}."""
        items = sorted(contributions.items(), key=lambda kv: -kv[1])[:top]
        out = []
        for fname, contrib in items:
            if contrib <= 0.005:
                continue
            out.append({
                "feature": fname, "value": feats.get(fname),
                "contribution": round(contrib * 100, 1),
            })
        return out
