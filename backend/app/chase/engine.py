"""Golden Hour Chase — forward trace, recoverability ranking, freeze requests.

Traces money forward from a reported fraud through the graph, estimates how
much is still sitting at each downstream account, and ranks freeze targets by
what can actually still be recovered (PRD §8 C4).
"""
from __future__ import annotations

from collections import defaultdict

from ..core.types import Complaint, Txn, now

GOLDEN_HOUR_MIN = 60
MAX_HOPS = 4
LOOKBACK_SLOP = 120  # seconds: include edges at/after the victim txn


class ChaseEngine:
    def __init__(self, svcs) -> None:
        self.svcs = svcs
        self.runs: dict[str, dict] = {}
        self.ncrp_accounts: set[str] = set()   # beneficiaries reported via 1930/NCRP

    # ---------- complaint intake ----------
    def file_complaint(self, txn_id: str | None, victim_vpa: str | None,
                       amount: float | None, channel: str = "1930-sim") -> Complaint | None:
        store = self.svcs.store
        txn: Txn | None = store.txns.get(txn_id) if txn_id else None
        if txn is None and victim_vpa:
            acc = store.by_vpa(victim_vpa)
            if acc:
                candidates = [t for t in store.txn_history
                              if t.payer == acc.id and (amount is None or abs(t.amount - amount) < amount * 0.5)]
                txn = candidates[-1] if candidates else None
        if txn is None:
            return None
        self.svcs.db.kv_inc("complaint_seq")
        n = self.svcs.db.kv_get("complaint_seq", 0)
        complaint = Complaint(
            complaint_id=f"C-{20000 + n}", txn_id=txn.txn_id, victim=txn.payer,
            amount=txn.amount, reported_at=now(), channel=channel,
            golden_deadline=now() + GOLDEN_HOUR_MIN * 60,
        )
        store.complaints[complaint.complaint_id] = complaint
        # the accused beneficiary + its account become hard-rule eligible
        self.ncrp_accounts.add(txn.payee)
        self.svcs.hub.publish("complaint_received", complaint.to_dict())
        self.run(complaint)
        return complaint

    # ---------- forward trace ----------
    def run(self, complaint: Complaint) -> dict:
        store, graph = self.svcs.store, self.svcs.graph
        txn = store.txns[complaint.txn_id]
        origin = txn.payee
        t0 = txn.ts - LOOKBACK_SLOP

        # BFS over PAID edges moving forward in time from the origin
        levels: dict[str, int] = {origin: 0}
        frontier = [origin]
        inflow: dict[str, float] = defaultdict(float)
        outflow: dict[str, float] = defaultdict(float)
        inflow[origin] = txn.amount
        edges_used: list[dict] = []
        for hop in range(1, MAX_HOPS + 1):
            nxt = []
            for nid in frontier:
                for i in graph._out.get(nid, ()):
                    e = graph.edges[i]
                    if e["type"] != "PAID" or e["props"].get("ts", 0) < t0:
                        continue
                    dst = e["dst"]
                    inflow[dst] += e["props"].get("amount", 0)
                    outflow[nid] += e["props"].get("amount", 0)
                    edges_used.append({"src": nid, "dst": dst, "amount": e["props"].get("amount", 0),
                                       "ts": e["props"].get("ts", 0), "txn_id": e["props"].get("txn_id")})
                    if dst not in levels:
                        levels[dst] = hop
                        nxt.append(dst)
            frontier = nxt
            if not frontier:
                break

        nodes = []
        for aid, hop in levels.items():
            acc = store.accounts.get(aid)
            present = max(0.0, inflow.get(aid, 0.0) - outflow.get(aid, 0.0))
            converted = False
            if acc and acc.persona == "merchant" and (acc.mcc or "").startswith("CRYPTO"):
                present, converted = 0.0, True
            nodes.append({
                "account": aid, "name": acc.name if acc else aid, "vpa": acc.vpa if acc else "",
                "bank": acc.ifsc if acc else "", "hop": hop,
                "inflow": round(inflow.get(aid, 0.0), 0),
                "outflow": round(outflow.get(aid, 0.0), 0),
                "present": round(present, 0),
                "converted": converted,
                "risk": graph.network_risk.get(aid, 0.0),
                "frozen": False,
            })
        nodes.sort(key=lambda n: (-n["present"], n["hop"]))
        first_hop_present = sum(n["present"] for n in nodes if n["hop"] == 1)
        full_present = sum(n["present"] for n in nodes)
        result = {
            "complaint_id": complaint.complaint_id,
            "txn_id": complaint.txn_id,
            "origin": origin,
            "edges": edges_used,
            "nodes": nodes,
            "recoverable_first_hop_only": round(first_hop_present, 0),
            "recoverable_full_trace": round(full_present, 0),
            "recovered": 0.0,
            "golden_deadline": complaint.golden_deadline,
            "freeze_requests": self._freeze_requests(nodes, complaint),
        }
        self.runs[complaint.complaint_id] = result
        self.svcs.hub.publish("chase_started", {"complaint_id": complaint.complaint_id,
                                                "nodes": len(nodes),
                                                "recoverable": result["recoverable_full_trace"]})
        return result

    @staticmethod
    def _freeze_requests(nodes: list[dict], complaint: Complaint) -> list[dict]:
        drafts = []
        for n in nodes:
            if n["present"] <= 0 or n["converted"]:
                continue
            drafts.append({
                "account": n["account"], "name": n["name"], "bank_ifsc": n["bank"],
                "amount_requested": n["present"],
                "text": (f"TO: Nodal Officer, {n['bank']} ({n['bank_ifsc']})\n"
                         f"SUB: Lien/freeze request — NCRP complaint {complaint.complaint_id}\n"
                         f"Account {n['account']} ({n['name']}): credit of ₹{n['present']:,.0f} "
                         f"linked to reported fraud txn {complaint.txn_id}. "
                         f"Request lien on credited funds u/s 106 BNSS pending investigation."),
            })
        return drafts

    def freeze(self, complaint_id: str, account: str) -> dict | None:
        run = self.runs.get(complaint_id)
        complaint = self.svcs.store.complaints.get(complaint_id)
        if not run or not complaint:
            return None
        for n in run["nodes"]:
            if n["account"] == account and not n["frozen"]:
                n["frozen"] = True
                amount = n["present"]
                run["recovered"] += amount
                complaint.recovered += amount
                complaint.status = "frozen"
                self.svcs.metrics.totals["recovered"] += amount
                self.svcs.hub.publish("chase_updated", {"complaint_id": complaint_id,
                                                        "account": account, "frozen_amount": amount})
                return {"account": account, "frozen_amount": amount,
                        "total_recovered": run["recovered"]}
        return None

    def get(self, complaint_id: str) -> dict | None:
        complaint = self.svcs.store.complaints.get(complaint_id)
        if not complaint:
            return None
        run = self.runs.get(complaint_id)
        return {"complaint": complaint.to_dict(), "trace": run}
