"""BehaviorAgent — device, SIM/PIN, call state, screen-share, travel."""
from __future__ import annotations

from .base import BaseAgent, Ctx, clip

WEIGHTS = {
    "screen_share": 0.26, "new_device": 0.16, "sim_change_72h": 0.18, "pin_reset_24h": 0.12,
    "on_call": 0.12, "impossible_travel": 0.08, "hour_deviation": 0.08,
}

# demo geo-hash coordinates (km) for impossible-travel checks
GEO_KM = {
    "PUNE-W": (0, 0), "PUNE-E": (18, 6), "MUMBAI": (120, 40), "NAGPUR": (700, -120),
    "DELHI": (1400, 1100), "JAIPUR": (1150, 950), "PATNA": (1500, -60),
    "BENGALURU": (150, -740), "KOLKATA": (1650, -90), "LUCKNOW": (1350, 400),
}


class BehaviorAgent(BaseAgent):
    name = "behavior"
    timeout_ms = 15

    def features(self, ctx: Ctx) -> dict:
        t, p = ctx.txn, ctx.payer
        device_known = t.device_fp in p.known_devices if t.device_fp else True
        last = None
        moves = ctx.store.ledger_moves.get(p.id, ())
        if moves:
            last = moves[-1]
        travel_kmh = 0.0
        if last is not None and t.geo:
            # infer previous geo from the payer's last txn payload if recorded
            prev_geo = ctx.store.txns.get(last[4]).geo if last[4] in ctx.store.txns else None
            if prev_geo and prev_geo in GEO_KM and t.geo in GEO_KM and t.geo != prev_geo:
                dt_h = max(t.ts - last[0], 1e-6) / 3600.0
                (x1, y1), (x2, y2) = GEO_KM[prev_geo], GEO_KM[t.geo]
                travel_kmh = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 / dt_h
        hour = int((t.ts % 86400 // 3600 + 5.5) % 24)
        return {
            "screen_share": bool(ctx.ov("screen_share", t.screen_share)),
            "new_device": bool(ctx.ov("new_device", not device_known)),
            "sim_change_72h": (ctx.ov("sim_change_hrs_ago", t.sim_changed_hrs_ago) or 999) <= 72,
            "pin_reset_24h": (ctx.ov("pin_reset_hrs_ago", t.pin_reset_hrs_ago) or 999) <= 24,
            "on_call": bool(ctx.ov("on_call", t.on_call)),
            "call_minutes": ctx.ov("call_minutes", t.call_minutes),
            "impossible_travel_kmh": round(travel_kmh, 0),
            "hour_deviation": not (p.usual_hours[0] <= hour <= p.usual_hours[1]),
        }

    def score(self, f: dict, ctx: Ctx) -> tuple[float, list]:
        call_f = clip((f["call_minutes"] or 0) / 30.0) if f["on_call"] else 0.0
        c = {
            "screen_share": WEIGHTS["screen_share"] * (1.0 if f["screen_share"] else 0.0),
            "new_device": WEIGHTS["new_device"] * (1.0 if f["new_device"] else 0.0),
            "sim_change_72h": WEIGHTS["sim_change_72h"] * (1.0 if f["sim_change_72h"] else 0.0),
            "pin_reset_24h": WEIGHTS["pin_reset_24h"] * (1.0 if f["pin_reset_24h"] else 0.0),
            "on_call": WEIGHTS["on_call"] * call_f,
            "impossible_travel": WEIGHTS["impossible_travel"] * clip(f["impossible_travel_kmh"] / 900.0),
            "hour_deviation": WEIGHTS["hour_deviation"] * (0.5 if f["hour_deviation"] else 0.0),
        }
        return sum(c.values()) * 100, self._signals(f, c)

    def confidence(self, f: dict, score: float) -> float:
        hard = f["screen_share"] or (f["sim_change_72h"] and f["new_device"])
        return 0.92 if hard else 0.7
