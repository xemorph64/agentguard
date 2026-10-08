"""Heterogeneous investigation graph.

Plays the role of Neo4j (+GDS) from the PRD. Hot-path queries are bounded by
hop count; network-wide properties (network_risk, communities, cycles) are
precomputed by a warm job and mirrored, exactly as §7.4 describes.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Optional

from ..core.types import now

NODE_TYPES = ("Account", "VPA", "Device", "Phone", "IPSubnet", "Case")
EDGE_TYPES = ("PAID", "HAS_VPA", "USED_DEVICE", "LINKED_PHONE", "SEEN_FROM", "SUBJECT_OF")

# Account-level types that participate in money flow
MONEY_NODE = "Account"


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}          # id -> {id, type, label, props{}}
        self.edges: list[dict] = []               # {src, dst, type, props{}}
        self._out: dict[str, list[int]] = defaultdict(list)
        self._in: dict[str, list[int]] = defaultdict(list)
        self.edge_by_txn: dict[str, int] = {}
        # precomputed warm properties
        self.network_risk: dict[str, float] = {}
        self.community: dict[str, int] = {}
        self.cycle_members: set[str] = set()
        self.flagged: set[str] = set()            # known-fraud nodes (cases, sanctions, NCRP)

    # ---------- construction ----------
    def add_node(self, node_id: str, ntype: str, label: str = "", **props) -> dict:
        n = self.nodes.get(node_id)
        if n is None:
            n = {"id": node_id, "type": ntype, "label": label or node_id, "props": props}
            self.nodes[node_id] = n
        else:
            n["props"].update(props)
            if label:
                n["label"] = label
        return n

    def add_edge(self, src: str, dst: str, etype: str, **props) -> dict:
        if etype == "PAID" and props.get("txn_id") in self.edge_by_txn:
            return self.edges[self.edge_by_txn[props["txn_id"]]]
        if etype == "USED_DEVICE":
            # one edge per (account, device); repeat use updates it instead of duplicating
            for i in self._out.get(src, ()):
                e = self.edges[i]
                if e["type"] == "USED_DEVICE" and e["dst"] == dst:
                    e["props"]["count"] = e["props"].get("count", 1) + 1
                    e["props"]["last_ts"] = props.get("ts", e["props"].get("last_ts"))
                    return e
            props = {"count": 1, "first_ts": props.get("ts"), "last_ts": props.get("ts"), **props}
        e = {"src": src, "dst": dst, "type": etype, "props": props}
        idx = len(self.edges)
        self.edges.append(e)
        self._out[src].append(idx)
        self._in[dst].append(idx)
        if etype == "PAID" and props.get("txn_id"):
            self.edge_by_txn[props["txn_id"]] = idx
        return e

    def node(self, node_id: str) -> Optional[dict]:
        return self.nodes.get(node_id)

    @staticmethod
    def is_money(e: dict) -> bool:
        """A PAID edge whose money actually moved (blocked/paused/cooling attempts did not)."""
        return e["type"] == "PAID" and e["props"].get("settled", True)

    # ---------- bounded hot-path queries (the only ones the decision path uses) ----------
    def neighbors(self, node_id: str, direction: str = "out", etype: Optional[str] = None) -> list[dict]:
        idxs = self._out.get(node_id, ()) if direction == "out" else self._in.get(node_id, ())
        res = []
        for i in idxs:
            e = self.edges[i]
            if etype and e["type"] != etype:
                continue
            other = e["dst"] if direction == "out" else e["src"]
            res.append({"edge": e, "node": self.nodes.get(other)})
        return res

    def fan_in(self, account: str, window_s: float = 86400, min_ts: float = 0.0) -> dict:
        """Distinct money sources into an account within the window (bounded, hot path)."""
        t0 = now() - window_s
        sources, total, count = set(), 0.0, 0
        for i in self._in.get(account, ()):
            e = self.edges[i]
            if not self.is_money(e):
                continue
            ts = e["props"].get("ts", 0)
            if ts < t0 or ts < min_ts:
                continue
            sources.add(e["src"])
            total += e["props"].get("amount", 0)
            count += 1
        return {"distinct_sources": len(sources), "amount_in": total, "txns": count, "sources": sorted(sources)}

    def fan_out(self, account: str, window_s: float = 86400) -> dict:
        t0 = now() - window_s
        sinks, total = set(), 0.0
        for i in self._out.get(account, ()):
            e = self.edges[i]
            if not self.is_money(e) or e["props"].get("ts", 0) < t0:
                continue
            sinks.add(e["dst"])
            total += e["props"].get("amount", 0)
        return {"distinct_sinks": len(sinks), "amount_out": total, "sinks": sorted(sinks)}

    def accounts_on_device(self, device_id: str) -> list[str]:
        return sorted({
            self.edges[i]["src"] for i in self._in.get(device_id, ())
            if self.edges[i]["type"] == "USED_DEVICE"
        })

    def shared_device_count(self, account: str) -> int:
        """How many *other* accounts share any device with this account (mule-farm signal)."""
        share: set[str] = set()
        for i in self._out.get(account, ()):
            e = self.edges[i]
            if e["type"] != "USED_DEVICE":
                continue
            share.update(self.accounts_on_device(e["dst"]))
        share.discard(account)
        return len(share)

    def trail(self, txn_id: str, hops: int = 4, direction: str = "both") -> dict:
        """Bounded BFS money-trail subgraph from the PAID edge of a txn."""
        start_edge_idx = self.edge_by_txn.get(txn_id)
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        if start_edge_idx is None:
            return {"nodes": [], "edges": [], "origin": None}
        se = self.edges[start_edge_idx]
        frontier = {se["src"], se["dst"]}
        edges.append(self._edge_json(se))
        nodes[se["src"]] = self._node_json(se["src"])
        nodes[se["dst"]] = self._node_json(se["dst"])
        seen_edges = {start_edge_idx}
        for _ in range(max(0, hops - 1)):
            nxt = set()
            for nid in frontier:
                if direction in ("both", "out"):
                    for i in self._out.get(nid, ()):
                        if i not in seen_edges and self.edges[i]["type"] == "PAID":
                            seen_edges.add(i); edges.append(self._edge_json(self.edges[i]))
                            nxt.add(self.edges[i]["dst"])
                if direction in ("both", "in"):
                    for i in self._in.get(nid, ()):
                        if i not in seen_edges and self.edges[i]["type"] == "PAID":
                            seen_edges.add(i); edges.append(self._edge_json(self.edges[i]))
                            nxt.add(self.edges[i]["src"])
            for nid in nxt:
                nodes[nid] = self._node_json(nid)
            frontier = nxt
        # enrich with one hop of device sharing for the mule-farm story
        device_links = []
        for nid in list(nodes)[:12]:
            acc = self.nodes.get(nid, {})
            if acc.get("type") != "Account":
                continue
            for i in self._out.get(nid, ()):
                e = self.edges[i]
                if e["type"] != "USED_DEVICE":
                    continue
                sharers = [a for a in self.accounts_on_device(e["dst"]) if a != nid]
                if len(sharers) >= 3:
                    device_links.append({"device": e["dst"], "account": nid, "shared_by": len(sharers)})
        return {"origin": txn_id, "nodes": list(nodes.values()), "edges": edges, "device_sharing": device_links}

    def distance_to_flagged(self, account: str, max_hops: int = 3) -> Optional[int]:
        """BFS over PAID edges (both directions) + shared-device links, bounded."""
        seen = {account}
        frontier = {account}
        for depth in range(1, max_hops + 1):
            nxt = set()
            for nid in frontier:
                for i in [*self._out.get(nid, ()), *self._in.get(nid, ())]:
                    e = self.edges[i]
                    if self.is_money(e):
                        other = e["dst"] if e["src"] == nid else e["src"]
                        if other not in seen:
                            if other in self.flagged:
                                return depth
                            seen.add(other); nxt.add(other)
                    elif e["type"] == "USED_DEVICE":
                        for other in self.accounts_on_device(e["dst"]):
                            if other not in seen:
                                if other in self.flagged:
                                    return depth
                                seen.add(other); nxt.add(other)
            frontier = nxt
        return None

    # ---------- warm-path jobs (async in the PRD; run every N seconds in demo) ----------
    def refresh_warm_properties(self, store) -> None:
        """network_risk, communities (shared-device components), cycle members."""
        t = now()
        # network risk per account
        for acc in store.accounts.values():
            fi = self.fan_in(acc.id, 86400)
            io = store.inflow_outflow(acc.id, since=t - 86400)
            # attempted outflow counts too: a blocked cash-out is still evidence of intent
            attempted_out = sum(self.edges[i]["props"].get("amount", 0) for i in self._out.get(acc.id, ())
                                if self.edges[i]["type"] == "PAID" and self.edges[i]["props"].get("ts", 0) >= t - 86400)
            passthrough = (max(io["outflow"], attempted_out) / io["inflow"]) if io["inflow"] > 0 else 0.0
            shared = self.shared_device_count(acc.id)
            hold = store.median_hold_minutes(acc.id)
            risk = 0.0
            risk += min(fi["distinct_sources"] / 10.0, 1.0) * 30
            risk += min((passthrough - 0.5) / 0.5, 1.0) * 25 if 0.5 < passthrough <= 1.5 else 0
            risk += min(shared / 6.0, 1.0) * 25
            if acc.age_days < 30 and fi["distinct_sources"] >= 3:
                risk += 10
            if hold is not None and hold < 30 and fi["distinct_sources"] >= 3:
                risk += 10
            if acc.id in self.flagged:
                risk += 15
            if acc.dormant_since is not None and fi["txns"] >= 3:
                risk += 10
            if acc.fri_level in ("HIGH", "VERY_HIGH"):
                risk += 10
            self.network_risk[acc.id] = round(min(risk, 100.0), 1)
        # communities: connected components over shared devices/phones
        parent: dict[str, str] = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        for acc_id in store.accounts:
            find(acc_id)
        dev_groups = defaultdict(list)
        for i in self.edges:
            if i["type"] == "USED_DEVICE":
                dev_groups[i["dst"]].append(i["src"])
        for members in dev_groups.values():
            for m in members[1:]:
                union(members[0], m)
        comp_ids: dict[str, int] = {}
        for acc_id in store.accounts:
            root = find(acc_id)
            if root not in comp_ids:
                comp_ids[root] = len(comp_ids)
            self.community[acc_id] = comp_ids[root]
        # bounded cycle detection (3..6 hops, value retention >= 0.8)
        self.cycle_members = set()
        rng_check = list(store.accounts)[:600]
        for start in rng_check:
            path = [(start, 0.0)]
            self._dfs_cycles(start, start, depth=0, max_depth=6, path=path, visited={start})

    def _dfs_cycles(self, start: str, cur: str, depth: int, max_depth: int,
                    path: list, visited: set) -> None:
        if depth >= max_depth:
            return
        for i in self._out.get(cur, ()):
            e = self.edges[i]
            if not self.is_money(e):
                continue
            nxt = e["dst"]
            amt = e["props"].get("amount", 0)
            if nxt == start and depth >= 2:
                first = path[1][1] if len(path) > 1 else 0.0
                retained = (amt / first) if first else 0.0
                if retained >= 0.8:
                    self.cycle_members.update(n for n, _ in path)
                return
            if nxt in visited or nxt not in self.nodes:
                continue
            if self.nodes[nxt].get("type") != "Account":
                continue
            visited.add(nxt)
            path.append((nxt, amt))
            self._dfs_cycles(start, nxt, depth + 1, max_depth, path, visited)
            path.pop()
            visited.discard(nxt)

    # ---------- serialisation for the UI ----------
    def _node_json(self, node_id: str) -> Optional[dict]:
        n = self.nodes.get(node_id)
        if not n:
            return None
        return {
            "id": n["id"], "type": n["type"], "label": n["label"],
            "risk": self.network_risk.get(node_id, 0.0) if n["type"] == "Account" else 0.0,
            "flagged": node_id in self.flagged,
            "cycle_member": node_id in self.cycle_members,
            "community": self.community.get(node_id),
            "props": {k: v for k, v in n["props"].items() if k != "balance"},
        }

    def _edge_json(self, e: dict) -> dict:
        return {
            "src": e["src"], "dst": e["dst"], "type": e["type"],
            "props": {k: v for k, v in e["props"].items()},
        }

    def subgraph_for(self, node_ids: list[str], include_edge_types: Optional[set] = None) -> dict:
        include = include_edge_types or {"PAID", "USED_DEVICE", "LINKED_PHONE", "SEEN_FROM", "HAS_VPA"}
        ids = set(node_ids)
        edges = []
        for e in self.edges:
            if e["type"] in include and e["src"] in ids and e["dst"] in ids:
                edges.append(self._edge_json(e))
        nodes = [self._node_json(nid) for nid in ids if nid in self.nodes]
        return {"nodes": [n for n in nodes if n], "edges": edges}

    def stats(self) -> dict:
        by_type = defaultdict(int)
        for n in self.nodes.values():
            by_type[n["type"]] += 1
        by_edge = defaultdict(int)
        for e in self.edges:
            by_edge[e["type"]] += 1
        return {"nodes": dict(by_type), "edges": dict(by_edge),
                "total_nodes": len(self.nodes), "total_edges": len(self.edges)}
