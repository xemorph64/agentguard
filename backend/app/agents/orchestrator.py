"""Orchestrator — deterministic hot path: agents → hard rules → fusion → typology → routing.

Parallel agent fan-out with per-agent timeouts; a timed-out agent contributes
"unknown" and can never let a payment silently through (fail-safe, PRD §10).
"""
from __future__ import annotations

import asyncio
import time

from ..core.types import AgentResult, Decision, Txn, now
from .aml import AMLAgent
from .base import Ctx
from .behavior import BehaviorAgent
from .counsel import CounselAgent
from . import explainer
from .mule import MuleAgent
from .transaction import TransactionAgent
from .velocity import VelocityAgent
from ..fraud import typology as typology_mod

COUNTERFACTUAL_MAP = {
    "payee_vpa_age_days": ({"payee_vpa_age_days": 180}, "the payee VPA were older than 90 days"),
    "payee_novel": ({"payee_novel": False}, "the payer had a prior relationship with this payee"),
    "on_call": ({"on_call": False}, "no active call were detected during the payment"),
    "new_device": ({"new_device": False}, "the device were already known to the account"),
    "screen_share": ({"screen_share": False}, "no screen-share flag were present"),
    "distinct_sources_24h": ({"distinct_sources_24h": 2, "unlinked_sources": 0.0},
                             "the payee had few inbound relationships"),
    "pass_through_24h": ({"pass_through_24h": 0.3}, "the payee retained funds instead of forwarding"),
    "network_risk": ({"network_risk": 10, "structuring_aggregation": 0,
                      "distance_to_flagged_hops": None},
                     "the payee were not embedded in a risky network"),
    "structuring_aggregation": ({"structuring_aggregation": 0},
                                "no sub-threshold aggregation existed on the payee"),
    "amount_z": ({"amount_z": 1.0}, "the amount were within the payer's baseline"),
    "drain": ({"drain": 0.1, "amount_z": 1.0}, "the payment were a normal share of the balance"),
    "sim_change_72h": ({"sim_change_hrs_ago": 400, "new_device": False},
                       "there were no recent SIM change"),
    "unlinked_sources": ({"unlinked_sources": 0.0, "distinct_sources_24h": 3},
                         "the payee's inbound sources were mutually linked"),
}

FRICTION_OUTCOMES = {"COOLING_ROOM", "HOLD_CREDIT", "PAUSE", "BLOCK", "STEP_UP"}


