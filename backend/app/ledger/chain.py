"""Tamper-evident decision ledger: SHA-256 hash chain + Merkle anchors + sandbox.

hash = sha256(prev_hash || canonical_json(record))  — genuine, recomputable.
`simulate tampering` mutates a row in a sandbox COPY; verification then breaks
at the exact sequence. The live chain is never modified.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


GENESIS = "0" * 64


class Ledger:
    def __init__(self, db) -> None:
        self.db = db
        self.tampered_seq: Optional[int] = None

    # ---------- append ----------
    def append(self, record: dict) -> dict:
        """Assigns seq/prev_hash/hash and appends. Returns the full ledger record."""
        last = self.db.ledger_last()
        seq = (last["seq"] + 1) if last else 1
        prev_hash = last["hash"] if last else GENESIS
        body = {k: v for k, v in record.items() if k not in ("seq", "prev_hash", "hash")}
        body["seq"] = seq
        body["prev_hash"] = prev_hash
        h = sha256_hex(prev_hash + canonical_json(body))
        body["hash"] = h
        self.db.ledger_append(
            ts=body.get("ts", 0), txn_id=body.get("txn_id", ""),
            decision=body.get("decision", ""), score=body.get("score", 0),
            payload=canonical_json(body), prev_hash=prev_hash, h=h,
        )
        return body

    # ---------- verify ----------
    def verify(self, mode: str = "live", from_seq: int = 0, to_seq: Optional[int] = None) -> dict:
        table = "ledger_sandbox" if mode == "sandbox" else "ledger"
        if mode == "sandbox" and self.db.sandbox_seq_count() == 0:
            return {"valid": True, "mode": "sandbox", "note": "sandbox empty — nothing tampered yet",
                    "records": 0, "anchor": self.current_anchor()}
        rows = self.db.ledger_rows(table, from_seq=from_seq, to_seq=to_seq, limit=100000)
        prev = None
        broken = None
        checked = 0
        for r in rows:
            body = json.loads(r["payload"])
            recomputed = sha256_hex(body.get("prev_hash", GENESIS) + canonical_json(
                {k: v for k, v in body.items() if k != "hash"}))
            # (1) record integrity: stored hash must equal hash of its own content
            if recomputed != r["hash"]:
                broken = {"seq": body.get("seq", r["seq"]), "reason": "record hash mismatch",
                          "expected": recomputed, "found": r["hash"]}
                break
            # (2) chain linkage: prev_hash must reference the previous record's hash
            if prev is not None and body.get("prev_hash") != prev["hash"]:
                broken = {"seq": body.get("seq", r["seq"]), "reason": "prev_hash linkage broken",
                          "expected": prev["hash"], "found": body.get("prev_hash")}
                break
            prev = body
            checked += 1
        result = {
            "valid": broken is None, "mode": mode, "records": checked,
            "first_seq": rows[0]["seq"] if rows else 0,
            "last_seq": rows[-1]["seq"] if rows else 0,
            "broken": broken,
            "anchor": self.current_anchor(),
        }
        return result

    def get_by_seq(self, seq: int, mode: str = "live") -> Optional[dict]:
        table = "ledger_sandbox" if mode == "sandbox" else "ledger"
        rows = self.db.ledger_rows(table, from_seq=seq, to_seq=seq, limit=1)
        return json.loads(rows[0]["payload"]) if rows else None

    def find(self, *, receipt_id: str = "", txn_id: str = "") -> Optional[dict]:
        for r in self.db.ledger_rows(limit=100000):
            body = json.loads(r["payload"])
            if receipt_id and body.get("receipt_id") == receipt_id:
                return body
            if txn_id and body.get("txn_id") == txn_id:
                return body
        return None

    def recent(self, limit: int = 50) -> list[dict]:
        rows = self.db.ledger_rows(limit=100000)
        return [json.loads(r["payload"]) for r in rows[-limit:]][::-1]

    # ---------- merkle ----------
    def merkle_root(self, hashes: list[str]) -> str:
        if not hashes:
            return sha256_hex("empty")
        level = list(hashes)
        while len(level) > 1:
            if len(level) % 2:
                level.append(level[-1])
            level = [sha256_hex(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
        return level[0]

    def current_anchor(self) -> dict:
        last = self.db.ledger_last()
        if not last:
            return {"hour_key": None, "root": None, "count": 0}
        hour_key = datetime.fromtimestamp(last["ts"], timezone.utc).strftime("%Y%m%d%H")
        hour_hashes = []
        for r in self.db.ledger_rows(limit=100000):
            body = json.loads(r["payload"])
            hk = datetime.fromtimestamp(body["ts"], timezone.utc).strftime("%Y%m%d%H")
            if hk == hour_key:
                hour_hashes.append(r["hash"])
        return {"hour_key": hour_key, "root": self.merkle_root(hour_hashes), "count": len(hour_hashes)}

    def anchor_hourly(self) -> dict:
        """Persist an anchor for the current hour (background job; hourly in prod, 5-min in demo)."""
        a = self.current_anchor()
        if a["hour_key"]:
            self.db.save_anchor(a["hour_key"], a["root"], a["count"])
        return a

    # ---------- sandbox tamper / restore ----------
    def tamper(self) -> dict:
        n = self.db.sandbox_load_from_ledger()
        if n == 0:
            return {"ok": False, "error": "ledger empty"}
        # pick a middle record and quietly change its amount (what a fraudster would try)
        rows = self.db.ledger_rows(limit=100000)
        target = rows[len(rows) // 2]
        seq = target["seq"]
        body = json.loads(target["payload"])
        old = body.get("amount")
        new = round((old or 1000) * 0.9, 2)
        self.db.sandbox_mutate(seq, "amount", new)
        self.tampered_seq = seq
        return {"ok": True, "seq": seq, "field": "amount", "was": old, "now": new}

    def restore(self) -> dict:
        self.db.sandbox_load_from_ledger()
        self.tampered_seq = None
        return {"ok": True}
