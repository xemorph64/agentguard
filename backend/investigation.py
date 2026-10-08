"""Investigation builder — the authoritative graph, focus, evidence and explanation.

Everything here is *derived* from the engine's recorded decisions and the in-memory graph.
The frontend never infers relationships, the focus account or the key transaction itself.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Iterable, Optional

from app.core.types import OUTCOME_META, TYPOLOGIES

SEVERITY = {"ALLOW": 0, "ALLOW_NUDGE": 1, "STEP_UP": 2, "COOLING_ROOM": 3, "HOLD_CREDIT": 3, "PAUSE": 3, "BLOCK": 4}
# what happened to the money: drives edge styling in the UI
STATE = {"ALLOW": "settled", "ALLOW_NUDGE": "settled", "HOLD_CREDIT": "held", "STEP_UP": "paused",
         "COOLING_ROOM": "paused", "PAUSE": "paused", "BLOCK": "blocked"}
SETTLED_OUTCOMES = {"ALLOW", "ALLOW_NUDGE", "HOLD_CREDIT"}
FRICTION = {"STEP_UP", "COOLING_ROOM", "HOLD_CREDIT", "PAUSE", "BLOCK"}
VICTIM_TYPOLOGIES = {"T1", "T2", "T3", "T4", "T5", "T6", "T7"}

FEATURE_LABELS = {
    # transaction
    "amount_z": "Amount far above the payer's normal", "drain": "Drains a large share of the balance",
    "payee_novel": "First payment to this beneficiary", "payee_vpa_age_days": "Beneficiary account is brand new",
    "just_below_threshold": "Amount sits just under a reporting threshold", "round_number": "Round-number amount",
    "hour_deviation": "Outside the payer's usual hours",
    # behaviour
    "screen_share": "Screen sharing active during payment", "new_device": "Unrecognised device",
    "sim_change_72h": "SIM changed in the last 72h", "pin_reset_24h": "UPI PIN reset in the last 24h",
    "on_call": "Payer on a call while paying", "impossible_travel": "Impossible travel since last payment",
    # velocity
    "micro_burst_10m": "Burst of ₹1-style micro payments", "distinct_payees_1h": "Many new payees in the last hour",
    "sum_1h_vs_baseline": "Hourly outflow far above baseline", "sub_threshold_clustering": "Repeated sub-threshold payments",
    "rapid_fire_1m": "Rapid-fire payments within a minute",
    # mule
    "distinct_sources_24h": "Many unrelated senders in 24h", "unlinked_sources": "Senders don't know each other",
    "pass_through_24h": "Rapid pass-through: money forwarded on", "median_hold_min": "Funds held only minutes",
    "account_age_days": "Very young account", "shared_device_accounts": "Device shared with other accounts",
    "dormant_awakening": "Dormant account suddenly active", "phone_fri": "Phone on fraud-risk list",
    # aml
    "network_risk": "Embedded in a risky network", "structuring_aggregation": "Sub-threshold payments aggregating here",
    "distance_to_flagged": "Close to a flagged account", "cycle_participation": "Part of a money cycle",
    "fan_out_2hop": "Money fans out within 2 hops", "ml_laundering_prob": "ML model: laundering-like transaction",
}
AGENT_LABELS = {"transaction": "Transaction", "behavior": "Behavior", "velocity": "Velocity",
                "mule": "Mule", "aml": "AML", "counsel": "Counsel"}
AGENT_ROLE = {
    "transaction": "Is the amount normal for this payer?",
    "behavior": "Is it really the customer, acting freely?",
    "velocity": "Is the pace of payments unusual?",
    "mule": "Is either side behaving like a mule?",
    "aml": "What does the money network around it say?",
    "counsel": "Customer's advocate: reasons it may be legitimate",
}


def pair_id(src: str, dst: str) -> str:
    return f"{src}>{dst}"


def device_edge_id(acc: str, dev: str) -> str:
    return f"{acc}~{dev}"


class Investigator:
    """Read-only view over the workspace (svcs + recorded decisions)."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.g = ws.svcs.graph
        self.store = ws.svcs.store

    # ------------------------------------------------------------------ primitives
    def decision(self, txn_id: str) -> Optional[dict]:
        return self.ws.decisions.get(txn_id)

    def paid_edges(self, accounts: Optional[set] = None, txn_ids: Optional[set] = None) -> list[dict]:
        out = []
        for e in self.g.edges:
            if e["type"] != "PAID":
                continue
            if txn_ids is not None and e["props"].get("txn_id") not in txn_ids:
                continue
            if accounts is not None and not (e["src"] in accounts and e["dst"] in accounts):
                continue
            out.append(e)
        return out

    def devices_of(self, acc: str) -> list[dict]:
        return [self.g.edges[i] for i in self.g._out.get(acc, ()) if self.g.edges[i]["type"] == "USED_DEVICE"]

    def risk_side_account(self, d: dict) -> str:
        r = d["request"]
        return r["payer"] if d.get("features", {}).get("risk_side") == "payer" else r["payee"]

    def account_risk(self) -> dict:
        """Per-account risk: network risk, or the score of decisions that implicate it."""
        risk = {a: self.g.network_risk.get(a, 0.0) for a in self.store.accounts}
        for d in self.ws.decisions.values():
            if not d.get("dominant") and d["outcome"] == "ALLOW":
                continue
            acc = self.risk_side_account(d)
            risk[acc] = max(risk.get(acc, 0.0), float(d["score"]))
        return risk

    # ------------------------------------------------------------------ graph
    def graph(self, accounts: Optional[Iterable[str]] = None, txn_ids: Optional[Iterable[str]] = None) -> dict:
        acc_scope = set(accounts) if accounts is not None else None
        txn_scope = set(txn_ids) if txn_ids is not None else None
        edges = self.paid_edges(acc_scope, txn_scope)
        risk = self.account_risk()

        pairs: dict[str, dict] = {}
        involved: set[str] = set()
        for e in edges:
            p = e["props"]
            pid = pair_id(e["src"], e["dst"])
            agg = pairs.get(pid)
            if agg is None:
                agg = pairs[pid] = {"id": pid, "type": "payment", "source": e["src"], "target": e["dst"],
                                    "transactions": []}
            outcome = p.get("outcome", "ALLOW")
            agg["transactions"].append({
                "id": p.get("txn_id"), "amount": round(float(p.get("amount", 0)), 2), "ts": p.get("ts"),
                "outcome": outcome, "state": STATE.get(outcome, "settled"), "score": p.get("score", 0),
                "typology": p.get("dominant"),
            })
            involved.update((e["src"], e["dst"]))
        edge_list = []
        for agg in pairs.values():
            txs = sorted(agg["transactions"], key=lambda t: t["ts"] or 0)
            worst = max(txs, key=lambda t: (SEVERITY.get(t["outcome"], 0), t["score"]))
            agg.update({
                "transactions": txs,
                "transaction_ids": [t["id"] for t in txs],
                "amount": round(sum(t["amount"] for t in txs), 2),
                "settled_amount": round(sum(t["amount"] for t in txs if t["state"] in ("settled", "held")), 2),
                "count": len(txs),
                "timestamp": txs[-1]["ts"], "first_ts": txs[0]["ts"],
                "outcome": worst["outcome"], "state": worst["state"],
                "risk": max(t["score"] for t in txs),
                "typology": worst["typology"],
            })
            edge_list.append(agg)

        # devices: every device used by an in-scope account; evidence = shared or used in a challenged payment
        friction_devices = set()
        for d in self.ws.decisions.values():
            dev = d["request"].get("device_fp")
            if dev and d["outcome"] in FRICTION and d.get("features", {}).get("behavior", {}).get("new_device"):
                friction_devices.add(dev)
        dev_nodes: dict[str, dict] = {}
        for acc in sorted(involved):
            for e in self.devices_of(acc):
                dev = e["dst"]
                users = self.g.accounts_on_device(dev)
                edge_list.append({"id": device_edge_id(acc, dev), "type": "device", "source": acc, "target": dev,
                                  "count": e["props"].get("count", 1), "first_ts": e["props"].get("first_ts"),
                                  "timestamp": e["props"].get("last_ts"),
                                  "registered": bool(e["props"].get("registered"))})
                if dev not in dev_nodes:
                    shared = len(users) >= 2
                    dev_nodes[dev] = {
                        "id": dev, "type": "device", "label": self._device_label(dev, users),
                        "risk": max((risk.get(u, 0.0) for u in users), default=0.0) if shared else 0.0,
                        "status": "shared" if shared else ("new" if dev in friction_devices else "normal"),
                        "metadata": {"accounts": users, "account_count": len(users),
                                     "evidence": shared or dev in friction_devices},
                    }

        flows = self._flows(edges)
        victims = self._victims(edges, risk)
        nodes = [self.account_node(a, risk, flows, victims) for a in sorted(involved) if a in self.store.accounts]
        nodes += list(dev_nodes.values())
        return {"nodes": nodes, "edges": edge_list}

    @staticmethod
    def _device_label(dev: str, users: list) -> str:
        return f"Phone · {len(users)} accounts" if len(users) >= 2 else "Phone"

    def _flows(self, edges: list[dict]) -> dict:
        f = defaultdict(lambda: {"in": 0.0, "out": 0.0, "in_count": 0, "out_count": 0,
                                 "attempted_out": 0.0, "attempted_in": 0.0, "sources": set(), "sinks": set()})
        for e in edges:
            p = e["props"]
            amt = float(p.get("amount", 0))
            settled = p.get("settled", True)
            f[e["src"]]["attempted_out"] += amt
            f[e["dst"]]["attempted_in"] += amt
            if settled:
                f[e["src"]]["out"] += amt
                f[e["src"]]["out_count"] += 1
                f[e["src"]]["sinks"].add(e["dst"])
                f[e["dst"]]["in"] += amt
                f[e["dst"]]["in_count"] += 1
                f[e["dst"]]["sources"].add(e["src"])
        return f

    def _victims(self, edges: list[dict], risk: dict) -> set:
        """Payers who sent money to an implicated account, or were the victim side of a fraud decision."""
        victims = set()
        for d in self.ws.decisions.values():
            if d.get("dominant") in VICTIM_TYPOLOGIES and d["outcome"] in FRICTION:
                victims.add(d["request"]["payer"])
        for e in edges:
            if risk.get(e["dst"], 0) >= 60 and risk.get(e["src"], 0) < 30 and e["dst"] != e["src"]:
                victims.add(e["src"])
        return victims

    def account_node(self, aid: str, risk: dict, flows: dict, victims: set) -> dict:
        acc = self.store.accounts[aid]
        r = round(risk.get(aid, 0.0), 1)
        fl = flows.get(aid) or {"in": 0, "out": 0, "in_count": 0, "out_count": 0, "attempted_out": 0,
                                "attempted_in": 0, "sources": set(), "sinks": set()}
        if aid in self.g.flagged:
            status = "flagged"
        elif r >= 60:
            status = "suspicious"
        elif aid in victims:
            status = "victim"
        elif r >= 30:
            status = "watch"
        else:
            status = "normal"
        return {
            "id": aid, "type": "account", "label": acc.name, "risk": r, "status": status,
            "metadata": {
                "persona": acc.persona, "age_days": acc.age_days, "balance": round(acc.balance, 2),
                "in_amount": round(fl["in"], 2), "out_amount": round(fl["out"], 2),
                "in_count": fl["in_count"], "out_count": fl["out_count"],
                "attempted_out": round(fl["attempted_out"], 2), "attempted_in": round(fl["attempted_in"], 2),
                "sources": len(fl["sources"]), "sinks": len(fl["sinks"]),
                "network_risk": self.g.network_risk.get(aid, 0.0), "flagged": aid in self.g.flagged,
                "verified_merchant": acc.verified_merchant,
            },
        }

    # ------------------------------------------------------------------ investigation
    def investigate(self, txn_ids: list[str], events: list[dict] | None = None, roles: dict | None = None,
                    accounts: Optional[Iterable[str]] = None) -> dict:
        decisions = [self.ws.decisions[t] for t in txn_ids if t in self.ws.decisions]
        acc_scope = set(accounts) if accounts is not None else None
        graph = self.graph(accounts=acc_scope, txn_ids=None if acc_scope is not None else set(txn_ids))
        node_by_id = {n["id"]: n for n in graph["nodes"]}

        # the key transaction is the moment of *detection*: at the highest severity, prefer an
        # engine decision over later hard-rule enforcement against an already-flagged account
        key = max(decisions, key=lambda d: (SEVERITY.get(d["outcome"], 0), not d.get("hard_rule"), d["score"], -d["ts"]),
                  default=None)
        focus = None
        if key and (SEVERITY[key["outcome"]] >= 2 or key.get("dominant")):
            focus = self.risk_side_account(key)
        else:
            risky = [n for n in graph["nodes"] if n["type"] == "account" and n["risk"] >= 30]
            if risky:
                focus = max(risky, key=lambda n: n["risk"])["id"]

        metrics = self.metrics(decisions, focus, graph)
        explanation = self.explain(key, focus, metrics, node_by_id) if key else None
        secondary = self._secondary(focus, graph, key)
        key_accounts = [a for a in [focus, *secondary] if a]
        return {
            "graph": graph,
            "focus": focus,
            "key_transaction": self.txn_detail(key) if key else None,
            "key_accounts": key_accounts,
            "investigation": {
                "focus": focus, "secondary": secondary,
                "key_transaction": key["txn_id"] if key else None,
                "explanation": explanation,
            },
            "explanation": explanation,
            "metrics": metrics,
            "timeline": self.timeline(decisions, events or []),
            "ground_truth": roles or {},
        }

    def _secondary(self, focus: Optional[str], graph: dict, key: Optional[dict]) -> list[str]:
        if not focus:
            return []
        weight = defaultdict(float)
        for e in graph["edges"]:
            if e["type"] == "payment" and focus in (e["source"], e["target"]):
                other = e["target"] if e["source"] == focus else e["source"]
                weight[other] += e["amount"]
            if e["type"] == "device" and e["source"] == focus:
                dev = next((n for n in graph["nodes"] if n["id"] == e["target"]), None)
                if dev and dev["metadata"]["evidence"]:
                    weight[e["target"]] += 1e12   # evidence devices first
        if key:
            for a in (key["request"]["payer"], key["request"]["payee"]):
                if a != focus:
                    weight[a] += 1e11
        return [k for k, _ in sorted(weight.items(), key=lambda kv: -kv[1])][:6]

    def metrics(self, decisions: list[dict], focus: Optional[str], graph: dict) -> dict:
        total = sum(float(d["request"]["amount"]) for d in decisions)
        settled = sum(float(d["request"]["amount"]) for d in decisions if d["outcome"] in SETTLED_OUTCOMES)
        m = {
            "transaction_count": len(decisions),
            "suspicious_count": sum(1 for d in decisions if d["outcome"] in FRICTION),
            "total_flow": round(total, 2), "settled_flow": round(settled, 2),
            "blocked_amount": round(sum(float(d["request"]["amount"]) for d in decisions if d["outcome"] == "BLOCK"), 2),
            "held_amount": round(sum(float(d["request"]["amount"]) for d in decisions if d["outcome"] == "HOLD_CREDIT"), 2),
            "paused_amount": round(sum(float(d["request"]["amount"]) for d in decisions
                                       if d["outcome"] in ("PAUSE", "COOLING_ROOM", "STEP_UP")), 2),
            "max_score": max((d["score"] for d in decisions), default=0),
            "by_outcome": dict(sorted(_count(d["outcome"] for d in decisions).items())),
        }
        if focus:
            ins = [d for d in decisions if d["request"]["payee"] == focus]
            outs = [d for d in decisions if d["request"]["payer"] == focus]
            ins_settled = [d for d in ins if d["outcome"] in SETTLED_OUTCOMES]
            outs_settled = [d for d in outs if d["outcome"] in SETTLED_OUTCOMES]
            inbound = sum(float(d["request"]["amount"]) for d in ins_settled)
            outbound_attempted = sum(float(d["request"]["amount"]) for d in outs)
            m.update({
                "inbound": round(inbound, 2),
                "inbound_attempted": round(sum(float(d["request"]["amount"]) for d in ins), 2),
                "outbound": round(sum(float(d["request"]["amount"]) for d in outs_settled), 2),
                "outbound_attempted": round(outbound_attempted, 2),
                "inbound_sources": len({d["request"]["payer"] for d in ins}),
                "outbound_sinks": len({d["request"]["payee"] for d in outs}),
                "pass_through": round(outbound_attempted / inbound, 3) if inbound else None,
                "holding_minutes": _holding_minutes(ins_settled, outs),
                "network_risk": self.g.network_risk.get(focus, 0.0),
                "focus_risk": next((n["risk"] for n in graph["nodes"] if n["id"] == focus), 0.0),
                "shared_device_accounts": self.g.shared_device_count(focus),
            })
        return m

    # ------------------------------------------------------------------ explanation
    def explain(self, key: dict, focus: Optional[str], m: dict, nodes: dict) -> dict:
        dom = key.get("dominant")
        outcome = key["outcome"]
        name = nodes.get(focus, {}).get("label", focus) if focus else None
        typ = next((t for t in key.get("typologies", []) if t["typology"] == dom), None)
        evidence = list(typ["evidence"]) if typ else []
        evidence += key.get("features", {}).get("graph_facts", [])
        amt = lambda x: f"₹{x:,.0f}"  # noqa: E731
        decision = f"{OUTCOME_META.get(outcome, {}).get('label', outcome)} — {key.get('routing_reason', '')}"

        if dom in ("T8", "T9", "T12", "T13") and focus:
            fast = (m.get("pass_through") or 0) >= 0.8 and (m.get("holding_minutes") or 999) < 30
            if dom == "T12":
                alert = "SHARED-DEVICE MULE FARM"
                summary = (f"{m.get('shared_device_accounts', 0) + 1} accounts operate from one phone and pay into "
                           f"{name}: {amt(m.get('inbound', 0))} from {m.get('inbound_sources', 0)} senders.")
            else:
                alert = "RAPID PASS-THROUGH DETECTED" if fast else "MULE COLLECTOR DETECTED"
                hold = m.get("holding_minutes")
                summary = (f"{name} received {amt(m.get('inbound', 0))} from {m.get('inbound_sources', 0)} unrelated "
                           f"senders and tried to forward {amt(m.get('outbound_attempted', 0))}"
                           + (f" within {hold:.0f} min of receiving it." if hold is not None else "."))
        elif dom == "T11":
            alert = "STRUCTURING PATTERN"
            near = [d for d in self.ws.decisions.values() if d["request"]["payee"] == focus
                    and 45000 <= d["request"]["amount"] < 50000]
            summary = (f"{len(near)} payments between ₹45,000 and ₹50,000 converge on {name} — "
                       f"{amt(sum(d['request']['amount'] for d in near))} kept under the reporting line.")
        elif dom == "T7":
            alert = "PROBE-THEN-DRAIN PATTERN"
            summary = (f"Tiny test payments checked the route, then {amt(key['request']['amount'])} was sent to "
                       f"{name}, which shares a phone with the probed accounts.")
        elif dom == "T3":
            alert = "ACCOUNT TAKEOVER"
            summary = (f"{amt(key['request']['amount'])} to {name} from an unrecognised phone right after a SIM "
                       f"swap and PIN reset.")
        elif dom in ("T1", "T4"):
            alert = "COERCED PAYMENT (DIGITAL ARREST)" if dom == "T1" else "REMOTE-ACCESS SCAM"
            summary = (f"{amt(key['request']['amount'])} to {name} while the payer was on a "
                       f"{key['request'].get('call_minutes', 0):.0f}-minute call with their screen shared.")
        elif SEVERITY.get(outcome, 0) >= 2:
            alert = "HIGH-RISK PAYMENT"
            summary = key.get("features", {}).get("case_summary", {}).get("headline", "")
        else:
            alert = None
            summary = (f"All {m['transaction_count']} payments matched the customers' normal behaviour — "
                       f"no account in this network shows mule, takeover or coercion signals.")
        return {"alert": alert, "headline": alert or "NO SUSPICIOUS ACTIVITY", "summary": summary,
                "evidence": evidence[:6], "decision": decision, "typology": dom,
                "typology_name": TYPOLOGIES.get(dom) if dom else None}

    # ------------------------------------------------------------------ transaction detail + evidence
    def txn_detail(self, d: dict) -> dict:
        r = d["request"]
        agents = []
        for a in d.get("agents", []):
            agents.append({
                "agent": a["agent"], "label": AGENT_LABELS.get(a["agent"], a["agent"]),
                "question": AGENT_ROLE.get(a["agent"], ""),
                "score": a["score"], "confidence": a["confidence"], "status": a["status"],
                "latency_ms": a["latency_ms"],
                "signals": [{**s, "label": FEATURE_LABELS.get(s["feature"], s["feature"].replace("_", " "))}
                            for s in a.get("signals", [])],
            })
        weights = self.ws.svcs.policy.policy["fusion"]["weights"]
        drivers = []
        for a in agents:
            if a["agent"] == "counsel" or a["status"] != "ok":
                continue
            for s in a["signals"]:
                if s.get("contribution", 0) < 4:      # below this a signal explains nothing
                    continue
                # points of the agent's score × the agent's fusion weight ≈ points of fused risk
                drivers.append({"agent": a["agent"], "feature": s["feature"], "label": s["label"],
                                "value": s.get("value"),
                                "contribution": round(s["contribution"] * weights.get(a["agent"], 0.2) * 5, 1)})
        drivers.sort(key=lambda s: -s["contribution"])
        return {
            "txn_id": d["txn_id"], "ts": d["ts"], "payer": r["payer"], "payee": r["payee"],
            "payer_label": self._label(r["payer"]), "payee_label": self._label(r["payee"]),
            "amount": r["amount"], "channel": r.get("channel"), "device_fp": r.get("device_fp"),
            "geo": r.get("geo"), "outcome": d["outcome"], "state": STATE.get(d["outcome"]),
            "outcome_label": OUTCOME_META.get(d["outcome"], {}).get("label", d["outcome"]),
            "score": d["score"], "dominant": d.get("dominant"),
            "typology_name": TYPOLOGIES.get(d.get("dominant")) if d.get("dominant") else None,
            "typologies": d.get("typologies", []), "routing_reason": d.get("routing_reason"),
            "hard_rule": d.get("hard_rule"), "hold_minutes": d.get("hold_minutes"),
            "counterfactual": d.get("counterfactual"),
            "counsel_discount": d.get("features", {}).get("counsel_discount", 0),
            "risk_side": d.get("features", {}).get("risk_side", "payee"),
            "risk_account": self.risk_side_account(d),
            "customer_message": d.get("customer_message"), "receipt_id": d.get("receipt_id"),
            "latency_ms": d.get("total_latency_ms"), "caption": d.get("caption", ""),
            "scenario": r.get("scenario"), "run_id": d.get("run_id"),
            "agents": agents, "drivers": drivers[:5],
            "evidence": self.evidence(d),
            "ledger": self.ws.svcs.ledger.find(txn_id=d["txn_id"]),
        }

    def _label(self, aid: str) -> str:
        acc = self.store.accounts.get(aid)
        return acc.name if acc else aid

    def evidence(self, d: dict) -> dict:
        """Signal → graph evidence: which nodes/edges each contributing signal is about."""
        r = d["request"]
        payer, payee, ts = r["payer"], r["payee"], d["ts"]
        side = self.risk_side_account(d)
        key_edge = pair_id(payer, payee)

        def in_edges(acc, pred=lambda p: True):
            return sorted({pair_id(self.g.edges[i]["src"], acc) for i in self.g._in.get(acc, ())
                           if self.g.edges[i]["type"] == "PAID" and pred(self.g.edges[i]["props"])})

        def out_edges(acc, pred=lambda p: True):
            return sorted({pair_id(acc, self.g.edges[i]["dst"]) for i in self.g._out.get(acc, ())
                           if self.g.edges[i]["type"] == "PAID" and pred(self.g.edges[i]["props"])})

        def device_links(acc):
            out_nodes, out_edges_ = set(), set()
            for e in self.devices_of(acc):
                users = self.g.accounts_on_device(e["dst"])
                if len(users) >= 2:
                    out_nodes.add(e["dst"])
                    out_nodes.update(users)
                    out_edges_.update(device_edge_id(u, e["dst"]) for u in users)
            return sorted(out_nodes), sorted(out_edges_)

        recent = lambda w: (lambda p: ts - w <= p.get("ts", 0) <= ts)  # noqa: E731
        ev: dict[str, dict] = {}
        for a in d.get("agents", []):
            for s in a.get("signals", []):
                f = s["feature"]
                nodes, edges = [], []
                if a["agent"] == "transaction":
                    nodes, edges = [payee] if f == "payee_vpa_age_days" else [payer, payee], [key_edge]
                elif a["agent"] == "behavior":
                    dev = r.get("device_fp")
                    nodes = [payer] + ([dev] if dev else [])
                    edges = [key_edge] + ([device_edge_id(payer, dev)] if dev and f == "new_device" else [])
                elif a["agent"] == "velocity":
                    nodes, edges = [payer], out_edges(payer, recent(86400 if "threshold" in f else 3600)) + [key_edge]
                elif a["agent"] == "mule":
                    if f == "shared_device_accounts":
                        nodes, edges = device_links(side)
                    elif f in ("pass_through_24h", "median_hold_min"):
                        nodes, edges = [side], in_edges(side) + out_edges(side)
                    else:
                        nodes, edges = [side], in_edges(side)
                elif a["agent"] == "aml":
                    if f == "ml_laundering_prob":
                        nodes, edges = [payer, payee], [key_edge]
                    elif f == "structuring_aggregation":
                        nodes = [payee]
                        edges = in_edges(payee, lambda p: 9000 < p.get("amount", 0) < 10000
                                         or 45000 < p.get("amount", 0) < 50000) or [key_edge]
                    elif f == "fan_out_2hop":
                        nodes, edges = [payee], out_edges(payee)
                    else:
                        nodes, edges = [payee], in_edges(payee) + out_edges(payee)
                if not nodes and not edges:
                    continue
                ev[f] = {"agent": a["agent"], "label": FEATURE_LABELS.get(f, f), "nodes": sorted(set(nodes)),
                         "edges": sorted(set(edges))}
        return ev

    # ------------------------------------------------------------------ timeline
    def timeline(self, decisions: list[dict], events: list[dict]) -> list[dict]:
        items = []
        for d in decisions:
            r = d["request"]
            mule = next((a for a in d.get("agents", []) if a["agent"] == "mule"), None)
            items.append({
                "kind": "txn", "ts": d["ts"], "txn_id": d["txn_id"], "payer": r["payer"], "payee": r["payee"],
                "amount": r["amount"], "outcome": d["outcome"], "state": STATE.get(d["outcome"]),
                "score": d["score"], "typology": d.get("dominant"), "caption": d.get("caption", ""),
                "hard_rule": d.get("hard_rule"), "routing_reason": d.get("routing_reason"),
                "risk_account": self.risk_side_account(d),
                "agents": {a["agent"]: a["score"] for a in d.get("agents", [])},
                "mule_side": (mule or {}).get("features", {}).get("side"),
            })
        for e in events:
            items.append({"kind": "event", **e})
        items.sort(key=lambda x: (x["ts"], 0 if x["kind"] == "event" else 1))
        for i, it in enumerate(items):
            it["seq"] = i
        return items

    # ------------------------------------------------------------------ account panel
    def account(self, aid: str) -> Optional[dict]:
        if aid not in self.store.accounts:
            return None
        acc = self.store.accounts[aid]
        risk = self.account_risk()
        edges = [e for e in self.g.edges if e["type"] == "PAID" and aid in (e["src"], e["dst"])]
        flows = self._flows(edges)
        node = self.account_node(aid, risk, flows, self._victims(edges, risk))
        counter = defaultdict(lambda: {"in": 0.0, "out": 0.0, "in_count": 0, "out_count": 0})
        for e in edges:
            p = e["props"]
            if e["src"] == aid:
                c = counter[e["dst"]]; c["out"] += p.get("amount", 0); c["out_count"] += 1
            else:
                c = counter[e["src"]]; c["in"] += p.get("amount", 0); c["in_count"] += 1
        connected = [{"id": k, "label": self._label(k), "risk": round(risk.get(k, 0.0), 1),
                      "in": round(v["in"], 2), "out": round(v["out"], 2),
                      "count": v["in_count"] + v["out_count"]}
                     for k, v in sorted(counter.items(), key=lambda kv: -(kv[1]["in"] + kv[1]["out"]))]
        devices = []
        for e in self.devices_of(aid):
            users = self.g.accounts_on_device(e["dst"])
            devices.append({"id": e["dst"], "accounts": [u for u in users if u != aid],
                            "registered": bool(e["props"].get("registered")), "uses": e["props"].get("count", 1)})
        txns = sorted((d for d in self.ws.decisions.values() if aid in (d["request"]["payer"], d["request"]["payee"])),
                      key=lambda d: d["ts"])
        signals = defaultdict(float)
        for d in txns:
            if self.risk_side_account(d) != aid or d["outcome"] == "ALLOW":
                continue
            for s in self.txn_detail(d)["drivers"]:
                signals[(s["feature"], s["label"])] = max(signals[(s["feature"], s["label"])], s["contribution"])
        io = self.store.inflow_outflow(aid)
        hold = _holding_minutes([d for d in txns if d["request"]["payee"] == aid and d["outcome"] in SETTLED_OUTCOMES],
                                [d for d in txns if d["request"]["payer"] == aid])
        return {
            **node,
            "persona": acc.persona, "age_days": acc.age_days,
            "money_in": round(io["inflow"], 2), "money_out": round(io["outflow"], 2),
            "net_flow": round(io["inflow"] - io["outflow"], 2),
            "attempted_out": node["metadata"]["attempted_out"],
            "transaction_count": len(txns), "holding_minutes": round(hold, 1) if hold is not None else None,
            "connected": connected[:25], "devices": devices,
            "shared_device_accounts": self.g.shared_device_count(aid),
            "risk_signals": [{"feature": f, "label": l, "contribution": round(c, 1)}
                             for (f, l), c in sorted(signals.items(), key=lambda kv: -kv[1])[:6]],
            "transactions": [{"txn_id": d["txn_id"], "ts": d["ts"], "payer": d["request"]["payer"],
                              "payee": d["request"]["payee"], "amount": d["request"]["amount"],
                              "outcome": d["outcome"], "score": d["score"], "direction":
                                  "out" if d["request"]["payer"] == aid else "in"} for d in txns[-30:]],
        }

    def neighbourhood(self, aid: str, hops: int) -> set[str]:
        """Accounts within N hops over payments (any outcome) and shared devices. hops<=0 → whole component."""
        seen, frontier = {aid}, {aid}
        depth = 0
        while frontier and (hops <= 0 or depth < hops):
            nxt = set()
            for n in frontier:
                for i in [*self.g._out.get(n, ()), *self.g._in.get(n, ())]:
                    e = self.g.edges[i]
                    if e["type"] == "PAID":
                        other = e["dst"] if e["src"] == n else e["src"]
                        if other not in seen:
                            nxt.add(other)
                    elif e["type"] == "USED_DEVICE":
                        for other in self.g.accounts_on_device(e["dst"]):
                            if other not in seen:
                                nxt.add(other)
            seen |= nxt
            frontier = nxt
            depth += 1
        return seen


def _count(items) -> dict:
    out: dict = defaultdict(int)
    for x in items:
        out[x] += 1
    return out


def _holding_minutes(ins: list[dict], outs: list[dict]) -> Optional[float]:
    """Median minutes between an outbound attempt and the most recent settled credit before it."""
    in_ts = sorted(d["ts"] for d in ins)
    holds = []
    for o in outs:
        prior = [t for t in in_ts if t <= o["ts"]]
        if prior:
            holds.append((o["ts"] - prior[-1]) / 60.0)
    return round(median(holds), 1) if holds else None
