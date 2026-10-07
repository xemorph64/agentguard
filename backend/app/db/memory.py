"""In-memory behavioral store.

This plays the role of Cassandra (per-account time-bucketed history, profiles)
and Redis (velocity counters) in the PRD architecture. It keeps the same query
shapes so a real Cassandra/Redis adapter can drop in behind the same methods.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Optional

from ..core.types import Account, Complaint, Hold, Txn, now

WINDOW_1M, WINDOW_1H, WINDOW_24H = 60, 3600, 86400


class MemoryStore:
    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}
        self.vpa_index: dict[str, str] = {}            # vpa -> account id
        self.txns: dict[str, Txn] = {}                 # txn_id -> Txn
        self.txn_history: deque = deque(maxlen=6000)   # recent txns (all)
        # account -> deque[(ts, direction, amount, counterparty, txn_id)]
        self.ledger_moves: dict[str, deque] = defaultdict(lambda: deque(maxlen=400))
        self.holds: dict[str, Hold] = {}               # hold_id -> Hold
        self.hold_by_txn: dict[str, str] = {}
        self.complaints: dict[str, Complaint] = {}
        self.killswitch: dict[str, dict] = {}          # account -> {active, channels, since}
        self.decisions: deque = deque(maxlen=2500)     # recent Decision.to_dict() for backtest
        self.pending: dict[str, dict] = {}             # txn_id -> pending cooling/step-up state
        self.txn_seq = 0
        self.demo_personas: dict[str, str] = {}        # name -> account id (Meera, Rahul...)

    # ---------- accounts ----------
    def add_account(self, acc: Account) -> Account:
        self.accounts[acc.id] = acc
        self.vpa_index[acc.vpa.lower()] = acc.id
        return acc

    def by_vpa(self, vpa: str) -> Optional[Account]:
        aid = self.vpa_index.get((vpa or "").lower().strip())
        return self.accounts.get(aid) if aid else None

    def demo(self, name: str) -> Account:
        return self.accounts[self.demo_personas[name]]

    # ---------- txn index / velocity ----------
    def record_txn(self, txn: Txn, settled: bool = True) -> None:
        self.txns[txn.txn_id] = txn
        self.txn_history.append(txn)
        if settled:
            # only settled movement feeds velocity/flow features (blocked = never happened)
            self.ledger_moves[txn.payer].append((txn.ts, "out", txn.amount, txn.payee, txn.txn_id))
            self.ledger_moves[txn.payee].append((txn.ts, "in", txn.amount, txn.payer, txn.txn_id))

    def _window(self, account: str, window_s: float, direction: Optional[str] = None) -> list:
        t0 = now() - window_s
        return [
            m for m in self.ledger_moves.get(account, ())
            if m[0] >= t0 and (direction is None or m[1] == direction)
        ]

    def velocity(self, account: str) -> dict:
        """Velocity counters over 1m/1h/24h — the Redis-shaped hot features."""
        out = {}
        for name, w in (("1m", WINDOW_1M), ("1h", WINDOW_1H), ("24h", WINDOW_24H)):
            outs = self._window(account, w, "out")
            ins = self._window(account, w, "in")
            out[f"count_out_{name}"] = len(outs)
            out[f"sum_out_{name}"] = sum(m[2] for m in outs)
            out[f"count_in_{name}"] = len(ins)
            out[f"sum_in_{name}"] = sum(m[2] for m in ins)
            if name in ("1h", "24h"):
                out[f"distinct_payees_{name}"] = len({m[3] for m in outs})
                out[f"distinct_sources_{name}"] = len({m[3] for m in ins})
        micros = [m for m in self._window(account, 600, "out") if m[2] <= 50]
        out["micro_burst_10m"] = len(micros)
        return out

    def inflow_outflow(self, account: str, since: float = 0.0) -> dict:
        ins = [m for m in self.ledger_moves.get(account, ()) if m[0] >= since and m[1] == "in"]
        outs = [m for m in self.ledger_moves.get(account, ()) if m[0] >= since and m[1] == "out"]
        return {
            "inflow": sum(m[2] for m in ins), "outflow": sum(m[2] for m in outs),
            "in_count": len(ins), "out_count": len(outs),
            "distinct_sources": len({m[3] for m in ins}),
            "first_in_ts": min((m[0] for m in ins), default=None),
            "last_out_ts": max((m[0] for m in outs), default=None),
        }

    def median_hold_minutes(self, account: str) -> Optional[float]:
        """Time between an inbound credit and the following outbound debit."""
        moves = sorted(self.ledger_moves.get(account, ()))
        holds = []
        last_in = None
        for ts, direction, _amt, _cp, _tid in moves:
            if direction == "in":
                last_in = ts
            elif direction == "out" and last_in is not None:
                holds.append((ts - last_in) / 60.0)
                last_in = None
        if not holds:
            return None
        holds.sort()
        n = len(holds)
        return holds[n // 2] if n % 2 else (holds[n // 2 - 1] + holds[n // 2]) / 2

    # ---------- holds ----------
    def add_hold(self, hold: Hold) -> Hold:
        self.holds[hold.hold_id] = hold
        self.hold_by_txn[hold.txn_id] = hold.hold_id
        return hold

    def active_holds(self) -> list:
        return [h for h in self.holds.values() if h.status in ("active", "reversal_requested")]
