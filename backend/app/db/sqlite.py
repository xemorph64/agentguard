"""SQLite persistence: hash-chain ledger, cases, policy versions, kv counters.

Demo mode runs on this embedded store; a PostgreSQL adapter can take its place
behind the same interface (PRD lists Postgres for ledger/cases/policy).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  txn_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  score REAL NOT NULL,
  payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_sandbox (
  seq INTEGER PRIMARY KEY,
  ts REAL, txn_id TEXT, decision TEXT, score REAL,
  payload TEXT, prev_hash TEXT, hash TEXT
);
CREATE TABLE IF NOT EXISTS merkle_anchors (
  hour_key TEXT PRIMARY KEY,
  root TEXT NOT NULL,
  count INTEGER NOT NULL,
  anchored_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cases (
  case_id TEXT PRIMARY KEY,
  created REAL NOT NULL,
  status TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
  version TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  yaml TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


class Db:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def wipe(self) -> None:
        with self._lock:
            self.conn.executescript(
                "DELETE FROM ledger; DELETE FROM ledger_sandbox; DELETE FROM merkle_anchors;"
                " DELETE FROM cases; DELETE FROM policies; DELETE FROM kv;"
            )
            self.conn.execute("DELETE FROM sqlite_sequence WHERE name='ledger'")
            self.conn.commit()

    # ---------- kv ----------
    def kv_get(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self.conn.commit()

    def kv_inc(self, key: str, by: int = 1) -> int:
        val = (self.kv_get(key, 0) or 0) + by
        self.kv_set(key, val)
        return val

    # ---------- policies ----------
    def save_policy(self, version: str, yaml_text: str, active: bool = False) -> None:
        import time
        with self._lock:
            self.conn.execute(
                "INSERT INTO policies (version, ts, yaml, active) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(version) DO UPDATE SET yaml=excluded.yaml, active=excluded.active",
                (version, time.time(), yaml_text, 1 if active else 0),
            )
            if active:
                self.conn.execute("UPDATE policies SET active=0 WHERE version != ?", (version,))
            self.conn.commit()

    def list_policies(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT version, ts, yaml, active FROM policies ORDER BY ts DESC").fetchall()
        return [dict(r) for r in rows]

    # ---------- ledger ----------
    def ledger_count(self, table: str = "ledger") -> int:
        return self.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]

    def ledger_last(self, table: str = "ledger"):
        return self.conn.execute(
            f"SELECT * FROM {table} ORDER BY seq DESC LIMIT 1").fetchone()

    def ledger_append(self, ts, txn_id, decision, score, payload, prev_hash, h) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO ledger (ts, txn_id, decision, score, payload, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, txn_id, decision, score, payload, prev_hash, h),
            )
            self.conn.commit()
            return cur.lastrowid

    def ledger_rows(self, table: str = "ledger", from_seq: int = 0, to_seq: int | None = None,
                    limit: int = 500, offset: int = 0) -> list[dict]:
        q = f"SELECT * FROM {table} WHERE seq >= ?"
        args: list = [from_seq]
        if to_seq is not None:
            q += " AND seq <= ?"
            args.append(to_seq)
        q += " ORDER BY seq ASC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def ledger_all_hashes(self, table: str = "ledger") -> list[tuple[int, str]]:
        rows = self.conn.execute(f"SELECT seq, hash FROM {table} ORDER BY seq").fetchall()
        return [(r["seq"], r["hash"]) for r in rows]

    def sandbox_load_from_ledger(self) -> int:
        with self._lock:
            self.conn.execute("DELETE FROM ledger_sandbox")
            self.conn.execute("INSERT INTO ledger_sandbox SELECT * FROM ledger")
            self.conn.commit()
        return self.ledger_count("ledger_sandbox")

    def sandbox_mutate(self, seq: int, field: str, value) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT payload FROM ledger_sandbox WHERE seq=?", (seq,)).fetchone()
            if not row:
                return 0
            payload = json.loads(row["payload"])
            payload[field] = value
            self.conn.execute(
                "UPDATE ledger_sandbox SET payload=?, score=? WHERE seq=?",
                (json.dumps(payload, sort_keys=True, separators=(",", ":")),
                 payload.get("score", 0), seq),
            )
            self.conn.commit()
        return seq

    def sandbox_seq_count(self) -> int:
        return self.ledger_count("ledger_sandbox")

    # ---------- merkle ----------
    def save_anchor(self, hour_key: str, root: str, count: int) -> None:
        import time
        with self._lock:
            self.conn.execute(
                "INSERT INTO merkle_anchors (hour_key, root, count, anchored_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(hour_key) DO UPDATE SET root=excluded.root, count=excluded.count,"
                " anchored_at=excluded.anchored_at",
                (hour_key, root, count, time.time()),
            )
            self.conn.commit()

    def list_anchors(self, limit: int = 24) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM merkle_anchors ORDER BY hour_key DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------- cases ----------
    def save_case(self, case_id: str, created: float, status: str, payload: dict) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO cases (case_id, created, status, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(case_id) DO UPDATE SET status=excluded.status, payload=excluded.payload",
                (case_id, created, status, json.dumps(payload)),
            )
            self.conn.commit()

    def list_cases(self, status: str | None = None, limit: int = 200) -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT payload FROM cases WHERE status=? ORDER BY created DESC LIMIT ?",
                (status, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT payload FROM cases ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def get_case(self, case_id: str) -> dict | None:
        row = self.conn.execute("SELECT payload FROM cases WHERE case_id=?", (case_id,)).fetchone()
        return json.loads(row["payload"]) if row else None
