"""AgentGuard pipeline: wires history, graph, velocity, lists, ledger, 5 scoring agents, orchestrator, explainer."""
import uuid
import pandas as pd
from .orchestrator import Orchestrator
from .services.history import AccountHistory
from .services.graph import LiveGraph
from .services.velocity import VelocityStore
from .services.lists import Lists
from .services.ledger import Ledger
from .agents.transaction import TransactionAgent
from .agents.behavior import BehaviorAgent
from .agents.velocity import VelocityAgent
from .agents.mule import MuleAgent
from .agents.aml import AMLAgent
from .agents.explainer import ExplainerAgent


class AgentGuard:
    def __init__(self, use_llm=False):
        self.history, self.graph, self.vel = AccountHistory(), LiveGraph(), VelocityStore()
        self.lists, self.ledger = Lists(), Ledger()
        agents = [TransactionAgent(self.history), BehaviorAgent(self.history), VelocityAgent(self.vel),
                  MuleAgent(self.history, self.graph), AMLAgent(self.history, self.graph)]
        self._tf = {}
        self.orch = Orchestrator(agents, ExplainerAgent(use_llm), self.ledger, self.lists, ctx_builder=self._ctx)

    def _ctx(self, txn):                      # shared features computed ONCE per txn
        tf = self.history.txn_features(txn)
        self._tf[txn["txn_id"]] = tf
        dev, known = txn.get("device_id"), self.history.devices.get(txn["src"], set())
        return {"tf": tf, "new_payee": bool(tf["new_payee"]), "new_device": bool(dev and known and dev not in known)}

    @staticmethod
    def _norm(t):
        t = dict(t)
        t["ts"] = pd.Timestamp(t["ts"])
        t.setdefault("txn_id", uuid.uuid4().hex[:12])
        t.setdefault("channel", "UPI"); t.setdefault("src_bank", 0); t.setdefault("dst_bank", 0); t.setdefault("cross_border", 0)
        return t

    def record(self, t):                      # learn from a transaction without scoring it (warm-up / replay)
        self.history.record(t); self.graph.add_txn(t["src"], t["dst"], t["amount"]); self.vel.record(t)

    def process(self, txn, learn=True):
        txn = self._norm(txn)
        out = self.orch.analyze(txn)
        out["features"] = self._tf.pop(txn["txn_id"], None)
        if learn:
            self.record(txn)
            if out["decision"] == "BLOCK" and any(f.startswith(("MULE_", "AML_", "KNOWN_MULE")) for f in out["flags"]):
                self.graph.flag(txn["dst"])   # confirmed bad payee strengthens graph signals for its neighbours
        return out
