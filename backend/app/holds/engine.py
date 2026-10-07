"""Hold engine — risk-proportional lien on beneficiary credits (PRD §5.3).

The payer is debited as usual; the beneficiary's credit sits in lien until the
timer expires, an analyst releases it, or it is reversed as fraud.
"""
from __future__ import annotations

import asyncio

from ..core.types import Decision, Hold, Txn, now


class HoldEngine:
    def __init__(self, svcs) -> None:
        self.svcs = svcs
        self._task: asyncio.Task | None = None

    # ---------- lifecycle ----------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._expirer())

    async def _expirer(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            t = now()
            for hold in list(self.svcs.store.holds.values()):
                if hold.status == "active" and hold.release_at <= t:
                    self.release(hold.hold_id, by="auto", note="timer expired with no complaint")

    # ---------- mutations ----------
    def create(self, txn: Txn, d: Decision, minutes: int) -> Hold:
        hold = Hold(
            hold_id=f"H-{txn.txn_id}", txn_id=txn.txn_id, beneficiary=txn.payee, payer=txn.payer,
            amount=txn.amount, created_ts=now(), release_at=now() + minutes * 60,
            reason=d.routing_reason, typology=d.dominant or "",
        )
        self.svcs.store.add_hold(hold)
        return hold

    def release(self, hold_id: str, by: str = "analyst", note: str = "") -> Hold | None:
        hold = self.svcs.store.holds.get(hold_id)
        if not hold or hold.status not in ("active", "reversal_requested"):
            return None
        hold.status = "released" if by != "auto" else "expired"
        hold.reason = (hold.reason + " · " if hold.reason else "") + (note or f"released by {by}")
        self.svcs.metrics.totals["released_count"] += 1
        self.svcs.hub.publish("hold_released", {**hold.to_dict(), "by": by})
        return hold

    def extend(self, hold_id: str, minutes: int) -> Hold | None:
        hold = self.svcs.store.holds.get(hold_id)
        if not hold or hold.status != "active":
            return None
        hold.release_at += minutes * 60
        self.svcs.hub.publish("hold_extended", hold.to_dict())
        return hold

    def request_reversal(self, hold_id: str) -> Hold | None:
        """Customer pressed 'I've been scammed, stop this' while the hold is live."""
        hold = self.svcs.store.holds.get(hold_id)
        if not hold or hold.status != "active":
            return None
        hold.status = "reversal_requested"
        self.svcs.hub.publish("hold_reversal_requested", hold.to_dict())
        return hold

    def reverse(self, hold_id: str) -> Hold | None:
        """Chase confirmed fraud → money moves back to the payer."""
        hold = self.svcs.store.holds.get(hold_id)
        if not hold or hold.status not in ("active", "reversal_requested"):
            return None
        hold.status = "reversed"
        payer, payee = self.svcs.store.accounts.get(hold.payer), self.svcs.store.accounts.get(hold.beneficiary)
        if payer:
            payer.balance += hold.amount
        if payee:
            payee.balance = max(0.0, payee.balance - hold.amount)
        self.svcs.metrics.totals["recovered"] += hold.amount
        self.svcs.hub.publish("hold_reversed", hold.to_dict())
        return hold

    def held_total(self, account_id: str) -> float:
        return sum(h.amount for h in self.svcs.store.holds.values()
                   if h.beneficiary == account_id and h.status in ("active", "reversal_requested"))
