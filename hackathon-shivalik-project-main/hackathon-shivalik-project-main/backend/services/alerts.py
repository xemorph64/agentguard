"""Mock SMS / email alerts (log only). Replace .add() with a real provider later."""
import time


class Alerts:
    def __init__(self):
        self.log = []

    def add(self, kind, to, text, txn_id=None):
        self.log.append({"ts": time.time(), "kind": kind, "to": to, "text": text, "txn_id": txn_id})
        self.log = self.log[-500:]
