"""Live transaction graph (NetworkX): fan-in/out, round-trip cycles, ring clusters."""
import networkx as nx


class LiveGraph:
    def __init__(self):
        self.G = nx.DiGraph()
        self.flagged = set()        # accounts confirmed/blocked as mule or laundering
        self._cache = {}

    def add_txn(self, src, dst, amount):
        if self.G.has_edge(src, dst):
            e = self.G[src][dst]; e["w"] += float(amount); e["n"] += 1
        else:
            self.G.add_edge(src, dst, w=float(amount), n=1)
        self._cache.clear()

    def flag(self, acct):
        self.flagged.add(acct); self._cache.clear()

    def _neighborhood(self, acct, radius=2, hub_degree=40, cap=400):
        """2-hop undirected neighbourhood via BFS. Hubs (merchants) are included but not expanded."""
        G, seen, frontier = self.G, {acct}, {acct}
        for _ in range(radius):
            nxt = set()
            for n in frontier:
                if n != acct and G.degree(n) > hub_degree:
                    continue
                nxt |= set(G.successors(n)) | set(G.predecessors(n))
            frontier = nxt - seen
            seen |= frontier
            if len(seen) >= cap:
                break
        return seen

    def graph_features(self, acct):
        if acct in self._cache:
            return self._cache[acct]
        G = self.G
        empty = {"fan_in": 0, "fan_out": 0, "in_cycle": False, "flagged_neighbors": 0,
                 "reach_2hop": 0, "cluster_size": 1, "cluster_density": 0.0, "cluster_suspicious": False}
        if acct not in G:
            return empty
        sub = G.subgraph(self._neighborhood(acct))
        cyc = False
        if len(sub) <= 400:                       # skip hub explosions (merchants)
            for i, c in enumerate(nx.simple_cycles(sub, length_bound=5)):
                if acct in c: cyc = True; break
                if i > 300: break
        flagged_nb = len((self.flagged & set(sub.nodes)) - {acct})
        comm = {n for n in sub.nodes if n == acct or G.degree(n) <= 40}     # cluster = 2-hop non-hub neighbourhood
        dens = nx.density(sub.subgraph(comm)) if len(comm) > 1 else 0.0
        fl_ratio = len(self.flagged & comm) / max(len(comm), 1)
        out = {"fan_in": G.in_degree(acct), "fan_out": G.out_degree(acct), "in_cycle": cyc,
               "flagged_neighbors": flagged_nb, "reach_2hop": len(sub) - 1,
               "cluster_size": len(comm), "cluster_density": round(dens, 3),
               "cluster_suspicious": len(comm) >= 4 and (cyc or dens > .3 or fl_ratio > .2)}
        self._cache[acct] = out
        return out

    def graph_score(self, acct):
        """0..1 structural risk."""
        g = self.graph_features(acct)
        return min(1.0, .40 * g["in_cycle"] + .25 * min(g["flagged_neighbors"] / 2, 1)
                   + .20 * min(g["fan_in"] / 10, 1) * (g["fan_out"] > 0)
                   + .15 * g["cluster_suspicious"])

    def export_for_ui(self, max_component=60):
        """Nodes/edges for the React graph view; suspicious ring components are red."""
        nodes, edges = [], []
        for comp in nx.weakly_connected_components(self.G):
            if not (3 <= len(comp) <= max_component):
                continue
            sub = self.G.subgraph(comp)
            has_cycle = any(True for _ in nx.simple_cycles(sub, length_bound=5)) if len(comp) <= 30 else False
            sus = has_cycle or bool(self.flagged & comp) or nx.density(sub) > .3
            nodes += [{"id": n, "suspicious": sus or n in self.flagged} for n in comp]
            edges += [{"source": u, "target": v, "weight": d["w"]} for u, v, d in sub.edges(data=True)]
        return {"nodes": nodes, "edges": edges}

    def export_subgraph(self, seeds, suspicious_seeds=(), max_nodes=250, hub_degree=40):
        """Neighbourhood (1 hop, hubs not expanded) of seed accounts; ring members & flagged accounts are red."""
        G, nodes = self.G, set()
        for a in seeds:
            if a not in G: continue
            nodes.add(a)
            if G.degree(a) <= hub_degree:
                nodes |= set(G.successors(a)) | set(G.predecessors(a))
            if len(nodes) >= max_nodes: break
        sub = G.subgraph(nodes)
        in_cycle = set()
        if len(sub) <= 300:
            for i, c in enumerate(nx.simple_cycles(sub, length_bound=5)):
                in_cycle |= set(c)
                if i > 300: break
        bad = set(suspicious_seeds) | self.flagged
        return {"nodes": [{"id": n, "suspicious": n in bad or n in in_cycle, "in_cycle": n in in_cycle,
                           "flagged": n in self.flagged, "degree": G.degree(n)} for n in sub.nodes],
                "edges": [{"source": u, "target": v, "weight": round(d["w"], 2), "count": d["n"]} for u, v, d in sub.edges(data=True)]}
