"""Tamper-evident audit ledger: every entry stores the hash of the previous one (hash chain)."""
import hashlib, json


class Ledger:
    def __init__(self):
        self.chain = []
        self._backup = {}

    @staticmethod
    def _h(prev, payload):
        return hashlib.sha256((prev + json.dumps(payload, sort_keys=True, default=str)).encode()).hexdigest()

    def append(self, record):
        payload = {k: record.get(k) for k in ("txn_id", "decision", "score", "flags", "latency_ms")}
        payload["reasons"] = (record.get("explanation") or {}).get("audit_reasons")
        prev = self.chain[-1]["hash"] if self.chain else "GENESIS"
        h = self._h(prev, payload)
        self.chain.append({"index": len(self.chain), "prev": prev, "payload": payload, "hash": h})
        return h

    def verify(self):
        prev = "GENESIS"
        for e in self.chain:
            if e["prev"] != prev or self._h(prev, e["payload"]) != e["hash"]:
                return {"valid": False, "broken_at": e["index"]}
            prev = e["hash"]
        return {"valid": True, "length": len(self.chain)}

    def tamper(self, index=0):                      # demo button: silently edit an old decision
        e = self.chain[index]
        self._backup.setdefault(index, dict(e["payload"]))
        e["payload"]["decision"] = "SUCCESS" if e["payload"]["decision"] != "SUCCESS" else "BLOCK"

    def repair(self):                               # DEMO ONLY: restore tampered payloads
        for i, p in self._backup.items():
            self.chain[i]["payload"] = p
        self._backup.clear()
