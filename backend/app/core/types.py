"""Core domain types for AgentGuard.

All money amounts are INR (floats). Timestamps are epoch seconds (float, UTC).
Display timezone is IST (+05:30) fixed offset — no tzdata dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

IST = timezone(timedelta(hours=5, minutes=30))

OUTCOMES = (
    "ALLOW", "ALLOW_NUDGE", "STEP_UP", "COOLING_ROOM",
    "HOLD_CREDIT", "PAUSE", "BLOCK",
)

# Outcome display metadata (used by Explainer + frontend)
OUTCOME_META = {
    "ALLOW": {"label": "Allowed", "severity": 0},
    "ALLOW_NUDGE": {"label": "Allowed + Nudge", "severity": 1},
    "STEP_UP": {"label": "Step-Up Auth", "severity": 2},
    "COOLING_ROOM": {"label": "Cooling Room", "severity": 3},
    "HOLD_CREDIT": {"label": "Hold Credit (Lien)", "severity": 3},
    "PAUSE": {"label": "Paused for Review", "severity": 3},
    "BLOCK": {"label": "Blocked", "severity": 4},
}

TYPOLOGIES = {
    "T1": "Digital arrest / authority impersonation",
    "T2": "Investment / task scam",
    "T3": "Account takeover (SIM swap)",
    "T4": "Remote-access / screen-share",
    "T5": "QR code swap at merchant",
    "T6": "Fake refund / collect abuse",
    "T7": "Micro-payment probing",
    "T8": "Mule account: fan-in collector",
    "T9": "Mule chain / layering",
    "T10": "Round-tripping",
    "T11": "Structuring (smurfing)",
    "T12": "Rented / sold accounts (mule farm)",
    "T13": "Dormant account awakening",
    "T14": "Scatter-gather",
}


def now() -> float:
    return datetime.now(timezone.utc).timestamp()


def ist(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, IST)


def ist_str(ts: float) -> str:
    return ist(ts).strftime("%d %b %Y · %H:%M:%S IST")


@dataclass
class Txn:
    """A UPI transaction request (or a historical, already-settled one)."""
    txn_id: str
    ts: float
    payer: str                 # account id
    payee: str                 # account id
    amount: float
    channel: str = "p2p"       # p2p | p2m | collect | netbanking
    device_fp: str = ""
    geo: str = ""              # geo-hash, e.g. "PUNE-W"
    on_call: bool = False
    call_minutes: float = 0.0
    screen_share: bool = False
    sim_changed_hrs_ago: Optional[float] = None
    pin_reset_hrs_ago: Optional[float] = None
    cooling_override: bool = False   # victim proceeded after Cooling Room
    stepup_failed: bool = False      # device re-auth failed (ATO resolution)
    label: Optional[str] = None      # ground-truth typology (synthetic data only)
    raw_vpa: str = ""
    source: str = "api"              # api | app | theatre | traffic | inject
    run_id: str = ""                 # theatre run this txn belongs to


@dataclass
class AgentResult:
    agent: str
    score: float               # 0..100 risk (Counsel: legitimacy 0..100)
    confidence: float = 0.5
    signals: list = field(default_factory=list)   # [{feature, value, weight, note}]
    features: dict = field(default_factory=dict)
    latency_ms: float = 0.0
    status: str = "ok"         # ok | timeout | error | unknown

    def to_dict(self) -> dict:
        return {
            "agent": self.agent, "score": round(self.score, 1),
            "confidence": round(self.confidence, 3),
            "signals": self.signals, "features": self.features,
            "latency_ms": round(self.latency_ms, 2), "status": self.status,
        }


@dataclass
class Decision:
    txn_id: str
    outcome: str
    score: float
    typologies: list           # [(T#, confidence)] serialised as dicts
    dominant: Optional[str]
    agent_results: list
    hard_rule: Optional[str]   # which hard rule fired, if any
    policy_version: str
    routing_reason: str
    customer_message: str      # payer-side
    beneficiary_message: str   # generic, non-tipping
    total_latency_ms: float
    features: dict
    ts: float
    receipt_id: str = ""
    hold_minutes: Optional[int] = None
    ablation: Optional[dict] = None   # scores without graph features (for adaptive demo)
    counterfactual: Optional[dict] = None

    def to_dict(self, with_agents: bool = True) -> dict:
        d = {
            "txn_id": self.txn_id, "outcome": self.outcome, "score": round(self.score, 1),
            "typologies": self.typologies, "dominant": self.dominant,
            "hard_rule": self.hard_rule, "policy_version": self.policy_version,
            "routing_reason": self.routing_reason,
            "customer_message": self.customer_message,
            "beneficiary_message": self.beneficiary_message,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "ts": self.ts, "receipt_id": self.receipt_id,
            "hold_minutes": self.hold_minutes,
            "counterfactual": getattr(self, "counterfactual", None),
        }
        if with_agents:
            d["agents"] = [a.to_dict() for a in self.agent_results]
            d["features"] = self.features
            if self.ablation:
                d["ablation"] = self.ablation
        return d


@dataclass
class Hold:
    hold_id: str
    txn_id: str
    beneficiary: str
    payer: str
    amount: float
    created_ts: float
    release_at: float
    status: str = "active"     # active | released | expired | reversed | reversal_requested
    reason: str = ""
    typology: str = ""

    def to_dict(self) -> dict:
        return {
            "hold_id": self.hold_id, "txn_id": self.txn_id,
            "beneficiary": self.beneficiary, "payer": self.payer,
            "amount": self.amount, "created_ts": self.created_ts,
            "release_at": self.release_at, "status": self.status,
            "seconds_left": max(0, int(self.release_at - now())),
            "reason": self.reason, "typology": self.typology,
        }


@dataclass
class Case:
    case_id: str
    txn_id: str
    created_ts: float
    status: str                # paused | hold | released | blocked | chase | closed
    amount: float
    typology: str
    outcome: str
    debate_log: list
    counterfactual: Optional[dict] = None
    actions: list = field(default_factory=list)
    notes: str = ""
    sla_due_ts: float = 0.0
    priority: float = 0.0      # amount x risk, drives queue order
    complaint_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id, "txn_id": self.txn_id,
            "created_ts": self.created_ts, "status": self.status,
            "amount": self.amount, "typology": self.typology,
            "outcome": self.outcome, "debate_log": self.debate_log,
            "counterfactual": self.counterfactual, "actions": self.actions,
            "notes": self.notes, "sla_due_ts": self.sla_due_ts,
            "sla_seconds_left": max(0, int(self.sla_due_ts - now())),
            "priority": round(self.priority, 1),
            "complaint_id": self.complaint_id,
        }


@dataclass
class Account:
    id: str
    name: str
    persona: str               # elderly | salaried | gig | student | merchant | biz | mule | ring
    vpa: str
    phone: str
    devices: list = field(default_factory=list)
    ifsc: str = ""
    city: str = ""
    balance: float = 10000.0
    age_days: int = 365
    kyc_tier: str = "full"
    fri_level: str = "LOW"     # from linked phone: LOW | MEDIUM | HIGH | VERY_HIGH
    cluster: str = ""          # family/social cluster id
    dormant_since: Optional[float] = None
    mcc: Optional[str] = None  # merchant category code if merchant
    verified_merchant: bool = False
    sanctioned: bool = False
    avg_amt_90d: float = 1500.0
    std_amt_90d: float = 600.0
    usual_hours: tuple = (8, 23)
    salary_day: Optional[int] = None
    known_payees: dict = field(default_factory=dict)   # account_id -> [ts, ...]
    known_devices: set = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "persona": self.persona,
            "vpa": self.vpa, "phone": self.phone, "devices": self.devices,
            "ifsc": self.ifsc, "city": self.city,
            "balance": round(self.balance, 2), "age_days": self.age_days,
            "kyc_tier": self.kyc_tier, "fri_level": self.fri_level,
            "cluster": self.cluster, "mcc": self.mcc,
            "verified_merchant": self.verified_merchant,
            "sanctioned": self.sanctioned,
            "avg_amt_90d": round(self.avg_amt_90d, 2),
            "std_amt_90d": round(self.std_amt_90d, 2),
            "dormant": self.dormant_since is not None,
        }


@dataclass
class Complaint:
    complaint_id: str
    txn_id: str
    victim: str
    amount: float
    reported_at: float
    channel: str               # 1930-sim | app | ncrp
    golden_deadline: float     # reported_at + 60 min
    status: str = "open"       # open | frozen | recovered | closed
    recovered: float = 0.0

    def to_dict(self) -> dict:
        return {
            "complaint_id": self.complaint_id, "txn_id": self.txn_id,
            "victim": self.victim, "amount": self.amount,
            "reported_at": self.reported_at, "channel": self.channel,
            "golden_deadline": self.golden_deadline,
            "golden_seconds_left": max(0, int(self.golden_deadline - now())),
            "status": self.status, "recovered": round(self.recovered, 2),
        }
