"""Behavior Agent: Isolation Forest anomaly + device intelligence + geo (impossible travel) + session signals."""
import math, joblib
from pathlib import Path
import numpy as np, pandas as pd
from ..orchestrator import BaseAgent, AgentResult
from ..channel_rules import noisy_or

MODEL = Path(__file__).parents[1] / "models" / "saved" / "behavior_iforest.joblib"


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


class BehaviorAgent(BaseAgent):
    name = "behavior"

    def __init__(self, history, model_path=MODEL):
        a = joblib.load(model_path)
        self.iso, self.features, self.ecdf = a["model"], a["features"], a["ecdf"]
        self.history = history

    def analyze(self, txn, ctx):
        comps, why, flags = [], [], []
        f = ctx.get("tf") or self.history.txn_features(txn)

        # 1) unsupervised anomaly: only the top ~10% most unusual transactions contribute
        x = pd.DataFrame([f])[self.features]
        pct = float(np.searchsorted(self.ecdf, -self.iso.score_samples(x)[0]) / len(self.ecdf))
        if pct > .90:
            comps.append(min(1.0, (pct - .90) / .10) * .60); why.append(f"behaviour in the top {100*(1-pct):.1f}% most unusual (Isolation Forest)")

        # 2) device intelligence
        dev, src = txn.get("device_id"), txn["src"]
        known = self.history.devices.get(src, set())
        new_device = bool(dev and known and dev not in known)
        if new_device:
            comps.append(.35); why.append("new device for this customer"); flags.append("BEH_NEW_DEVICE")
        if dev and len(self.history.device_accts.get(dev, ())) >= 4:
            comps.append(.70); why.append(f"device shared by {len(self.history.device_accts[dev])} accounts (possible mule farm)")
            flags.append("BEH_SHARED_DEVICE")

        # 3) impossible travel
        last = self.history.last_loc.get(src)
        if last and txn.get("lat") is not None:
            hrs = max((pd.Timestamp(txn["ts"]).timestamp() - last[0]) / 3600, 1 / 60)
            km = haversine_km(last[1], last[2], txn["lat"], txn["lon"])
            speed = km / hrs
            if km > 100 and speed > 900:
                comps.append(.75); why.append(f"impossible travel: {km:.0f} km in {hrs*60:.0f} min"); flags.append("BEH_IMPOSSIBLE_TRAVEL")
            elif km > 100 and speed > 300 and hrs < 6:
                comps.append(.35); why.append(f"fast travel: {km:.0f} km in {hrs:.1f} h")

        # 4) IP country vs home country
        if txn.get("ip_country") and txn.get("home_country") and txn["ip_country"] != txn["home_country"]:
            comps.append(.30); why.append(f"IP country {txn['ip_country']} differs from home {txn['home_country']}")

        # 5) bot-like session: large payment seconds after login
        if txn.get("session_age_s") is not None and txn["session_age_s"] < 5 and txn["amount"] >= 20_000:
            comps.append(.35); why.append("large payment within seconds of login (bot-like)"); flags.append("BEH_BOT_LIKE")

        score = round(100 * noisy_or(comps), 1)
        return AgentResult(self.name, score, "; ".join(why) or "behaviour consistent with history", flags,
                           {"anomaly_percentile": round(pct, 3), "new_device": new_device})