class Orchestrator:
    def __init__(self, svcs) -> None:
        self.svcs = svcs
        self.agents = [TransactionAgent(), BehaviorAgent(), VelocityAgent(), MuleAgent(), AMLAgent()]
        self.counsel = CounselAgent()

    # ---------- context ----------
    def build_ctx(self, txn: Txn, disable_graph: bool = False, overrides: dict | None = None) -> Ctx:
        store = self.svcs.store
        payer = store.accounts[txn.payer]
        payee = store.accounts[txn.payee]
        return Ctx(
            txn=txn, payer=payer, payee=payee, store=store, graph=self.svcs.graph,
            policy=self.svcs.policy.policy, now_ts=txn.ts,
            disable_graph=disable_graph, overrides=overrides or {},
            stepup_failed=txn.stepup_failed,
        )

    # ---------- agent fan-out ----------
    async def run_agents(self, ctx: Ctx) -> list[AgentResult]:
        timeouts = ctx.policy.get("agent_timeouts_ms", {})
        async def one(a):
            t_ms = timeouts.get(a.name, a.timeout_ms) / 1000.0
            try:
                return await asyncio.wait_for(asyncio.to_thread(a.run, ctx), timeout=t_ms * 2 + 0.05)
            except asyncio.TimeoutError:
                return AgentResult(agent=a.name, score=-1, status="timeout",
                                   signals=[{"feature": "timeout", "value": f">{t_ms:.2f}s"}])
        return list(await asyncio.gather(*[one(a) for a in self.agents]))

    # ---------- hard rules ----------
    def hard_rules(self, ctx: Ctx) -> tuple[str | None, str | None]:
        p, payee = ctx.payer, ctx.payee
        if p.sanctioned or payee.sanctioned:
            return "sanctions_list", "BLOCK"
        if payee.fri_level == "VERY_HIGH":
            return "fri_very_high_payee", "BLOCK"
        ks = self.svcs.store.killswitch.get(p.id)
        if ks and ks.get("active"):
            return "customer_killswitch", "BLOCK"
        if payee.id in self.svcs.chase.ncrp_accounts:
            return "ncrp_reported_beneficiary", "HOLD_CREDIT"
        return None, None

    # ---------- fusion ----------
    def fuse(self, results: list[AgentResult], ctx: Ctx, hard_rule: str | None) -> tuple[float, AgentResult, float, bool]:
        weights = ctx.policy["fusion"]["weights"]
        ok = [r for r in results if r.status == "ok" and r.agent in weights]
        wsum = sum(weights[r.agent] for r in ok)
        score = sum(weights[r.agent] * r.score for r in ok) / wsum if wsum else 0.0
        # peak-aware boost: one confident specialist must not be averaged away by quiet ones
        peak = max((r.score for r in ok), default=0.0)
        if peak >= 50:
            score += (peak - score) / 2
        counsel_res = self.counsel.run(ctx)
        discount = self.counsel.discount(counsel_res.score, bool(hard_rule))
        final = max(0.0, score - discount)
        any_unknown = any(r.status != "ok" for r in results)
        return final, counsel_res, discount, any_unknown

    # ---------- main entry ----------
    async def score(self, txn: Txn, record: bool = True, with_ablation: bool = False,
                    with_counterfactual: bool = True, run_id: str = "") -> Decision:
        svcs = self.svcs
        t0 = time.perf_counter()
        txn.ts = txn.ts or now()
        if run_id:
            txn.run_id = run_id
        svcs.hub.publish("transaction", {"txn_id": txn.txn_id, "payer": txn.payer,
                                         "payee": txn.payee, "amount": txn.amount,
                                         "source": txn.source, "run_id": txn.run_id})

        ctx = self.build_ctx(txn)
        results = await self.run_agents(ctx)
        hard_rule, hard_outcome = self.hard_rules(ctx)
        fused, counsel_res, discount, any_unknown = self.fuse(results, ctx, hard_rule)
        score = int(round(min(fused, 100)))

        feats = {r.agent: r.features for r in results if r.status == "ok"}
        agent_scores = {r.agent: r.score for r in results if r.status == "ok"}
        agent_scores["counsel"] = counsel_res.score
        typs = typology_mod.classify(ctx, feats, agent_scores)
        dominant = typs[0]["typology"] if typs else None

        if hard_rule:
            outcome, reason, minutes = hard_outcome, f"hard rule: {hard_rule}", None
            score = max(score, 90)
        else:
            if any_unknown:
                score = max(score, svcs.policy.th["pause_low"])
            outcome, reason, minutes = svcs.policy.route(
                score, typs, dominant,
                cooling_override=txn.cooling_override, stepup_failed=txn.stepup_failed,
            )
            if outcome == "HOLD_CREDIT" and minutes is None:
                ratio = txn.amount / max(svcs.store.inflow_outflow(txn.payee, since=txn.ts - 7 * 86400)["inflow"], 1.0)
                minutes = svcs.policy.hold_minutes(dominant, score, ratio)

        decision = Decision(
            txn_id=txn.txn_id, outcome=outcome, score=score, typologies=typs, dominant=dominant,
            agent_results=results + [counsel_res], hard_rule=hard_rule,
            policy_version=svcs.policy.active_version, routing_reason=reason,
            customer_message=explainer.customer_message(outcome, minutes),
            beneficiary_message=explainer.beneficiary_message(),
            total_latency_ms=(time.perf_counter() - t0) * 1000,
            features={k: v for k, v in feats.items()}, ts=txn.ts,
            hold_minutes=minutes,
        )
        decision.receipt_id = f"RC-{txn.txn_id}"

        # ablation (adaptive-attacker demo): same txn with graph features unavailable
        if with_ablation and not hard_rule:
            ctx2 = self.build_ctx(txn, disable_graph=True)
            res2 = [a.run(ctx2) for a in self.agents]
            fused2, _c, _d, _u = self.fuse(res2, ctx2, None)
            score2 = int(round(fused2))
            typs2 = typology_mod.classify(ctx2, {r.agent: r.features for r in res2},
                                          {r.agent: r.score for r in res2})
            dom2 = typs2[0]["typology"] if typs2 else None
            out2, _r2, _m2 = svcs.policy.route(score2, typs2, dom2)
            decision.ablation = {"score": score2, "outcome": out2,
                                 "note": "same payment re-scored with graph features unavailable"}

        # counterfactual: flip the single most-contributing signal to a benign value
        if with_counterfactual and outcome in FRICTION_OUTCOMES and not hard_rule:
            top_agent = max((r for r in results if r.status == "ok" and r.signals),
                            key=lambda r: r.signals[0]["contribution"], default=None)
            if top_agent:
                feat_name = top_agent.signals[0]["feature"]
                if feat_name in COUNTERFACTUAL_MAP:
                    overrides, text = COUNTERFACTUAL_MAP[feat_name]
                    cf_txn = Txn(**{**txn.__dict__})
                    cf_ctx = self.build_ctx(cf_txn, overrides=overrides)
                    res3 = [a.run(cf_ctx) for a in self.agents]
                    fused3, c3, _d, _u = self.fuse(res3, cf_ctx, None)
                    score3 = int(round(fused3))
                    typs3 = typology_mod.classify(cf_ctx, {r.agent: r.features for r in res3},
                                                  {r.agent: r.score for r in res3})
                    dom3 = typs3[0]["typology"] if typs3 else None
                    out3, _r3, _m3 = svcs.policy.route(
                        score3, typs3, dom3, cooling_override=txn.cooling_override,
                        stepup_failed=txn.stepup_failed)
                    decision.counterfactual = {
                        "flip_feature": feat_name, "text": f"Would be {out3} if {text}.",
                        "score_if": score3, "outcome_if": out3,
                    }

        # debate log + graph facts for the analyst side
        graph_facts = self._graph_facts(ctx)
        decision.features["debate_log"] = explainer.debate_log(
            decision.agent_results, score, typs, reason, decision.counterfactual)
        decision.features["analyst_narrative"] = explainer.analyst_narrative(decision, graph_facts)
        decision.features["graph_facts"] = graph_facts
        decision.features["counsel_discount"] = discount

        if record:
            await self._apply(txn, decision)
        decision.total_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        return decision

    # ---------- side effects ----------
    async def _apply(self, txn: Txn, d: Decision) -> None:
        svcs = self.svcs
        settled = d.outcome in ("ALLOW", "ALLOW_NUDGE", "HOLD_CREDIT")
        svcs.store.record_txn(txn, settled=settled)

        summary = {
            "txn_id": txn.txn_id, "score": d.score, "amount": txn.amount,
            "typologies": d.typologies, "dominant": d.dominant, "outcome": d.outcome,
            "cooling_override": txn.cooling_override, "stepup_failed": txn.stepup_failed,
            "hard_rule": d.hard_rule, "label": txn.label, "ts": txn.ts,
        }
        svcs.store.decisions.append(summary)

        ledger_record = {
            "ts": d.ts, "txn_id": txn.txn_id, "receipt_id": d.receipt_id,
            "decision": d.outcome, "score": d.score, "amount": txn.amount,
            "typology": [t["typology"] for t in d.typologies[:2]],
            "policy_version": d.policy_version,
            "model_versions": {"agents": "rules-v1.2", "fusion": "linear-v1.2",
                               "typology": "rules-v1.2"},
            "features_hash": self._features_hash(d),
            "channel": txn.channel, "source": txn.source,
        }
        record_row = svcs.ledger.append(ledger_record)
        svcs.hub.publish("ledger_written", {"seq": record_row["seq"], "txn_id": txn.txn_id,
                                            "hash": record_row["hash"][:16] + "…"})

        if settled:
            payer, payee = svcs.store.accounts[txn.payer], svcs.store.accounts[txn.payee]
            payer.balance = max(0.0, payer.balance - txn.amount)
            payee.balance += txn.amount
            payer.known_payees.setdefault(txn.payee, []).append(txn.ts)
            if txn.device_fp:
                payer.known_devices.add(txn.device_fp)

        if d.outcome == "HOLD_CREDIT" and d.hold_minutes:
            hold = svcs.holds.create(txn, d, d.hold_minutes)
            svcs.hub.publish("hold_created", hold.to_dict())

        if d.outcome in ("PAUSE",):
            case = svcs.cases.create(txn, d, status="paused")
            svcs.hub.publish("case_created", case.to_dict())
        elif d.outcome == "COOLING_ROOM":
            case = svcs.cases.create(txn, d, status="cooling")
            svcs.store.pending[txn.txn_id] = {"txn": txn, "decision": d.to_dict(),
                                              "kind": "cooling", "case_id": case.case_id}
            svcs.hub.publish("case_created", case.to_dict())
        elif d.outcome == "STEP_UP" and not txn.stepup_failed:
            svcs.store.pending[txn.txn_id] = {"txn": txn, "decision": d.to_dict(), "kind": "stepup"}

        svcs.metrics.record_decision(d, txn)
        svcs.hub.publish("decision", {**d.to_dict(with_agents=False),
                                      "agents": [{"agent": a.agent, "score": a.score,
                                                  "latency_ms": a.latency_ms} for a in d.agent_results]})

    def _graph_facts(self, ctx: Ctx) -> list[str]:
        payee = ctx.payee
        facts = []
        fi = ctx.graph.fan_in(payee.id, 86400)
        if fi["distinct_sources"] >= 4:
            facts.append(f"{fi['distinct_sources']} distinct inbound sources in 24h")
        shared = ctx.graph.shared_device_count(payee.id)
        if shared >= 3:
            facts.append(f"payee's device shared with {shared} other accounts")
        dist = ctx.graph.distance_to_flagged(payee.id, max_hops=3)
        if dist:
            facts.append(f"{dist} hop(s) from a flagged account")
        if payee.id in ctx.graph.cycle_members:
            facts.append("participates in a value cycle")
        risk = ctx.graph.network_risk.get(payee.id)
        if risk and risk >= 60:
            facts.append(f"network risk {risk:.0f}/100")
        return facts

    @staticmethod
    def _features_hash(d: Decision) -> str:
        import hashlib
        import json
        blob = json.dumps({k: v for k, v in d.features.items() if k != "debate_log"},
                          sort_keys=True, separators=(",", ":"), default=str)
        return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:32]
