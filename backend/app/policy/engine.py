"""Policy engine: versioned YAML policies, routing matrix, backtest."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Optional

import yaml

from ..core.types import Decision, Txn, now

DEFAULT_POLICY_PATH = Path(__file__).with_name("default_policy.yaml")


class PolicyEngine:
    def __init__(self, db) -> None:
        self.db = db
        self.policy: dict = {}
        self.active_version: str = ""

    # ---------- lifecycle ----------
    def load_default(self) -> dict:
        data = yaml.safe_load(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
        self.activate(data, persist=True)
        return data

    def activate(self, data: dict, persist: bool = True) -> str:
        version = data.get("policy_version")
        if not version:
            raise ValueError("policy_version is required")
        self.policy = data
        self.active_version = version
        if persist:
            self.db.save_policy(version, yaml.safe_dump(data, sort_keys=False), active=True)
        return version

    def restore_latest(self) -> dict:
        rows = self.db.list_policies()
        if rows:
            self.policy = yaml.safe_load(rows[0]["yaml"])
            self.active_version = rows[0]["version"]
            return self.policy
        return self.load_default()

    # ---------- accessors ----------
    @property
    def th(self) -> dict:
        return self.policy["thresholds"]

    def route_for(self, typology: str) -> str:
        return self.policy.get("routing", {}).get(typology, "score_bands")

    # ---------- routing: (score, dominant typology, state) -> outcome ----------
    def route(self, score: int, typologies: list, dominant: Optional[str],
              cooling_override: bool = False, stepup_failed: bool = False,
              hard_rule: Optional[str] = None, hard_outcome: Optional[str] = None,
              mule_cashout: bool = False) -> tuple[str, str, Optional[int]]:
        """Returns (outcome, reason, hold_minutes)."""
        t = self.th
        if hard_rule:
            return hard_outcome or "BLOCK", f"hard rule: {hard_rule}", None

        # scripted escalation: victim acknowledged the Cooling Room and proceeded
        if cooling_override and dominant in ("T1", "T2"):
            minutes = self.hold_minutes(dominant, score, 1.0)
            return "HOLD_CREDIT", "coercion typology continued after Cooling Room → hold credit", minutes

        if stepup_failed:
            return "BLOCK", "step-up re-authentication failed", None

        # the payer itself is the suspected mule: holding the beneficiary's credit would let the
        # layering continue one hop later — stop the cash-out at source
        if mule_cashout and score >= t["stepup"]:
            return "BLOCK", f"{dominant}: suspected mule forwarding received funds → block cash-out", None

        conf = typologies[0]["confidence"] if typologies else 0.0
        if dominant and conf >= 0.4:
            route = self.route_for(dominant)
            if route == "cooling_room" and score >= t["cooling"]:
                return "COOLING_ROOM", f"{dominant} coercion signals → cooling room", None
            if route == "step_up" and score >= t["stepup"]:
                return "STEP_UP", f"{dominant} account-takeover signals → device re-auth", None
            if route == "hold_credit" and score >= t["cooling"]:
                minutes = self.hold_minutes(dominant, score, 1.0)
                return "HOLD_CREDIT", f"{dominant} mule-side risk → hold beneficiary credit", minutes
            if route == "rate_limit_block" and score >= t["stepup"]:
                return "BLOCK", f"{dominant} probing pattern → rate-limit and block", None
            if route == "silent_monitor":
                # AML: never tip off. Watch silently; escalate only if score is high.
                esc = self.policy.get("silent_monitor_escalation", {})
                if score >= t["pause_high"] or (conf >= esc.get("confidence", 2.0) and score >= esc.get("score", 101)):
                    return "PAUSE", f"{dominant} high-confidence ring pattern → quiet analyst review", None
                return "ALLOW_NUDGE", f"{dominant} → silent monitoring, no customer friction", None
            # score below the typology's friction gate → nudge only
            return ("ALLOW_NUDGE", f"{dominant} signals below friction gate", None) \
                if score > t["allow"] else ("ALLOW", "low risk", None)

        # no dominant typology → score bands
        if score >= t["block"]:
            return "BLOCK", f"score {score} ≥ {t['block']}", None
        if t["pause_low"] <= score:
            return "PAUSE", f"score {score} ambiguous → analyst review", None
        if score > t["nudge"]:
            return "ALLOW_NUDGE", f"score {score} → contextual nudge", None
        if score > t["allow"]:
            return "ALLOW_NUDGE", f"score {score} → light nudge", None
        return "ALLOW", f"score {score} ≤ {t['allow']}", None

    def hold_minutes(self, typology: Optional[str], score: int, amount_ratio: float) -> int:
        cfg = self.policy["hold"]
        base = cfg["base_minutes"].get(typology or "default", cfg["base_minutes"]["default"])
        f = 0.5 + score / 200.0
        g = min(max(amount_ratio, 0.8), 2.0)
        return int(min(max(base * f * g, cfg["min_minutes"]), cfg["max_minutes"]))

    # ---------- backtest: re-route historical decisions under a candidate policy ----------
    def backtest(self, candidate: dict, decisions: list[dict]) -> dict:
        saved_policy, saved_version = self.policy, self.active_version
        try:
            self.policy = copy.deepcopy(candidate)
            result = self._replay(self._snapshot_policy(), decisions)
        finally:
            self.policy, self.active_version = saved_policy, saved_version
        baseline = self._replay(None, decisions)
        return {
            "candidate_version": candidate.get("policy_version", "candidate"),
            "baseline": baseline,
            "candidate": result,
            "diff": {
                "fraud_caught": result["fraud_caught"] - baseline["fraud_caught"],
                "fraud_value_caught": result["fraud_value_caught"] - baseline["fraud_value_caught"],
                "legit_delayed": result["legit_delayed"] - baseline["legit_delayed"],
                "legit_blocked": result["legit_blocked"] - baseline["legit_blocked"],
            },
        }

    def _snapshot_policy(self) -> dict:
        return copy.deepcopy(self.policy)

    def _replay(self, override_policy: Optional[dict], decisions: list[dict]) -> dict:
        if override_policy is not None:
            self.policy = override_policy
        caught = delayed = blocked_good = 0
        value_caught = 0.0
        for d in decisions:
            outcome, _reason, _m = self.route(
                int(round(d["score"])), d.get("typologies") or [], d.get("dominant"),
                cooling_override=bool(d.get("cooling_override")),
                stepup_failed=bool(d.get("stepup_failed")),
                hard_rule=d.get("hard_rule"),
                hard_outcome=("HOLD_CREDIT" if d.get("hard_rule") == "ncrp_reported_beneficiary" else "BLOCK"),
            )
            friction = outcome in ("ALLOW_NUDGE", "STEP_UP", "COOLING_ROOM", "HOLD_CREDIT", "PAUSE", "BLOCK")
            hard_friction = outcome in ("STEP_UP", "COOLING_ROOM", "HOLD_CREDIT", "PAUSE", "BLOCK")
            if d.get("label"):
                if hard_friction:
                    caught += 1
                    value_caught += d.get("amount", 0)
            else:
                if outcome == "BLOCK":
                    blocked_good += 1
                elif friction:
                    delayed += 1
        return {
            "fraud_caught": caught,
            "fraud_value_caught": round(value_caught, 0),
            "legit_delayed": delayed,
            "legit_blocked": blocked_good,
        }
